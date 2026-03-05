"""
Mess-Steuerung: Webanwendung für die Prüfplan-gesteuerte Messungsplanung.

Liest PDF-Prüfpläne ein und sagt dem Werker anhand des Teilezählers,
welche Maße wann und wie zu messen sind.
"""

import os
import re
import json
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import pdfplumber

app = Flask(__name__, static_folder="static")
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


def parse_pruefintervall(text):
    """Parse Prüfintervall string to an integer (every N parts).

    Examples:
        'Jedes Teil'       -> 1
        '1 aus 20 Teilen'  -> 20
        '1 aus 100 Teilen' -> 100
    """
    if not text:
        return 1
    text = text.strip()
    if text.lower().startswith("jedes"):
        return 1
    match = re.search(r"1\s+aus\s+(\d+)", text)
    if match:
        return int(match.group(1))
    # Fallback: try to find any number
    nums = re.findall(r"\d+", text)
    if nums:
        return int(nums[-1])
    return 1


def extract_pruefplan_from_pdf(filepath):
    """Extract inspection plan rows from a PDF file.

    Returns a dict with header info and a list of measurement positions.
    """
    header = {}
    positions = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 2:
                        continue

                    # Try to extract header info
                    cells = [c.strip() if c else "" for c in row]
                    for i, cell in enumerate(cells):
                        if "Artikel-Nr" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["artikel_nr"] = cells[i + 1]
                        if "Bezeichnung" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["bezeichnung"] = cells[i + 1]
                        if "Zeichnungs-Nr" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["zeichnungs_nr"] = cells[i + 1]

                    # Try to parse as measurement row
                    # Expected: Pos.Nr | Prüfmerkmal | UT | OT | Messmittel | Prüfintervall | Dokumentation | Kapa Kürzel
                    if len(cells) >= 7:
                        pos_nr = cells[0]
                        # Skip header rows
                        if "Pos" in pos_nr or "Nr" in pos_nr:
                            continue
                        # Validate: pos_nr should be a number (possibly with decimal like 5.1)
                        if not re.match(r"^\d+\.?\d*$", pos_nr.replace(",", ".")):
                            continue

                        pruefmerkmal = cells[1]
                        ut = cells[2] if len(cells) > 2 else ""
                        ot = cells[3] if len(cells) > 3 else ""
                        messmittel = cells[4] if len(cells) > 4 else ""
                        pruefintervall_text = cells[5] if len(cells) > 5 else ""
                        dokumentation = cells[6] if len(cells) > 6 else ""
                        kapa_kuerzel = cells[7].strip() if len(cells) > 7 and cells[7] else ""

                        intervall = parse_pruefintervall(pruefintervall_text)

                        positions.append({
                            "pos_nr": pos_nr,
                            "pruefmerkmal": pruefmerkmal,
                            "ut": ut,
                            "ot": ot,
                            "messmittel": messmittel,
                            "pruefintervall_text": pruefintervall_text,
                            "pruefintervall": intervall,
                            "dokumentation": dokumentation,
                            "kapa_kuerzel": kapa_kuerzel,
                        })

    return {"header": header, "positions": positions}


# --- API Routes ---

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/upload", methods=["POST"])
def upload_pdf():
    """Upload a Prüfplan PDF and extract measurement data."""
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei hochgeladen"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Keine Datei ausgewählt"}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF-Dateien werden unterstützt"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    try:
        data = extract_pruefplan_from_pdf(filepath)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"Fehler beim Einlesen: {str(e)}"}), 500
    finally:
        # Clean up uploaded file
        if os.path.exists(filepath):
            os.remove(filepath)


@app.route("/api/manual-entry", methods=["POST"])
def manual_entry():
    """Accept manually entered inspection plan data (JSON)."""
    data = request.get_json()
    if not data or "positions" not in data:
        return jsonify({"error": "Ungültige Daten"}), 400
    # Recalculate intervals from text if needed
    for pos in data["positions"]:
        if "pruefintervall" not in pos and "pruefintervall_text" in pos:
            pos["pruefintervall"] = parse_pruefintervall(pos["pruefintervall_text"])
    return jsonify(data)


@app.route("/api/check-measurements", methods=["POST"])
def check_measurements():
    """Given a part count and positions, return which measurements are due now."""
    data = request.get_json()
    part_count = data.get("part_count", 0)
    positions = data.get("positions", [])
    kapa_filter = data.get("kapa_filter", "")

    due = []
    for pos in positions:
        # Filter by Kapa Kürzel if set
        if kapa_filter and pos.get("kapa_kuerzel", "") != kapa_filter:
            continue
        interval = pos.get("pruefintervall", 1)
        if interval <= 0:
            interval = 1
        if part_count % interval == 0:
            due.append(pos)

    return jsonify({"part_count": part_count, "due_measurements": due})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

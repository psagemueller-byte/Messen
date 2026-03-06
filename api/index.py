"""
Mess-Steuerung: Serverless API für Vercel.

Liest PDF-Prüfpläne ein und sagt dem Werker anhand des Teilezählers,
welche Maße wann und wie zu messen sind.
"""

import os
import re
import json
import tempfile
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import pdfplumber

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB


def parse_pruefintervall(text):
    """Parse Prüfintervall string to an integer (every N parts)."""
    if not text:
        return 1
    text = text.strip()
    if text.lower().startswith("jedes"):
        return 1
    match = re.search(r"1\s+aus\s+(\d+)", text)
    if match:
        return int(match.group(1))
    nums = re.findall(r"\d+", text)
    if nums:
        return int(nums[-1])
    return 1


def extract_pruefplan_from_pdf(filepath):
    """Extract inspection plan rows from a PDF file.

    Detects column layout dynamically by scanning table headers for known
    keywords so that the parser works regardless of column order or count.
    """
    header = {}
    positions = []

    # Known header keywords → field mapping
    HEADER_KEYWORDS = {
        "pos": "pos_nr",
        "nr": "pos_nr",
        "prüfmerkmal": "pruefmerkmal",
        "merkmal": "pruefmerkmal",
        "nennmaß": "pruefmerkmal",
        "nennmass": "pruefmerkmal",
        "ut": "ut",
        "untere": "ut",
        "ot": "ot",
        "obere": "ot",
        "messmittel": "messmittel",
        "meßmittel": "messmittel",
        "prüfintervall": "pruefintervall_text",
        "intervall": "pruefintervall_text",
        "häufigkeit": "pruefintervall_text",
        "dokumentation": "dokumentation",
        "doku": "dokumentation",
        "aufzeichnung": "dokumentation",
        "kapa": "kapa_kuerzel",
        "kapazität": "kapa_kuerzel",
        "abteilung": "kapa_kuerzel",
        "abt": "kapa_kuerzel",
    }

    with pdfplumber.open(filepath) as pdf:
        col_map = None  # index → field name

        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 2:
                        continue

                    cells = [c.strip() if c else "" for c in row]

                    # --- Try to extract document header info ---
                    for i, cell in enumerate(cells):
                        if "Artikel-Nr" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["artikel_nr"] = cells[i + 1]
                        if "Bezeichnung" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["bezeichnung"] = cells[i + 1]
                        if "Zeichnungs-Nr" in cell and i + 1 < len(cells) and cells[i + 1]:
                            header["zeichnungs_nr"] = cells[i + 1]

                    # --- Detect column headers ---
                    if col_map is None:
                        detected = {}
                        for i, cell in enumerate(cells):
                            cell_lower = cell.lower().strip()
                            for keyword, field in HEADER_KEYWORDS.items():
                                if keyword in cell_lower and field not in detected.values():
                                    detected[i] = field
                                    break
                        # Accept if we found at least pos_nr and one more field
                        if "pos_nr" in detected.values() and len(detected) >= 3:
                            col_map = detected
                            continue  # skip header row itself

                    # --- Parse data rows using detected column map ---
                    if col_map is None:
                        # Fallback: no header detected yet, skip
                        continue

                    # Get pos_nr column
                    pos_col = [i for i, f in col_map.items() if f == "pos_nr"]
                    if not pos_col:
                        continue
                    pos_nr = cells[pos_col[0]] if pos_col[0] < len(cells) else ""

                    # Skip non-data rows
                    if not pos_nr:
                        continue
                    if not re.match(r"^\d+\.?\d*$", pos_nr.replace(",", ".")):
                        continue

                    row_data = {
                        "pos_nr": "",
                        "pruefmerkmal": "",
                        "ut": "",
                        "ot": "",
                        "messmittel": "",
                        "pruefintervall_text": "",
                        "dokumentation": "",
                        "kapa_kuerzel": "",
                    }

                    for col_idx, field in col_map.items():
                        if col_idx < len(cells):
                            row_data[field] = cells[col_idx]

                    row_data["pruefintervall"] = parse_pruefintervall(row_data["pruefintervall_text"])
                    row_data["kapa_kuerzel"] = row_data["kapa_kuerzel"].strip().upper()

                    positions.append(row_data)

    return {"header": header, "positions": positions}


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

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        try:
            data = extract_pruefplan_from_pdf(tmp.name)
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": f"Fehler beim Einlesen: {str(e)}"}), 500
        finally:
            os.unlink(tmp.name)


@app.route("/api/debug-pdf", methods=["POST"])
def debug_pdf():
    """Upload a PDF and return raw table data for debugging column mapping."""
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        try:
            raw_tables = []
            with pdfplumber.open(tmp.name) as pdf:
                for pi, page in enumerate(pdf.pages):
                    for ti, table in enumerate(page.extract_tables()):
                        raw_tables.append({
                            "page": pi + 1,
                            "table": ti + 1,
                            "rows": [[c.strip() if c else "" for c in row] for row in table if row],
                        })
            return jsonify({"tables": raw_tables})
        finally:
            os.unlink(tmp.name)


@app.route("/api/manual-entry", methods=["POST"])
def manual_entry():
    """Accept manually entered inspection plan data (JSON)."""
    data = request.get_json()
    if not data or "positions" not in data:
        return jsonify({"error": "Ungültige Daten"}), 400
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
        if kapa_filter and pos.get("kapa_kuerzel", "") != kapa_filter:
            continue
        interval = pos.get("pruefintervall", 1)
        if interval <= 0:
            interval = 1
        if part_count % interval == 0:
            due.append(pos)

    return jsonify({"part_count": part_count, "due_measurements": due})

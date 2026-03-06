"""
Mess-Steuerung: Serverless API für Vercel.

Liest PDF-Prüfpläne ein und sagt dem Werker anhand des Teilezählers,
welche Maße wann und wie zu messen sind.
"""

import os
import re
import json
import math
import base64
import tempfile
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import pdfplumber
import fitz  # PyMuPDF

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


@app.route("/api/upload-drawing", methods=["POST"])
def upload_drawing():
    """Upload a technical drawing PDF. Extracts red circle markers with position
    numbers and renders page 1 as a PNG image for display.

    Returns JSON:
        image: base64-encoded PNG of page 1
        markers: [ { pos_nr: "1", x: 0.12, y: 0.34 }, ... ]
    """
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei hochgeladen"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF-Dateien werden unterstützt"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        try:
            result = extract_drawing_markers(tmp.name)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": f"Fehler: {str(e)}"}), 500
        finally:
            os.unlink(tmp.name)


def extract_drawing_markers(filepath):
    """Extract red circle markers and their labels from a technical drawing PDF.

    Strategy:
    1. Use PyMuPDF to get all drawing paths on page 1
    2. Find paths that are red circles/ellipses (small, roughly square bbox)
    3. Extract all text blocks with positions
    4. Match each red circle to the nearest text (the position number)
    5. Render page as PNG for frontend display
    """
    doc = fitz.open(filepath)
    page = doc[0]
    pw = page.rect.width
    ph = page.rect.height

    # --- Step 1: Find red circles ---
    red_circles = []

    for drawing in page.get_drawings():
        # Check if the path contains red color (stroke or fill)
        stroke_color = drawing.get("color")
        fill_color = drawing.get("fill")

        is_red_stroke = is_red(stroke_color)
        is_red_fill = is_red(fill_color)

        if not is_red_stroke and not is_red_fill:
            continue

        # Get bounding rect of this drawing
        rect = drawing.get("rect")
        if not rect:
            continue
        r = fitz.Rect(rect)
        bw = r.width
        bh = r.height

        # Filter: circle markers are small and roughly square
        # Typical marker: 5-25 pt diameter on A3/A4 drawing
        if bw < 2 or bh < 2:
            continue
        if bw > pw * 0.08 or bh > ph * 0.08:
            continue  # too large
        aspect = max(bw, bh) / max(min(bw, bh), 0.1)
        if aspect > 2.5:
            continue  # not circle-like

        # Check if path contains curves (circles use 'c' items = bezier curves)
        items = drawing.get("items", [])
        has_curves = any(item[0] == "c" for item in items)
        if not has_curves:
            continue  # circles are made of bezier curves, not lines

        cx = (r.x0 + r.x1) / 2
        cy = (r.y0 + r.y1) / 2
        radius = max(bw, bh) / 2

        red_circles.append({
            "cx": cx, "cy": cy,
            "radius": radius,
            "x": cx / pw,  # relative 0-1
            "y": cy / ph,
        })

    # --- Step 2: Extract all text with positions ---
    text_blocks = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox = span.get("bbox", [0, 0, 0, 0])
                tcx = (bbox[0] + bbox[2]) / 2
                tcy = (bbox[1] + bbox[3]) / 2
                text_blocks.append({"text": text, "cx": tcx, "cy": tcy})

    # --- Step 3: Match circles to nearest number text ---
    markers = []
    used_texts = set()

    for circle in red_circles:
        best_text = None
        best_dist = float("inf")
        search_radius = circle["radius"] * 3  # look within 3x radius

        for i, tb in enumerate(text_blocks):
            if i in used_texts:
                continue
            # Check if text looks like a position number
            clean = tb["text"].replace(".", "").replace(",", "").strip()
            if not re.match(r"^\d+$", clean):
                continue
            dist = math.sqrt((circle["cx"] - tb["cx"]) ** 2 +
                             (circle["cy"] - tb["cy"]) ** 2)
            if dist < search_radius and dist < best_dist:
                best_dist = dist
                best_text = (i, clean)

        if best_text:
            used_texts.add(best_text[0])
            markers.append({
                "pos_nr": best_text[1],
                "x": circle["x"],
                "y": circle["y"],
            })

    # --- Step 4: Render page as PNG ---
    # Render at 150 DPI for good quality without being too large
    mat = fitz.Matrix(150 / 72, 150 / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    doc.close()

    return {
        "image": f"data:image/png;base64,{image_b64}",
        "markers": markers,
        "page_width": pw,
        "page_height": ph,
        "detected_circles": len(red_circles),
        "matched_markers": len(markers),
    }


def is_red(color):
    """Check if a color tuple represents red."""
    if not color or not isinstance(color, (list, tuple)):
        return False
    if len(color) < 3:
        return False
    r, g, b = color[0], color[1], color[2]
    # Red: high R, low G, low B
    return r > 0.6 and g < 0.35 and b < 0.35

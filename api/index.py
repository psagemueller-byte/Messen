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

    Uses three complementary strategies:
    A) Find red-colored text that is a number (most reliable – the number IS the label)
    B) Find red circle/ellipse drawing paths, match to nearest number text
    C) Find PDF annotations (circles, stamps) that are red

    All strategies contribute to a merged result. Duplicates (same pos_nr or
    very close position) are deduplicated.
    """
    doc = fitz.open(filepath)
    page = doc[0]
    pw = page.rect.width
    ph = page.rect.height

    markers = {}  # pos_nr -> {x, y} to deduplicate

    # === Strategy A: Red-colored number text ===
    # This is the most reliable: the position number inside each red circle
    # is rendered as red text. PyMuPDF gives us text color per span.
    text_dict = page.get_text("dict")
    all_texts = []  # collect all text for strategy B matching

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                bbox = span.get("bbox", [0, 0, 0, 0])
                tcx = (bbox[0] + bbox[2]) / 2
                tcy = (bbox[1] + bbox[3]) / 2
                color_int = span.get("color", 0)
                # PyMuPDF encodes span color as integer: 0xRRGGBB
                sr = ((color_int >> 16) & 0xFF) / 255.0
                sg = ((color_int >> 8) & 0xFF) / 255.0
                sb = (color_int & 0xFF) / 255.0

                all_texts.append({
                    "text": text, "cx": tcx, "cy": tcy,
                    "is_red": is_red_rgb(sr, sg, sb),
                })

                # Check: red text that is a simple number (1-999)
                clean = text.replace(".", "").replace(",", "").strip()
                if is_red_rgb(sr, sg, sb) and re.match(r"^\d{1,3}$", clean):
                    num = clean.lstrip("0") or "0"
                    if num not in markers:
                        markers[num] = {"x": tcx / pw, "y": tcy / ph}

    # === Strategy B: Red drawing paths (circles) ===
    red_circles = []
    for drawing in page.get_drawings():
        stroke_color = drawing.get("color")
        fill_color = drawing.get("fill")

        if not is_red_color(stroke_color) and not is_red_color(fill_color):
            continue

        rect = drawing.get("rect")
        if not rect:
            continue
        r = fitz.Rect(rect)
        bw = r.width
        bh = r.height

        # Filter: small, roughly square
        if bw < 2 or bh < 2:
            continue
        if bw > pw * 0.08 or bh > ph * 0.08:
            continue
        aspect = max(bw, bh) / max(min(bw, bh), 0.1)
        if aspect > 2.5:
            continue

        # Circles use bezier curves ('c' items) in PDF
        items = drawing.get("items", [])
        has_curves = any(item[0] == "c" for item in items)
        if not has_curves:
            continue

        cx = (r.x0 + r.x1) / 2
        cy = (r.y0 + r.y1) / 2
        radius = max(bw, bh) / 2
        red_circles.append({"cx": cx, "cy": cy, "radius": radius})

    # Match circles to nearest number text (any color)
    used_texts = set()
    for circle in red_circles:
        best = None
        best_dist = float("inf")
        search_r = circle["radius"] * 3.5

        for i, tb in enumerate(all_texts):
            if i in used_texts:
                continue
            clean = tb["text"].replace(".", "").replace(",", "").strip()
            if not re.match(r"^\d{1,3}$", clean):
                continue
            dist = math.sqrt((circle["cx"] - tb["cx"]) ** 2 +
                             (circle["cy"] - tb["cy"]) ** 2)
            if dist < search_r and dist < best_dist:
                best_dist = dist
                best = (i, clean.lstrip("0") or "0")

        if best:
            used_texts.add(best[0])
            pos_nr = best[1]
            if pos_nr not in markers:
                markers[pos_nr] = {
                    "x": circle["cx"] / pw,
                    "y": circle["cy"] / ph,
                }

    # === Strategy C: PDF annotations ===
    for annot in page.annots() or []:
        # Circle/Square annotations
        if annot.type[0] in (4, 5):  # Circle=4, Square=5
            ac = annot.colors
            stroke = ac.get("stroke")
            fill_c = ac.get("fill")
            if not is_red_color(stroke) and not is_red_color(fill_c):
                continue
            ar = annot.rect
            bw, bh = ar.width, ar.height
            if bw > pw * 0.08 or bh > ph * 0.08:
                continue
            cx = (ar.x0 + ar.x1) / 2
            cy = (ar.y0 + ar.y1) / 2
            # Find nearest number text
            best = None
            best_dist = float("inf")
            for i, tb in enumerate(all_texts):
                clean = tb["text"].replace(".", "").replace(",", "").strip()
                if not re.match(r"^\d{1,3}$", clean):
                    continue
                dist = math.sqrt((cx - tb["cx"]) ** 2 + (cy - tb["cy"]) ** 2)
                if dist < max(bw, bh) * 2 and dist < best_dist:
                    best_dist = dist
                    best = clean.lstrip("0") or "0"
            if best and best not in markers:
                markers[best] = {"x": cx / pw, "y": cy / ph}

    # === Render page as PNG ===
    mat = fitz.Matrix(150 / 72, 150 / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    doc.close()

    # Sort markers by pos_nr numerically
    sorted_markers = sorted(
        [{"pos_nr": k, "x": v["x"], "y": v["y"]} for k, v in markers.items()],
        key=lambda m: int(m["pos_nr"]) if m["pos_nr"].isdigit() else 999,
    )

    return {
        "image": f"data:image/png;base64,{image_b64}",
        "markers": sorted_markers,
        "page_width": pw,
        "page_height": ph,
        "detected_circles": len(red_circles),
        "matched_markers": len(sorted_markers),
    }


@app.route("/api/debug-drawing", methods=["POST"])
def debug_drawing():
    """Debug endpoint: show what PyMuPDF finds in the drawing PDF."""
    if "file" not in request.files:
        return jsonify({"error": "Keine Datei"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Nur PDF"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        try:
            doc = fitz.open(tmp.name)
            page = doc[0]

            # Collect all red drawings
            red_drawings = []
            for drawing in page.get_drawings():
                sc = drawing.get("color")
                fc = drawing.get("fill")
                if is_red_color(sc) or is_red_color(fc):
                    items = drawing.get("items", [])
                    red_drawings.append({
                        "stroke": list(sc) if sc else None,
                        "fill": list(fc) if fc else None,
                        "rect": list(drawing.get("rect", [])),
                        "item_types": [it[0] for it in items],
                        "n_items": len(items),
                    })

            # Collect all red text
            red_texts = []
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        ci = span.get("color", 0)
                        sr = ((ci >> 16) & 0xFF) / 255.0
                        sg = ((ci >> 8) & 0xFF) / 255.0
                        sb = (ci & 0xFF) / 255.0
                        if is_red_rgb(sr, sg, sb):
                            red_texts.append({
                                "text": span.get("text", ""),
                                "bbox": list(span.get("bbox", [])),
                                "color_hex": f"#{ci:06x}",
                                "font": span.get("font", ""),
                            })

            # Collect annotations
            annots = []
            for annot in page.annots() or []:
                annots.append({
                    "type": list(annot.type),
                    "rect": list(annot.rect),
                    "colors": {
                        "stroke": list(annot.colors.get("stroke", [])),
                        "fill": list(annot.colors.get("fill", [])),
                    },
                    "content": annot.info.get("content", ""),
                })

            doc.close()
            return jsonify({
                "red_drawings": red_drawings[:50],
                "red_texts": red_texts[:50],
                "annotations": annots[:50],
                "total_drawings": len(list(page.get_drawings())) if False else "see red_drawings",
            })
        finally:
            os.unlink(tmp.name)


def is_red_rgb(r, g, b):
    """Check if RGB values (0-1 float) represent red."""
    return r > 0.6 and g < 0.35 and b < 0.35


def is_red_color(color):
    """Check if a PyMuPDF color tuple represents red."""
    if not color or not isinstance(color, (list, tuple)):
        return False
    if len(color) < 3:
        return False
    return is_red_rgb(color[0], color[1], color[2])

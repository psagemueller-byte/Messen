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
import cv2
import numpy as np

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
    """Extract red marker pins from a technical drawing PDF.

    Technical drawings often embed the actual drawing as a raster image with
    red pin-shaped markers (circle head + pointer tail) overlaid.  This
    function extracts the embedded image, uses OpenCV to find the red regions,
    isolates each circle head, and reads the position number via OCR.

    Falls back to PyMuPDF vector/text analysis when no embedded images exist.
    """
    doc = fitz.open(filepath)
    page = doc[0]
    pw = page.rect.width
    ph = page.rect.height

    # Check for embedded raster image that covers the page
    img_list = page.get_images(full=True)
    has_fullpage_image = False
    if img_list:
        blocks = page.get_text("dict").get("blocks", [])
        for block in blocks:
            if block.get("type") == 1:  # image block
                bbox = block.get("bbox", [0, 0, 0, 0])
                coverage = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (pw * ph)
                if coverage > 0.5:
                    has_fullpage_image = True
                    break

    if has_fullpage_image:
        markers = _extract_markers_raster(doc, page)
    else:
        markers = _extract_markers_vector(doc, page, pw, ph)

    # Render page as PNG for frontend display
    mat = fitz.Matrix(150 / 72, 150 / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    doc.close()

    sorted_markers = sorted(
        markers,
        key=lambda m: int(m["pos_nr"]) if m["pos_nr"].isdigit() else 999,
    )

    return {
        "image": f"data:image/png;base64,{image_b64}",
        "markers": sorted_markers,
        "page_width": pw,
        "page_height": ph,
        "matched_markers": len(sorted_markers),
    }


def _extract_markers_raster(doc, page):
    """Extract markers from embedded raster image using OpenCV + OCR."""
    xref = page.get_images(full=True)[0][0]
    pix = fitz.Pixmap(doc, xref)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    img_h, img_w = img.shape[:2]

    # Find red regions in HSV color space
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 80, 80]), np.array([10, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([170, 80, 80]), np.array([180, 255, 255]))
    red_mask = mask1 | mask2

    contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    raw_markers = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 100:
            continue
        x, y, w, h = cv2.boundingRect(c)
        hull = cv2.convexHull(c)
        raw_markers.append({"x": x, "y": y, "w": w, "h": h, "hull": hull})

    # Read the number inside each marker's circle head
    markers = []
    for m in raw_markers:
        interior_gray = _extract_marker_interior(img, red_mask, m, img_w, img_h)
        pos_nr = _recognize_number(interior_gray)
        cx = m["x"] + m["w"] // 2
        cy = m["y"] + m["h"] // 2
        markers.append({
            "pos_nr": pos_nr or "?",
            "x": cx / img_w,
            "y": cy / img_h,
        })

    return markers


def _extract_marker_interior(img, red_mask, m, img_w, img_h):
    """Extract the white circle-head interior as a grayscale crop.

    Fills the convex hull of the marker, subtracts red pixels, finds the
    largest remaining blob (the white circle head), and returns only that
    area with everything else masked to white.
    """
    x, y, w, h = m["x"], m["y"], m["w"], m["h"]
    pad = 5
    x1, y1 = max(0, x - pad), max(0, y - pad)
    x2, y2 = min(img_w, x + w + pad), min(img_h, y + h + pad)

    crop = img[y1:y2, x1:x2].copy()
    crop_red = red_mask[y1:y2, x1:x2]
    ch, cw = crop.shape[:2]

    hull_mask = np.zeros((ch, cw), dtype=np.uint8)
    shifted_hull = m["hull"] - np.array([x1, y1])
    cv2.fillConvexPoly(hull_mask, shifted_hull, 255)

    interior = hull_mask & cv2.bitwise_not(crop_red)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(interior)
    if n_labels < 2:
        return None
    largest = max(range(1, n_labels), key=lambda i: stats[i, cv2.CC_STAT_AREA])
    circle_interior = (labels == largest).astype(np.uint8) * 255

    ix, iy, iw, ih = cv2.boundingRect(circle_interior)
    if iw < 5 or ih < 5:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    gray[circle_interior == 0] = 255
    return gray[iy:iy + ih, ix:ix + iw]


def _recognize_number(interior_gray, max_num=50):
    """Recognize a position number using OpenCV template matching.

    Renders each candidate number (1..max_num) with cv2.putText, resizes it
    to match the text crop dimensions, and picks the best match via
    normalized cross-correlation.  No external OCR binary needed.
    """
    if interior_gray is None:
        return ""

    font = cv2.FONT_HERSHEY_SIMPLEX
    best_result = ""
    best_score = -1

    for thresh_val in [140, 160, 180]:
        _, binary = cv2.threshold(interior_gray, thresh_val, 255,
                                  cv2.THRESH_BINARY)
        text_coords = np.where(binary < 128)
        if len(text_coords[0]) < 10:
            continue

        y_min, y_max = text_coords[0].min(), text_coords[0].max()
        x_min, x_max = text_coords[1].min(), text_coords[1].max()
        text_crop = binary[y_min:y_max + 1, x_min:x_max + 1]
        th, tw = text_crop.shape
        if th < 3 or tw < 3:
            continue

        for num in range(1, max_num + 1):
            text = str(num)
            for scale in [0.8, 1.0, 1.2]:
                (rtw, rth), _ = cv2.getTextSize(text, font, scale, 2)
                canvas = np.ones((rth + 10, rtw + 10), dtype=np.uint8) * 255
                cv2.putText(canvas, text, (5, rth + 5), font, scale, 0, 2,
                            cv2.LINE_AA)

                rc = np.where(canvas < 128)
                if len(rc[0]) == 0:
                    continue
                rendered = canvas[rc[0].min():rc[0].max() + 1,
                                  rc[1].min():rc[1].max() + 1]

                resized = cv2.resize(rendered, (tw, th),
                                     interpolation=cv2.INTER_AREA)
                score = cv2.matchTemplate(text_crop, resized,
                                          cv2.TM_CCOEFF_NORMED)
                if score.size > 0 and score.max() > best_score:
                    best_score = score.max()
                    best_result = text

    return best_result if best_score > 0.3 else ""


def _extract_markers_vector(doc, page, pw, ph):
    """Fallback: extract markers from PDF vector data and text colors."""
    markers = {}

    # Strategy A: Red-colored number text
    text_dict = page.get_text("dict")
    all_texts = []

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
                sr = ((color_int >> 16) & 0xFF) / 255.0
                sg = ((color_int >> 8) & 0xFF) / 255.0
                sb = (color_int & 0xFF) / 255.0
                is_red = sr > 0.6 and sg < 0.35 and sb < 0.35

                all_texts.append({"text": text, "cx": tcx, "cy": tcy})

                clean = text.replace(".", "").replace(",", "").strip()
                if is_red and re.match(r"^\d{1,3}$", clean):
                    num = clean.lstrip("0") or "0"
                    if num not in markers:
                        markers[num] = {"x": tcx / pw, "y": tcy / ph}

    # Strategy B: Red circle drawing paths
    for drawing in page.get_drawings():
        sc = drawing.get("color") or []
        fc = drawing.get("fill") or []
        sc_red = len(sc) >= 3 and sc[0] > 0.6 and sc[1] < 0.35 and sc[2] < 0.35
        fc_red = len(fc) >= 3 and fc[0] > 0.6 and fc[1] < 0.35 and fc[2] < 0.35
        if not sc_red and not fc_red:
            continue
        rect = drawing.get("rect")
        if not rect:
            continue
        r = fitz.Rect(rect)
        if r.width < 2 or r.height < 2 or r.width > pw * 0.08:
            continue
        items = drawing.get("items", [])
        if not any(it[0] == "c" for it in items):
            continue
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        radius = max(r.width, r.height) / 2

        best, best_dist = None, float("inf")
        for tb in all_texts:
            clean = tb["text"].replace(".", "").replace(",", "").strip()
            if not re.match(r"^\d{1,3}$", clean):
                continue
            dist = math.sqrt((cx - tb["cx"]) ** 2 + (cy - tb["cy"]) ** 2)
            if dist < radius * 3.5 and dist < best_dist:
                best_dist = dist
                best = clean.lstrip("0") or "0"
        if best and best not in markers:
            markers[best] = {"x": cx / pw, "y": cy / ph}

    return [{"pos_nr": k, "x": v["x"], "y": v["y"]} for k, v in markers.items()]

import os
import json
import re
import fitz  # PyMuPDF
import pdfplumber
import pytesseract
import numpy as np
import cv2
from PIL import Image
import imagehash
from imagehash import phash

# <-- adjust this path if your installer went somewhere different
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# <-- ensure you have the Tesseract OCR installed and this path is correct


from paper_sz_ort_utils import _classify_pdf_layout, _filter_rectangles_for_layout

# local helpers (tiny, self-contained)
def _bbox_inside_page(page, bbox, tol=0.1):
    """
    MuPDF page coords via page.rect: (0,0,w,h). Returns True if bbox fully within page, positive area.
    """
    x0, y0, x1, y1 = _normalized_bbox(bbox)
    w, h = float(page.rect.width), float(page.rect.height)
    if (x1 - x0) <= 0 or (y1 - y0) <= 0:
        return False
    if x0 < -tol or y0 < -tol or x1 > w + tol or y1 > h + tol:
        return False
    return True

def _normalized_bbox(b):
    x0,y0,x1,y1 = b
    return (min(x0,x1), min(y0,y1), max(x0,x1), max(y0,y1))

# --- rotation-aware bbox transform (top-left origin) ---
def transform_bbox_for_rotation(bbox, pw, ph, pr):
    """
    Transform a top-left-origin bbox (x1,y1,x2,y2) for a page with rotation pr in {0,90,180,270}.
    pw = page width, ph = page height.
    Returns a normalized, clamped bbox.
    """
    x1, y1, x2, y2 = map(float, bbox)

    pr = int(pr) % 360
    if pr == 0:
        nx1, ny1, nx2, ny2 = x1, y1, x2, y2
    elif pr == 90:
        # origin effectively at top-right
        nx1, ny1 = y1,        pw - x2
        nx2, ny2 = y2,        pw - x1
    elif pr == 180:
        # origin effectively at bottom-right
        nx1, ny1 = ph - y2,   pw - x2
        nx2, ny2 = ph - y1,   pw - x1
    elif pr == 270:
        # origin effectively at bottom-left
        nx1, ny1 = ph - y2,   x1
        nx2, ny2 = ph - y1,   x2
    else:
        # Non-standard angle -> no-op fallback
        nx1, ny1, nx2, ny2 = x1, y1, x2, y2

    # normalize
    if nx2 < nx1: nx1, nx2 = nx2, nx1
    if ny2 < ny1: ny1, ny2 = ny2, ny1

    # clamp to page
    # def _clamp(v, lo, hi): return max(lo, min(hi, v))
    # nx1 = _clamp(nx1, 0.0, pw)
    # ny1 = _clamp(ny1, 0.0, ph)
    # nx2 = _clamp(nx2, 0.0, pw)
    # ny2 = _clamp(ny2, 0.0, ph)

    return (nx1, ny1, nx2, ny2)

def _clamp_bbox(b, w, h, tol=1e-6):
    x0,y0,x1,y1 = _normalized_bbox(b)
    x0 = max(0.0, min(x0, w - tol))
    y0 = max(0.0, min(y0, h - tol))
    x1 = max(tol, min(x1, w))
    y1 = max(tol, min(y1, h))
    # ensure positive area
    if x1 <= x0: x1 = min(w, x0 + tol)
    if y1 <= y0: y1 = min(h, y0 + tol)
    return (x0,y0,x1,y1)

# Constants
TEMPLATE_STORE = "templates"  # Directory to save template profiles (rectangle's text and images)

class TemplateManager:
    """
    Handles saving and loading of client-defined template profiles:
    - Rectangle coordinates
    - Extracted reference content (text and image hashes)
    - Versioned storage under: templates/<client>/<client>_vN.json
    Backward compatible with flat storage: templates/<template_id>.json
    """
    _ID_RE = re.compile(r"^(?P<client>[A-Za-z0-9_\-]+)_v(?P<ver>\d+)$")

    def __init__(self, store_dir=TEMPLATE_STORE):
        os.makedirs(store_dir, exist_ok=True)
        self.store_dir = store_dir

    # ---------- helpers ----------
    def parse_template_id(self, template_id: str) -> tuple[str, int | None]:
        m = self._ID_RE.match(template_id)
        if not m:
            return template_id, None
        return m.group("client"), int(m.group("ver"))

    def _client_dir(self, client: str) -> str:
        return os.path.join(self.store_dir, client)

    def _resolve_profile_path(self, template_id: str, for_write: bool = False) -> str:
        client, ver = self.parse_template_id(template_id)
        if ver is None:
            # legacy flat path
            path = os.path.join(self.store_dir, f"{template_id}.json")
            if for_write:
                os.makedirs(self.store_dir, exist_ok=True)
            return path
        # versioned path
        cdir = self._client_dir(client)
        if for_write:
            os.makedirs(cdir, exist_ok=True)
        return os.path.join(cdir, f"{template_id}.json")

    def list_versions(self, client: str) -> list[int]:
        cdir = self._client_dir(client)
        if not os.path.isdir(cdir):
            return []
        out = []
        for fn in os.listdir(cdir):
            if fn.endswith(".json") and fn.startswith(f"{client}_v"):
                m = self._ID_RE.match(fn[:-5])  # strip .json
                if m:
                    out.append(int(m.group("ver")))
        out.sort()
        return out

    def latest_version_number(self, client: str) -> int:
        vers = self.list_versions(client)
        return vers[-1] if vers else 0

    def latest_version_id(self, client: str) -> str | None:
        n = self.latest_version_number(client)
        return f"{client}_v{n}" if n > 0 else None

    def next_version_id(self, client: str) -> str:
        return f"{client}_v{self.latest_version_number(client) + 1}"

    # ---------- public API ----------
    def save_profile(self, template_id: str, pdf_path: str, rectangles: list,
                     image_map: dict = None):
        """
        Extract content within rectangles and save as JSON profile.
        Stored under templates/<client>/<client>_vN.json if template_id looks like "<client>_vN",
        otherwise legacy flat path templates/<template_id>.json.

        Enhancements:
        - Detect reference PDF layout (A3/A4 + H/V) and filter rectangles to this layout (ANY passes everywhere).
        - Skip out-of-bounds rectangles during extraction instead of crashing.
        """
        from template_utils import extract_zones_content  # local import for reliability
        # 1) detect layout and filter rectangles accordingly
        paper, orient, _ = _classify_pdf_layout(pdf_path)
        print(f"[Template] Detected layout: PDF name={pdf_path} paper={paper}, orientation={orient}")

        active_rects = _filter_rectangles_for_layout(rectangles, paper, orient)

        if not active_rects:
            raise ValueError(
                f"No rectangles match the reference PDF layout (paper={paper}, orientation={orient}). "
                "Tag rectangles with 'paper'/'orientation' or choose a matching reference PDF."
            )

        # 2) extract contents safely; collect skips (no crash)
        contents, used_rects, skipped = extract_zones_content(pdf_path, active_rects, _return_skips=True)

        if skipped:
            print(f"[Template] Warning: {len(skipped)} rectangle(s) skipped (OOB/invalid) while saving '{template_id}'.")

        if not contents:
            raise ValueError("All rectangles were invalid/out-of-bounds for the reference PDF; nothing to save.")

        # 3) store filtered rectangles only (template stays layout-specific)
        profile  = {
            'rectangles': used_rects,           # store the set that actually worked for this layout
            'contents': contents,
            'image_map': image_map or {}
        }
        path = self._resolve_profile_path(template_id, for_write=True)
        with open(path, 'w') as f:
            json.dump(profile, f, indent=2)

    def save_profile_multi(self, template_id: str,
                        rectangles: list[dict],
                        index_to_path: dict[int, str],
                        image_map: dict | None = None) -> None:
        """
        Multi-PDF variant of save_profile:
        - Rectangles may come from different PDFs (identified by file_idx).
        - Extracts text/ocr/image hash by calling extract_zones_content ONCE per source PDF,
            passing ONLY the rectangles that belong to that PDF.
        - Filters rectangles by the SOURCE PDF's (paper, orientation) so mismatched rects are skipped early.
        - Persists one unified profile under templates/<client>/<client>_vN.json.

        Expected rect keys per item:
        {
            "page": int,               # 1-based page number you drew on (robustly normalized below)
            "bbox": [x0,y0,x1,y1],     # as-drawn (top-left origin) absolute px
            "paper": "A1|A2|A3|A4|ANY",
            "orientation": "H|V|ANY",
            "file_idx": int            # index of the uploaded PDF this rect came from
        }
        """
        from template_utils import extract_zones_content  # local import for reliability

        used_rects: list[dict] = []
        contents: list[dict] = []
        skipped_total = 0

        # ---- 1) Group rectangles by source PDF index ----
        grouped: dict[int, list[dict]] = {}
        for r in rectangles or []:
            fidx = int(r.get("file_idx", 0))
            grouped.setdefault(fidx, []).append(r)

        if not grouped:
            raise ValueError("No rectangles were provided for multi-PDF save.")

        # ---- 2) Process each source PDF exactly once ----
        for fidx, rects_for_pdf in grouped.items():
            src_pdf = index_to_path.get(fidx)
            if not src_pdf or not os.path.exists(src_pdf):
                print(f"[TemplateMulti] Missing/invalid source for file_idx={fidx}; skipping group.")
                continue

            # 2a) Normalize rects: ensure 1-based page, normalized bbox, preserve paper/orientation
            norm_rects: list[dict] = []
            for r in rects_for_pdf:
                # Normalize page to 1-based (extract_zones_content expects 1-based, clamps internally)
                page = r.get("page", 0)
                try:
                    page = int(page)
                except Exception:
                    page = 0
                if page < 0:
                    page = 0

                # Normalize bbox
                x0, y0, x1, y1 = r.get("bbox", (0, 0, 0, 0))
                bbox = _normalized_bbox((x0, y0, x1, y1))

                nr = {"page": page, "bbox": bbox}
                if "paper" in r:
                    nr["paper"] = r["paper"]
                if "orientation" in r:
                    nr["orientation"] = r["orientation"]
                norm_rects.append(nr)

            # 2b) Detect the SOURCE PDF layout and filter to matching rectangles
            try:
                src_paper, src_orient, _ = _classify_pdf_layout(src_pdf)
                print(f"[TemplateMulti] Source={src_pdf} layout paper={src_paper}, orientation={src_orient}")
            except Exception as e:
                print(f"[TemplateMulti] Could not classify layout for {src_pdf}: {e}")
                src_paper, src_orient = None, None

            if src_paper and src_orient:
                active_rects = _filter_rectangles_for_layout(norm_rects, src_paper, src_orient)
            else:
                # If layout detection fails, use all rects for this source as a fallback.
                active_rects = norm_rects

            if not active_rects:
                print(f"[TemplateMulti] No rectangles match the source layout for {src_pdf}; skipping this group.")
                continue

            # 2c) Extract ONCE per PDF with all of its rectangles together (critical for correctness/perf)
            try:
                cnt, used, skipped = extract_zones_content(src_pdf, active_rects, _return_skips=True)
            except Exception as e:
                print(f"[TemplateMulti] Extraction failed for {src_pdf}: {e}")
                continue

            # 2d) Accumulate and tag provenance
            contents.extend(cnt)
            for u in used:
                u["source_index"] = fidx
                u["source_pdf"] = os.path.basename(src_pdf)
            used_rects.extend(used)
            skipped_total += len(skipped)

            if skipped:
                print(f"[TemplateMulti] {len(skipped)} rectangle(s) skipped (OOB/invalid) for {src_pdf}.")

        # ---- 3) Persist unified profile ----
        if not contents:
            raise ValueError("No valid rectangles after processing all source PDFs; nothing to save.")

        profile = {
            "rectangles": used_rects,          # as-drawn (top-left origin), page is 0-based here (from extractor)
            "contents": contents,              # transformed bbox + text + image_hash per rect
            "image_map": image_map or {},
            # Optional for audit/debug; uncomment if you want to store the full mapping:
            # "sources": {str(i): p for i, p in index_to_path.items()}
        }
        path = self._resolve_profile_path(template_id, for_write=True)
        with open(path, "w") as f:
            json.dump(profile, f, indent=2)

        if skipped_total:
            print(f"[TemplateMulti] Warning: {skipped_total} rectangle(s) skipped across sources while saving '{template_id}'.")

    def load_profile(self, template_id: str) -> dict:
        """
        Load saved profile JSON. First try versioned path. If missing, fall back to legacy flat path.
        """
        path_v = self._resolve_profile_path(template_id, for_write=False)
        if os.path.exists(path_v):
            with open(path_v, 'r') as f:
                return json.load(f)

        # fallback to legacy flat path
        path_legacy = os.path.join(self.store_dir, f"{template_id}.json")
        if os.path.exists(path_legacy):
            with open(path_legacy, 'r') as f:
                return json.load(f)

        raise FileNotFoundError(
            f"Template profile not found for '{template_id}'. "
            f"Checked: {path_v} and {path_legacy}"
        )



def extract_zones_content(pdf_path: str, rectangles: list, _return_skips: bool = False):
    """
    For each bbox (with optional 'page' field), extract text (native + OCR) and compute an image hash.
    Returns list of:
      { 'page': int, 'bbox':(x0,y0,x1,y1), 'text':str, 'image_hash':str }

    Enhancements:
      - Accepts 1-based 'page' in rectangles (clamps into range).
      - Skips out-of-bounds/invalid bboxes instead of raising.
      - When _return_skips=True, returns (results, used_rects, skipped_list).
    """
    results = []
    used_rects = []
    skipped = []

    doc = fitz.open(pdf_path)
    try:
        with pdfplumber.open(pdf_path) as pm:
            for rect in rectangles:
                # 1) page handling: 0-based (clamped)
                page_num = int(rect.get("page", 0) or 0)
                if page_num < 0:
                    page_num = 0
                elif page_num >= doc.page_count:
                    page_num = doc.page_count - 1


                page_fz = doc[page_num]
                page_pl = pm.pages[page_num]

                # page metrics + rotation
                pr = (getattr(page_fz, "rotation", 0) or 0) % 360
                pw = float(page_pl.width)
                ph = float(page_pl.height)

                # 2) original bbox (as drawn / top-left)
                x0, y0, x1, y1 = _normalized_bbox(rect['bbox'])
                orig_bbox = (x0, y0, x1, y1)
                print(f"[Extract] Page {page_num} (size={pw:.1f}x{ph:.1f}, rotation={pr}) - bbox: {orig_bbox}")   

                # 3) rotation-aware bbox for actual extraction
                tx0, ty0, tx1, ty1 = transform_bbox_for_rotation(orig_bbox, pw, ph, pr)
                t_bbox = (tx0, ty0, tx1, ty1)
                # t_bbox = _clamp_bbox(t_bbox, pw, ph)
                print(f"[Extract] Page {page_num} (size={pw:.1f}x{ph:.1f}, rotation={pr}) - transformed bbox: {t_bbox}")

                # 4) OOB check on transformed bbox
                if not _bbox_inside_page(page_fz, orig_bbox):
                    skipped.append({
                        "page": page_num,
                        "bbox": rect["bbox"],
                        "reason": "oob_or_invalid",
                        "page_size": (pw, ph),
                        "rotation": pr
                    })
                    continue

                # 5) extract native text (words overlap against transformed bbox)
                words = page_fz.get_text("words")
                extracted = [w[4] for w in words if overlaps((w[0], w[1], w[2], w[3]), t_bbox)]
                text = " ".join(extracted).strip()

                # 6) OCR fallback only if native empty (crop via transformed bbox): there are 2 options: 1) using fitz, 2) using pdfplumber
                if not text:
                    # fitz method
                    pix_ocr = page_fz.get_pixmap(clip=fitz.Rect(*orig_bbox), dpi=300)
                    img_ocr = Image.frombytes("RGB", [pix_ocr.width, pix_ocr.height], pix_ocr.samples)
                    text = pytesseract.image_to_string(img_ocr)

                    # pdfplumber method:
                    # crop_img = page_pl.crop(orig_bbox).to_image(resolution=300).original
                    # text = pytesseract.image_to_string(crop_img, config='--psm 6') #psm 6 is not working that much good in our case

                # 7) image hash from fitz clip (transformed bbox)
                pix = page_fz.get_pixmap(clip=fitz.Rect(*orig_bbox), dpi=100)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                ihash = str(phash(img))

                # results record (page stays 0-based)
                results.append({
                    'page': page_num,
                    'bbox': t_bbox,            # transformed region actually used
                    'text': text,
                    'image_hash': ihash
                })

                # store *as drawn* (top-left origin) in template (1-based page)
                used_rects.append({
                    'page': page_num,
                    'bbox': orig_bbox,
                    **({k: rect[k] for k in ('paper', 'orientation') if k in rect})
                })
    finally:
        doc.close()

    if _return_skips:
        return results, used_rects, skipped
    return results


def overlaps(b1, b2, tol=0) -> bool:
    """
    Check if two bboxes overlap (with optional tolerance).
    b1, b2: (x0,y0,x1,y1)
    """
    return not (b1[2] < b2[0]-tol or b1[0] > b2[2]+tol or b1[3] < b2[1]-tol or b1[1] > b2[3]+tol)


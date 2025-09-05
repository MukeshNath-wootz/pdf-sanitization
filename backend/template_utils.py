import os
import json
import re
from typing import List, Dict, Tuple, Optional

import fitz  # PyMuPDF
import pdfplumber
import pytesseract
import numpy as np
import cv2
from PIL import Image
import imagehash
from imagehash import phash

# Portable Tesseract path: works on Render (Linux) and Windows (if env is set)
pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD", "tesseract")

from paper_sz_ort_utils import _classify_page_layout, _filter_rectangles_for_layout

# Optional Supabase Storage
# =========================
try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None

_SUPABASE_URL = os.getenv("SUPABASE_URL")
# prefer service role for signing and upserts; anon key also works if bucket is public
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
_SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "pdf-sanitization")
_SUPABASE_TEMPLATES_PREFIX = os.getenv("SUPABASE_TEMPLATES_PREFIX", "templates").rstrip("/")

_sb = create_client(_SUPABASE_URL, _SUPABASE_KEY) if (create_client and _SUPABASE_URL and _SUPABASE_KEY) else None

# =========================
# Local constants & helpers
# =========================
TEMPLATE_STORE = "templates"  # local fallback (keeps your current layout)
os.makedirs(TEMPLATE_STORE, exist_ok=True)

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

# =========================
# Template Manager
# =========================
class TemplateManager:
    """
    Handles saving and loading of client-defined template profiles:
    - Rectangle coordinates
    - Extracted reference content (text and image hashes)
    - Versioned storage under: templates/<client>/<client>_vN.json
    Backward compatible with flat storage: templates/<template_id>.json
    """
    _ID_RE = re.compile(r"^(?P<client>[A-Za-z0-9_\-]+)_v(?P<ver>\d+)$")

    def __init__(self, store_dir: str = TEMPLATE_STORE):
        self.store_dir = store_dir
        os.makedirs(self.store_dir, exist_ok=True)
        self.sb = _sb
        self.bucket = _SUPABASE_BUCKET
        self.prefix = _SUPABASE_TEMPLATES_PREFIX

    # ---------- helpers ----------
    def parse_template_id(self, template_id: str) -> Tuple[str, Optional[int]]:
        m = self._ID_RE.match(template_id)
        if not m:
            return template_id, None
        return m.group("client"), int(m.group("ver"))

    def _client_dir(self, client: str) -> str:
        return os.path.join(self.store_dir, client)

    def _resolve_profile_path(self, template_id: str, for_write: bool = False) -> str:
        client, ver = self.parse_template_id(template_id)
        if ver is None:
            path = os.path.join(self.store_dir, f"{template_id}.json")
            if for_write: os.makedirs(self.store_dir, exist_ok=True)
            return path
        cdir = self._client_dir(client)
        if for_write: os.makedirs(cdir, exist_ok=True)
        return os.path.join(cdir, f"{template_id}.json")

    def _sb_key_for(self, template_id: str) -> Optional[str]:
        if not self.sb:
            return None
        client, ver = self.parse_template_id(template_id)
        if ver is None:
            # legacy flat id
            return f"{self.prefix}/{template_id}.json"
        return f"{self.prefix}/{client}/{template_id}.json"

     # ---------- list versions ----------
    def list_versions(self, client: str) -> List[int]:
        """
        Prefer listing from Supabase; fall back to local disk.
        """
        if self.sb:
            try:
                remote_dir = f"{self.prefix}/{client}"
                items = self.sb.storage.from_(self.bucket).list(path=remote_dir) or []
                out = []
                for it in items:
                    nm = (it.get("name") or "").strip()
                    if nm.endswith(".json") and nm.startswith(f"{client}_v"):
                        m = self._ID_RE.match(nm[:-5])
                        if m:
                            out.append(int(m.group("ver")))
                out.sort()
                return out
            except Exception:
                pass  # fall back to local

        cdir = self._client_dir(client)
        if not os.path.isdir(cdir):
            return []
        out = []
        for fn in os.listdir(cdir):
            if fn.endswith(".json") and fn.startswith(f"{client}_v"):
                m = self._ID_RE.match(fn[:-5])
                if m:
                    out.append(int(m.group("ver")))
        out.sort()
        return out

    def latest_version_number(self, client: str) -> int:
        vers = self.list_versions(client)
        return vers[-1] if vers else 0

    def latest_version_id(self, client: str) -> Optional[str]:
        n = self.latest_version_number(client)
        return f"{client}_v{n}" if n > 0 else None

    def next_version_id(self, client: str) -> str:
        return f"{client}_v{self.latest_version_number(client) + 1}"

    # ---------- save/load ----------
    def _save_profile_local(self, template_id: str, profile: dict) -> None:
        path = self._resolve_profile_path(template_id, for_write=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)

    def _save_profile_remote(self, template_id: str, profile: dict) -> None:
        if not self.sb:
            return
        key = self._sb_key_for(template_id)
        if not key:
            return
        data = json.dumps(profile, ensure_ascii=False).encode("utf-8")
        self.sb.storage.from_(self.bucket).upload(
            key, data, {"contentType": "application/json", "upsert":"true"}
        )

    def _load_profile_remote(self, template_id: str) -> Optional[dict]:
        if not self.sb:
            return None
        key = self._sb_key_for(template_id)
        if not key:
            return None
        try:
            data = self.sb.storage.from_(self.bucket).download(key)
            if not data:
                return None
            # supabase-py may return bytes or str; normalize
            if isinstance(data, bytes):
                return json.loads(data.decode("utf-8"))
            return json.loads(data)
        except Exception:
            return None

    def save_profile(
        self,
        template_id: str,
        pdf_path: str,
        rectangles: list,
        image_map: dict | None = None
    ):
        """
        Single-PDF reference profile save.
        Filters rectangles by detected layout, extracts contents, then writes JSON
        both locally and to Supabase (if configured).
        """
        paper, orient, _ = _classify_page_layout(pdf_path)
        active_rects = _filter_rectangles_for_layout(rectangles, paper, orient)
        if not active_rects:
            raise ValueError(
                f"No rectangles match the reference PDF layout (paper={paper}, orientation={orient})."
            )

        contents, used_rects, skipped = extract_zones_content(pdf_path, active_rects, _return_skips=True)
        if not contents:
            raise ValueError("All rectangles were invalid/out-of-bounds; nothing to save.")

        profile = {
            "rectangles": used_rects,
            "contents": contents,
            "image_map": image_map or {},
        }
        # local + remote
        self._save_profile_local(template_id, profile)
        self._save_profile_remote(template_id, profile)

    def save_profile_multi(
        self,
        template_id: str,
        rectangles: List[dict],
        index_to_path: Dict[int, str],
        image_map: Optional[dict] = None,
    ) -> None:
        """
        Multi-PDF reference save:
          - groups rectangles by 'file_idx'
          - for each source PDF, filters rects by its layout
          - extracts contents once per source
          - stores a unified profile
        """
        used_rects: List[dict] = []
        contents: List[dict] = []
        skipped_total = 0

        # ---- 1) Group rectangles by source PDF index ----
        grouped: Dict[int, List[dict]] = {}
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

             # 2a) Normalize rects: ensure 0-based page, normalized bbox, preserve paper/orientation
            norm_rects: List[dict] = []
            for r in rects_for_pdf:
                pg = int(r.get("page", 0) or 0)
                if pg < 0: pg = 0
                x0, y0, x1, y1 = r.get("bbox", (0, 0, 0, 0))
                bbox = _normalized_bbox((x0, y0, x1, y1))
                nr = {"page": pg, "bbox": bbox}
                if "paper" in r: nr["paper"] = r["paper"]
                if "orientation" in r: nr["orientation"] = r["orientation"]
                norm_rects.append(nr)

            # 2b) Detect the SOURCE PDF layout and filter to matching rectangles
            try:
                src_paper, src_orient, _ = _classify_page_layout(src_pdf)
                print(f"[TemplateMulti] Source={src_pdf} layout paper={src_paper}, orientation={src_orient}")
            except Exception:
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
            # ---- 3) Persist unified profile ----    
            contents.extend(cnt)
            for u in used:
                u["source_index"] = fidx
                u["source_pdf"] = os.path.basename(src_pdf)
            used_rects.extend(used)
            skipped_total += len(skipped)
            
            if skipped:
                print(f"[TemplateMulti] {len(skipped)} rectangle(s) skipped (OOB/invalid) for {src_pdf}.")

        if not contents:
            raise ValueError("No valid rectangles after processing all source PDFs; nothing to save.")

        profile = {
            "rectangles": used_rects,          # as-drawn (top-left origin), page is 0-based here (from extractor)
            "contents": contents,              # transformed bbox + text + image_hash per rect
            "image_map": image_map or {},
            # Optional for audit/debug; uncomment if you want to store the full mapping:
            # "sources": {str(i): p for i, p in index_to_path.items()}
        }
        # local + remote
        self._save_profile_local(template_id, profile)
        self._save_profile_remote(template_id, profile)

        if skipped_total:
            print(f"[TemplateMulti] {skipped_total} rectangle(s) skipped during save for '{template_id}'.")

    def load_profile(self, template_id: str) -> dict:
        """
        Try Supabase first; fall back to local file paths.
        """
        remote = self._load_profile_remote(template_id)
        if remote is not None:
            return remote

        path_v = self._resolve_profile_path(template_id, for_write=False)
        if os.path.exists(path_v):
            with open(path_v, "r", encoding="utf-8") as f:
                return json.load(f)

        # legacy flat path fallback
        path_legacy = os.path.join(self.store_dir, f"{template_id}.json")
        if os.path.exists(path_legacy):
            with open(path_legacy, "r", encoding="utf-8") as f:
                return json.load(f)

        raise FileNotFoundError(f"Template profile not found for '{template_id}'.")



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
                #if not text:
                    # fitz method
                    #pix_ocr = page_fz.get_pixmap(clip=fitz.Rect(*orig_bbox), dpi=300)
                    #img_ocr = Image.frombytes("RGB", [pix_ocr.width, pix_ocr.height], pix_ocr.samples)
                    #text = pytesseract.image_to_string(img_ocr)

                    ## pdfplumber method:
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


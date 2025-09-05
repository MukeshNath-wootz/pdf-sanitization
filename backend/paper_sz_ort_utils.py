# ---- pipeline.py (helpers for paper/orientation) ----
import fitz
from collections import defaultdict
import math

_CANON = {
    # canonical sizes in PDF points (72 dpi)
    # (short_side_pts, long_side_pts)
    "A4": (595.0, 842.0),   # 210 × 297 mm
    "A3": (842.0, 1191.0),  # 297 × 420 mm
}

def _normalize_paper(val):
    if not val:
        return "ANY"
    v = str(val).strip().upper()
    if v in ("A1", "A2", "A3", "A4"):
        return v
    return "ANY"

def _normalize_orientation(val):
    if not val:
        return "ANY"
    v = str(val).strip().upper()
    # accept various synonyms
    if v in ("H", "HOR", "HORIZ", "HORIZONTAL", "LANDSCAPE"):
        return "H"
    if v in ("V", "VERT", "VERTICAL", "PORTRAIT", "P"):
        return "V"
    return "ANY"

def _rel_close(a, b, rel_tol=0.1):
    # within 10% by default
    if b == 0:
        return abs(a) < 1e-6
    return abs(a - b) <= rel_tol * b

def _guess_paper_from_size(width_pts: float, height_pts: float, rel_tol=0.1):
    """
    Guess ISO paper (A1/A2/A3/A4) by comparing max dimension.
    Returns 'A1' | 'A2' | 'A3' | 'A4'
    """
    w, h = float(width_pts), float(height_pts)
    max_dim = max(w, h)

    if max_dim > 2000:
        return "A1"
    if max_dim > 1500:
        return "A2"
    if max_dim > 1100:
        return "A3"
    return "A4"

def _classify_page_layout(pdf_path: str, tol=0.1):
    """
    Inspect the first page. Returns (paper, orientation, (w,h)).
      - paper: 'A3' | 'A4' | 'A2' | 'A1' | 'ANY'
      - orientation: 'H' (landscape) or 'V' (portrait)
      - (w,h): page width/height in points
    """
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        r = page.rect
        w, h = float(r.width), float(r.height)
        orientation = "H" if w >= h else "V"
        paper = _guess_paper_from_size(w, h, tol)
        return paper, orientation, (w, h)
    finally:
        doc.close()
        
def _classify_pdf_layout(pdf_path: str, tol=0.1):
    """
    Inspect *all* pages of the PDF.

    Returns:
      List of (paper, orientation, (w,h)) — one tuple per page
        - paper: 'A3' | 'A4' | 'A2' | 'A1' | 'ANY'
        - orientation: 'H' (landscape) or 'V' (portrait)
        - (w,h): page width/height in points
    """
    page_layouts = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            r = page.rect
            w, h = float(r.width), float(r.height)
            orientation = "H" if w >= h else "V"
            paper = _guess_paper_from_size(w, h, tol)
            page_layouts.append((paper, orientation, (w, h)))
        return page_layouts
    finally:
        doc.close()


def _filter_rectangles_for_layout(rectangles: list, paper: str, orientation: str):
    """
    Keep only rectangles whose (paper, orientation) match the given layout.
    Missing metadata is treated as 'ANY'.
    """
    p = (paper or "ANY").upper()
    o = (orientation or "ANY").upper()

    out = []
    for r in rectangles:
        rp = _normalize_paper(r.get("paper", "ANY"))
        ro = _normalize_orientation(r.get("orientation", "ANY"))

        # print(f"[DEBUG] Filtering rect for layout: {r} against paper={p}, orient={o} (rp={rp}, ro={ro})")

        paper_ok = (rp == p)
        orient_ok = (ro == o)
        if paper_ok and orient_ok:
            out.append(r)
    return out



def _validate_replicated_rects_for_pdf(
    pdf_path: str,
    replicated_rectangles: list[dict],
    page_is_one_based: bool = False,
    tol: float = 0.05
) -> dict:
    """
    Validate that every rectangle lies within its page's bounds.
    Returns a dict of issues; empty dict => all good.

    Structure:
      {
        <page_index_0_based>: [
          {'bbox': (x0,y0,x1,y1), 'reason': 'out_of_bounds'|'non_positive_area', 'page_size': (w,h)}
        ],
        'page_out_of_range': [{'page': <original_page>, 'bbox': (...)}]
      }
    """
    issues = defaultdict(list)
    doc = fitz.open(pdf_path)
    try:
        for rect in replicated_rectangles:
            pg_in = rect.get("page", 1)
            p0 = (pg_in - 1) if page_is_one_based else pg_in

            # invalid page index
            if p0 < 0 or p0 >= doc.page_count:
                issues['page_out_of_range'].append({'page': pg_in, 'bbox': rect['bbox']})
                continue

            page_rect = doc[p0].rect  # fitz.Rect
            x0, y0, x1, y1 = rect["bbox"]

            # normalize bbox; allow user-provided reversed corners
            if x1 < x0: x0, x1 = x1, x0
            if y1 < y0: y0, y1 = y1, y0

            # non-positive area
            if (x1 - x0) <= 0 or (y1 - y0) <= 0:
                issues[p0].append({'bbox': rect['bbox'], 'reason': 'non_positive_area',
                                   'page_size': (page_rect.x1 - page_rect.x0, page_rect.y1 - page_rect.y0)})
                continue

            # out-of-bounds (with tiny tolerance for rounding)
            if (x0 < page_rect.x0 - tol or
                y0 < page_rect.y0 - tol or
                x1 > page_rect.x1 + tol or
                y1 > page_rect.y1 + tol):
                issues[p0].append({'bbox': rect['bbox'], 'reason': 'out_of_bounds',
                                   'page_size': (page_rect.x1 - page_rect.x0, page_rect.y1 - page_rect.y0)})

    finally:
        doc.close()

    return dict(issues)

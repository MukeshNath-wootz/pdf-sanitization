# detection_utils.py
import fitz

# Function to find rectangles containing manual names in a PDF
def find_manual_name_rects(pdf_path: str, names: list[str]) -> list[dict]:
    """
    Search each page for exact occurrences of each name/phrase.
    Returns list of {'page':i,'bbox':(...)}.
    """
    hits = []
    doc = fitz.open(pdf_path)
    for i, pg in enumerate(doc):
        for name in names:
            for r in pg.search_for(name):
                hits.append({"page": i, "bbox": (r.x0, r.y0, r.x1, r.y1)})
    doc.close()
    return hits

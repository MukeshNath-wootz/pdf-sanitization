# redaction_engine.py
import fitz  # PyMuPDF

SAVE_OPTS = {"garbage": 4, "deflate": True, "clean": True}

class RedactionEngine:
    @staticmethod
    def redact(pdf_path: str, rectangles: list[dict], output_path: str):
        """
        Redact each rectangle (page, bbox) fully and save result.
        rectangles: [{"page": int, "bbox": (x1, y1, x2, y2)}, ...]
        """
        doc = fitz.open(pdf_path)
        try:
            # Group rects by page to apply once per page
            by_page: dict[int, list[fitz.Rect]] = {}
            for rect in rectangles or []:
                pno = int(rect.get("page", 0))
                bbox = rect.get("bbox")
                if not bbox or len(bbox) != 4:
                    continue  # skip bad inputs
                r = fitz.Rect(*bbox)
                # if not r.is_finite or r.is_empty:
                #     continue
                by_page.setdefault(pno, []).append(r)

            # Add redaction annots per page, then apply once
            for pno, rects in by_page.items():
                if pno < 0 or pno >= len(doc):
                    continue
                page = doc[pno]
                for r in rects:
                    page.add_redact_annot(r, fill=(1, 1, 1))  # white fill
                page.apply_redactions()  # burn them in

            # Save with optimization options
            doc.save(output_path, **SAVE_OPTS)
        finally:
            doc.close()




# import fitz

# SAVE_OPTS = {"garbage": 1, "deflate": False, "clean": False}
# class RedactionEngine:
#     @staticmethod
#     def redact(pdf_path: str, rectangles: list[dict], output_path: str):
#         """
#         Redact each rectangle (page, bbox) fully and save result.
#         """
#         doc = fitz.open(pdf_path)
        
#         # 1) mark all redaction annots
#         for rect in rectangles:
#             pg = doc[rect.get("page", 0)]
#             r  = fitz.Rect(*rect["bbox"])
#             pg.add_redact_annot(r, fill=(1,1,1))
#             pg.apply_redactions()
#         # 2) apply them per-page
#         for pg in doc:
#             if hasattr(pg, "apply_redactions"):
#                 pg.apply_redactions()
#             else:
#                 # fallback: per-page
#                 for page in doc:
#                     page.apply_redactions()
#         doc.save(output_path)
#         doc.close()

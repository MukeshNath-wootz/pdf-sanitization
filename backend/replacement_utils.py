# replacement_utils.py
import os, fitz
from collections import defaultdict
from style_utils import sample_span_style


SAVE_OPTS = {"garbage": 4, "deflate": True, "clean": True}
#thus function collects all rectangles corresponding to manual names
#and returns a list of these rectangles and a list of replacement list if any replacementable name found in the list of manual names
#the replacement list contains the page number, rectangle, old text, new text, font, size, and color
def collect_manual_replacements(
    pdf_path: str,
    manual_names: list[str],
    replacements: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    """
    Returns:
      manual_rects: [{'page': i, 'bbox': (x0,y0,x1,y1)} …]
      replacement_data: [{
        'page': i,
        'rect': fitz.Rect,
        'old_text': str,
        'new_text': str,
        'font': str,
        'size': float,
        'color': tuple
      }, …]
    """
    manual_rects = []
    replacement_data = []
    doc = fitz.open(pdf_path)
    for page_index, page in enumerate(doc):
        text_dict = page.get_text("dict")
        for name in manual_names:
            for inst in page.search_for(name):
                # always redact this rect
                manual_rects.append({
                    "page": page_index,
                    "bbox": (inst.x0, inst.y0, inst.x1, inst.y1)
                })
                # if we have a mapping, capture style & new text
                if name in replacements:
                    font, size, col = sample_span_style(text_dict, inst)
                    replacement_data.append({
                        "page": page_index,
                        "rect": inst,
                        "old_text": name,
                        "new_text": replacements[name],
                        "font": font,
                        "size": size,
                        "color": col
                    })
    doc.close()
    return manual_rects, replacement_data

def apply_manual_replacements(
    pdf_path: str,
    replacement_data: list[dict],
    template_rects: list[dict]
) -> None:
    """
    Opens pdf_path, overlays each new_text in its rect if len(new)<=len(old),
    but only if that rect does NOT overlap any of the template_rects.
    Saves via a tmp swap to avoid PyMuPDF incremental-save errors.
    """
    print(f"[DEBUG]  apply_manual_replacements called: pdf_path={pdf_path!r}")

    if not replacement_data:
        return

     # Build a mapping: page index → list of fitz.Rect for template zones
    tmpl_by_page: dict[int, list[fitz.Rect]] = defaultdict(list)
    for tr in template_rects:
        pg = tr["page"]
        x0, y0, x1, y1 = tr["bbox"]
        tmpl_by_page[pg].append(fitz.Rect(x0, y0, x1, y1))


    tmp_path = pdf_path + ".tmp"
    doc = fitz.open(pdf_path)

    for item in replacement_data:
        pg = item["page"]
        orig_rect: fitz.Rect = item["rect"]

        # Skip if overlaps any template zone on the same page
        if any(orig_rect.intersects(t) for t in tmpl_by_page.get(pg, [])):
            print(f"[DEBUG]  Skipping '{item['old_text']}' on page {pg}: overlaps template zone {orig_rect}")
            continue

        old, new = item["old_text"], item["new_text"]

        # 4 point padding on top and bottom
        pad = 4
        rect = fitz.Rect(orig_rect.x0-pad, orig_rect.y0-pad/2, orig_rect.x1+pad, orig_rect.y1 + pad)

        x0, y0, x1, y1 = rect
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        rect = fitz.Rect(x0, y0, x1, y1)

        print(f"[DEBUG]  Replacing '{old}' with '{new}' at {rect} on page {pg}")

        if len(new) <= len(old):
            fontsize = float(item["size"])
            fontname = item["font"]
            color = item["color"]
            print(f"[DEBUG]  Using style: {fontname}, {fontsize}, {color}")
            # Try shrinking until it fits
            for attempt in range(10):
                drawn = doc[pg].insert_textbox(
                    rect, new,
                    fontname=fontname,
                    fontsize=fontsize,
                    color=color,
                    align=1,  # center-aligned
                    overlay=True,
                    rotate=doc[pg].rotation  # keep upright
                )
                print(f"Attempt {attempt}: fontsize={fontsize:.2f}, drawn={drawn}")
                if drawn > 0:
                    break
                fontsize *= 0.90  # shrink font size by 10% each attempt
            else:
                print(f"[WARN] Could not fit '{new}' in {rect} even at very small size.")


    # write to tmp, then atomically replace
    doc.save(tmp_path, **SAVE_OPTS)
    doc.close()
    os.replace(tmp_path, pdf_path)


# def replace_manual_texts(pdf_in: str, replacements: dict[str, str], pdf_out: str):
#     """
#     For each old→new mapping:
#       • On each page, redact just the old text
#       • Immediately apply that redaction
#       • Overlay the new text in the same box using sampled font/size/color

#     Saves to pdf_out (in-place safe via .tmp swap if pdf_out==pdf_in).
#     """
#     print(f"[DEBUG]  replace_manual_texts called: pdf_in={pdf_in!r}, pdf_out={pdf_out!r}")
#     overwrite = os.path.abspath(pdf_out) == os.path.abspath(pdf_in)
#     tmp_path  = pdf_out + ".tmp" if overwrite else pdf_out

#     doc = fitz.open(pdf_in)
#     for page in doc:
#         text_dict = page.get_text("dict")
#         hits = []  # (rect, new_text, font, size, color)

#         # 1) Gather all manual-replacement hits on this page
#         for old, new in replacements.items():
#             for inst in page.search_for(old):
#                 font, size, col = sample_span_style(text_dict, inst)
#                 print(f"[DEBUG]  Found '{old}' at {inst} with style: {font}, {size}, {col}")
#                 hits.append((inst, new, font, size, col))
#                 page.add_redact_annot(inst, fill=(1, 1, 1))

#         # 2) Apply only this page’s redactions right away
#         if hits:
#             page.apply_redactions()

#             # 3) Overlay each replacement in its box
#             for rect, new, font, size, col in hits:
#                 print(f"[DEBUG]  Inserting replacement text: {new!r} at {rect}")
#                 print(f"[DEBUG]  Using style: {font}, {size}, {col}")
#                 page.insert_textbox(
#                     rect,
#                     new,
#                     fontname=font,
#                     fontsize=size,
#                     color=col,
#                     align=0,
#                     overlay=True
#                 )


#     # 4) Save out (tmp if overwriting)
#     doc.save(tmp_path)
#     print(f"[DEBUG]  doc.save → {tmp_path!r}")
#     doc.close()
#     if overwrite:
#         os.replace(tmp_path, pdf_out)

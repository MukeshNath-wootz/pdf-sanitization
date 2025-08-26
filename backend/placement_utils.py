# placement_utils.py
import os
import fitz
from PIL import Image as PILImage
from style_utils import sample_span_style

SAVE_OPTS = {"garbage": 4, "deflate": True, "clean": True}

def insert_content_in_rectangles(
    pdf_in: str,
    rectangles: list[dict],
    pdf_out: str,
    image_map: dict[int,str] | None = None,
    text_map:  dict[int,str] | None = None
):
    """
    Into each rect (page,bbox):
      • If idx in image_map, insert & scale that image.
      • If idx in text_map, insert that text with matching style.
    In-place safe via .tmp swap if pdf_out==pdf_in.
    """
    overwrite = os.path.abspath(pdf_out) == os.path.abspath(pdf_in)
    tmp_path  = pdf_out + ".tmp" if overwrite else pdf_out

    doc = fitz.open(pdf_in)
    for idx, info in enumerate(rectangles):
        # → convert to zero-based
        page_num = int(info.get("page", 0) or 0)

        # safety clamp
        if page_num < 0:
            page_num = 0
        elif page_num >= doc.page_count:
            page_num = doc.page_count-1

        pg = doc[page_num]
        rect = fitz.Rect(*info["bbox"])

        # — image insertion —
        if image_map and idx in image_map:
            img_path = image_map[idx]
            with PILImage.open(img_path) as pil:
                img_w, img_h = pil.size

            rot = int(pg.rotation) % 360
            # preserve aspect; swap effective ratio at 90/270
            img_ratio = img_w / img_h
            eff_ratio = img_ratio if rot in (0, 180) else (1.0 / img_ratio)

            bw, bh = rect.width, rect.height
            if bw / bh > eff_ratio:
                nh, nw = bh, bh * eff_ratio
            else:
                nw, nh = bw, bw / eff_ratio

            x0 = rect.x0 + (bw - nw) / 2
            y0 = rect.y0 + (bh - nh) / 2
            tgt = fitz.Rect(x0, y0, x0 + nw, y0 + nh)

            print(f"[DEBUG] Inserting image {img_path} into page {page_num} at {tgt} (orig bbox {rect}, rot={rot})")
            pg.insert_image(
                tgt,
                filename=img_path,
                rotate=rot,
                overlay=True,        
                keep_proportion=True
            )

       # --- text insertion (bbox already transformed) ---
        if text_map and idx in text_map:
            txt = text_map[idx]
            rot = int(pg.rotation) % 360

            # sample style from page content near/inside this rect (your existing helper)
            txt_dict = pg.get_text("dict")
            font, size, col = sample_span_style(txt_dict, rect)

            # Keep existing behavior; just add rotate. Fallback for older PyMuPDF.
            try:
                pg.insert_textbox(
                    rect, txt,
                    fontname=font, fontsize=size,
                    color=col, align=0, overlay=True,
                    rotate=rot               # NEW: rotate text to page orientation
                )
            except TypeError:
                # If your PyMuPDF version doesn't support rotate= on insert_textbox,
                # fall back to previous behavior (unrotated text).
                pg.insert_textbox(
                    rect, txt,
                    fontname=font, fontsize=size,
                    color=col, align=0, overlay=True
                )


    doc.save(tmp_path, **SAVE_OPTS)
    doc.close()
    if overwrite:
        os.replace(tmp_path, pdf_out)

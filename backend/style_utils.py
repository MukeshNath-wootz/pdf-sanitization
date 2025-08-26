# style_utils.py
import fitz

def normalize_font_name(orig_font: str) -> str:
    """
    Map any PDF font name to a built-in PyMuPDF font:
    helv, courier, times, symbol, zapfdingbats.
    """
    f = orig_font.lower()
    if "helv" in f or "arial" in f:
        return "helv"
    if "courier" in f:
        return "courier"
    if "times" in f:
        return "times"
    if "symbol" in f:
        return "symbol"
    if "dingbat" in f or "zapf" in f:
        return "zapfdingbats"
    return "helv"

def sample_span_style(text_dict: dict, inst_rect: fitz.Rect):
    """
    Return (fontname, fontsize, color) for the first span intersecting inst_rect,
    with the font normalized to a built-in font.
    """
    default = ("helv", 12, (0, 0, 0))
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sb = fitz.Rect(span["bbox"])
                if sb.intersects(inst_rect):
                    font = normalize_font_name(span["font"])
                    size = span["size"]
                    c    = span.get("color", 0)
                    r = ((c >> 16) & 255) / 255
                    g = ((c >>  8) & 255) / 255
                    b = ( c        & 255) / 255
                    return (font, size, (r, g, b))
    return default

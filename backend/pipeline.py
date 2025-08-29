# pipeline.py
import os
import argparse
import fitz  # PyMuPDF
from collections import defaultdict
import tempfile

from template_utils       import TemplateManager, extract_zones_content
from template_utils       import transform_bbox_for_rotation
from scoring_utils        import ConfidenceScorer
from redaction_engine     import RedactionEngine
# from detection_utils      import find_manual_name_rects
# from replacement_utils    import replace_manual_texts
from replacement_utils    import collect_manual_replacements, apply_manual_replacements
from placement_utils      import insert_content_in_rectangles
from llm_utils import get_sensitive_terms_from_llm
from paper_sz_ort_utils    import _classify_pdf_layout, _filter_rectangles_for_layout, _validate_replicated_rects_for_pdf

import tempfile

# Optional Supabase client (same envs as api_app.py)
try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None

_SB_URL  = os.getenv("SUPABASE_URL")
_SB_KEY  = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
_SB_BUCKET = os.getenv("SUPABASE_BUCKET", "pdf-sanitization")
_SB_LOGOS_PREFIX = os.getenv("SUPABASE_LOGOS_PREFIX", "logos").rstrip("/")

_sb = create_client(_SB_URL, _SB_KEY) if (create_client and _SB_URL and _SB_KEY) else None


def extract_raw_text(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []
    for page in doc: #here we can make changes agar maanlo bss apan ko ek hi page ka text chahiye
        # “text” gives a single string with newlines for blocks/paragraphs
        pages.append(page.get_text("text"))
    return pages

def dedupe_text_pages(pages_text: list[str]) -> str:
    """
    Deduplicate lines across all pages (case-insensitive), 
    preserving the first occurrence order.
    Returns a single string joined by newlines.
    """
    seen = set()
    out = []
    for page in pages_text:
        for line in page.splitlines():
            norm = line.strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                out.append(line.strip())
    return "\n".join(out)


def process_batch(
    pdf_paths: list[str],
    template_id: str,
    output_dir: str,
    threshold: float = 0.9,
    manual_names: list[str] | None = None,
    text_replacements: dict[str, str] | None = None,
    image_map: dict[int, str] | None = None,
    input_root: str | None = None,
) -> list[dict]:
    """
    For each PDF:
      1. (Optional) augment manual_names via LLM
      2. Load template & replicate its rectangles across pages
      3. For each page:
         - Score *all* template rects on that page
         - If ANY rect has rscore<0.9 OR tscore<0.85 OR iscore<0.85,
           → record that page’s rects in low_confidence_by_page
           → skip redacting ALL rects on that page
         - Else
           → mark all that page’s rects as high_conf_template_rects
      4. Always collect manual_rects & do replacements
      5. Redact only high_conf_template_rects + manual_rects
      6. Record per-PDF low_confidence_by_page for output

       If `input_root` is provided, preserve folder hierarchy under `output_dir`.
    """
    # ── Load the saved template profile ──
    tm = TemplateManager()
    profile      = tm.load_profile(template_id)
    rectangles   = profile["rectangles"]   # [{'page': i, 'bbox': (...)} …]
    ref_contents = profile["contents"]     # [{'text': "...", 'image_hash': "..."} …]

    # NEW: if caller didn't pass image_map, take it from the profile
    if image_map is None:
        image_map = profile.get("image_map", None)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    low_conf = []

    for pdf in pdf_paths:
        base = os.path.splitext(os.path.basename(pdf))[0]
        
        # Determine sanitized path, preserving hierarchy if input_root given
        if input_root:
            rel_path = os.path.relpath(pdf, input_root)
            sub_dir = os.path.dirname(rel_path)
            target_dir = os.path.join(output_dir, sub_dir)
            os.makedirs(target_dir, exist_ok=True)
            sanitized = os.path.join(target_dir, f"{base}_sanitized.pdf")
        else:
            sanitized = os.path.join(output_dir, f"{base}_sanitized.pdf")

    # ── Step 0: AUTO-EXTRACT sensitive terms via LLM…
        # 0.1) first, get ALL text from this PDF:
        all_text = extract_raw_text(pdf)
        deduped_text = dedupe_text_pages(all_text)

        # 0.2) write context to LLM
        context = (
            "These texts come from engineering/manufacturing drawings "
            "for machine parts. Non-sensitive info includes part names, "
            "dimensions, machining processes and steps, safety notes and notes regarding any manufacturing steps. Sensitive info includes "
            "personal names, emails, phone/fax numbers, postal addresses, country names, and Copyright notes."
            "text can be in any language, but mostly English."
        )
        # 0.3) call the LLM helper
        # new_terms = get_sensitive_terms_from_llm(deduped_text, context)
        print("new_terms:", new_terms)
        # 0.4) merge with your existing manual list—no duplicates
        existing_manual_names = manual_names[:] if manual_names is not None else []
        manual_names = existing_manual_names[:]  # copy
        for term in new_terms:
            if term not in manual_names:
                manual_names.append(term)


    # ── Step 1: Determine page count & replicate template zones ──
        paper, orient, (pw, ph) = _classify_pdf_layout(pdf_path=pdf, tol=0.05)
        print(f"[Layout] {pdf}: paper={paper}, orientation={orient}, size=({pw:.1f}×{ph:.1f})")

        # filter rectangles for this layout
        filtered = _filter_rectangles_for_layout(rectangles, paper, orient)
        # Attach template index: find 'r' inside original 'rectangles' by identity/equality
        active_rectangles = []
        for i, r in enumerate(rectangles):
            if r in filtered:
                rr = dict(r)  # shallow copy
                rr["tidx"] = i  # original template index
                active_rectangles.append(rr)
        print(f"[Layout] Active rectangles for {pdf}: {len(active_rectangles)} found")
        print(f"[Layout] Active rectangles for {pdf}: {active_rectangles}")

        if not active_rectangles:
            # No applicable rectangles for this PDF's layout -> flag low-confidence and skip
            print(f"[Layout] No rectangles match layout (paper={paper}, orientation={orient}). Skipping {pdf} and flagging.")
            # flag across all pages so reviewer sees it's a layout mismatch
            doc = fitz.open(pdf)
            page_to_bboxes = {i: [r["bbox"] for r in rectangles] for i in range(doc.page_count)}
            doc.close()
            low_conf.append({
                "pdf": pdf,
                "low_rects": page_to_bboxes
            })
            continue

        # if we do have matches, carry on as usual, but replicate only these:
        doc = fitz.open(pdf)
        num_pages = len(doc)
        print(f"[Layout] {pdf} has {num_pages} pages")
        rot_meta = [(p.rotation % 360, p.rect.width, p.rect.height) for p in doc]
        doc.close()

        replicated_rectangles = [
            {"page": i, "bbox": r["bbox"], "tidx": r["tidx"]}
            for i in range(num_pages)
            for r in active_rectangles
        ]
        for rect in replicated_rectangles:
            print(f"page: {rect['page']}, bbox: {rect['bbox']}, tidx: {rect['tidx']}")

        replicated_rectangles_rotaware = []
        for rr in replicated_rectangles:
            p0 = rr["page"]
            pr, pw, ph = rot_meta[p0]
            tb = transform_bbox_for_rotation(rr["bbox"], pw, ph, pr)
            replicated_rectangles_rotaware.append({"page": rr["page"], "bbox": tb, "tidx": rr["tidx"]})

        # oob_issues = _validate_replicated_rects_for_pdf(
        #     pdf_path=pdf,
        #     replicated_rectangles=replicated_rectangles,
        #     page_is_one_based=False
        # )

        # if oob_issues:
        #     # build low_conf structure {page_num: [bboxes]}
        #     page_to_bboxes = defaultdict(list)

        #     # page-index errors
        #     for meta in oob_issues.get('page_out_of_range', []):
        #         page_to_bboxes[int(meta['page'])].append(meta['bbox'])

        #     # per-page errors (keys are 0-based page indexes)
        #     for p0, lst in oob_issues.items():
        #         if p0 == 'page_out_of_range':
        #             continue
        #         for it in lst:
        #             page_to_bboxes[int(p0)].append(it['bbox'])

        #     print(f"[Safety] Out-of-bounds rectangles for {pdf}. Skipping this PDF and flagging as low-confidence.")
        #     low_conf.append({
        #         "pdf": pdf,
        #         "low_rects": dict(page_to_bboxes)
        #     })
        #     continue  # move to next PDF


    # ── Step 2: Extract & score template zones ──
        tgt_contents = extract_zones_content(pdf, replicated_rectangles)

        record_scores = []
        for tgt, rect in zip(tgt_contents, replicated_rectangles):
            ti     = rect["tidx"]                 # template index
            ref    = ref_contents[ti]             # correct reference for this rectangle
            pg     = tgt["page"]
            tscore = ConfidenceScorer.score_text(ref["text"],  tgt["text"])
            iscore = ConfidenceScorer.score_image(ref["image_hash"], tgt["image_hash"])
            rscore = (tscore + iscore) / 2
            record_scores.append({
                "page": pg,
                "bbox": rect["bbox"],
                "tidx": ti,          # keep it!
                "tscore": tscore,
                "iscore": iscore,
                "rscore": rscore
            })

        # 2.1) group scores by page
        pages = defaultdict(list)
        for rec in record_scores:
            pages[rec["page"]].append(rec)

        # 2.2) per-page high/low classification
        THRESH_R  = threshold
        THRESH_T  = 0.9 * threshold
        THRESH_I  = 0.9 * threshold

        high_conf_rects = []
        low_confidence_by_page = {}

        for pg, recs in pages.items():
            # page_low = False
            for rec in recs:
                if (rec["rscore"] < THRESH_R
                    or rec["tscore"] < THRESH_T
                    or rec["iscore"] < THRESH_I):
                    # mark that particular template rectangle as low-conf
                    low_confidence_by_page[pg] = rec["bbox"]
                else:
                    # this rectangle is high-confidence
                    high_conf_rects.append({
                        "page": rec["page"],
                        "bbox": rec["bbox"],
                        "tidx": rec["tidx"]
                    })
            
        # transform high-conf rects for redaction
        doc = fitz.open(pdf)
        high_conf_rects_rotaware = []
        for rc in high_conf_rects:
            p0 = rc["page"]            # rc["page"] is 0-based from extractor
            pr = (doc[p0].rotation or 0) % 360
            pw = doc[p0].rect.width
            ph = doc[p0].rect.height
            tb = transform_bbox_for_rotation(rc["bbox"], pw, ph, pr)
            # keep page 0-based for redaction engine
            high_conf_rects_rotaware.append({"page": p0, "bbox": tb, "tidx": rc["tidx"]})
        doc.close()

    # ── Step 3: Collect manual-name redaction rectangles + replacement info ──
        manual_rects, manual_rep_data = collect_manual_replacements(
            pdf,
            manual_names or [],
            text_replacements or {}
        )
        # print(f"Found {len(manual_rects)} manual redaction zones in {pdf}")
        # print(f"These are the rectangles corresponding to the manual names: {manual_rects}")
        # print(f"Found {len(manual_rep_data)} manual replacements in {pdf}")
        

    # ── Step 4: Combine all redaction rectangles ──
        all_rects = high_conf_rects_rotaware + manual_rects
        # print (all_rects)

    # ── Step 5: Redact template + manual zones ──
        RedactionEngine.redact(pdf, all_rects, sanitized)

    # ── Step 6: Place images into specified rectangles ──
        # Resolve Supabase storage keys (e.g., "logos/<client>/<file>") to local temp files
        def _resolve_logo_to_local(val: str, _cache: dict) -> str:
            # cache by key → local path to avoid re-downloading for multiple pages
            if val in _cache:
                return _cache[val]

            # If it looks like a Supabase storage key and client is configured, download
            looks_like_storage_key = isinstance(val, str) and ("/" in val) and not val.lower().startswith(("http://", "https://"))
            if _sb and looks_like_storage_key:
                # Treat 'val' as a storage key under the same bucket (e.g., "logos/acme/logo.png")
                try:
                    data = _sb.storage.from_(_SB_BUCKET).download(val)
                    tmp_dir = _cache.setdefault("__dir__", tempfile.mkdtemp(prefix="logos_"))
                    local = os.path.join(tmp_dir, os.path.basename(val))
                    with open(local, "wb") as f:
                        # supabase-py may return bytes or str
                        if isinstance(data, bytes):
                            f.write(data)
                        else:
                            f.write(data.encode("utf-8"))
                    _cache[val] = local
                    return local
                except Exception:
                    # fall through to return the original value
                    pass

            # Not a storage key or download failed → return original (may already be a local path)
            _cache[val] = val
            return val

        if image_map:
            print(f"[Place] image placement initiated: {image_map}")
            print(f"[Place] High-conf rects: {len(high_conf_rects_rotaware)}")
            # image_map keys in template are template indices (as strings)
            # We need to create an enumerated map aligned to the rectangles we pass now.
            _logo_cache = {}
            enum_image_map = {}
            for idx, rc in enumerate(high_conf_rects_rotaware):
                ti = rc.get("tidx")
                mapped = None
                if ti is not None and isinstance(image_map, dict):
                    mapped = image_map.get(ti)
                print(f"[Place] idx={idx} (page={rc['page']}, tidx={ti}) → mapped={mapped}")
                if mapped:
                    enum_image_map[idx] = _resolve_logo_to_local(mapped, _logo_cache)

            if not enum_image_map:
                print("[Place][SKIP] No rects eligible for image placement after confidence gating "
                      "(or no mapped images for the surviving tidx’s).")

            if enum_image_map:
                insert_content_in_rectangles(
                    pdf_in     = sanitized,
                    rectangles = high_conf_rects_rotaware,  # each has (page, bbox, tidx)
                    pdf_out    = sanitized,
                    image_map  = enum_image_map            # remapped to local enumeration
                )

    # ── Step 7: Overlay allowed manual replacements ──
        apply_manual_replacements(sanitized, manual_rep_data, replicated_rectangles)
        
        if low_confidence_by_page:
            print(f"Low-confidence rectangles found in {pdf}: {low_confidence_by_page}")
            low_conf.append({
                "pdf": pdf,
                "low_rects": low_confidence_by_page
            })


    return low_conf

# --- pipeline.py (ADD this function below process_batch) ---
def process_low_conf_batch(
    low_conf: list[dict],
    new_template_id: str,
    output_dir: str,
    threshold: float = 0.9,
    image_map: dict[int, str] | None = None,   # ignored by design (step 6 skipped)
    input_root: str | None = None,
) -> list[dict]:
    """
    Secondary pass for low-confidence PDFs.

    Rules:
      - Only runs if len(low_conf) < 4.
      - User provides/chooses a *new* template (e.g., clientA_v2) that has been saved already.
      - We enforce version tracking per client: pick the provided ID if it exists;
        otherwise attempt to use the client's next version automatically.
      - Skips primary steps 0, 3, 6, 7.
      - Keeps the same scoring and output structure for low-confidence reporting.

    Input 'low_conf' is what process_batch returned:
      [ { 'pdf': <path>, 'low_rects': { page_num: [bboxes...] or single bbox } }, ... ]
    """

    # ── guard: # low-confidence PDFs must be < 4 ──
    if not low_conf:
        print("No low-confidence PDFs provided to secondary process. Nothing to do.")
        return []
    if len(low_conf) < 3:
        print(f"Secondary process is limited to 3 PDFs. Received: {len(low_conf)}. Skipping.")
        return []

    # ── Resolve the correct template version to load ──
    tm = TemplateManager()
    client, provided_ver = tm.parse_template_id(new_template_id)

    # Try to load exactly what the user selected (preferred).
    chosen_template_id = None
    try:
        tm.load_profile(new_template_id)
        chosen_template_id = new_template_id
    except Exception:
        # If user mistyped, try the next expected version automatically
        expected_next = tm.next_version_id(client)
        try:
            tm.load_profile(expected_next)
            print(f"Note: '{new_template_id}' not found. Using '{expected_next}' instead.")
            chosen_template_id = expected_next
        except Exception as e:
            raise FileNotFoundError(
                f"Could not find template '{new_template_id}' or expected next '{expected_next}'. "
                f"Save the new template first with TemplateManager.save_profile(...)."
            ) from e

    # ── Load template profile ──
    profile      = tm.load_profile(chosen_template_id)
    rectangles   = profile["rectangles"]
    ref_contents = profile["contents"]

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Normalize the low_conf input shape: {page -> [bbox, ...]}
    def _normalize_pages_dict(p):
        norm = {}
        for k, v in p.items():
            # keys might be strings; ensure int
            try:
                pg = int(k)
            except Exception:
                pg = k  # last resort
            if isinstance(v, list) and (len(v) == 0 or isinstance(v[0], (list, tuple))):
                norm[pg] = v
            else:
                # single bbox -> list
                norm[pg] = [v]
        return norm

    # Build the set of PDFs to process
    pdf_entries = []
    for entry in low_conf:
        pdf_path = entry.get("pdf")
        page_map = entry.get("low_rects", {})
        pdf_entries.append({
            "pdf": pdf_path,
            "low_rects": _normalize_pages_dict(page_map)
        })

    results_low_conf = []

    for item in pdf_entries:
        pdf = item["pdf"]
        base = os.path.splitext(os.path.basename(pdf))[0]

        # Preserve hierarchy if input_root is provided
        if input_root:
            rel_path   = os.path.relpath(pdf, input_root)
            sub_dir    = os.path.dirname(rel_path)
            target_dir = os.path.join(output_dir, sub_dir)
            os.makedirs(target_dir, exist_ok=True)
            sanitized  = os.path.join(target_dir, f"{base}_sanitized.pdf")
        else:
            sanitized  = os.path.join(output_dir, f"{base}_sanitized.pdf")

        print(f"\n[Secondary] Processing low-confidence PDF: {pdf}")
        print(f"[Secondary] Using template: {chosen_template_id} (skip steps 0, 3, 6, 7)")

        # ── Step 1: Determine page count & replicate template zones ──
       
        paper, orient, (pw, ph) = _classify_pdf_layout(pdf_path=pdf, tol=0.05)
        print(f"[Secondary][Layout] {pdf}: paper={paper}, orientation={orient}, size=({pw:.1f}×{ph:.1f})")

        active_rectangles = _filter_rectangles_for_layout(rectangles, paper, orient)

        if not active_rectangles:
            print(f"[Secondary][Layout] No rectangles match layout (paper={paper}, orientation={orient}). Skipping {pdf} and flagging.")
            doc = fitz.open(pdf)
            page_to_bboxes = {i: [r["bbox"] for r in rectangles] for i in range(doc.page_count)}
            doc.close()
            low_conf.append({
                "pdf": pdf,
                "low_rects": page_to_bboxes
            })
            continue
        
        # IMPORTANT: use 1-based page indices in replicas; extract_zones_content() converts to 0-based.
        doc = fitz.open(pdf)
        num_pages = len(doc)
        doc.close()

        replicated_rectangles = [
            {"page": i, "bbox": r["bbox"]}
            for i in range(num_pages)
            for r in active_rectangles
        ]

        oob_issues = _validate_replicated_rects_for_pdf(
            pdf_path=pdf,
            replicated_rectangles=replicated_rectangles,
            page_is_one_based=False
        )

        if oob_issues:
            page_to_bboxes = defaultdict(list)

            for meta in oob_issues.get('page_out_of_range', []):
                page_to_bboxes[int(meta['page'])].append(meta['bbox'])

            for p0, lst in oob_issues.items():
                if p0 == 'page_out_of_range':
                    continue
                for it in lst:
                    page_to_bboxes[int(p0)].append(it['bbox'])

            print(f"[Secondary][Safety] Out-of-bounds rectangles for {pdf}. Skipping this PDF and flagging as low-confidence.")
            results_low_conf.append({
                "pdf": pdf,
                "low_rects": dict(page_to_bboxes)
            })
            continue

        # ── Step 2: Extract & score template zones ──
        tgt_contents = extract_zones_content(pdf, replicated_rectangles)

        record_scores = []
        for ref, tgt, rect in zip(ref_contents, tgt_contents, replicated_rectangles):
            pg      = tgt["page"]   # 0-based (from extractor)
            tscore  = ConfidenceScorer.score_text(ref["text"], tgt["text"])
            iscore  = ConfidenceScorer.score_image(ref["image_hash"], tgt["image_hash"])
            rscore  = (tscore + iscore) / 2
            record_scores.append({
                "page": pg,
                "bbox": rect["bbox"],
                "tscore": tscore,
                "iscore": iscore,
                "rscore": rscore
            })

        # Group by page
        pages = defaultdict(list)
        for rec in record_scores:
            pages[rec["page"]].append(rec)

        # Thresholds aligned with primary
        THRESH_R = threshold
        THRESH_T = 0.85
        THRESH_I = 0.85

        high_conf_rects = []
        low_confidence_by_page = defaultdict(list)

        for pg, recs in pages.items():
            for rec in recs:
                if (rec["rscore"] < THRESH_R
                    or rec["tscore"] < THRESH_T
                    or rec["iscore"] < THRESH_I):
                    low_confidence_by_page[pg].append(rec["bbox"])
                else:
                    high_conf_rects.append({
                        "page": rec["page"],   # 0-based index for RedactionEngine
                        "bbox": rec["bbox"]
                    })

        # after building high_conf_rects
        doc = fitz.open(pdf)
        high_conf_rects_rotaware = []
        for rc in high_conf_rects:
            p0 = rc["page"]   # 0-based
            pr = (doc[p0].rotation or 0) % 360
            pw = doc[p0].rect.width
            ph = doc[p0].rect.height
            tb = transform_bbox_for_rotation(rc["bbox"], pw, ph, pr)
            high_conf_rects_rotaware.append({"page": p0, "bbox": tb})
        doc.close()


        # ── Step 4: Combine redaction rectangles ──
        # (Secondary run intentionally skips manual steps and image placement)
        all_rects = high_conf_rects_rotaware

        # ── Step 5: Redact ──
        RedactionEngine.redact(pdf, all_rects, sanitized)

        # ── Steps 6 & 7: skipped by design ──

        if low_confidence_by_page:
            print(f"[Secondary] Low-confidence rectangles remain in {pdf}: {dict(low_confidence_by_page)}")
            results_low_conf.append({
                "pdf": pdf,
                "low_rects": dict(low_confidence_by_page)  # keep shape consistent
            })
        else:
            print(f"[Secondary] All template rectangles high-confidence for {pdf}.")

    # Log some version stats for traceability
    client_versions = tm.list_versions(client)
    secondary_runs  = max(0, len(client_versions) - 1)  # v1 assumed as initial baseline
    print(f"\n[Secondary] Client '{client}' versions present: {client_versions} (secondary runs so far ≈ {secondary_runs})")

    return results_low_conf


# Placeholder for UI prototype (e.g., Streamlit or Flask)
def launch_ui():
    pass  # TODO: implement web UI for template upload, drawing, batch processing


if __name__ == '__main__':
    # Example CLI usage
    parser = argparse.ArgumentParser(
        description='Sanitize a batch of PDFs by template + manual rules.'
    )


    # ── allow either a flat list of files or a root folder to scan ──
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--batch',
        nargs='+',
        help='Explicit list of PDF files to process'
    )
    group.add_argument(
        '--input-dir',
        help='Root folder; recursively find all “*.pdf” beneath it'
    )

    parser.add_argument(
        '--template',
        required=True,
        help='Template ID (json profile name)'
    )
    parser.add_argument(
        '--out',
        required=True,
        help='Output folder'
    )
    parser.add_argument(
        '--names',
        default='',
        help='Comma-separated extra sensitive names/phrases'
    )

    args = parser.parse_args()

    # build the pdf_paths list
    if args.batch:
        pdf_paths = args.batch
        input_root = None
    else:
        pdf_paths = []
        for root, dirs, files in os.walk(args.input_dir):
            for fn in files:
                if fn.lower().endswith('.pdf'):
                    pdf_paths.append(os.path.join(root, fn))
        input_root = args.input_dir
        
    # parse manual names
    manual_names = [n.strip() for n in args.names.split(',') if n.strip()]

    # run
    low = process_batch(
        pdf_paths=pdf_paths,
        template_id=args.template,
        output_dir=args.out,
        threshold=0.9,
        manual_names=manual_names,
        input_root=input_root
    )

    if low:
        print("Low-confidence files (< threshold):")
        for info in low:
            print(f"  {info['pdf']}: {info['low_rects']}")
    else:
        print("All PDFs sanitized successfully.")

# api_app.py
import os, shutil, tempfile, zipfile, json, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, File, Request
import fitz 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from pipeline import process_batch, process_text_only, extract_raw_text, dedupe_text_pages
from llm_utils import get_sensitive_terms_from_llm
from template_utils import TemplateManager

# --- CORS: allow specific origins from env, else default to * for dev ---
_raw = os.getenv("CORS_ORIGINS", "").strip()
allow_origins = [o.strip() for o in _raw.split(",") if o.strip()] if _raw else ["*"]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Optional friendly root
@app.get("/")
async def root():
    return {"ok": True, "service": "pdf-sanitization-api"}

# Single, consistent output folder (local fallback serving)
STATIC_DIR = os.path.abspath("output_sanitized")
os.makedirs(STATIC_DIR, exist_ok=True)

import time
# helper to delete old zip files
def delete_old_zips(folder: str, hours: int = 1):
    """
    Deletes ZIP files in `folder` older than `hours`.
    """
    now = time.time()
    cutoff = now - hours * 3600

    for file in os.listdir(folder):
        if file.endswith(".zip"):
            path = os.path.join(folder, file)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    print(f"[Cleanup] Deleted old zip: {file}")
            except Exception as e:
                print(f"[Cleanup Error] Could not delete {file}: {e}")

def zip_sanitized_pdfs(pdf_paths: list[str], output_dir: str, zip_name: str) -> str:
    zip_path = os.path.join(output_dir, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for path in pdf_paths:
            arcname = os.path.basename(path)
            zipf.write(path, arcname=arcname)
    return zip_path
    
def _safe_client_id(s: str) -> str:
    s = (s or "").strip().lower().replace(" ", "_")
    return "".join(ch for ch in s if ch.isalnum() or ch in "_-") or "template"

def zip_append_with_versions(zip_path: str, file_paths: list[str]) -> str:
    """
    Append files into an existing ZIP or create it if not present.
    If a file's arcname already exists, append _2, _3, ... to its arcname.
    """
    mode = "a" if os.path.exists(zip_path) else "w"
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as z:
        existing = set(z.namelist())
        for fp in file_paths:
            base = os.path.basename(fp)
            name, ext = os.path.splitext(base)
            arc = base
            idx = 2
            while arc in existing:
                arc = f"{name}_{idx}{ext}"
                idx += 1
            z.write(fp, arcname=arc)
            existing.add(arc)
    return zip_path

# helpers for passlog to show only low conf pages filtering out processed pages
def _passlog_path_for(client: str) -> str:
    return os.path.join(STATIC_DIR, f"{client}_passlog.json")

def _load_passlog(client: str) -> dict:
    path = _passlog_path_for(client)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # normalize to lists of ints
            fixed = {}
            for k, v in (data.items() if isinstance(data, dict) else []):
                try:
                    fixed[k] = sorted({int(x) for x in (v or [])})
                except Exception:
                    fixed[k] = []
            return fixed
    except Exception:
        return {}

def _save_passlog(client: str, data: dict) -> None:
    path = _passlog_path_for(client)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def _norm_key_from_path(p: str) -> str:
    """
    Use base filename, strip one or MORE trailing '_sanitized' suffixes, keep .pdf.
    'X_sanitized.pdf' or 'X_sanitized_sanitized.pdf' -> 'X.pdf'
    """
    base = os.path.basename(p or "")
    lower = base.lower()
    # loop off trailing '_sanitized' segments
    while lower.endswith("_sanitized.pdf"):
        base = base[: -(len("_sanitized.pdf"))] + ".pdf"
        lower = base.lower()
    return base


# ---------- Optional Supabase outputs/templates/logos ----------
try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None

_SB_URL  = os.getenv("SUPABASE_URL")
_SB_KEY  = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
_SB_BUCKET = os.getenv("SUPABASE_BUCKET", "pdf-sanitization")
_SB_OUT_PREFIX = os.getenv("SUPABASE_OUTPUTS_PREFIX", "sanitized").rstrip("/")
_SB_TPL_PREFIX = os.getenv("SUPABASE_TEMPLATES_PREFIX", "templates").rstrip("/")
_SB_LOGOS_PREFIX = os.getenv("SUPABASE_LOGOS_PREFIX", "logos").rstrip("/")

_sb = create_client(_SB_URL, _SB_KEY) if (create_client and _SB_URL and _SB_KEY) else None

def _sb_upload_and_sign(local_path: str, client: str, job_id: str) -> str | None:
    """
    Upload local PDF to Supabase and return a URL (public or 24h signed).
    Returns None if Supabase not configured or upload fails.
    """
    if not _sb:
        return None
    try:
        key_name = os.path.basename(local_path)
        remote_path = f"{_SB_OUT_PREFIX}/{client}/{job_id}/{key_name}"
        with open(local_path, "rb") as f:
            _sb.storage.from_(_SB_BUCKET).upload(
                remote_path, f, {"contentType": "application/pdf", "upsert": "true"}
            )
        # Try public first (if bucket is public)
        try:
            public_url = _sb.storage.from_(_SB_BUCKET).get_public_url(remote_path)
            if public_url:
                return public_url
        except Exception:
            pass
        # Otherwise signed for 24h
        signed = _sb.storage.from_(_SB_BUCKET).create_signed_url(remote_path, 60 * 60 * 24)
        return signed.get("signedURL")
    except Exception:
        return None


@app.post("/api/sanitize")
async def sanitize(
    request: Request,
    files: list[UploadFile] = File(...),
    template_zones: str = Form(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    image_map: str = Form("{}"),  # JSON: {tidx: "logos/<filename>"}
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
    secondary: bool = Form(default=False),
):
    # 1) persist uploads to a temp folder
    tmp_input = tempfile.mkdtemp(prefix="in_")
    paths: list[str] = []
    for file in files:
        dst = os.path.join(tmp_input, file.filename)
        with open(dst, "wb") as f:
            shutil.copyfileobj(file.file, f)
        paths.append(dst)

    index_to_path = {i: p for i, p in enumerate(paths)}  # file_idx -> path

    # 2) normalize JSON inputs
    zones = json.loads(template_zones or "[]")
    for z in zones:
        if "paper" not in z and "size" in z:
            z["paper"] = z.pop("size")
        if "file_idx" not in z:
            z["file_idx"] = 0

    names = json.loads(manual_names or "[]")
    replacements = json.loads(text_replacements or "{}")
    raw_map = json.loads(image_map or "{}")
    img_map = {int(k): v for k, v in raw_map.items()} if raw_map else {}

    client = _safe_client_id(client_name)   # moved earlier so both branches can use it
    template_id = None                      # will be set in template branch
    low_conf = []                           # default; template branch will overwrite


    if (len(zones) == 0) and (names or replacements):
        # Manual-only path (no template save)
        process_text_only(
            pdf_paths=paths,                 # you already built this list above
            output_dir=STATIC_DIR,           # reuse your standard output folder
            manual_names=names,
            text_replacements=replacements,
            input_root=None,
            secondary=False
        )
        template_id = "manual_only"          # so the response object has something sensible
        # then fall through to the common ZIP/response code below
    else:
        # 3) versioned template id
        tm = TemplateManager()
        template_id = tm.next_version_id(client)
    
        # 4) save profile (multi-pdf)
        tm.save_profile_multi(
            template_id=template_id,
            rectangles=zones,
            index_to_path=index_to_path,
            image_map=img_map,
        )
    
        # 5) run batch
        low_conf = process_batch(
            pdf_paths=paths,
            template_id=template_id,
            output_dir=STATIC_DIR,
            threshold=threshold,
            manual_names=names,
            text_replacements=replacements,
            image_map=img_map,
            input_root=None,
            secondary=secondary,   # <-- skip LLM/manual/replacements when True
        )

    # -- Passlog: filter out pages that have passed before & update the passlog with new passes
    passlog = _load_passlog(client)
    # Build a quick map of failing pages returned by pipeline, keyed by normalized base name
    failing_by_base = {}
    for item in (low_conf or []):
        base_key = _norm_key_from_path(item.get("pdf") or "")
        pages = sorted({int(k) for k in (item.get("low_rects") or {}).keys()})
        failing_by_base[base_key] = pages

    # Count pages for each input path, compute new passes, and update passlog
    for p in paths:
        base_key = _norm_key_from_path(p)
        try:
            # how many pages in this PDF
            with fitz.open(p) as d:
                n_pages = int(d.page_count)
        except Exception:
            # if something odd, fall back to: only treat non-failing pages we saw as passes via current failing set
            n_pages = None

        already = set(passlog.get(base_key, []))
        failing = set(failing_by_base.get(base_key, []))

        if n_pages is not None and n_pages > 0:
            all_pages = set(range(n_pages))
            newly_passed = all_pages - failing
        else:
            # no count -> treat pages that are not reported as failing (unknown) as newly passed = empty
            newly_passed = set()

        if newly_passed:
            passlog[base_key] = sorted(already | newly_passed)

    # Now filter the low_conf we’re about to return: drop any page that is already in passlog
    filtered_low_conf = []
    for item in (low_conf or []):
        base_key = _norm_key_from_path(item.get("pdf") or "")
        already = set(passlog.get(base_key, []))
        page_to_bboxes = item.get("low_rects") or {}
        kept = {}
        for k, v in page_to_bboxes.items():
            try:
                pidx = int(k)
            except Exception:
                continue
            if pidx in already:
                continue
            kept[pidx] = v
        if kept:
            filtered_low_conf.append({"pdf": item.get("pdf"), "low_rects": kept})

    # overwrite low_conf with the filtered view and persist the passlog
    low_conf = filtered_low_conf
    _save_passlog(client, passlog)

    # 6) — Clean up old ZIPs first
    delete_old_zips(STATIC_DIR, hours=1)
    # 7) zip sanitized PDFs
    sanitized_paths = []
    for p in paths:
        base = os.path.splitext(os.path.basename(p))[0]
        fn = f"{base}_sanitized.pdf"
        sanitized_path = os.path.join(STATIC_DIR, fn)
        # if pipeline did not produce it (edge cases), create a copy so zipping is safe
        if not os.path.exists(sanitized_path):
            try:
                os.makedirs(os.path.dirname(sanitized_path), exist_ok=True)
                shutil.copyfile(p, sanitized_path)
            except Exception as _e:
                print("[API] Could not create fallback sanitized file:", sanitized_path, _e)
                # If even copy fails, we just won't include it
                sanitized_path = None
        if sanitized_path and os.path.exists(sanitized_path):
            sanitized_paths.append(sanitized_path)
    
    zip_filename = f"{client}_sanitized_pdfs.zip"
    zip_path = os.path.join(STATIC_DIR, zip_filename)
    if sanitized_paths:
        zip_append_with_versions(zip_path, sanitized_paths)



    # Optional cleanup of individual PDFs
    # for f in sanitized_paths:
    #     try:
    #         os.remove(f)
    #     except Exception:
    #         pass
    accept = (request.headers.get("accept") or "").lower()
    zip_url = f"/api/download/{os.path.basename(zip_path)}"
    
    if os.path.exists(zip_path) and "application/json" in accept:
        # Success path: return JSON (no Supabase; local URLs only)
        outs = []
        for p in paths:
            base = os.path.splitext(os.path.basename(p))[0]
            fn = f"{base}_sanitized.pdf"
            outs.append({"name": fn, "url": f"/api/download/{fn}"})
        return {
            "success": True,
            "outputs": outs,
            "zip_url": zip_url,
            "template_id": template_id,
            "client": client,
            "low_conf": low_conf,
        }
    
    # Legacy / default: send the ZIP directly (unchanged for non-JSON callers)
    if os.path.exists(zip_path):
        return FileResponse(zip_path, filename=zip_filename, media_type="application/zip")


    # Fallback: return JSON with URLs if ZIP creation failed
    job_id = uuid.uuid4().hex
    outs = []
    for p in paths:
        base = os.path.splitext(os.path.basename(p))[0]
        fn = f"{base}_sanitized.pdf"
        local_out = os.path.join(STATIC_DIR, fn)

        public_url = _sb_upload_and_sign(local_out, client=client, job_id=job_id)
        if public_url:
            outs.append({"name": fn, "url": public_url})
        else:
            outs.append({"name": fn, "url": f"/api/download/{fn}"})

    return {
        "success": True,
        "outputs": outs,
        "template_id": template_id,
        "client": client,
        "low_conf": low_conf,
    }



@app.post("/api/sanitize-existing")
async def sanitize_existing(
    request: Request,
    files: list[UploadFile] = File(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
    secondary: bool = Form(default=False),
):
    tm = TemplateManager()
    client = _safe_client_id(client_name)
    template_id = tm.latest_version_id(client)

    if not template_id:
        return JSONResponse(
            {"success": False, "error": f"No template found for client '{client}'."},
            status_code=404,
        )

    # confirm template exists
    tm.load_profile(template_id)

    # save uploads to a temp folder (not into output dir)
    tmp_input = tempfile.mkdtemp(prefix="in_")
    paths: list[str] = []
    for f in files:
        dst = os.path.join(tmp_input, f.filename)
        with open(dst, "wb") as w:
            shutil.copyfileobj(f.file, w)
        paths.append(dst)

    names = json.loads(manual_names or "[]")
    replacements = json.loads(text_replacements or "{}")

    # load image_map from template (if present)
    prof = tm.load_profile(template_id)
    raw_map = prof.get("image_map") or {}
    image_map = {int(k): v for k, v in raw_map.items()} if raw_map else {}

    low_conf = process_batch(
        pdf_paths=paths,
        template_id=template_id,
        output_dir=STATIC_DIR,
        threshold=threshold,
        manual_names=names,
        text_replacements=replacements,
        image_map=image_map,
        input_root=None,
        secondary=secondary,
    )
    # -- Passlog: filter out pages that have passed before & update the passlog with new passes
    passlog = _load_passlog(client)
    # Build a quick map of failing pages returned by pipeline, keyed by normalized base name
    failing_by_base = {}
    for item in (low_conf or []):
        base_key = _norm_key_from_path(item.get("pdf") or "")
        pages = sorted({int(k) for k in (item.get("low_rects") or {}).keys()})
        failing_by_base[base_key] = pages

    # Count pages for each input path, compute new passes, and update passlog
    for p in paths:
        base_key = _norm_key_from_path(p)
        try:
            # how many pages in this PDF
            with fitz.open(p) as d:
                n_pages = int(d.page_count)
        except Exception:
            # if something odd, fall back to: only treat non-failing pages we saw as passes via current failing set
            n_pages = None

        already = set(passlog.get(base_key, []))
        failing = set(failing_by_base.get(base_key, []))

        if n_pages is not None and n_pages > 0:
            all_pages = set(range(n_pages))
            newly_passed = all_pages - failing
        else:
            # no count -> treat pages that are not reported as failing (unknown) as newly passed = empty
            newly_passed = set()

        if newly_passed:
            passlog[base_key] = sorted(already | newly_passed)

    # Now filter the low_conf we’re about to return: drop any page that is already in passlog
    filtered_low_conf = []
    for item in (low_conf or []):
        base_key = _norm_key_from_path(item.get("pdf") or "")
        already = set(passlog.get(base_key, []))
        page_to_bboxes = item.get("low_rects") or {}
        kept = {}
        for k, v in page_to_bboxes.items():
            try:
                pidx = int(k)
            except Exception:
                continue
            if pidx in already:
                continue
            kept[pidx] = v
        if kept:
            filtered_low_conf.append({"pdf": item.get("pdf"), "low_rects": kept})

    # overwrite low_conf with the filtered view and persist the passlog
    low_conf = filtered_low_conf
    _save_passlog(client, passlog)


    # 6) — Clean up old ZIPs first
    delete_old_zips(STATIC_DIR, hours=1)
    # 7) zip sanitized PDFs
    sanitized_paths = []
    for p in paths:
        base = os.path.splitext(os.path.basename(p))[0]
        fn = f"{base}_sanitized.pdf"
        sanitized_path = os.path.join(STATIC_DIR, fn)
        # if pipeline did not produce it (edge cases), create a copy so zipping is safe
        if not os.path.exists(sanitized_path):
            try:
                os.makedirs(os.path.dirname(sanitized_path), exist_ok=True)
                shutil.copyfile(p, sanitized_path)
            except Exception as _e:
                print("[API] Could not create fallback sanitized file:", sanitized_path, _e)
                # If even copy fails, we just won't include it
                sanitized_path = None
        if sanitized_path and os.path.exists(sanitized_path):
            sanitized_paths.append(sanitized_path)
    
    zip_filename = f"{client}_sanitized_pdfs.zip"
    zip_path = os.path.join(STATIC_DIR, zip_filename)
    if sanitized_paths:
        zip_append_with_versions(zip_path, sanitized_paths)



    # Optional cleanup of individual PDFs
    # for f in sanitized_paths:
    #     try:
    #         os.remove(f)
    #     except Exception:
    #         pass
    accept = (request.headers.get("accept") or "").lower()
    zip_url = f"/api/download/{os.path.basename(zip_path)}"
    
    if os.path.exists(zip_path) and "application/json" in accept:
        outs = []
        for p in paths:
            base = os.path.splitext(os.path.basename(p))[0]
            fn = f"{base}_sanitized.pdf"
            outs.append({"name": fn, "url": f"/api/download/{fn}"})
        return {
            "success": True,
            "outputs": outs,
            "zip_url": zip_url,
            "template_id": template_id,
            "client": client,
            "low_conf": low_conf,
        }
    
    if os.path.exists(zip_path):
        return FileResponse(zip_path, filename=zip_filename, media_type="application/zip")


    # fallback
    job_id = uuid.uuid4().hex
    outs = []
    for p in paths:
        base = os.path.splitext(os.path.basename(p))[0]
        fn = f"{base}_sanitized.pdf"
        local_out = os.path.join(STATIC_DIR, fn)

        public_url = _sb_upload_and_sign(local_out, client=client, job_id=job_id)
        if public_url:
            outs.append({"name": fn, "url": public_url})
        else:
            outs.append({"name": fn, "url": f"/api/download/{fn}"})

    return {
        "success": True,
        "outputs": outs,
        "template_id": template_id,
        "client": client,
        "low_conf": low_conf,
    }


@app.post("/api/generate-sensitive-terms")
async def generate_sensitive_terms(
    files: list[UploadFile] = File(...),
    context: str = Form(default="")
):
    """
    Generate sensitive terms using LLM from uploaded PDF files.
    Returns a list of detected sensitive terms that can be used in the UI.
    """
    if not files:
        return JSONResponse({"error": "No files provided"}, status_code=400)
    
    # Save uploaded files temporarily
    tmp_input = tempfile.mkdtemp(prefix="llm_")
    pdf_paths = []
    
    try:
        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                continue
            dst = os.path.join(tmp_input, file.filename)
            with open(dst, "wb") as f:
                shutil.copyfileobj(file.file, f)
            pdf_paths.append(dst)
        
        if not pdf_paths:
            return JSONResponse({"error": "No PDF files found"}, status_code=400)
        
        # Extract text from all PDFs
        all_text_pages = []
        for pdf_path in pdf_paths:
            pages_text = extract_raw_text(pdf_path)
            all_text_pages.extend(pages_text)
        
        # Deduplicate text across all pages
        deduped_text = dedupe_text_pages(all_text_pages)
        
        # Default context if not provided
        if not context.strip():
            context = (
                "These texts come from engineering/manufacturing drawings "
                "for machine parts. Non-sensitive info includes part names, "
                "dimensions, machining processes and steps, safety notes and notes regarding any manufacturing steps. Sensitive info includes "
                "personal names, emails, phone/fax numbers, postal addresses, country names, and Copyright notes."
                "text can be in any language, but mostly English."
            )
        
        # Generate sensitive terms using LLM
        if deduped_text.strip():
            sensitive_terms = get_sensitive_terms_from_llm(deduped_text, context)
        else:
            sensitive_terms = []
        
        return {
            "success": True,
            "sensitive_terms": sensitive_terms,
            "total_pages_processed": len(all_text_pages),
            "text_length": len(deduped_text)
        }
        
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate sensitive terms: {str(e)}"}, status_code=500)
    
    finally:
        # Clean up temporary files
        try:
            shutil.rmtree(tmp_input)
        except Exception:
            pass



@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    media = "application/zip" if filename.lower().endswith(".zip") else "application/pdf"
    return FileResponse(file_path, filename=filename, media_type=media)



@app.get("/api/clients")
async def list_clients():
    # Supabase-first listing of templates/<client>/, fallback to local disk
    if _sb:
        try:
            top = _sb.storage.from_(_SB_BUCKET).list(path=_SB_TPL_PREFIX) or []
            candidates = [it.get("name", "") for it in top if it.get("name")]
            clients = []
            for name in candidates:
                if "." in name:
                    continue  # skip files at templates/ root
                sub = _sb.storage.from_(_SB_BUCKET).list(path=f"{_SB_TPL_PREFIX}/{name}") or []
                has_template = any(
                    ent.get("name", "").startswith(f"{name}_v") and ent.get("name", "").endswith(".json")
                    for ent in sub
                )
                if has_template:
                    clients.append(name)
            clients.sort()
            return {"clients": clients}
        except Exception:
            pass  # fall back to local

    tm = TemplateManager()
    root = Path(tm.store_dir)
    root.mkdir(parents=True, exist_ok=True)
    clients = sorted([p.name for p in root.iterdir() if p.is_dir()])
    return {"clients": clients}


@app.post("/api/upload-logo")
async def upload_logo(file: UploadFile = File(...)):
    """
    Upload a single company logo and return the storage key to use in image_map.
    Stored at: logos/<filename>
    """
    filename = file.filename
    data = file.file.read()

    if _sb:
        key = f"{_SB_LOGOS_PREFIX}/{filename}"
        _sb.storage.from_(_SB_BUCKET).upload(
            key, data, {"contentType": file.content_type or "image/png", "upsert": "true"}
        )
        return {"key": key}

    # Local fallback
    local_dir = os.path.join("assets", "logos")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)
    with open(local_path, "wb") as f:
        f.write(data)
    # Return a "key-like" path that the pipeline will treat as local
    return {"key": f"logos/{filename}"}

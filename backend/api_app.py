# api_app.py
import os, shutil, tempfile, zipfile, json, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, File, Request, BackgroundTasks
import fitz 
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import asyncio
from concurrent.futures import ThreadPoolExecutor

from pipeline import process_batch, process_text_only, extract_raw_text, dedupe_text_pages
from llm_utils import get_sensitive_terms_from_llm
from template_utils import TemplateManager


# --- CORS: allow specific origins from env, else default to * for dev ---
_raw = os.getenv("CORS_ORIGINS", "").strip()
allow_origins = [o.strip() for o in _raw.split(",") if o.strip()] if _raw else ["*"]

app = FastAPI()

# ---- Concurrency controls (env-tunable) ----
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
SANITIZE_WORKERS = int(os.getenv("SANITIZE_WORKERS", str(MAX_CONCURRENT_JOBS)))
SEM = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
EXECUTOR = ThreadPoolExecutor(max_workers=SANITIZE_WORKERS)

def _new_job_workspace(prefix: str = "wootz_job_"):
    job_id = uuid.uuid4().hex
    base = Path(tempfile.mkdtemp(prefix=f"{prefix}{job_id}_"))
    (base / "uploads").mkdir(exist_ok=True, parents=True)
    (base / "outputs").mkdir(exist_ok=True, parents=True)
    return job_id, base

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    Delete zip files older than 'hours' hours in the specified folder.
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
    
def _safe_filename(name: str) -> str:
    name = (name or "file.pdf").strip().replace("\\", "/").split("/")[-1]
    keep = "-_.() "
    cleaned = "".join(c for c in name if c.isalnum() or c in keep).strip()
    return cleaned or "file.pdf"


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
    for _ in range(3):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return
        except Exception:
            time.sleep(0.05)
    # last attempt
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _norm_key_from_path(p: str) -> str:
    """
    normalize path to base name only (no directories, lowercase)
    """
    try:
        return os.path.splitext(os.path.basename(p))[0].strip().lower()
    except Exception:
        return (p or "").strip().lower()


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
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    template_zones: str = Form(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    image_map: str = Form("{}"),  # JSON: {tidx: "logos/<filename>"}
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
    secondary: bool = Form(default=False),
):
    # --- per-job workspace and uploads ---
    job_id, workdir = _new_job_workspace()
    uploads_dir = workdir / 'uploads'
    out_dir = workdir / 'outputs'

    # 1) persist uploads to a temp folder
    tmp_input = str(uploads_dir)
    paths: list[str] = []
    for file in files:
        safe = _safe_filename(file.filename)
        dst = os.path.join(tmp_input, safe)
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
    # ===== Manual-only path (no template save) =====
        # Run text-only replacement under concurrency gate, outputting into this job's out_dir
        async with SEM:
            loop = asyncio.get_running_loop()
            def _run_manual():
                return process_text_only(
                    pdf_paths=paths,
                    output_dir=str(out_dir),     # job-scoped outputs
                    manual_names=names,
                    text_replacements=replacements,
                    input_root=None,
                    secondary=False
                )
            await loop.run_in_executor(EXECUTOR, _run_manual)

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
    
        # 5) Run the heavy batch with this template under concurrency gate, into job out_dir
        async with SEM:
            loop = asyncio.get_running_loop()
            def _run_batch():
                return process_batch(
                    pdf_paths=paths,
                    template_id=template_id,
                    output_dir=str(out_dir),     # job-scoped outputs
                    threshold=threshold,
                    manual_names=names,
                    text_replacements=replacements,
                    image_map=img_map,
                    input_root=None,
                    secondary=secondary,
                )
            low_conf = await loop.run_in_executor(EXECUTOR, _run_batch)


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
    
    # 7) Collect sanitized PDFs from this job's output dir and zip them (collision-proof)
    sanitized_paths = [str(p) for p in (out_dir.glob("*_sanitized.pdf")) if p.is_file()]
    
    # Optional fallback: if none found (edge cases), include originals as "_sanitized.pdf"
    if not sanitized_paths:
        for p in paths:
            dst = out_dir / (Path(p).stem + "_sanitized.pdf")
            try:
                shutil.copyfile(p, dst)
                sanitized_paths.append(str(dst))
            except Exception:
                pass  # if copy fails, skip
    
    # Create a unique, per-job zip inside the job workspace
    zip_filename = f"{client}_{job_id}_sanitized.zip"
    job_zip_path = out_dir / zip_filename
    with zipfile.ZipFile(job_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for pth in sanitized_paths:
            zf.write(pth, arcname=os.path.basename(pth))
    
    # Expose the ZIP (and individual PDFs) via STATIC_DIR so /api/download works
    final_zip_path = os.path.join(STATIC_DIR, zip_filename)
    try:
        shutil.copyfile(job_zip_path, final_zip_path)
    except Exception:
        pass
    
    outs = []
    for pth in sanitized_paths:
        base = os.path.basename(pth)
        dest = os.path.join(STATIC_DIR, base)
        try:
            shutil.copyfile(pth, dest)
        except Exception:
            pass
        outs.append({"name": base, "url": f"/api/download/{base}"})
    
    zip_url = f"/api/download/{os.path.basename(final_zip_path)}"
    
    accept = (request.headers.get("accept") or "").lower()
    
    if os.path.exists(final_zip_path) and "application/json" in accept:
        # schedule asynchronous cleanup of the job workspace
        def _cleanup(path: Path):
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
        background_tasks.add_task(_cleanup, workdir)
    
        return {
            "success": True,
            "outputs": outs,
            "zip_url": zip_url,
            "template_id": template_id,
            "client": client,
            "low_conf": low_conf,
        }
    
    if os.path.exists(final_zip_path):
        def _cleanup(path: Path):
            try:
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                pass
        background_tasks.add_task(_cleanup, workdir)
        return FileResponse(final_zip_path, filename=os.path.basename(final_zip_path), media_type="application/zip")
    
    # Fallback: return JSON with URLs if ZIP creation failed
    # (Optionally upload each PDF to Supabase here if desired)
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
    background_tasks: BackgroundTasks,
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

    # --- per-job workspace and uploads ---
    job_id, workdir = _new_job_workspace()
    uploads_dir = workdir / 'uploads'
    out_dir = workdir / 'outputs'


    # save uploads to a temp folder (not into output dir)
    tmp_input = str(uploads_dir)    
    paths: list[str] = []
    for f in files:
        safe = _safe_filename(f.filename)
        dst = os.path.join(tmp_input, safe)
        with open(dst, "wb") as w:
            shutil.copyfileobj(f.file, w)
        paths.append(dst)

    names = json.loads(manual_names or "[]")
    replacements = json.loads(text_replacements or "{}")

    # load image_map from template (if present)
    prof = tm.load_profile(template_id)
    raw_map = prof.get("image_map") or {}
    image_map = {int(k): v for k, v in raw_map.items()} if raw_map else {}

    async with SEM:
        loop = asyncio.get_running_loop()
        def _run2():
            return process_batch(
                pdf_paths=paths,
                template_id=template_id,
                output_dir=str(out_dir),
                threshold=threshold,
                manual_names=names,
                text_replacements=replacements,
                image_map=image_map,
                input_root=None,
                secondary=secondary,
            )
        low_conf = await loop.run_in_executor(EXECUTOR, _run2)

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
    sanitized_paths = [str(p) for p in (out_dir.glob("*_sanitized.pdf")) if p.is_file()]
    zip_filename = f"{client}_{job_id}_sanitized.zip"
    job_zip_path = out_dir / zip_filename
    with zipfile.ZipFile(job_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for pth in (sanitized_paths or []):
            if os.path.exists(pth):
                zf.write(pth, arcname=os.path.basename(pth))
    zip_path = os.path.join(STATIC_DIR, zip_filename)
    try:
        shutil.copyfile(job_zip_path, zip_path)
    except Exception:
        pass
    zip_url = f"/api/download/{os.path.basename(zip_path)}"

    # mirror individual outputs into STATIC_DIR for download endpoints
    outs = []
    for pth in (sanitized_paths or []):
        base = os.path.basename(pth)
        dest = os.path.join(STATIC_DIR, base)
        try:
            shutil.copyfile(pth, dest)
        except Exception:
            pass
        outs.append({"name": base, "url": f"/api/download/{base}"})

    # schedule workspace cleanup
    def _cleanup2(path: Path):
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass
    background_tasks.add_task(_cleanup2, workdir)

    accept = (request.headers.get("accept") or "").lower()
    if os.path.exists(zip_path) and "application/json" in accept:
        return {
            "success": True,
            "outputs": outs,
            "zip_url": zip_url,
            "template_id": template_id,
            "client": client,
            "low_conf": low_conf,
        }
    if os.path.exists(zip_path):
        return FileResponse(zip_path, filename=os.path.basename(zip_path), media_type="application/zip")



    # fallback
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
    context: str = Form(default=""),
    background_tasks: BackgroundTasks = None,
):
    """
    Generate sensitive terms using LLM from uploaded PDF files.
    Safe for concurrent users: per-job workspace + bounded off-loop execution.
    """
    if not files:
        return JSONResponse({"error": "No files provided"}, status_code=400)

    # Per-job workspace (isolates user runs)
    job_id, workdir = _new_job_workspace(prefix="llm_")
    uploads_dir = workdir / "uploads"

    # Save only PDF uploads into this job's folder
    pdf_paths: list[str] = []
    for file in files:
        filename = _safe_filename(file.filename or "")
        if not filename.lower().endswith(".pdf"):
            continue
        dst = uploads_dir / filename
        with open(dst, "wb") as f:
            shutil.copyfileobj(file.file, f)
        pdf_paths.append(str(dst))

    if not pdf_paths:
        # Schedule cleanup and exit early
        if background_tasks:
            background_tasks.add_task(lambda p: shutil.rmtree(p, ignore_errors=True), workdir)
        return JSONResponse({"error": "No PDF files found"}, status_code=400)

    # Default context if not provided
    if not context.strip():
        context = (
            "These texts come from engineering/manufacturing drawings for machine parts. "
            "Non-sensitive info includes part names, dimensions, machining processes/steps, "
            "safety notes, and general manufacturing notes. Sensitive info includes personal names, "
            "emails, phone/fax numbers, postal addresses, country names, and copyright notices. "
            "Text can be in any language, mostly English."
        )

    # Run heavy work off the event loop under a small concurrency gate
    async with SEM:
        loop = asyncio.get_running_loop()

        def _run_extract_and_llm():
            # Extract text from all PDFs
            all_text_pages: list[str] = []
            for pdf_path in pdf_paths:
                pages_text = extract_raw_text(pdf_path)
                all_text_pages.extend(pages_text)

            # Deduplicate across pages/files
            deduped_text = dedupe_text_pages(all_text_pages)

            # Call LLM only if we have something meaningful
            sensitive_terms = get_sensitive_terms_from_llm(deduped_text, context) if deduped_text.strip() else []

            return {
                "success": True,
                "sensitive_terms": sensitive_terms,
                "total_pages_processed": len(all_text_pages),
                "text_length": len(deduped_text),
            }

        try:
            result = await loop.run_in_executor(EXECUTOR, _run_extract_and_llm)
        except Exception as e:
            # Ensure cleanup even on failure
            if background_tasks:
                background_tasks.add_task(lambda p: shutil.rmtree(p, ignore_errors=True), workdir)
            return JSONResponse({"error": f"Failed to generate sensitive terms: {str(e)}"}, status_code=500)

    # Schedule workspace cleanup after successful response
    if background_tasks:
        background_tasks.add_task(lambda p: shutil.rmtree(p, ignore_errors=True), workdir)

    return result



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
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    local_path = os.path.join(local_dir, unique_name)
    with open(local_path, "wb") as f:
        f.write(data)
    return {"key": f"logos/{unique_name}"}

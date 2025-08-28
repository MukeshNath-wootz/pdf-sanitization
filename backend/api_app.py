# api_app.py
import os, shutil, tempfile, json, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from pipeline import process_batch
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

def _safe_client_id(s: str) -> str:
    s = (s or "").strip().lower().replace(" ", "_")
    return "".join(ch for ch in s if ch.isalnum() or ch in "_-") or "template"

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
                remote_path, f, {"contentType": "application/pdf", "upsert": True}
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
    files: list[UploadFile] = File(...),
    template_zones: str = Form(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    image_map: str = Form("{}"),  # JSON: {tidx: "logos/<filename>"}
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
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

    # 3) versioned template id
    tm = TemplateManager()
    client = _safe_client_id(client_name)
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
    )

    # 6) build output links (Supabase preferred, fallback to local download)
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
    files: list[UploadFile] = File(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
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
    )

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


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(file_path, filename=filename, media_type="application/pdf")


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
            key, data, {"contentType": file.content_type or "image/png", "upsert": True}
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

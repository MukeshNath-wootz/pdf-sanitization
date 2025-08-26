# api_app.py
from logging import root
import os, shutil, tempfile, json
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pipeline import process_batch
from template_utils import TemplateManager

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = os.path.abspath("output_sanitized")
os.makedirs(STATIC_DIR, exist_ok=True)

@app.post("/api/sanitize")
async def sanitize(
    files: list[UploadFile],
    template_zones: str = Form(...),
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    image_map: str = Form('{}'), # JSON string for mapping rect-indices to logos
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),  # ← NEW: receive client's name from UI
    # template_source_index: int = Form(default=0)  # ← NEW: which file to use for template rects
):
    tmp_input = tempfile.mkdtemp()
    paths = []
    for file in files:
        path = os.path.join(tmp_input, file.filename)
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        paths.append(path)

    index_to_path = {i: p for i, p in enumerate(paths)}  # ✅ map file_idx → saved path

    # if 0 <= template_source_index < len(paths):
    #     # move the chosen template file to index 0
    #     paths.insert(0, paths.pop(template_source_index))
        
    # Save template inputs
    zones = json.loads(template_zones or "[]")
    # normalize: accept legacy 'size' and ensure 'file_idx'
    for z in zones:
        if "paper" not in z and "size" in z:
            z["paper"] = z.pop("size")
        if "file_idx" not in z:
            z["file_idx"] = 0  # default to first file if missing

    names = json.loads(manual_names or "[]")
    replacements = json.loads(text_replacements or "{}")
    raw_map = json.loads(image_map or "{}")
    img_map = {int(k): v for k, v in raw_map.items()} if raw_map else {}

## versioned template id
    tm = TemplateManager()
    # 1) build safe client id (folder name) from client_name
    def safe_id(s: str) -> str:
        s = (s or "").strip().lower().replace(" ", "_")
        return "".join(ch for ch in s if ch.isalnum() or ch in "_-") or "template"
    client = safe_id(client_name)

    # 2) compute next version id, e.g. "<client>_v1", "<client>_v2", ...
    #    TemplateManager handles version discovery and folder creation
    template_id = tm.next_version_id(client)  # e.g. "acme_manufacturing_v1"

    # 3) save profile to templates/<client>/<client>_vN.json
    # Old version for single pdf
    # tm.save_profile(template_id, pdf_path=paths[0], rectangles=zones, image_map=img_map)
    
    # NEW: multi-PDF save with per-rect extraction from its own source PDF
    tm.save_profile_multi(
        template_id=template_id,
        rectangles=zones,
        index_to_path=index_to_path,
        image_map=img_map,
    )

    low_conf = process_batch(
        pdf_paths=paths,
        template_id=template_id,
        output_dir=STATIC_DIR,
        threshold=threshold,
        manual_names=names,
        text_replacements=replacements,
        image_map=img_map
    )

    result_files = []
    for fn in os.listdir(STATIC_DIR):
        if fn.endswith("_sanitized.pdf"):
            result_files.append({
                "originalName": fn,
                "path": f"/api/output_sanitized/{fn}"
            })

    return {"success": True, "files": [{"originalName": os.path.basename(p)} for p in paths],
             "template_id": template_id, "client": client, "low_conf": low_conf}

# NEW: existing-client flow (skip rectangles; use client's v1)
@app.post("/api/sanitize-existing")
async def sanitize_existing(
    files: list[UploadFile],
    manual_names: str = Form(default="[]"),
    text_replacements: str = Form(default="{}"),
    threshold: float = Form(default=0.9),
    client_name: str = Form(...),
):
    tm = TemplateManager()

    # safe client id (same rule you used earlier)
    def safe_id(s: str) -> str:
        s = (s or "").strip().lower().replace(" ", "_")
        return "".join(ch for ch in s if ch.isalnum() or ch in "_-") or "template"
    client = safe_id(client_name)
    template_id = tm.latest_version_id(client)

    # 1) ensure template exists (templates/<client>/<client>_v1.json)
    #    TemplateManager can resolve path; using for_write=False to avoid creating it
    try:
        # If you have a 'load_profile', this also validates presence:
        tm.load_profile(template_id)
    except Exception as e:
        return {"success": False, "error": f"Template '{template_id}' not found for client '{client}'"}

    # 2) persist uploads to disk
    STATIC_DIR = "sanitized_outputs"
    os.makedirs(STATIC_DIR, exist_ok=True)
    paths = []
    for f in files:
        dst = os.path.join(STATIC_DIR, f.filename)
        with open(dst, "wb") as w:
            shutil.copyfileobj(f.file, w)
        paths.append(dst)

    # 3) de/serialize text inputs
    names = json.loads(manual_names or "[]")
    replacements = json.loads(text_replacements or "{}")
    template_for_image = tm.load_profile(template_id)
    raw_map = template_for_image.get("image_map", None)  # raw image map {string index -> image path}
    image_map = {int(k): v for k, v in raw_map.items()} if raw_map else {}

    # 4) run batch with the fixed version 'client_v1'
    low_conf = process_batch(
        pdf_paths=paths,
        template_id=template_id,
        output_dir=STATIC_DIR,
        threshold=threshold,
        manual_names=names,
        text_replacements=replacements,
        image_map=image_map,  # use whatever saved with template
    )

    # 5) return files for download (same shape as your other endpoint)
    result_files = [{"originalName": os.path.basename(p)} for p in paths]
    return {
        "success": True,
        "files": result_files,
        "template_id": template_id,
        "client": client,
        "low_conf": low_conf,
    }


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = os.path.join(STATIC_DIR, filename)
    return FileResponse(file_path, filename=filename)

@app.get("/api/clients")
async def list_clients():
    tm = TemplateManager()
    root = Path(tm.store_dir)
    root.mkdir(parents=True, exist_ok=True)
    clients = sorted([p.name for p in root.iterdir() if p.is_dir()])
    return {"clients": clients}

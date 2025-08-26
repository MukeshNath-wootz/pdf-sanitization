import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Map, FilePlus2, ZoomIn, ZoomOut, RotateCcw,
  Loader2, Trash2, Plus, MousePointer2, FileDown, CheckCircle2, Info, Upload
} from "lucide-react";
import JSZip from "jszip";
import { saveAs } from "file-saver";

// ------------------------------
// PDF.js v4 ESM imports + worker
// ------------------------------
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist/build/pdf";
GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).href;

// ------------------------------
// Small helpers
// ------------------------------
const fmt = (n) => Math.round(n * 100) / 100;

function parseReplacementMap(raw) {
  const out = {};
  const errors = [];
  const trimmed = (raw || "").trim();
  if (!trimmed) return { map: out, errors };
  try {
    const obj = JSON.parse(trimmed);
    if (obj && typeof obj === "object" && !Array.isArray(obj)) {
      for (const [k, v] of Object.entries(obj)) {
        if (typeof k !== "string" || typeof v !== "string") {
          errors.push("JSON keys and values must be strings");
          continue;
        }
        out[k] = v;
      }
      return { map: out, errors };
    }
  } catch {
    /* fallthrough to line mode */
  }
  const lines = trimmed.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const idx = line.indexOf(":");
    if (idx <= 0) {
      errors.push(`Line ${i + 1}: missing ':' separator`);
      continue;
    }
    const left = line.slice(0, idx).trim();
    const right = line.slice(idx + 1).trim();
    if (!left) errors.push(`Line ${i + 1}: empty 'old' value`);
    if (!right) errors.push(`Line ${i + 1}: empty 'new' value`);
    if (left && right) out[left] = right;
  }
  return { map: out, errors };
}

function parseEraseTerms(raw) {
  const set = new Set();
  (raw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .forEach((t) => set.add(t));
  return Array.from(set);
}

// Convert px rect to normalized [0..1]
function normalizeRect(pxRect, w, h) {
  const x1 = Math.max(0, Math.min(pxRect.x1, w));
  const x2 = Math.max(0, Math.min(pxRect.x2, w));
  const y1 = Math.max(0, Math.min(pxRect.y1, h));
  const y2 = Math.max(0, Math.min(pxRect.y2, h));
  const left = Math.min(x1, x2);
  const right = Math.max(x1, x2);
  const top = Math.min(y1, y2);
  const bottom = Math.max(y1, y2);
  return { x1n: left / w, y1n: top / h, x2n: right / w, y2n: bottom / h };
}

// Denormalize to pixels for drawing
function denormalizeRect(nr, w, h) {
  const x1 = nr.x1n * w, y1 = nr.y1n * h;
  const x2 = nr.x2n * w, y2 = nr.y2n * h;
  return { x1, y1, x2, y2 };
}

// To PDF points (viewport scale=1)
function normRectToPdfPoints(nr, pageWpts, pageHpts) {
  return {
    x1: nr.x1n * pageWpts,
    y1: nr.y1n * pageHpts,
    x2: nr.x2n * pageWpts,
    y2: nr.y2n * pageHpts,
  };
}

const Button = ({ className = "", disabled, onClick, title, children }) => (
  <button
    className={`px-3 py-2 rounded-2xl shadow hover:shadow-md border border-black/10 disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
    disabled={disabled}
    onClick={onClick}
    title={title}
    type="button"
  >
    {children}
  </button>
);

const Labeled = ({ label, children, hint }) => (
  <div className="mb-3">
    <div className="flex items-center gap-2 mb-1">
      <span className="text-sm font-medium text-zinc-700">{label}</span>
      {hint ? (
        <span className="inline-flex items-center text-xs text-zinc-500 gap-1">
          <Info size={14} /> {hint}
        </span>
      ) : null}
    </div>
    {children}
  </div>
);

// ================================
// Main App
// ================================
export default function PDFSanitizerApp() {
  // Files & PDF state
  const [files, setFiles] = useState([]);
  const [activeFileIdx, setActiveFileIdx] = useState(0);
  const activeFile = files[activeFileIdx] || null;

  const [pdfDoc, setPdfDoc] = useState(null);
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [zoom, setZoom] = useState(1.0);

  const [loadingPdf, setLoadingPdf] = useState(false);
  const [rendering, setRendering] = useState(false);
  const renderTaskRef = useRef(null);
  const baseCanvasRef = useRef(null);       // PDF raster canvas
  const overlayCanvasRef = useRef(null);    // RED rubber-band + rectangles

  // Drag state for overlay (like App0)
  const isDrawingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });
  const currentPosRef = useRef({ x: 0, y: 0 });

  // Rectangles: per page -> [{ nr, kind, logoFile }]
  const [pageRects, setPageRects] = useState({});

  // Right panel inputs
  const [eraseRaw, setEraseRaw] = useState("");
  const eraseTerms = useMemo(() => parseEraseTerms(eraseRaw), [eraseRaw]);

  const [replRaw, setReplRaw] = useState("");
  const { map: replacementMap, errors: replErrors } = useMemo(
    () => parseReplacementMap(replRaw),
    [replRaw]
  );

  const [submitting, setSubmitting] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");

  // Load current file as PDF
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!activeFile) {
        setPdfDoc(null);
        setNumPages(0);
        setPageNumber(1);
        return;
      }
      setLoadingPdf(true);
      try {
        const buf = await activeFile.arrayBuffer();
        const doc = await getDocument({ data: buf }).promise;
        if (cancelled) return;
        setPdfDoc(doc);
        setNumPages(doc.numPages);
        setPageNumber(1);
      } catch (e) {
        console.error("PDF load error", e);
      } finally {
        if (!cancelled) setLoadingPdf(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [activeFileIdx, activeFile]);

  // Render current page at zoom, then size overlay to match
  useEffect(() => {
    async function render() {
      if (!pdfDoc || !baseCanvasRef.current) return;

      // Cancel previous render if any
      if (renderTaskRef.current) {
        try { renderTaskRef.current.cancel(); } catch {}
        renderTaskRef.current = null;
      }
      setRendering(true);
      try {
        const page = await pdfDoc.getPage(pageNumber);
        const viewport = page.getViewport({ scale: zoom });

        const canvas = baseCanvasRef.current;
        const ctx = canvas.getContext("2d");
        canvas.width = viewport.width;
        canvas.height = viewport.height;

        const task = page.render({ canvasContext: ctx, viewport });
        renderTaskRef.current = task;
        await task.promise;

        // Size overlay exactly like base canvas
        const o = overlayCanvasRef.current;
        if (o) {
          o.width = viewport.width;
          o.height = viewport.height;
          drawOverlay(); // initial paint
        }
      } catch (e) {
        if (e?.name !== "RenderingCancelledException") {
          console.error("Render error", e);
        }
      } finally {
        setRendering(false);
      }
    }
    render();
  }, [pdfDoc, pageNumber, zoom]);

  // Redraw overlay whenever rectangles / page / rendering change
  useEffect(() => { drawOverlay(); }, [pageRects, pageNumber, rendering, zoom]);

  // Draw overlay: saved rects (red band) + live rubber-band during drag
  function drawOverlay() {
    const o = overlayCanvasRef.current;
    if (!o) return;
    const ctx = o.getContext("2d");
    ctx.clearRect(0, 0, o.width, o.height);

    const arr = pageRects[pageNumber] || [];
    for (const r of arr) {
      const { x1, y1, x2, y2 } = denormalizeRect(r.nr, o.width, o.height);
      const x = Math.min(x1, x2), y = Math.min(y1, y2);
      const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);

      ctx.fillStyle = "rgba(255,0,0,0.25)";  // red band
      ctx.strokeStyle = "red";
      ctx.lineWidth = 2;
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
    }

    if (isDrawingRef.current) {
      const { x: x1, y: y1 } = startPosRef.current;
      const { x: x2, y: y2 } = currentPosRef.current;
      const x = Math.min(x1, x2), y = Math.min(y1, y2);
      const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);

      ctx.fillStyle = "rgba(255,0,0,0.25)";
      ctx.strokeStyle = "red";
      ctx.lineWidth = 2;
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
    }
  }

  // Pointer helpers
  function relPoint(e) {
    const o = overlayCanvasRef.current;
    const r = o.getBoundingClientRect();
    const x = (e.clientX ?? e.touches?.[0]?.clientX) - r.left;
    const y = (e.clientY ?? e.touches?.[0]?.clientY) - r.top;
    return { x, y };
  }

  function handlePointerDown(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!overlayCanvasRef.current) return;
    isDrawingRef.current = true;
    const p = relPoint(e);
    startPosRef.current = p;
    currentPosRef.current = p;
    drawOverlay();
  }

  function handlePointerMove(e) {
    if (!isDrawingRef.current) return;
    e.preventDefault();
    const p = relPoint(e);
    currentPosRef.current = p;
    drawOverlay();
  }

  function handlePointerUp(e) {
    if (!isDrawingRef.current) return;
    e.preventDefault();
    isDrawingRef.current = false;

    const o = overlayCanvasRef.current;
    const { x: x1, y: y1 } = startPosRef.current;
    const { x: x2, y: y2 } = currentPosRef.current;
    const w = o?.width || 0, h = o?.height || 0;
    if (!w || !h) return;

    const nr = normalizeRect({ x1, y1, x2, y2 }, w, h);
    if (nr.x1n !== nr.x2n && nr.y1n !== nr.y2n) {
      setPageRects((prev) => {
        const next = { ...prev };
        const arr = next[pageNumber] ? [...next[pageNumber]] : [];
        arr.push({ nr, kind: "redact", logoFile: undefined });
        next[pageNumber] = arr;
        return next;
      });
    }
    drawOverlay();
  }

  function removeRect(idx) {
    setPageRects((prev) => {
      const next = { ...prev };
      const arr = (next[pageNumber] || []).slice();
      arr.splice(idx, 1);
      next[pageNumber] = arr;
      return next;
    });
  }

  function updateRectKind(idx, kind) {
    setPageRects((prev) => {
      const next = { ...prev };
      const arr = (next[pageNumber] || []).slice();
      if (arr[idx]) {
        const willLogo = kind === "logo";
        arr[idx] = { ...arr[idx], kind, logoFile: willLogo ? arr[idx].logoFile : undefined };
      }
      next[pageNumber] = arr;
      return next;
    });
  }

  function setRectLogoFile(idx, file) {
    setPageRects((prev) => {
      const next = { ...prev };
      const arr = (next[pageNumber] || []).slice();
      if (arr[idx]) arr[idx] = { ...arr[idx], logoFile: file };
      next[pageNumber] = arr;
      return next;
    });
  }

  function clearPageRects() {
    setPageRects((prev) => ({ ...prev, [pageNumber]: [] }));
  }

  // Build rectangles + logoUploads for ALL pages
  async function buildPayloadAndLogos() {
    if (!pdfDoc) return { rectangles: [], logoUploads: [] };
    const rectangles = [];
    const logoUploads = [];
    let rect_idx = 0;

    for (let p = 1; p <= numPages; p++) {
      const page = await pdfDoc.getPage(p);
      const viewport1 = page.getViewport({ scale: 1.0 });
      const pageW = viewport1.width, pageH = viewport1.height;
      const rotation = page.rotate; // 0, 90, 180, 270
      const arr = pageRects[p] || [];
      for (const r of arr) {
        const pts = normRectToPdfPoints(r.nr, pageW, pageH);
        rectangles.push({
          rect_idx,
          page: p - 1,
          kind: r.kind,
          bbox_norm: [r.nr.x1n, r.nr.y1n, r.nr.x2n, r.nr.y2n],
          bbox_pts: [fmt(pts.x1), fmt(pts.y1), fmt(pts.x2), fmt(pts.y2)],
          page_w_pts: fmt(pageW),
          page_h_pts: fmt(pageH),
          rotation,
        });
        if (r.kind === "logo" && r.logoFile instanceof File) {
          const field = `logo_${rect_idx}`;
          logoUploads.push({ rect_idx, field, file: r.logoFile });
        }
        rect_idx++;
      }
    }
    return { rectangles, logoUploads };
  }

  function ct(h) { return (h.get("content-type") || "").toLowerCase(); }

  async function onSubmit() {
    try {
      setSubmitting(true);
      setStatusMsg("Packaging request…");

      const { rectangles, logoUploads } = await buildPayloadAndLogos();

      const form = new FormData();
      files.forEach((f) => form.append("pdf_paths", f, f.name)); // backend reads uploaded files

      form.append("rectangles", JSON.stringify(rectangles));
      form.append("erase_terms", JSON.stringify(eraseTerms));
      form.append("replacement_map", JSON.stringify(replacementMap));

      const image_map = {};
      for (const u of logoUploads) image_map[u.rect_idx] = u.field;
      form.append("image_map", JSON.stringify(image_map));
      for (const u of logoUploads) form.append(u.field, u.file, u.file.name);

      setStatusMsg("Calling /api/sanitize…");
      const res = await fetch("/api/sanitize", { method: "POST", body: form });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Server ${res.status}: ${text.slice(0, 500)}`);
      }

      const contentType = ct(res.headers);
      if (contentType.includes("application/zip")) {
        const blob = await res.blob();
        saveAs(blob, "sanitized_pdfs.zip");
        setStatusMsg("Downloaded ZIP from server.");
        return;
      }
      if (contentType.includes("application/pdf")) {
        const blob = await res.blob();
        saveAs(blob, "sanitized.pdf");
        setStatusMsg("Downloaded PDF from server.");
        return;
      }
      if (contentType.includes("application/json")) {
        const data = await res.json();
        if (Array.isArray(data?.files)) {
          const zip = new JSZip();
          for (const f of data.files) {
            if (f?.filename && f?.data) zip.file(f.filename, f.data, { base64: true });
          }
          const blob = await zip.generateAsync({ type: "blob" });
          saveAs(blob, "sanitized_pdfs.zip");
          setStatusMsg("Zipped server JSON payload and downloaded.");
          return;
        }
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        saveAs(blob, "sanitize_response.json");
        setStatusMsg("Saved server JSON response.");
        return;
      }
      const blob = await res.blob();
      saveAs(blob, "sanitize_result.bin");
      setStatusMsg("Saved unknown server response.");
    } catch (err) {
      console.error(err);
      setStatusMsg(String(err.message || err));
    } finally {
      setSubmitting(false);
    }
  }

  const rectsForPage = useMemo(() => pageRects[pageNumber] || [], [pageRects, pageNumber]);

  return (
    <div className="min-h-screen w-full bg-gradient-to-b from-white to-zinc-50 text-zinc-900">
      <header className="sticky top-0 z-10 bg-white/70 backdrop-blur border-b border-zinc-200">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-3 justify-between">
          <div className="flex items-center gap-2">
            <Map className="text-zinc-800" />
            <h1 className="text-lg font-semibold">PDF Sanitizer — Template Markup</h1>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => setZoom((z) => Math.max(0.25, fmt(z - 0.1)))} title="Zoom out"><ZoomOut size={16} /></Button>
            <div className="px-2 text-sm tabular-nums">{Math.round(zoom * 100)}%</div>
            <Button onClick={() => setZoom((z) => Math.min(4, fmt(z + 0.1)))} title="Zoom in"><ZoomIn size={16} /></Button>
            <Button onClick={() => setZoom(1)} title="Reset zoom"><RotateCcw size={16} /></Button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-4 grid grid-cols-12 gap-4">
        {/* LEFT: viewer */}
        <section className="col-span-8">
          <div className="mb-3 p-2 rounded-2xl border border-dashed border-zinc-300 bg-white flex items-center justify-between">
            <div className="flex items-center gap-2">
              <label className="cursor-pointer inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-zinc-100 hover:bg-zinc-200">
                <FilePlus2 size={16} />
                <span className="text-sm">Add PDFs</span>
                <input
                  type="file"
                  accept="application/pdf"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const list = Array.from(e.target.files || []);
                    if (list.length) {
                      setFiles((prev) => [...prev, ...list]);
                      setActiveFileIdx(0);
                    }
                  }}
                />
              </label>
              {files.length > 0 && (
                <div className="text-xs text-zinc-600">{files.length} files selected</div>
              )}
            </div>

            <div className="flex items-center gap-2">
              {files.length > 0 && (
                <select
                  className="text-sm bg-white border border-zinc-300 rounded-xl px-2 py-1"
                  value={String(activeFileIdx)}
                  onChange={(e) => setActiveFileIdx(Number(e.target.value))}
                >
                  {files.map((f, i) => (
                    <option key={i} value={String(i)}>{f.name}</option>
                  ))}
                </select>
              )}
              {activeFile && (
                <button
                  className="text-xs text-red-600 hover:underline"
                  onClick={() => {
                    setFiles((prev) => prev.filter((_, i) => i !== activeFileIdx));
                    setActiveFileIdx(0);
                    setPdfDoc(null);
                    setNumPages(0);
                    setPageNumber(1);
                    setPageRects({});
                  }}
                >Remove active</button>
              )}
            </div>
          </div>

          {/* Page controls */}
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Button onClick={() => setPageNumber((p) => Math.max(1, p - 1))} disabled={!pdfDoc || pageNumber <= 1} title="Previous page">⟵ Prev</Button>
              <div className="text-sm tabular-nums">
                Page <span className="font-semibold">{numPages ? pageNumber : 0}</span> / {numPages}
              </div>
              <Button onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))} disabled={!pdfDoc || pageNumber >= numPages} title="Next page">Next ⟶</Button>
            </div>
            <div className="text-xs text-zinc-500 flex items-center gap-2">
              {loadingPdf && <span className="inline-flex items-center gap-1"><Loader2 className="animate-spin" size={14} /> loading PDF…</span>}
              {rendering && <span className="inline-flex items-center gap-1"><Loader2 className="animate-spin" size={14} /> rendering…</span>}
            </div>
          </div>

          {/* PDF canvas + overlay canvas */}
          <div className="relative rounded-2xl overflow-hidden border border-zinc-200 bg-white">
            <canvas ref={baseCanvasRef} className="block w-full h-auto" />
            <canvas
              ref={overlayCanvasRef}
              className="absolute inset-0 z-30"
              style={{
                position: 'absolute',
                left: 0,
                top: 0,
                width: '100%',
                height: '100%',
                cursor: 'crosshair'
              }}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerLeave={handlePointerUp}
            />
          </div>

          {/* Rectangles list */}
          <div className="mt-3 p-3 rounded-2xl border bg-white">
            <div className="flex items-center justify-between mb-2">
              <div className="font-medium">Rectangles on this page</div>
              <Button onClick={clearPageRects} disabled={!rectsForPage.length}><Trash2 size={16} /> Clear</Button>
            </div>
            {!rectsForPage.length ? (
              <div className="text-sm text-zinc-500">
                Drag on the PDF to draw a rectangle. A red band will indicate the area.
              </div>
            ) : (
              <div className="space-y-3">
                {rectsForPage.map((r, i) => (
                  <div key={i} className="flex flex-col gap-2 p-2 rounded-xl border">
                    <div className="flex items-center gap-3 text-sm">
                      <span className="px-2 py-1 rounded-full bg-zinc-100">#{i + 1}</span>
                      <select
                        className="border rounded-lg px-2 py-1"
                        value={r.kind}
                        onChange={(e) => updateRectKind(i, e.target.value)}
                      >
                        <option value="redact">Redact this rectangle (no replacement)</option>
                        <option value="logo">Insert logo here</option>
                      </select>
                      <span className="text-xs text-zinc-500">
                        (norm) x1:{fmt(r.nr.x1n)} y1:{fmt(r.nr.y1n)} x2:{fmt(r.nr.x2n)} y2:{fmt(r.nr.y2n)}
                      </span>
                      <button className="ml-auto text-red-600 hover:underline" onClick={() => removeRect(i)}>
                        remove
                      </button>
                    </div>

                    {r.kind === "logo" && (
                      <div className="flex items-center gap-3">
                        <label className="cursor-pointer inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-zinc-100 hover:bg-zinc-200">
                          <Upload size={16} />
                          <span className="text-sm">Upload logo for this rectangle</span>
                          <input
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={(e) => {
                              const file = (e.target.files && e.target.files[0]) || undefined;
                              setRectLogoFile(i, file);
                            }}
                          />
                        </label>
                        <div className="text-xs text-zinc-600">
                          {r.logoFile ? (<span>Selected: <b>{r.logoFile.name}</b></span>) : (<span className="text-zinc-400">No file selected</span>)}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* RIGHT: controls */}
        <aside className="col-span-4 space-y-4">
          <div className="p-4 rounded-2xl border bg-white">
            <h2 className="font-semibold mb-3 flex items-center gap-2"><MousePointer2 size={16} /> Workflow</h2>
            <ol className="list-decimal list-inside text-sm text-zinc-700 space-y-1">
              <li>Add one or more PDFs and pick the active one</li>
              <li>Navigate pages and draw rectangles (red band shows selection)</li>
              <li>For each rectangle, choose <b>Redact</b> or <b>Insert logo</b>; upload a logo file if chosen</li>
              <li>Fill in erase terms & replacement map for text</li>
              <li>Click <b>Run Sanitization</b> to send to backend</li>
            </ol>
          </div>

          <div className="p-4 rounded-2xl border bg-white">
            <h2 className="font-semibold mb-3">Text erase & replacements</h2>

            <Labeled label="Text to be erased (comma-separated)" hint="Duplicates are auto-removed">
              <textarea
                className="w-full h-16 text-sm border rounded-xl p-2"
                placeholder="client name, address, phone, ..."
                value={eraseRaw}
                onChange={(e) => setEraseRaw(e.target.value)}
              />
              <div className="mt-1 text-xs text-zinc-500">
                Parsed terms: <span className="font-medium">{eraseTerms.length}</span>
              </div>
            </Labeled>

            <Labeled label="Replacement text map" hint="JSON or \nold:new per line">
              <textarea
                className="w-full h-28 text-sm border rounded-xl p-2 font-mono"
                placeholder={'{"Old Ltd.": "YourCo"}\nsecret123: ******'}
                value={replRaw}
                onChange={(e) => setReplRaw(e.target.value)}
              />
              {replErrors.length ? (
                <div className="mt-2 text-xs text-red-600 space-y-1">
                  {replErrors.map((e, i) => <div key={i}>• {e}</div>)}
                </div>
              ) : (
                <div className="mt-1 text-xs text-emerald-700 flex items-center gap-1"><CheckCircle2 size={14} /> {Object.keys(replacementMap).length} pairs</div>
              )}
            </Labeled>
          </div>

          <div className="p-4 rounded-2xl border bg-white">
            <h2 className="font-semibold mb-3 flex items-center gap-2"><FileDown size={16} /> Run</h2>
            <div className="text-xs text-zinc-600 mb-3">
              Sends PDFs, rectangles (with <b>rect_idx</b>), text erase terms & replacement map, and attaches uploaded logos with an <code>image_map</code> that maps <code>rect_idx</code> → <code>logo_rect_idx</code> field.
            </div>
            <Button className="w-full bg-black text-white hover:bg-zinc-800" onClick={onSubmit} disabled={!files.length || submitting}>
              {submitting ? (
                <span className="inline-flex items-center gap-2"><Loader2 className="animate-spin" size={16} /> Processing…</span>
              ) : (
                <span className="inline-flex items-center gap-2"><Plus size={16} /> Run Sanitization</span>
              )}
            </Button>
            {statusMsg && (
              <div className={`mt-3 text-xs ${statusMsg.toLowerCase().includes("error") ? "text-red-600" : "text-zinc-700"}`}>
                {statusMsg}
              </div>
            )}
          </div>
        </aside>
      </main>

      <footer className="max-w-7xl mx-auto px-4 pb-8 text-xs text-zinc-500">
        Red-band drawing overlay (canvas), pdfjs-dist v4 ESM worker via import.meta.url, JSZip + file-saver.
      </footer>
    </div>
  );
}

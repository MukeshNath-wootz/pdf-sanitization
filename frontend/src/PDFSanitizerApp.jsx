import React, { useEffect, useMemo, useRef, useState } from "react";
// Backend base URL (set Vercel env: VITE_API_BASE=https://<your-render>.onrender.com)
const API_BASE =
   (typeof import !== "undefined" && typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE
     ? import.meta.env.VITE_API_BASE
     : process.env.REACT_APP_API_BASE || ""
   ).replace(/\/+$/, "");
/* ================== Inline Icon Components ================== */
function IconUploadCloud(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M3 15a4 4 0 0 0 4 4h10a5 5 0 0 0 0-10 7 7 0 0 0-13 2" /><path d="M12 12v9" /><path d="m16 16-4-4-4 4" /></svg>);}
function IconX(props){return(<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M18 6 6 18" /><path d="M6 6l12 12" /></svg>);}
function IconCheck(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M20 6 9 17l-5-5" /></svg>);}
function IconChevronDown(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="m6 9 6 6 6-6" /></svg>);}
function IconChevronLeft(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="m15 18-6-6 6-6" /></svg>);}
function IconEye(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>);}
function IconPlus(props){return(<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M12 5v14" /><path d="M5 12h14" /></svg>);}
function IconTrash2(props){return(<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" {...props}><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /><path d="M10 11v6" /><path d="M14 11v6" /></svg>);}

/* ================== PDF.js via CDN (no import.meta) ================== */
/* global pdfjsLib */
let pdfjsReady = false;
async function ensurePdfJs() {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (pdfjsReady) return;
  await new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    s.onload = resolve; s.onerror = reject; document.head.appendChild(s);
  });
  await new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    s.onload = resolve; s.onerror = reject; document.head.appendChild(s);
  });
  if (window.pdfjsLib?.GlobalWorkerOptions) {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }
  pdfjsReady = true;
}

/* ================== Utils (+ self-tests) ================== */
function isPdf(file){ if(!file||typeof file.name!=="string")return false;
  const ext=file.name.toLowerCase().endsWith(".pdf");
  const mime=file.type==="application/pdf"||file.type==="";
  return ext||mime;
}
function filterClients(q,opts){const s=(q||"").trim().toLowerCase();if(!s)return opts.slice();return opts.filter(c=>c.toLowerCase().includes(s));}
function parseEraseCSV(input){const s=(input||"").trim();if(!s)return[];const arr=s.split(/[,\n]/).map(t=>t.trim()).filter(Boolean);
  const seen=new Set();const out=[];for(const t of arr){const k=t.toLowerCase();if(!seen.has(k)){seen.add(k);out.push(t);}}return out;}
function parseReplacementMap(raw){const text=(raw||"").trim();const result={};const errors=[];if(!text)return{map:result,errors};
  if(text.startsWith("{")){try{const obj=JSON.parse(text);if(obj&&typeof obj==="object"&&!Array.isArray(obj)){for(const [k,v] of Object.entries(obj)){const kk=String(k).trim();const vv=String(v??"").trim();if(!kk){errors.push("Empty key in JSON");continue;}result[kk]=vv;}return{map:result,errors};}
    errors.push('JSON must be like {"old":"new"}');}catch{errors.push("Invalid JSON. Use object or line pairs old:new");}}
  const lines=text.split(/\n|,/).map(l=>l.trim()).filter(Boolean);
  for(const line of lines){const idx=line.indexOf(":");if(idx===-1){errors.push(`Missing ':' in "${line}"`);continue;}
    const left=line.slice(0,idx).trim();const right=line.slice(idx+1).trim();if(!left){errors.push(`Empty key in "${line}"`);continue;}result[left]=right;}
  return {map:result,errors};}
(function(){try{console.groupCollapsed("self-tests");
  console.assert(JSON.stringify(filterClients("",["A","B"]))===JSON.stringify(["A","B"]),"filter empty");
  console.assert(JSON.stringify(filterClients("a",["Bar","baz","Qux"]))===JSON.stringify(["Bar","baz"]),"filter includes");
  console.assert(isPdf({name:"x.PDF",type:""})===true,"isPdf ext ok");
  console.assert(parseEraseCSV("foo, bar\nbaz, Foo").length===4,"erase parse");
  console.assert(Object.keys(parseReplacementMap('{"a":"b","c":"d"}').map).length===2,"repl JSON");
  console.assert(Object.keys(parseReplacementMap("a:b\nc:d").map).length===2,"repl pairs");
  console.assert(Object.keys(buildImageMapForTest([{id:"1"},{id:"2"}],{"1":{action:"logo",logoName:"L1"},"2":{action:"redact"}})).length===1,"imageMap build");
  console.log("All self-tests passed ✅");console.groupEnd();}catch(e){console.warn("self-tests failed",e);}})();

// helper used in tests only
function buildImageMapForTest(rects, actions){
  const imageMap = {};
  rects.forEach((r, idx) => {
    const a = actions[r.id];
    if (a && a.action === "logo" && a.logoName) imageMap[idx] = a.logoName;
  });
  return imageMap;
}

/* ================== Demo clients ================== */
// const EXISTING_CLIENTS = ["Acme Manufacturing","Barfee Engineering","Client A","Client B"];

/* ================== Searchable Client Dropdown ================== */
function SearchableClientDropdown({ value, onChange, options }) {
  const [open,setOpen]=useState(false); const [q,setQ]=useState(""); const boxRef=useRef(null);
  const filtered=useMemo(()=>filterClients(q,options),[q,options]); const showNoResults=q.trim()&&filtered.length===0;
  useEffect(()=>{const onClick=e=>{if(!boxRef.current) return; if(!boxRef.current.contains(e.target)) setOpen(false);};
    document.addEventListener("mousedown",onClick); return()=>document.removeEventListener("mousedown",onClick);},[]);
  return(<div className="relative" ref={boxRef}>
    <label className="block text-sm mb-1 text-neutral-300">Client <span className="text-rose-500" aria-hidden="true">*</span></label>
    <div className="rounded-2xl border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm flex items-center gap-2" onClick={()=>setOpen(true)}>
      <input className="bg-transparent outline-none text-sm flex-1" placeholder={value?value:"Search or select client…"}
             value={q} onChange={e=>setQ(e.target.value)} onFocus={()=>setOpen(true)} />
      <IconChevronDown className="h-4 w-4 text-neutral-400" />
    </div>
    {open&&(<div className="absolute z-20 mt-2 w-full rounded-xl border border-neutral-700 bg-neutral-900 shadow-lg max-h-60 overflow-auto">
      <button type="button" className="w-full text-left px-3 py-2 hover:bg-neutral-800 text-sm flex items-center gap-2"
              onClick={()=>{onChange("new");setOpen(false);setQ("");}}>
        <IconPlus className="h-4 w-4" /> ➕ New client…
      </button>
      <div className="border-t border-neutral-800" />
      {showNoResults ? (<div className="px-3 py-2 text-xs text-neutral-500">No clients found</div>) :
        filtered.map(opt=>(
          <button key={opt} type="button" className="w-full text-left px-3 py-2 hover:bg-neutral-800 text-sm"
                  onClick={()=>{onChange(opt);setOpen(false);setQ("");}}>{opt}</button>
        ))}
    </div>)}
  </div>);
}

/* ================== New Client Setup Page (2-step UI) ================== */
function NewClientSetupPage({ pdfFiles, clientName, onBack }) {
  const [activeIndex,setActiveIndex]=useState(0);
  const [rects,setRects]=useState([]);                 // {id,x,y,w,h} normalized
  const [rectActions,setRectActions]=useState({});      // id -> { action: 'redact'|'logo', logoFile?: File }
  const [draft,setDraft]=useState(null);                // {x,y,w,h} in px while drawing
  const [renderError,setRenderError]=useState("");
  const [templateFileIdx, setTemplateFileIdx] = useState(null);

  // Step 2 inputs
  const [step,setStep]=useState(1);                     // 1: rectangles; 2: text+run
  const [eraseRaw,setEraseRaw]=useState("");
  const eraseList=useMemo(()=>parseEraseCSV(eraseRaw),[eraseRaw]);
  const [replRaw,setReplRaw]=useState("");
  const replParsed=useMemo(()=>parseReplacementMap(replRaw),[replRaw]);
  const [threshold,setThreshold]=useState(0.9);
  const [pageMeta, setPageMeta] = useState(null);


  const pdfCanvasRef=useRef(null), overlayRef=useRef(null), wrapRef=useRef(null);
  const file=pdfFiles[activeIndex];

  // Render first page to canvas
  useEffect(()=>{let cancelled=false;
    async function render(){setRenderError(""); const canvas=pdfCanvasRef.current; if(!canvas||!file) return;
      try{
        await ensurePdfJs(); const data=await file.arrayBuffer();
        const task=window.pdfjsLib.getDocument({data}); const pdf=await task.promise; const page=await pdf.getPage(1);
        // const viewport=page.getViewport({scale:1}); 
        // const width=wrapRef.current?wrapRef.current.clientWidth:800;
        // const scale=width/viewport.width; 
        const vp=page.getViewport({scale: 1});

        // classify page size by your buckets in PDF points
        function classifySize(w, h) {
          const maxDim = Math.max(w, h);
          if (maxDim > 2000) return "A1";
          if (maxDim > 1500) return "A2";
          if (maxDim > 1100) return "A3";
          return "A4";
        }

        const meta = {
          pageNo: 0, // first page
          width: Math.floor(vp.width),
          height: Math.floor(vp.height),
          sizeClass: classifySize(vp.width, vp.height),
          orientation: vp.width >= vp.height ? "H" : "V"
        };
        setPageMeta(meta);

        const ctx=canvas.getContext("2d"); 
        canvas.width=Math.floor(vp.width); 
        canvas.height=Math.floor(vp.height);
        // Optional: ensure CSS doesn't auto-scale; let the container scroll instead
        canvas.style.width  = `${Math.floor(vp.width)}px`;
        canvas.style.height = `${Math.floor(vp.height)}px`;
        if (overlayRef.current) {
          overlayRef.current.width  = canvas.width;
          overlayRef.current.height = canvas.height;
          overlayRef.current.style.width  = `${canvas.width}px`;
          overlayRef.current.style.height = `${canvas.height}px`;
        }
        await page.render({canvasContext:ctx,viewport:vp}).promise; if(cancelled) return;
      }catch(err){console.error(err); setRenderError("Failed to render PDF first page.");}
    }
    render(); return()=>{cancelled=true;};
  },[file]);

  // Draw overlay (rects + draft)
  useEffect(()=>{const overlay=overlayRef.current; if(!overlay) return; const ctx=overlay.getContext("2d");
    ctx.clearRect(0,0,overlay.width,overlay.height);
  rects.filter(r => r.fileIdx === activeIndex).forEach(r => {
    const { x, y, w, h } = r;
    ctx.strokeStyle = "rgba(255,0,0,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
});
    if(draft){ctx.strokeStyle="rgba(0,200,255,0.9)"; ctx.lineWidth=2; ctx.setLineDash([4,3]); ctx.strokeRect(draft.x,draft.y,draft.w,draft.h); ctx.setLineDash([]);}
  },[rects, draft, activeIndex]);

  // Drawing handlers (Pointer Events)
  const startRef=useRef(null); const drawingRef=useRef(false);
  const [confirmUI,setConfirmUI]=useState(null);

  const onPointerDown=e=>{if(!overlayRef.current) return; e.preventDefault();
    overlayRef.current.setPointerCapture?.(e.pointerId);
    const r=overlayRef.current.getBoundingClientRect();
    const x=e.clientX-r.left, y=e.clientY-r.top; startRef.current={x,y}; drawingRef.current=true; setDraft({x,y,w:0,h:0});};
  const onPointerMove=e=>{if(!drawingRef.current||!overlayRef.current||!startRef.current) return; e.preventDefault();
    const r=overlayRef.current.getBoundingClientRect(); const x2=e.clientX-r.left,y2=e.clientY-r.top;
    const x=Math.min(startRef.current.x,x2), y=Math.min(startRef.current.y,y2);
    const w=Math.abs(x2-startRef.current.x), h=Math.abs(y2-startRef.current.y); setDraft({x,y,w,h});};
  const onPointerUp=e=>{if(!drawingRef.current) return; e.preventDefault(); drawingRef.current=false;
    overlayRef.current.releasePointerCapture?.(e.pointerId);
    if(!draft||draft.w<4||draft.h<4){setDraft(null);setConfirmUI(null);return;}
    setConfirmUI({left:draft.x+draft.w+8, top:draft.y+draft.h+8});};

  const confirmDraft=()=>{
    if (!overlayRef.current || !draft) return;
    const id = String(Date.now()) + "-" + Math.random().toString(36).slice(2);
    if (templateFileIdx === null) {
      setTemplateFileIdx(activeIndex);
    }
    // Store absolute px coordinates
    setRects(prev => [...prev, {
      id,
      x: Math.round(draft.x),
      y: Math.round(draft.y),
      w: Math.round(draft.w),
      h: Math.round(draft.h),
      // NEW: capture the source you drew on
      fileIdx: activeIndex,
      page: (pageMeta?.pageNo ?? 0),
      paper: (pageMeta?.sizeClass ?? "A4"),
      orientation: (pageMeta?.orientation ?? "H"),
    }]);
    setRectActions(prev => ({ ...prev, [id]: { action: "redact" } }));
    setDraft(null); setConfirmUI(null);
  };
  const cancelDraft=()=>{setDraft(null); setConfirmUI(null);};
  const removeRect=id=>{
    setRects(prev=>prev.filter(r=>r.id!==id));
    setRectActions(prev=>{const n={...prev}; delete n[id]; return n;});
  };

  // Build payload + call backend
  async function runSanitization() {
    if (!pdfFiles.length) { alert("Please add at least one PDF."); return; }
    // Build zones for ALL rects (multi-PDF template)
    const template_zones = [];
    const image_map = {};                        // index-aligned with template_zones
    const form = new FormData();
    pdfFiles.forEach(f => form.append("files", f));

    rects.forEach((r) => {
      // 0-based page number everywhere
      const zone = {
        page: (r.page ?? 0),
        bbox: [r.x, r.y, r.x + r.w, r.y + r.h],
        paper: r.paper ?? "A4",
        orientation: r.orientation ?? "H",
        file_idx: r.fileIdx ?? 0,
      };
      const idxInZones = template_zones.push(zone) - 1;

      // image_map keyed by index in template_zones (use storage key returned by /api/upload-logo)
      const a = rectActions[r.id];
      if (a?.action === "logo") {
        if (!a.logoKey) {
          console.warn("Logo rectangle without uploaded key; skipping placement for this rect.");
        } else {
          image_map[idxInZones] = a.logoKey; // e.g., "logos/WootzWork_logo.png"
        }
      }
    });

    form.append("template_zones", JSON.stringify(template_zones));
    form.append("manual_names", JSON.stringify(eraseList));
    form.append("text_replacements", JSON.stringify(replParsed.map));
    form.append("image_map", JSON.stringify(image_map));
    form.append("threshold", String(threshold));
    form.append("client_name", clientName); // ← NEW: tell API which name to save the template under
    // form.append("template_source_index", String(templateFileIdx ?? activeIndex));

    const res = await fetch(`${API_BASE}/api/sanitize`, { method: "POST", body: form });
    if (!res.ok) { alert("Backend error while sanitizing."); return; }
    const payload = await res.json();

    // Optional: show which template id was created, e.g., acme_v1
    if (payload.template_id) {
      console.log("Saved template:", payload.template_id);
    }

    const results = (payload.outputs || []).map(o => ({
      name: o.name,
      url: o.url, // already public/signed or /api/download/...
    }));
    if (!results.length) { alert("No output files reported by backend."); return; }

    const list = results.map(r => `<li><a href="${r.url}" target="_blank" rel="noreferrer">${r.name}</a></li>`).join("");
    const w = window.open("", "_blank");
    if (w) { w.document.write(`<h3>Sanitized Results</h3><ul>${list}</ul>`); w.document.close(); }
    else { alert("Pop-up blocked. Check console for URLs."); console.log("Sanitized results:", results); }
  }

  // ------- UI -------
  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto max-w-7xl px-4 py-6">
        <header className="mb-4 flex items-center gap-3">
          <button className="inline-flex items-center gap-2 rounded-xl border border-neutral-800 bg-neutral-900 px-3 py-1.5 text-sm hover:bg-neutral-800" onClick={onBack} type="button">
            <IconChevronLeft className="h-4 w-4" /> Back
          </button>
          <h1 className="text-xl font-semibold">Wootz.Sanitize</h1>
          <span className="text-neutral-500 text-sm">/ New client: {clientName}</span>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* LEFT: PDF Viewer */}
          <section className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm text-neutral-300">
                Preview: <span className="text-neutral-100 font-medium">{file ? file.name : "No file"}</span>
              </div>
              <div className="flex items-center gap-2"><IconEye className="text-neutral-400" /><span className="text-xs text-neutral-400">Showing first page</span></div>
            </div>

            <div className="flex gap-2 mb-3 overflow-auto">
              {pdfFiles.map((f,i)=>(
                <button key={`${f.name}-${i}`} className={`text-xs rounded-lg border px-2 py-1 ${i===activeIndex?"border-emerald-600 bg-emerald-600 text-white":"border-neutral-700 bg-neutral-800 text-neutral-300 hover:bg-neutral-750"}`}
                        onClick={()=>setActiveIndex(i)} type="button">
                  {f.name}{templateFileIdx===i ? "  • template" : ""}
                </button>
              ))}
            </div>

            {/* Wrapper MUST be positioning context for overlay; also force position via style to avoid CSS framework issues */}
            <div ref={wrapRef} className="relative w-full overflow-auto" style={{ position: "relative" }}>
              <canvas ref={pdfCanvasRef} className="block " />
              <canvas
                ref={overlayRef}
                // inline styles ensure absolute overlay even if utility classes aren't loaded
                style={{position:"absolute", inset:0, zIndex:10, cursor:"crosshair", touchAction:"none"}}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
              />
              {confirmUI&&draft&&(
                <div className="absolute z-20 rounded-xl border border-neutral-700 bg-neutral-900 text-sm shadow-md"
                     style={{left:confirmUI.left, top:confirmUI.top}}>
                  <div className="flex">
                    <button type="button" className="px-3 py-1.5 hover:bg-neutral-800 border-r border-neutral-800 inline-flex items-center gap-1" onClick={confirmDraft}><IconCheck />Confirm</button>
                    <button type="button" className="px-3 py-1.5 hover:bg-neutral-800 inline-flex items-center gap-1" onClick={cancelDraft}><IconX />Cancel</button>
                  </div>
                </div>
              )}
            </div>

            {renderError && <div className="mt-3 text-sm text-rose-400">{renderError}</div>}
            <p className="mt-3 text-xs text-neutral-500">Tip: click–drag on the preview to draw a rectangle. Release to confirm.</p>
          </section>

          {/* RIGHT: Tools */}
          <section className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-4 space-y-6">
            {/* Stepper */}
            <div className="flex items-center gap-2 text-xs">
              <button type="button" onClick={()=>setStep(1)} className={`px-2 py-1 rounded ${step===1?"bg-neutral-700":"bg-neutral-800 hover:bg-neutral-700"}`}>1. Rectangles</button>
              <span className="text-neutral-500">→</span>
              <button type="button" onClick={()=>setStep(2)} className={`px-2 py-1 rounded ${step===2?"bg-neutral-700":"bg-neutral-800 hover:bg-neutral-700"}`}>2. Text & Run</button>
            </div>

            {step === 1 ? (
              <div>
                <h2 className="text-sm font-semibold text-neutral-200 mb-2">Mark images to remove / place logos</h2>
                <p className="text-xs text-neutral-500 mb-4">
                  Draw rectangles on the left preview. Each rectangle can be redacted or replaced with a logo.
                </p>

                {rects.length===0?(
                  <div className="text-sm text-neutral-400">No rectangles added yet.</div>
                ):(
                  <ul className="space-y-2">
                    {rects.map((r,idx)=>(
                      <li key={r.id} className="rounded-xl border border-neutral-800 bg-neutral-900 p-3 text-sm">
                        <div className="flex items-center justify-between">
                          <div className="font-medium text-neutral-200">Rectangle #{idx+1}</div>
                          <button type="button" className="inline-flex items-center gap-1 rounded-lg border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800"
                                  onClick={()=>removeRect(r.id)} title="Remove rectangle">
                            <IconTrash2 /> Remove
                          </button>
                        </div>

                        <div className="mt-1 text-xs text-neutral-500">
                          x:{r.x}, y:{r.y}, w:{r.w}, h:{r.h} (px)
                        </div>

                        {/* Action selector */}
                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
                          <label className="text-xs text-neutral-300">
                            Action
                            <select
                              className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"
                              value={rectActions[r.id]?.action || "redact"}
                              onChange={(e)=>{
                                const action = e.target.value === "logo" ? "logo" : "redact";
                                setRectActions(prev=>({
                                  ...prev,
                                  [r.id]: { action, logoFile: action==="logo" ? prev[r.id]?.logoFile || null : undefined }
                                }));
                              }}
                            >
                              <option value="redact">Redact this rectangle (no replacement)</option>
                              <option value="logo">Insert logo here at this place</option>
                            </select>
                          </label>

                          {/* Logo upload when needed */}
                          {rectActions[r.id]?.action === "logo" && (
                            <label className="text-xs text-neutral-300">
                              Upload logo
                              <input
                                type="file"
                                accept=".png,.jpg,.jpeg,.webp,.svg"
                                className="mt-1 block w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs file:mr-3 file:rounded file:border-0 file:bg-neutral-700 file:px-3 file:py-1"
                                onChange={async (e)=>{
                                  const f = e.target.files?.[0] || null;
                                  if (!f) {
                                    setRectActions(prev=>({...prev,[r.id]:{ action:"logo", logoFile:null, logoKey:undefined }}));
                                    return;
                                  }
                                  // 1) upload to backend -> returns { key: "logos/<filename>" }
                                  const fd = new FormData();
                                  fd.append("file", f);
                                  let key;
                                  try {
                                    const resp = await fetch(`${API_BASE}/api/upload-logo`, { method: "POST", body: fd });
                                    if (!resp.ok) throw new Error(`Logo upload failed (${resp.status})`);
                                    const js = await resp.json();
                                    key = js.key;
                                  } catch (err) {
                                    alert(`Logo upload failed. ${err?.message || err}`);
                                    return;
                                  }
                                  // 2) store both file (for UI display) and key (for API image_map)
                                  setRectActions(prev=>({...prev,[r.id]:{ action:"logo", logoFile:f, logoKey:key }}));
                                }}
                              />
                              {rectActions[r.id]?.logoFile && (
                                <div className="mt-1 text-neutral-400">{rectActions[r.id].logoFile.name}{rectActions[r.id]?.logoKey ? (<span className="ml-2 text-xs text-emerald-400">({rectActions[r.id].logoKey})</span>) : null}</div>
                              )}
                            </label>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="mt-4 flex justify-end">
                  <button type="button" onClick={()=>setStep(2)} className="rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700">
                    Next: Text & Run →
                  </button>
                </div>
              </div>
            ) : (
              <div>
                {/* Step 2: Text + Run */}
                <h2 className="text-sm font-semibold text-neutral-200 mb-2">Text to be erased</h2>
                <p className="text-xs text-neutral-500 mb-2">Comma or newline separated phrases to redact.</p>
                <textarea value={eraseRaw} onChange={e=>setEraseRaw(e.target.value)} rows={4}
                          placeholder="e.g. ACME LTD, +1-222-333-4444, 221B Baker Street"
                          className="w-full rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm placeholder:text-neutral-500"/>
                <div className="mt-1 text-xs text-neutral-400">Parsed {eraseList.length} item(s): {eraseList.join(", ")||"—"}</div>

                <h2 className="mt-5 text-sm font-semibold text-neutral-200 mb-2">Replacement text map</h2>
                <p className="text-xs text-neutral-500 mb-2">
                  Use JSON like <span className="font-mono">&lbrace;&quot;old&quot;:&quot;new&quot;&rbrace;</span> or one pair per line as <span className="font-mono">old:new</span>.
                </p>
                <textarea value={replRaw} onChange={e=>setReplRaw(e.target.value)} rows={6}
                          placeholder='{"Client A":"Wootz","ACME LTD":"Wootz Industries"}'
                          className="w-full rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm placeholder:text-neutral-500"/>
                {replParsed.errors.length>0 ? (
                  <ul className="mt-2 text-xs text-rose-400 list-disc pl-5">{replParsed.errors.map((er,i)=><li key={i}>{er}</li>)}</ul>
                ) : (
                  <div className="mt-1 text-xs text-neutral-400">Parsed map keys: {Object.keys(replParsed.map).length}</div>
                )}

                <div className="mt-4 flex items-center justify-between gap-3">
                  <div className="text-xs text-neutral-300">
                    Threshold:&nbsp;
                    <input type="number" step="0.01" min="0" max="1" value={threshold}
                           onChange={e=>setThreshold(parseFloat(e.target.value)||0)}
                           className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"/>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" onClick={()=>setStep(1)} className="rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-1.5 text-sm hover:bg-neutral-700">← Back</button>
                    <button type="button" onClick={runSanitization}
                            className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border border-emerald-700 bg-emerald-600 text-white hover:bg-emerald-500">
                      <IconCheck /> Run Sanitization
                    </button>
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

/* ================== Existing Client Page (unchanged placeholder) ================== */
function ExistingClientPage({ pdfFiles, clientName, onBack, onTreatAsNew  }) {
  const [mode, setMode] = useState("use-existing"); // 'use-existing' | 'treat-as-new'
  const [eraseRaw, setEraseRaw] = useState("");
  const eraseList = useMemo(() => parseEraseCSV(eraseRaw), [eraseRaw]);
  const [replRaw, setReplRaw] = useState("");
  const replParsed = useMemo(() => parseReplacementMap(replRaw), [replRaw]);
  const [threshold, setThreshold] = useState(0.9);

  // We’ll ask the parent App to switch to the New Client flow when needed.
  // We'll pass this as a prop shortly.
  const goToNewFlow = typeof onTreatAsNew === "function" ? onTreatAsNew : null;

  async function runSanitizationExisting() {
    if (!pdfFiles.length) { alert("Please add at least one PDF."); return; }
    const form = new FormData();
    pdfFiles.forEach(f => form.append("files", f));
    form.append("manual_names", JSON.stringify(eraseList));
    form.append("text_replacements", JSON.stringify(replParsed.map));
    form.append("threshold", String(threshold));
    form.append("client_name", clientName); // key for v1 template on server

    const res = await fetch(`${API_BASE}/api/sanitize-existing`, { method: "POST", body: form });
    if (!res.ok) { alert("Backend error while sanitizing."); return; }
    const payload = await res.json();

    if (payload.template_id) console.log("Using template:", payload.template_id);

    const results = (payload.outputs || []).map(o => ({
      name: o.name,
      url: o.url,
    }));
    if (!results.length) { alert("No output files reported by backend."); return; }

    const list = results.map(r => `<li><a href="${r.url}" target="_blank" rel="noreferrer">${r.name}</a></li>`).join("");
    const w = window.open("", "_blank");
    if (w) { w.document.write(`<h3>Sanitized Results</h3><ul>${list}</ul>`); w.document.close(); }
    else { alert("Pop-up blocked. Check console for URLs."); console.log("Sanitized results:", results); }
  }

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto max-w-4xl px-4 py-6">
        <header className="mb-4 flex items-center gap-3">
          <button className="inline-flex items-center gap-2 rounded-xl border border-neutral-800 bg-neutral-900 px-3 py-1.5 text-sm hover:bg-neutral-800" onClick={onBack} type="button">
            <IconChevronLeft className="h-4 w-4" /> Back
          </button>
          <h1 className="text-xl font-semibold">Wootz.Sanitize</h1>
          <span className="text-neutral-500 text-sm">/ Existing client: {clientName}</span>
        </header>

        <section className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-6 space-y-5">
          <div className="text-xs text-neutral-500">Uploaded PDFs: {pdfFiles.map((f)=>f.name).join(", ")}</div>

          {/* Mode picker */}
          <div className="grid sm:grid-cols-2 gap-3">
            <label className="text-xs text-neutral-300">
              Choose mode
              <select
                className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"
                value={mode}
                onChange={(e)=>setMode(e.target.value)}
              >
                <option value="use-existing">Run sanitization using the existing template</option>
                <option value="treat-as-new">Run sanitization, considering this existing client as a new client</option>
              </select>
            </label>
          </div>

          {/* If user wants to treat as new, we route to the New Client (rectangles) flow */}
          {mode === "treat-as-new" ? (
            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={()=>{ if (goToNewFlow) goToNewFlow(); }}
                className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border border-amber-600 bg-amber-500 text-black hover:bg-amber-400"
              >
                <IconCheck /> Continue to New Client Flow
              </button>
            </div>
          ) : (
            <>
              {/* Existing behavior (Text & Run) */}
              <div>
                <h2 className="text-sm font-semibold text-neutral-200 mb-2">Text to be erased</h2>
                <p className="text-xs text-neutral-500 mb-2">Comma or newline separated phrases to redact.</p>
                <textarea value={eraseRaw} onChange={e=>setEraseRaw(e.target.value)} rows={4}
                          placeholder="e.g. ACME LTD, +1-222-333-4444, 221B Baker Street"
                          className="w-full rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm placeholder:text-neutral-500"/>
                <div className="mt-1 text-xs text-neutral-400">Parsed {eraseList.length} item(s): {eraseList.join(", ")||"—"}</div>
              </div>

              <div>
                <h2 className="text-sm font-semibold text-neutral-200 mb-2">Replacement text map</h2>
                <p className="text-xs text-neutral-500 mb-2">
                  Use JSON like <span className="font-mono">&lbrace;&quot;old&quot;:&quot;new&quot;&rbrace;</span> or one pair per line as <span className="font-mono">old:new</span>.
                </p>
                <textarea value={replRaw} onChange={e=>setReplRaw(e.target.value)} rows={6}
                          placeholder='{"Client A":"Wootz","ACME LTD":"Wootz Industries"}'
                          className="w-full rounded-xl border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm placeholder:text-neutral-500"/>
               {replParsed.errors.length>0 ? (
                  <ul className="mt-2 text-xs text-rose-400 list-disc pl-5">{replParsed.errors.map((er,i)=><li key={i}>{er}</li>)}</ul>
                ) : (
                  <div className="mt-1 text-xs text-neutral-400">Parsed map keys: {Object.keys(replParsed.map).length}</div>
                )}
              </div>

              <div className="flex items-center justify-between gap-3">
                <div className="text-xs text-neutral-300">
                  Threshold:&nbsp;
                  <input type="number" step="0.01" min="0" max="1" value={threshold}
                         onChange={e=>setThreshold(parseFloat(e.target.value)||0)}
                         className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"/>
                </div>
                <button type="button" onClick={runSanitizationExisting}
                        className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border border-emerald-700 bg-emerald-600 text-white hover:bg-emerald-500">
                  <IconCheck /> Run Sanitization
                </button>
              </div>
            </>
          )}
         </section>
       </div>
     </main>
   );
 }

/* ================== Main (Home + flow switcher) ================== */
// const EXISTING = ["Acme Manufacturing","Barfee Engineering","Client A","Client B"];
// Dynamically loaded from the API
// (fallback empty until fetched)
// Note: SearchableClientDropdown expects an array of strings.

export default function App() {
  const [stage,setStage]=useState("home"); // 'home' | 'newClient' | 'existingClient'
  const [files,setFiles]=useState([]); 
  const [clientChoice,setClientChoice]=useState(""); 
  const [newClientName,setNewClientName]=useState(""); 
  const [submitting,setSubmitting]=useState(false);
  const [existingClients, setExistingClients] = useState([]);
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/clients`);
        const data = await res.json();
        setExistingClients(Array.isArray(data.clients) ? data.clients : []);
      } catch (e) {
        console.warn("Failed to load clients", e);
        setExistingClients([]);
      }
    })();
  }, []);
  const fileInputRef=useRef(null); const onPickFiles=()=>fileInputRef.current?.click();

  const addFiles=(incoming)=>{const pdfs=(incoming||[]).filter(f=>{const ok=isPdf(f); if(!ok) alert(`"${f.name}" is not a PDF and was skipped.`); return ok;});
    const key=f=>`${f.name}::${f.size}`; const existing=new Set(files.map(key)); const merged=[...files]; for(const f of pdfs){if(!existing.has(key(f))) merged.push(f);} setFiles(merged);};
  const onFileChange=e=>{const list=Array.from(e.target.files||[]); addFiles(list); e.currentTarget.value="";};
  const onDrop=e=>{e.preventDefault(); e.stopPropagation(); const list=Array.from(e.dataTransfer.files||[]); addFiles(list);};
  const onDragOver=e=>e.preventDefault();

  const clientValid=useMemo(()=>clientChoice==="new"?newClientName.trim().length>0:clientChoice.trim().length>0,[clientChoice,newClientName]);
  const canSubmit=files.length>0&&clientValid&&!submitting;

  const handleSubmit=e=>{e.preventDefault(); if(!canSubmit) return; setSubmitting(true); try{ if(clientChoice==="new") setStage("newClient"); else setStage("existingClient"); } finally{ setSubmitting(false);}};

  if(stage==="newClient"){
    return (
      <NewClientSetupPage 
        pdfFiles={files} 
        clientName={clientChoice==="new"?newClientName.trim():clientChoice} 
        onBack={()=>setStage("home")} 
      />
    );
  }
  if(stage==="existingClient"){
    return (
      <ExistingClientPage
        pdfFiles={files}
        clientName={clientChoice}
        onBack={()=>setStage("home")}
        onTreatAsNew={()=>setStage("newClient")}  // ← allow child to jump into new-client (rectangles) flow
      />
    );
  }

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto max-w-4xl px-4 py-10">
        <header className="mb-8">
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight">Wootz.Sanitize</h1>
          <p className="mt-1 text-sm text-neutral-400">Add engineering drawings/files and select a client to proceed</p>
        </header>

        <section className="rounded-2xl border border-neutral-800 bg-neutral-900/40 shadow-xl">
          <form onSubmit={handleSubmit} className="p-6 sm:p-8 space-y-6">
            <div className="flex flex-col sm:flex-row gap-3 sm:gap-4 items-stretch sm:items-end">
              <div className="flex-1">
                <label className="block text-sm mb-1 text-neutral-300">Files <span className="text-rose-500" aria-hidden="true">*</span></label>
                <div className="flex items-center gap-3">
                  <button type="button" onClick={onPickFiles} className="inline-flex items-center gap-2 rounded-2xl border border-neutral-700 bg-neutral-800 px-4 py-2 text-sm hover:bg-neutral-750 active:scale-[0.99] transition">
                    <IconUploadCloud /> Upload files
                  </button>
                  <span className="text-xs text-neutral-400">{files.length>0?`${files.length} selected`:"PDFs only"}</span>
                </div>
                <input ref={fileInputRef} type="file" className="hidden" multiple accept="application/pdf,.pdf" onChange={onFileChange} />
              </div>

              <div className="sm:w-80"><SearchableClientDropdown value={clientChoice} onChange={setClientChoice} options={existingClients} /></div>
            </div>

            {clientChoice==="new"&&(
              <div className="sm:w-[28rem]">
                <label className="block text-sm mb-1 text-neutral-300">New client name</label>
                <input type="text" value={newClientName} onChange={e=>setNewClientName(e.target.value)} placeholder="Enter client name"
                       className="w-full rounded-2xl border border-neutral-700 bg-neutral-800 px-4 py-2 text-sm placeholder:text-neutral-500"/>
              </div>
            )}

            <div onDrop={onDrop} onDragOver={onDragOver} className="rounded-2xl border border-dashed border-neutral-700 bg-neutral-900/40 p-6 text-center hover:border-neutral-600">
              <p className="text-sm text-neutral-300">Drag & drop PDF files here</p>
              <p className="mt-1 text-xs text-neutral-500">or use the Upload button above</p>
            </div>

            {files.length>0&&(
              <div className="space-y-2">
                <h3 className="text-sm font-medium text-neutral-300">Selected files</h3>
                <ul className="divide-y divide-neutral-800 rounded-xl border border-neutral-800 overflow-hidden">
                  {files.map((f,idx)=>(
                    <li key={`${f.name}-${idx}`} className="flex items-center justify-between gap-3 bg-neutral-900/30 px-4 py-2">
                      <div className="truncate text-sm"><span className="truncate font-medium text-neutral-200">{f.name}</span>
                        <span className="ml-2 text-neutral-500 text-xs">{(f.size/1024).toFixed(0)} KB</span></div>
                      <button type="button" onClick={()=>setFiles(prev=>prev.filter((_,i)=>i!==idx))}
                              className="inline-flex items-center gap-1 rounded-xl border border-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-800" title="Remove">
                        <IconX /> Remove
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="pt-2">
              <button type="submit" disabled={!canSubmit}
                      className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border ${canSubmit?"border-emerald-700 bg-emerald-600 text-white hover:bg-emerald-500":"border-neutral-800 bg-neutral-900 text-neutral-500 cursor-not-allowed"}`}
                      title={!canSubmit?"Select PDF(s) and choose client first":"Submit"}>
                <IconCheck /> Submit
              </button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
}

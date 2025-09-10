import React, { useEffect, useMemo, useRef, useState } from "react";
import "./home.css";
// Backend base URL (set Vercel env: VITE_API_BASE=https://<your-render>.onrender.com)
 const API_BASE = String(process.env.REACT_APP_API_BASE || "").replace(/\/+$/, "");

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

function absApiUrl(path) {
  const base = (API_BASE || "").replace(/\/+$/,"");
  const p = String(path || "");
  if (!base) return p;                 // dev proxy case
  return p.startsWith("/") ? `${base}${p}` : `${base}/${p}`;
}

async function downloadFile(url, suggestedName) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Download failed (${resp.status})`);
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = suggestedName || "download";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href), 5000);
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
function NewClientSetupPage({ pdfFiles, clientName, onBack, initialSecondary  }) {
  const [activeIndex,setActiveIndex]=useState(0);
  const [rects,setRects]=useState([]);                 // {id,x,y,w,h} normalized
  const [rectActions,setRectActions]=useState({});      // id -> { action: 'redact'|'logo', logoFile?: File }
  const [draft,setDraft]=useState(null);                // {x,y,w,h} in px while drawing
  const [renderError,setRenderError]=useState("");
  const [templateFileIdx, setTemplateFileIdx] = useState(null);
  const [pageIndex, setPageIndex] = useState(0);   // 0-based current page
  const [pageCount, setPageCount] = useState(1);   // total pages (set after pdf load)
  // ---- Secondary flow states (top-level) ----
  const [isSecondaryMode, setIsSecondaryMode] = useState(false);         // global flag
  const [lastLowConf, setLastLowConf] = useState([]);                 // array of { pdf, low_rects }
  const [lastZipUrl, setLastZipUrl] = useState("");                   // download URL provided by API
  const [secondaryFiles, setSecondaryFiles] = useState([]);           // preloaded sanitized PDFs for secondary
  const [secondaryClient, setSecondaryClient] = useState("");         // client to re-use
  function removeSecondaryAt(index) {
   setSecondaryFiles(prev => {
     const next = prev.slice();
     next.splice(index, 1);
     // if emptied, exit secondary mode
     if (next.length === 0) {
       setIsSecondaryMode(false);
     }
     return next;
   });
 }

 
  // Step 2 inputs
  const [step,setStep]=useState(1);                     // 1: rectangles; 2: LLM + run; 3: results
  const [threshold,setThreshold]=useState(0.9);
  const [pageMeta, setPageMeta] = useState(null);
  
  // LLM term generation states
  const [llmTerms, setLlmTerms] = useState([]);         // [{term: "ABC", replacement: ""}, ...]
  const [isGeneratingTerms, setIsGeneratingTerms] = useState(false);
  const [llmContext, setLlmContext] = useState("");
  
  // Processing states
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);
 
  const hasLlmTerms = Array.isArray(llmTerms) && llmTerms.length > 0;
  const canProceed = (rects.length > 0) || hasLlmTerms;

  // Function to generate sensitive terms using LLM
  async function generateSensitiveTerms() {
    if (!currentFiles.length) {
      alert("Please add at least one PDF file first.");
      return;
    }

    setIsGeneratingTerms(true);
    try {
      const form = new FormData();
      currentFiles.forEach(f => form.append("files", f));
      if (llmContext.trim()) {
        form.append("context", llmContext.trim());
      }

      const res = await fetch(`${API_BASE}/api/generate-sensitive-terms`, {
        method: "POST",
        body: form
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.error || "Failed to generate sensitive terms");
      }

      const data = await res.json();
      const terms = data.sensitive_terms || [];
      
      // Convert to UI format: [{term: "ABC", replacement: ""}, ...]
      const formattedTerms = terms.map(term => ({
        term: term,
        replacement: ""
      }));
      
      setLlmTerms(formattedTerms);
      
      if (terms.length === 0) {
        alert("No sensitive terms were detected by the LLM. You may need to add terms manually.");
      } else {
        alert(`Generated ${terms.length} sensitive terms. Review and modify them as needed.`);
      }
    } catch (error) {
      console.error("Error generating sensitive terms:", error);
      alert(`Failed to generate sensitive terms: ${error.message}`);
    } finally {
      setIsGeneratingTerms(false);
    }
  }

  // Function to update LLM term replacement
  function updateLlmTermReplacement(index, replacement) {
    setLlmTerms(prev => prev.map((item, i) => 
      i === index ? { ...item, replacement } : item
    ));
  }

  // Function to remove LLM term
  function removeLlmTerm(index) {
    setLlmTerms(prev => prev.filter((_, i) => i !== index));
  }

  // Function to add new LLM term
  function addNewLlmTerm() {
    setLlmTerms(prev => [...prev, { term: "", replacement: "" }]);
  }

  // Function to update LLM term text
  function updateLlmTermText(index, term) {
    setLlmTerms(prev => prev.map((item, i) => 
      i === index ? { ...item, term } : item
    ));
  }
 
  useEffect(() => {
    if (!initialSecondary) return;
    const { files: secFiles, client, lowConf } = initialSecondary || {};
    if (Array.isArray(secFiles) && secFiles.length > 0) {
      setIsSecondaryMode(true);
      setSecondaryFiles(secFiles);
      setSecondaryClient(client || clientName);
      setLastLowConf(Array.isArray(lowConf) ? lowConf : []);
      setActiveIndex(0);
      setPageIndex(0);
      setStep(1);
    }
    // do not clear here; App clears pendingSecondary one-shot
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSecondary]);
 
  
  async function autoLoadSecondaryFromLowConf(lowConf, client) {
  const targets = (lowConf || []).map(it => {
    const base = (it.pdf || "").split(/[\\/]/).pop() || "";
    return `${base.replace(/\.pdf$/i, "")}_sanitized.pdf`;
  });
 
   const fetched = [];
   for (const name of targets) {
     try {
       const url = `${API_BASE}/api/download/${encodeURIComponent(name)}`;
       const resp = await fetch(url);
       if (!resp.ok) continue;
       const blob = await resp.blob();
       fetched.push(new File([blob], name, { type: "application/pdf" }));
     } catch (e) {
       console.warn("Failed to fetch sanitized file:", name, e);
     }
   }
 
   if (fetched.length) {
     setIsSecondaryMode(true);
     setSecondaryFiles(fetched);
     setSecondaryClient(client);
     setLastLowConf(lowConf);
   
     // 🔑 ensure the UI actually shows the new batch from the start
     setActiveIndex(0);
     setPageIndex(0);
     setStep(1);
   } else {
     alert("Could not auto-load low-confidence PDFs.");
   }

 }

  const currentFiles = isSecondaryMode ? secondaryFiles : pdfFiles;
  const file = currentFiles[activeIndex];
 
  const lowPagesForActive = React.useMemo(() => {
    if (!isSecondaryMode || !lastLowConf?.length) return [];
    const currentName = currentFiles?.[activeIndex]?.name || "";
    if (!currentName) return [];
    const normalizedBase = currentName.replace(/(_sanitized)+\.pdf$/i, ".pdf");
    const hit = lastLowConf.find(it => (it.pdf || "").endsWith(normalizedBase));
    if (!hit) return [];
    return Object.keys(hit.low_rects || {}).map(n => Number(n)).sort((a,b)=>a-b);
  }, [isSecondaryMode, lastLowConf, currentFiles, activeIndex]);


  const pdfCanvasRef=useRef(null), overlayRef=useRef(null), wrapRef=useRef(null);
  const [renderScale, setRenderScale] = useState(0.85); // scale down the PDF view while keeping stable coordinates

  // Render first page to canvas
  useEffect(()=>{let cancelled=false;
    async function render(){setRenderError(""); const canvas=pdfCanvasRef.current; if(!canvas||!file) return;
      try{
        await ensurePdfJs(); const data=await file.arrayBuffer();
        const task=window.pdfjsLib.getDocument({data}); 
        const pdf=await task.promise; 
        const total = pdf.numPages || pdf._pdfInfo?.numPages || 1;
        setPageCount(total);

        // Clamp index
        const pno = Math.min(Math.max(0, pageIndex), total - 1);
        const page = await pdf.getPage(pno + 1);
        // Base viewport (scale 1) for stable PDF-space coordinates
        const baseVp = page.getViewport({ scale: 1 });
        // Fit into container and apply user renderScale, preventing overflow
        const maxW = wrapRef.current ? Math.max(320, wrapRef.current.clientWidth || 800) : 800;
        const fitScale = Math.min(1, maxW / baseVp.width);
        const scale = Math.min(renderScale, fitScale);
        const vp = page.getViewport({ scale });

        // classify page size by your buckets in PDF points
        function classifySize(w, h) {
          const maxDim = Math.max(w, h);
          if (maxDim > 2000) return "A1";
          if (maxDim > 1500) return "A2";
          if (maxDim > 1100) return "A3";
          return "A4";
        }

        const meta = {
          pageNo: pno,
          width: Math.floor(baseVp.width),
          height: Math.floor(baseVp.height),
          sizeClass: classifySize(baseVp.width, baseVp.height),
          orientation: baseVp.width >= baseVp.height ? "H" : "V"
        };
        setPageMeta(meta);

        const ctx=canvas.getContext("2d"); 
        canvas.width=Math.floor(vp.width); 
        canvas.height=Math.floor(vp.height);
        // Keep CSS size equal to pixel size for accurate overlay mapping
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
  },[file, pageIndex, renderScale]);

  // Draw overlay (rects + draft)
  useEffect(()=>{const overlay=overlayRef.current; if(!overlay) return; const ctx=overlay.getContext("2d");
    ctx.clearRect(0,0,overlay.width,overlay.height);
  const sx = (pageMeta?.width && overlay.width) ? (overlay.width / pageMeta.width) : 1;
  const sy = (pageMeta?.height && overlay.height) ? (overlay.height / pageMeta.height) : 1;
  rects
    .filter(r => r.fileIdx === activeIndex && (r.page ?? 0) === (pageMeta?.pageNo ?? 0))
    .forEach(r => {
    const x = Math.round(r.x * sx);
    const y = Math.round(r.y * sy);
    const w = Math.round(r.w * sx);
    const h = Math.round(r.h * sy);
    ctx.strokeStyle = "rgba(255,0,0,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
});
    if(draft){ctx.strokeStyle="rgba(0,200,255,0.9)"; ctx.lineWidth=2; ctx.setLineDash([4,3]); ctx.strokeRect(draft.x,draft.y,draft.w,draft.h); ctx.setLineDash([]);}
  },[rects, draft, activeIndex, pageMeta]);

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
    setConfirmUI({left:draft.x+draft.w + 8, top:draft.y+draft.h + 8});};

  const confirmDraft=()=>{
    if (!overlayRef.current || !draft) return;
    const id = String(Date.now()) + "-" + Math.random().toString(36).slice(2);
    if (templateFileIdx === null) {
      setTemplateFileIdx(activeIndex);
    }
    // Store coordinates in base (scale=1) PDF units so they remain correct across zoom changes
    const sx = (overlayRef.current?.width && pageMeta?.width) ? (pageMeta.width / overlayRef.current.width) : 1;
    const sy = (overlayRef.current?.height && pageMeta?.height) ? (pageMeta.height / overlayRef.current.height) : 1;
    setRects(prev => [...prev, {
      id,
      x: Math.round(draft.x * sx),
      y: Math.round(draft.y * sy),
      w: Math.round(draft.w * sx),
      h: Math.round(draft.h * sy),
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
    if (!currentFiles.length) { alert("Please add at least one PDF."); return; }
    
    setIsProcessing(true);
    setProcessingProgress(0);
    // Build zones for ALL rects (multi-PDF template)
    const template_zones = [];
    const image_map = {};                        // index-aligned with template_zones
    const form = new FormData();
    currentFiles.forEach(f => form.append("files", f));

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


    // Build manual names and replacements from LLM terms (and manual adds via UI)
    const allManualNames = [];
    const allTextReplacements = {};

    // Add LLM/Manual UI terms
    llmTerms.forEach(item => {
      if (item.term && item.term.trim()) {
        const term = item.term.trim();
        if (!allManualNames.includes(term)) {
          allManualNames.push(term);
        }
        if (item.replacement && item.replacement.trim()) {
          allTextReplacements[term] = item.replacement.trim();
        }
      }
    });

    form.append("template_zones", JSON.stringify(template_zones));
    form.append("manual_names", JSON.stringify(allManualNames));
    form.append("text_replacements", JSON.stringify(allTextReplacements));
    form.append("image_map", JSON.stringify(image_map));
    form.append("threshold", String(threshold));
    form.append("client_name", clientName); // ← NEW: tell API which name to save the template under
    form.append("secondary", isSecondaryMode ? "true" : "false");
    // form.append("template_source_index", String(templateFileIdx ?? activeIndex));

    // Request JSON so we can read low_conf and show an on-demand Download ZIP button
    setProcessingProgress(50);
    const res = await fetch(`${API_BASE}/api/sanitize`, {
      method: "POST",
      headers: { "Accept": "application/json" },
      body: form
    });
    setProcessingProgress(100);
    
    if (!res.ok) { 
      setIsProcessing(false);
      setProcessingProgress(0);
      alert("Backend error while sanitizing."); 
      return; 
    }
    const payload = await res.json();
    
    // Persist low_conf / zip_url for secondary and download UX
    const lowConf = Array.isArray(payload.low_conf) ? payload.low_conf : [];
    setLastLowConf(lowConf);
    setLastZipUrl(payload.zip_url ? absApiUrl(payload.zip_url) : "");
    setSecondaryClient(clientName);
    
    // Optionally: surface outputs list somewhere (you previously opened a popup);
    // now you can render a button that uses payload.zip_url to download any time.

     // previously for auto zip download 
    // const res = await fetch(`${API_BASE}/api/sanitize`, { method: "POST", body: form });
    // if (!res.ok) { alert("Backend error while sanitizing."); return; }
    // const contentType = res.headers.get("content-type") || "";

    // if (contentType.includes("application/zip")) {
    //   const blob = await res.blob();
    //   const downloadUrl = window.URL.createObjectURL(blob);
    //   const a = document.createElement("a");
    //   a.href = downloadUrl;
    //   a.download = `${clientName}_sanitized_pdfs.zip`; // You can customize the name if needed
    //   document.body.appendChild(a);
    //   a.click();
    //   a.remove();
    //   window.URL.revokeObjectURL(downloadUrl);
    //   return;
    // }
    // Optional: show which template id was created, e.g., acme_v1 (fallback for older JSON-based response)
    // const payload = await res.json();
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
    
    // Processing completed
    setIsProcessing(false);
    setProcessingProgress(0);
    
    // Switch to results tab
    setStep(3);
  }

  // ------- UI -------
  return (
     <main className="screen">
      <div className="wrap">
        <header className="toolbar">
          <button className="btn" onClick={onBack} type="button">
            <IconChevronLeft className="h-4 w-4" /> Back
          </button>
          <h1 className="text-xl font-semibold" style={{margin:0}}>Wootz.Sanitize</h1>
          <span className="muted" style={{fontSize:12}}>/ New client: {clientName}</span>
        </header>

        <div className="grid-2">
          {/* LEFT: PDF Viewer - 3/4 width */}
          <section className="panel section">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm text-neutral-300">
                Preview: <span className="text-neutral-100 font-medium">{file ? file.name : "No file"}</span>
              </div>
              <div className="flex items-center gap-2"><IconEye className="text-neutral-400" /><span className="text-xs text-neutral-400">Showing PDFs to draw rectangles</span></div>
            </div>

            <div className="flex gap-2 mb-3 overflow-auto">
              {currentFiles
                .map((f, i) => ({ f, i }))       // pair file with original index
                .filter(({ f }) => f.name.toLowerCase().endsWith(".pdf"))
                .slice(0, 8) //top 8 pdfs only
                .map(({ f, i }) => (
                  <button
                    key={`${f.name}-${i}`}
                    className={`text-xs rounded-lg border px-2 py-1 ${
                      i === activeIndex
                        ? "border-emerald-600 bg-emerald-600 text-white"
                        : "border-neutral-700 bg-neutral-800 text-neutral-300 hover:bg-neutral-750"
                    }`}
                    onClick={() => setActiveIndex(i)}
                    type="button"
                  >
                    {f.name}
                    {templateFileIdx === i ? "  • template" : ""}
                  </button>
              ))}
            </div>
            {/* Page navigation */}
            <div className="mb-2 flex justify-center text-sm">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setPageIndex(p => Math.max(0, p - 1))}
                  className="btn"
                  disabled={pageIndex <= 0}
                >
                  ← Prev
                </button>
            
                <span className="text-neutral-400">
                  Page {pageIndex + 1} / {pageCount}
                </span>
            
                <button
                  type="button"
                  onClick={() => setPageIndex(p => Math.min(pageCount - 1, p + 1))}
                  className="btn"
                  disabled={pageIndex >= pageCount - 1}
                >
                  Next →
                </button>
              </div>
            </div>

            
            {/* Quick-jump chips (secondary mode only) */}
            {isSecondaryMode && lastLowConf && lastLowConf.length > 0 && (() => {
              const currentBase = (file?.name || "").replace(/_sanitized\.pdf$/i, ".pdf");
              const entry = lastLowConf.find(it => (it.pdf || "").endsWith(currentBase));
              const pages = entry
                ? Object.entries(entry.low_rects || {})
                    // accept both new shape (array of bboxes) and old (single bbox)
                    .filter(([_, v]) => (Array.isArray(v) ? v.length > 0 : !!v))
                    .map(([k]) => Number(k))
                    .sort((a, b) => a - b)
                : [];

              return pages.length ? (
                <div className="mb-3 flex flex-wrap gap-2 text-xs">
                  <span className="text-neutral-400">Jump to low-confidence pages:</span>
                  {pages.map(p0 => (
                    <button
                      key={p0}
                      type="button"
                      onClick={() => setPageIndex(Number(p0))}
                      className="rounded-full border border-amber-600/60 px-2 py-0.5 hover:bg-amber-900/30"
                      title="Go to page"
                    >
                      {Number(p0) + 1}
                    </button>
                  ))}
                </div>
              ) : null;
            })()}
  
            
            {/* Remove current PDF from batch */}
            {isSecondaryMode && (
              <div className="mb-2">
                <button
                  type="button"
                  onClick={() => {
                    const copy = [...secondaryFiles];
                    copy.splice(activeIndex, 1);
                    if (copy.length === 0) {
                      setIsSecondaryMode(false);
                      setSecondaryFiles([]);
                    } else {
                      setSecondaryFiles(copy);
                      setActiveIndex(i => Math.min(i, copy.length - 1));
                    }
                  }}
                  className="inline-flex items-center gap-1 rounded-md border border-rose-700/70 px-2 py-1 text-xs hover:bg-rose-900/30"
                >
                  <IconTrash2 /> Remove this PDF
                </button>
              </div>
            )}

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

          {/* RIGHT: Tools - 1/4 width */}
         <section className="panel section">
            {/* Stepper */}
            <div className="flex items-center gap-2 text-xs">
              <button type="button" onClick={()=>setStep(1)} className={`px-2 py-1 rounded ${step===1?"bg-neutral-700":"bg-neutral-800 hover:bg-neutral-700"}`}>1. Rectangles</button>
              <span className="text-neutral-500">→</span>
              <button type="button" onClick={()=>setStep(2)} className={`px-2 py-1 rounded ${step===2?"bg-neutral-700":"bg-neutral-800 hover:bg-neutral-700"}`}>2. Text & Run</button>
              {(lastZipUrl || (lastLowConf && lastLowConf.length > 0)) && (
                <>
                  <span className="text-neutral-500">→</span>
                  <button type="button" onClick={()=>setStep(3)} className={`px-2 py-1 rounded ${step===3?"bg-neutral-700":"bg-neutral-800 hover:bg-neutral-700"}`}>3. Results</button>
                </>
              )}
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
                                  const maxKB = 100;
                                  if (f.size / 1024 > maxKB) {
                                    alert(`Please upload a logo smaller than ${maxKB} KB.`);
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
                  <button type="button" onClick={()=>setStep(2)} className="btn">
                    Next: Text & Run →
                  </button>
                </div>
              </div>
            ) : step === 2 ? (
              <div>
                {/* Step 2: Text + Run */}
                
                {/* LLM Term Generation Section */}
                <div className="mb-6 rounded-xl border border-neutral-800 bg-neutral-900/40 p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-semibold text-neutral-200">Generate sensitive terms using LLM</h2>
                    <button
                      type="button"
                      onClick={generateSensitiveTerms}
                      disabled={isGeneratingTerms || !currentFiles.length}
                      className="inline-flex items-center gap-2 rounded-lg border border-blue-600 bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {isGeneratingTerms ? (
                        <>
                          <div className="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent"></div>
                          Generating...
                        </>
                      ) : (
                        <>
                          <IconPlus className="h-3 w-3" />
                          Generate Terms
                        </>
                      )}
                    </button>
                  </div>
                  
                  <div className="mb-3">
                    <label className="text-xs text-neutral-300 mb-1 block">
                      Custom context (optional)
                    </label>
                    <textarea
                      value={llmContext}
                      onChange={e => setLlmContext(e.target.value)}
                      rows={2}
                      placeholder="Provide additional context for the LLM to better identify sensitive terms..."
                      className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs placeholder:text-neutral-500"
                    />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-xs font-medium text-neutral-300">Terms ({llmTerms.length})</h3>
                    </div>

                    <div className="max-h-60 overflow-y-auto space-y-2">
                      {llmTerms.length === 0 ? (
                        <div className="text-xs text-neutral-400">No terms yet. Add terms manually or generate via LLM.</div>
                      ) : (
                        llmTerms.map((item, index) => (
                          <div key={index} className="flex items-center gap-2 rounded-lg border border-neutral-700 bg-neutral-800 p-2">
                            <div className="flex-1 grid grid-cols-2 gap-2">
                              <div>
                                <label className="text-xs text-neutral-400 block mb-1">Term</label>
                                <input
                                  type="text"
                                  value={item.term}
                                  onChange={e => updateLlmTermText(index, e.target.value)}
                                  placeholder="Sensitive term"
                                  className="w-full rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-200 placeholder:text-neutral-500"
                                />
                              </div>
                              <div>
                                <label className="text-xs text-neutral-400 block mb-1">Replace with</label>
                                <input
                                  type="text"
                                  value={item.replacement}
                                  onChange={e => updateLlmTermReplacement(index, e.target.value)}
                                  placeholder="Leave blank to redact"
                                  className="w-full rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-200 placeholder:text-neutral-500"
                                />
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() => removeLlmTerm(index)}
                              className="inline-flex items-center justify-center rounded border border-red-600 bg-red-600 p-1 text-white hover:bg-red-500"
                              title="Remove term"
                            >
                              <IconX className="h-3 w-3" />
                            </button>
                          </div>
                        ))
                      )}
                    </div>

                    <div className="flex justify-end">
                      <button
                        type="button"
                        onClick={addNewLlmTerm}
                        className="inline-flex items-center gap-1 rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-600"
                      >
                        <IconPlus className="h-3 w-3" />
                        Add Term
                      </button>
                    </div>
                  </div>
                </div>


                <div className="mt-4 flex items-center justify-between gap-3">
                  <div className="text-xs text-neutral-300">
                    Threshold:&nbsp;
                    <input type="number" step="0.01" min="0" max="1" value={threshold}
                           onChange={e=>setThreshold(parseFloat(e.target.value)||0)}
                           className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"/>
                  </div>
                  <div className="flex gap-2">
                    <button type="button" onClick={()=>setStep(1)} className="btn">← Back</button>
                    <button
                      type="button"
                      onClick={runSanitization}
                      disabled={!canProceed || isProcessing}
                      className={`btn btn-primary ${isProcessing ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      {isProcessing ? (
                        <>
                          <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent mr-2"></div>
                          Processing...
                        </>
                      ) : (
                        <>
                          <IconCheck /> Run Sanitization
                        </>
                      )}
                    </button>
                    
                    {/* Progress Bar */}
                    {isProcessing && (
                      <div className="mt-4">
                        <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                          <span>Processing PDFs...</span>
                          <span>{processingProgress}%</span>
                        </div>
                        <div className="w-full bg-neutral-700 rounded-full h-2">
                          <div 
                            className="bg-blue-600 h-2 rounded-full transition-all duration-300 ease-out"
                            style={{ width: `${processingProgress}%` }}
                          ></div>
                        </div>
                      </div>
                    )}

                  </div>
                </div>
              </div>
              ) : (
                <div>
                  {/* Step 3: Results */}
                  {(lastZipUrl || (lastLowConf && lastLowConf.length > 0)) ? (
                    <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-3 text-sm">
                      {lastZipUrl && (
                        <button
                          type="button"
                          className="inline-flex items-center rounded-lg border border-neutral-700 px-3 py-1.5 hover:bg-neutral-800 mr-2"
                          onClick={async () => { await downloadFile(lastZipUrl, `${clientName}_sanitized_pdfs.zip`); }}
                        >
                          Download ZIP
                        </button>
                      )}
                      {lastLowConf && lastLowConf.length > 0 && (
                        <button
                          type="button"
                          className="inline-flex items-center rounded-lg border border-neutral-700 px-3 py-1.5 hover:bg-neutral-800"
                          onClick={() => autoLoadSecondaryFromLowConf(lastLowConf, clientName)}
                        >
                          Proceed with secondary process batch
                        </button>
                      )}
                      {lastLowConf && lastLowConf.length > 0 && (
                        <div className="mt-3 text-xs text-neutral-400">
                          <div className="font-medium text-neutral-300 mb-1">Low-confidence summary</div>
                          <ul className="list-disc pl-5 space-y-1">
                            {lastLowConf.map((it, idx) => {
                              const base = (it.pdf || "").split(/[\\/]/).pop() || it.pdf;
                              const pages = Object.keys(it.low_rects || {}).map(n => Number(n) + 1);
                              return (
                                <li key={idx}>
                                  <span className="text-neutral-200">{base}</span>
                                  {pages.length ? ` — pages: ${pages.join(", ")}` : ""}
                                </li>
                              );
                            })}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-xs text-neutral-400">No results to display yet.</div>
                  )}
                </div>
              )}
          </section>
        </div>
      </div>
    </main>
  );
}

/* ================== Existing Client Page (unchanged placeholder) ================== */
function ExistingClientPage({ pdfFiles, clientName, onBack, onTreatAsNew, onProceedSecondary }) {
  const [mode, setMode] = useState("use-existing"); // 'use-existing' | 'treat-as-new'

  // Stepper: 1: Text & Run; 2: Results
  const [step, setStep] = useState(2);

  const [threshold, setThreshold] = useState(0.9);
  const [lastZipUrl, setLastZipUrl] = useState("");
  const [lastLowConf, setLastLowConf] = useState([]);

  // LLM term generation states
  const [llmTerms, setLlmTerms] = useState([]); // [{term, replacement}]
  const [isGeneratingTerms, setIsGeneratingTerms] = useState(false);
  const [llmContext, setLlmContext] = useState("");

  // Processing states
  const [isProcessing, setIsProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);

  // Gate run with canProceed (Existing: LLM terms only)
  const hasLlmTerms = Array.isArray(llmTerms) && llmTerms.length > 0;
  const canProceed = hasLlmTerms;

  // We'll ask the parent App to switch to the New Client flow when needed.
  // We'll pass this as a prop shortly.
  const goToNewFlow = typeof onTreatAsNew === "function" ? onTreatAsNew : null;
  const goToSecondary = typeof onProceedSecondary === "function" ? onProceedSecondary : null;

  // Function to generate sensitive terms using LLM
  async function generateSensitiveTerms() {
    if (!pdfFiles.length) {
      alert("Please add at least one PDF file first.");
      return;
    }

    setIsGeneratingTerms(true);
    try {
      const form = new FormData();
      pdfFiles.forEach(f => form.append("files", f));
      if (llmContext.trim()) {
        form.append("context", llmContext.trim());
      }

      const res = await fetch(`${API_BASE}/api/generate-sensitive-terms`, {
        method: "POST",
        body: form
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.error || "Failed to generate sensitive terms");
      }

      const data = await res.json();
      const terms = data.sensitive_terms || [];
      
      // Convert to UI format: [{term: "ABC", replacement: ""}, ...]
      const formattedTerms = terms.map(term => ({
        term: term,
        replacement: ""
      }));
      
      setLlmTerms(formattedTerms);
      
      if (terms.length === 0) {
        alert("No sensitive terms were detected by the LLM. You may need to add terms manually.");
      } else {
        alert(`Generated ${terms.length} sensitive terms. Review and modify them as needed.`);
      }
    } catch (error) {
      console.error("Error generating sensitive terms:", error);
      alert(`Failed to generate sensitive terms: ${error.message}`);
    } finally {
      setIsGeneratingTerms(false);
    }
  }

  // Function to update LLM term replacement
  function updateLlmTermReplacement(index, replacement) {
    setLlmTerms(prev => prev.map((item, i) => 
      i === index ? { ...item, replacement } : item
    ));
  }

  // Function to remove LLM term
  function removeLlmTerm(index) {
    setLlmTerms(prev => prev.filter((_, i) => i !== index));
  }

  // Function to add new LLM term
  function addNewLlmTerm() {
    setLlmTerms(prev => [...prev, { term: "", replacement: "" }]);
  }

  // Function to update LLM term text
  function updateLlmTermText(index, term) {
    setLlmTerms(prev => prev.map((item, i) => 
      i === index ? { ...item, term } : item
    ));
  }

  async function runSanitizationExisting() {
    if (!pdfFiles.length) { alert("Please add at least one PDF."); return; }
  
    // Build manual names and replacements from LLM terms only (no separate text/replacements UI)
    const allManualNames = [];
    const allTextReplacements = {};

    llmTerms.forEach(item => {
      if (item.term && item.term.trim()) {
        const term = item.term.trim();
        if (!allManualNames.includes(term)) {
          allManualNames.push(term);
        }
        if (item.replacement && item.replacement.trim()) {
          allTextReplacements[term] = item.replacement.trim();
        }
      }
    });

    // Progress start
    setIsProcessing(true);
    setProcessingProgress(20);
  
    const form = new FormData();
    pdfFiles.forEach(f => form.append("files", f));
    form.append("manual_names", JSON.stringify(allManualNames));
    form.append("text_replacements", JSON.stringify(allTextReplacements));
    form.append("threshold", String(threshold));
    form.append("client_name", clientName);
    form.append("secondary", "false");
  
    setProcessingProgress(50);
    const res = await fetch(`${API_BASE}/api/sanitize-existing`, {
      method: "POST",
      headers: { "Accept": "application/json" },
      body: form
    });
  
    setProcessingProgress(100);
  
    if (!res.ok) {
      setIsProcessing(false);
      setProcessingProgress(0);
      alert("Backend error while sanitizing.");
      return;
    }
    const payload = await res.json();
  
    if (payload.template_id) console.log("Using template:", payload.template_id);
  
    const lowConf = Array.isArray(payload.low_conf) ? payload.low_conf : [];
    setLastLowConf(lowConf);
    setLastZipUrl(payload.zip_url ? (absApiUrl(payload.zip_url)) : "");
  
    // Finish & jump to Results tab
    setIsProcessing(false);
    setProcessingProgress(0);
    setStep(3);
  }


  return (
     <main className="screen">
       <div className="wrap">
         <header className="toolbar">
           <button className="btn" onClick={onBack} type="button">
             <IconChevronLeft className="h-4 w-4" /> Back
           </button>
           <h1 className="text-xl font-semibold" style={{ margin: 0 }}>Wootz.Sanitize</h1>
           <span className="muted" style={{ fontSize: 12 }}>/ Existing client: {clientName}</span>
         </header>
   
         <section className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-6 space-y-5">
           <div className="text-xs text-neutral-500">
             Uploaded PDFs: {pdfFiles.map((f) => f.name).join(", ")}
           </div>
   
           {/* Mode picker */}
           <div className="grid sm:grid-cols-2 gap-3">
             <label className="text-xs text-neutral-300">
               Choose mode
               <select
                 className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"
                 value={mode}
                 onChange={(e) => setMode(e.target.value)}
               >
                 <option value="use-existing">Run sanitization using the existing template</option>
                 <option value="treat-as-new">Run sanitization, considering this existing client as a new client</option>
               </select>
             </label>
           </div>
   
           {mode === "treat-as-new" ? (
             <div className="flex items-center justify-end">
               <button
                 type="button"
                 onClick={() => { if (goToNewFlow) goToNewFlow(); }}
                 className="inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border border-amber-600 bg-amber-500 text-black hover:bg-amber-400"
               >
                 <IconCheck /> Continue to New Client Flow
               </button>
             </div>
           ) : (
             <>
               {/* Two-tab stepper */}
               <div className="flex items-center gap-2 text-xs">
                 <button
                   type="button"
                   onClick={() => setStep(2)}
                   className={`px-2 py-1 rounded ${step === 2 ? "bg-neutral-700" : "bg-neutral-800 hover:bg-neutral-700"}`}
                 >
                   1. Text &amp; Run
                 </button>
                 {(lastZipUrl || (lastLowConf && lastLowConf.length > 0)) && (
                   <>
                     <span className="text-neutral-500">→</span>
                     <button
                       type="button"
                       onClick={() => setStep(3)}
                       className={`px-2 py-1 rounded ${step === 3 ? "bg-neutral-700" : "bg-neutral-800 hover:bg-neutral-700"}`}
                     >
                       2. Results
                     </button>
                   </>
                 )}
               </div>
   
               {/* ---- TAB: Text & Run (LLM-only) ---- */}
               {step === 2 && (
                 <>
                   {/* LLM Term Generation */}
                   <div className="mt-2 rounded-xl border border-neutral-800 bg-neutral-900/40 p-4">
                     <div className="flex items-center justify-between mb-3">
                       <h2 className="text-sm font-semibold text-neutral-200">Generate sensitive terms using LLM</h2>
                       <button
                         type="button"
                         onClick={generateSensitiveTerms}
                         disabled={isGeneratingTerms || !pdfFiles.length}
                         className="inline-flex items-center gap-2 rounded-lg border border-blue-600 bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                       >
                         {isGeneratingTerms ? (
                           <>
                             <div className="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent"></div>
                             Generating...
                           </>
                         ) : (
                           <>
                             <IconPlus className="h-3 w-3" />
                             Generate Terms
                           </>
                         )}
                       </button>
                     </div>
   
                     <div className="mb-3">
                       <label className="text-xs text-neutral-300 mb-1 block">Custom context (optional)</label>
                       <textarea
                         value={llmContext}
                         onChange={(e) => setLlmContext(e.target.value)}
                         rows={2}
                         placeholder="Provide additional context for the LLM to better identify sensitive terms..."
                         className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs placeholder:text-neutral-500"
                       />
                     </div>
   
                     <div className="space-y-2">
                       <div className="flex items-center justify-between">
                         <h3 className="text-xs font-medium text-neutral-300">Terms ({llmTerms.length})</h3>
                         <button
                           type="button"
                           onClick={addNewLlmTerm}
                           className="inline-flex items-center gap-1 rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-300 hover:bg-neutral-600"
                         >
                           <IconPlus className="h-3 w-3" />
                           Add Term
                         </button>
                       </div>
   
                       <div className="max-h-60 overflow-y-auto space-y-2">
                         {llmTerms.length === 0 ? (
                           <div className="text-xs text-neutral-400">
                             No terms yet. Add terms manually or generate via LLM.
                           </div>
                         ) : (
                           llmTerms.map((item, index) => (
                             <div
                               key={index}
                               className="flex items-center gap-2 rounded-lg border border-neutral-700 bg-neutral-800 p-2"
                             >
                               <div className="flex-1 grid grid-cols-2 gap-2">
                                 <div>
                                   <label className="text-xs text-neutral-400 block mb-1">Term</label>
                                   <input
                                     type="text"
                                     value={item.term}
                                     onChange={(e) => updateLlmTermText(index, e.target.value)}
                                     placeholder="Sensitive term"
                                     className="w-full rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-200 placeholder:text-neutral-500"
                                   />
                                 </div>
                                 <div>
                                   <label className="text-xs text-neutral-400 block mb-1">Replace with</label>
                                   <input
                                     type="text"
                                     value={item.replacement}
                                     onChange={(e) => updateLlmTermReplacement(index, e.target.value)}
                                     placeholder="Leave blank to redact"
                                     className="w-full rounded border border-neutral-600 bg-neutral-700 px-2 py-1 text-xs text-neutral-200 placeholder:text-neutral-500"
                                   />
                                 </div>
                               </div>
                               <button
                                 type="button"
                                 onClick={() => removeLlmTerm(index)}
                                 className="inline-flex items-center justify-center rounded border border-red-600 bg-red-600 p-1 text-white hover:bg-red-500"
                                 title="Remove term"
                               >
                                 <IconX className="h-3 w-3" />
                               </button>
                             </div>
                           ))
                         )}
                       </div>
                     </div>
                   </div>
   
                   {/* Threshold + Run + Progress */}
                   <div className="flex items-center justify-between gap-3 mt-3">
                     <div className="text-xs text-neutral-300">
                       Threshold:&nbsp;
                       <input
                         type="number"
                         step="0.01"
                         min="0"
                         max="1"
                         value={threshold}
                         onChange={(e) => setThreshold(parseFloat(e.target.value) || 0)}
                         className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1 text-xs"
                       />
                     </div>
   
                     <button
                       type="button"
                       onClick={runSanitizationExisting}
                       disabled={!canProceed || isProcessing}
                       className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm transition border border-emerald-700 bg-emerald-600 text-white hover:bg-emerald-500 ${
                         (!canProceed || isProcessing) ? "opacity-50 cursor-not-allowed" : ""
                       }`}
                     >
                       {isProcessing ? (
                         <>
                           <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent"></div>
                           Processing...
                         </>
                       ) : (
                         <>
                           <IconCheck /> Run Sanitization
                         </>
                       )}
                     </button>
                   </div>
   
                   {isProcessing && (
                     <div className="mt-4">
                       <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
                         <span>Processing PDFs...</span>
                         <span>{processingProgress}%</span>
                       </div>
                       <div className="w-full bg-neutral-700 rounded-full h-2">
                         <div
                           className="bg-blue-600 h-2 rounded-full transition-all duration-300 ease-out"
                           style={{ width: `${processingProgress}%` }}
                         />
                       </div>
                     </div>
                   )}
                 </>
               )}
   
               {/* ---- TAB: Results ---- */}
               {step === 3 && (
                 <div className="mt-3">
                   {(lastZipUrl || (lastLowConf && lastLowConf.length > 0)) ? (
                     <div className="rounded-xl border border-neutral-800 bg-neutral-900 p-3 text-sm">
                       {lastZipUrl && (
                         <button
                           type="button"
                           className="inline-flex items-center rounded-lg border border-neutral-700 px-3 py-1.5 hover:bg-neutral-800 mr-2"
                           onClick={async () => {
                             try {
                               await downloadFile(lastZipUrl, `${clientName}_sanitized_pdfs.zip`);
                             } catch (e) {
                               alert("Could not download the ZIP. See console for details.");
                               console.error(e);
                             }
                           }}
                         >
                           Download ZIP
                         </button>
                       )}
   
                       {(lastLowConf && lastLowConf.length > 0) && (
                         <button
                           type="button"
                           className="inline-flex items-center rounded-lg border border-neutral-700 px-3 py-1.5 hover:bg-neutral-800"
                           onClick={() => {
                             if (goToSecondary) {
                               goToSecondary(lastLowConf, clientName);
                             } else {
                               alert("Secondary hand-off not wired in App()");
                             }
                           }}
                         >
                           Proceed with secondary process batch
                         </button>
                       )}
   
                       {(lastLowConf && lastLowConf.length > 0) && (
                         <div className="mt-3 text-xs text-neutral-400">
                           <div className="font-medium text-neutral-300 mb-1">Low-confidence summary</div>
                           <ul className="list-disc pl-5 space-y-1">
                             {lastLowConf.map((it, idx) => {
                               const base = (it.pdf || "").split(/[\\/]/).pop() || it.pdf;
                               const pages = Object.keys(it.low_rects || {}).map((n) => Number(n) + 1);
                               return (
                                 <li key={idx}>
                                   <span className="text-neutral-200">{base}</span>
                                   {pages.length ? ` — pages: ${pages.join(", ")}` : ""}
                                 </li>
                               );
                             })}
                           </ul>
                         </div>
                       )}
                     </div>
                   ) : (
                     <div className="text-xs text-neutral-400">No results to display yet.</div>
                   )}
                 </div>
               )}
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
  const [pendingSecondary, setPendingSecondary] = useState(null); 
  // shape: { files: File[], client: string, lowConf: any[] }
  useEffect(() => {
    if (stage !== "newClient") return;
    if (!pendingSecondary) return;
    // clear it after mount so it’s not re-used
    const t = setTimeout(() => setPendingSecondary(null), 0);
    return () => clearTimeout(t);
  }, [stage, pendingSecondary]);

 
  async function proceedSecondaryFromExisting(lowConf, client) {
   // download the sanitized PDFs reported in low_conf (same logic as NewClient auto loader)
   const targets = (lowConf || []).map(it => {
     const base = (it.pdf || "").split(/[\\/]/).pop() || "";
     return `${base.replace(/\.pdf$/i, "")}_sanitized.pdf`;
   });
 
   const fetched = [];
   for (const name of targets) {
     try {
       const url = `${API_BASE}/api/download/${encodeURIComponent(name)}`;
       const resp = await fetch(url);
       if (!resp.ok) continue;
       const blob = await resp.blob();
       fetched.push(new File([blob], name, { type: "application/pdf" }));
     } catch (e) {
       console.warn("Failed to fetch sanitized file:", name, e);
     }
   }
 
   if (!fetched.length) {
     alert("Could not auto-load low-confidence PDFs for secondary.");
     return;
   }
   setPendingSecondary({ files: fetched, client, lowConf });
   setStage("newClient");
 }

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
        clientName={clientChoice === "new" ? newClientName.trim() : clientChoice}
        onBack={() => setStage("home")}
        initialSecondary={pendingSecondary}
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
        onProceedSecondary={proceedSecondaryFromExisting}
      />
    );
  }

  return (
    <main className="home">
      <div className="container">
        <header className="brand">
          <h1 className="title">Wootz.Sanitize</h1>
          <p className="subtitle">Upload PDF drawings and select an existing template or create a new one</p>
        </header>

        <section className="card section">
          <form onSubmit={handleSubmit}>
            <div className="row">
              <div>
                <label className="label">Files <span style={{color: 'var(--danger)'}}>*</span></label>
                <div style={{display:'flex', alignItems:'center', gap:12}}>
                  <button type="button" onClick={onPickFiles} className="btn">
                    <IconUploadCloud /> Upload PDFs
                  </button>
                  <span className="muted">{files.length>0?`${files.length} selected`:"PDFs only"}</span>
                </div>
                <input ref={fileInputRef} type="file" className="hidden" multiple accept="application/pdf,.pdf" onChange={onFileChange} />

                {/* Drag & drop removed by request; upload via button only */}

                {files.length>0 && (
                  <div className="filelist">
                    <h3>Selected files</h3>
                    <ul className="files">
                      {files.map((f, idx) => (
                        <li key={`${f.name}-${idx}`} className="file">
                          <div style={{display:'flex', alignItems:'center', minWidth:0}}>
                            <span className="name">{f.name}</span>
                            <span className="meta">{(f.size/1024).toFixed(0)} KB</span>
                          </div>
                          <button type="button" className="btn remove" title="Remove"
                                  onClick={()=>setFiles(prev=>prev.filter((_,i)=>i!==idx))}>
                            <IconX /> Remove
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="template" style={{marginTop: 18}}>
                  <label className="label">Template</label>
                  <select
                    className="select"
                    value={clientChoice}
                    onChange={(e)=> setClientChoice(e.target.value)}
                  >
                    <option value="" disabled>Select a template…</option>
                    <option value="new">Create new template…</option>
                    {existingClients.map(c => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>

                {clientChoice === 'new' && (
                  <div style={{marginTop:14}}>
                    <label className="label">New template name</label>
                    <input
                      type="text"
                      value={newClientName}
                      onChange={e=>setNewClientName(e.target.value)}
                      placeholder="Name this template"
                      className="select"
                    />
                  </div>
                )}
              </div>
            </div>

            <div className="footer">
              <button type="submit" className="btn btn-primary" disabled={!canSubmit} title={!canSubmit?"Select PDF(s) and choose or name a template":"Submit"}>
                <IconCheck /> Continue
              </button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
}



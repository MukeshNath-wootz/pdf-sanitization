// src/PDFSanitizerApp.jsx
import React, { useState, useRef, useEffect } from 'react';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf';
// import the webpack‐friendly entry from the non-legacy build:
// import pdfjsWorker from 'pdfjs-dist/legacy/build/pdf.worker.entry';


// serve the worker you just copied to public/
pdfjsLib.GlobalWorkerOptions.workerSrc = `${process.env.PUBLIC_URL}/pdf.worker.js`;

import JSZip from 'jszip';
export default function PDFSanitizerApp() {
  // — UI state
  const [step, setStep] = useState(1);
  const [files, setFiles] = useState([]);
  const [templateBuffer, setTemplateBuffer] = useState(null);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.0);
  const [redactionZones, setRedactionZones] = useState([]);
  const [manualNames, setManualNames] = useState('');
  const [replacementRules, setReplacementRules] = useState('{"OLD":"NEW"}');
  const [threshold, setThreshold] = useState(0.9);
  const [processed, setProcessed] = useState([]);
  const [imageMapRules, setImageMapRules] = useState('{}');

  // — Canvas refs
  const canvasRef = useRef();
  const overlayRef = useRef();
  const renderTaskRef = useRef(null);

  // Step 1: pick files
  const onFilesChange = e => {
    setFiles(Array.from(e.target.files || []));
  };
  const chooseTemplate = async file => {
    const buf = await file.arrayBuffer();
    setTemplateBuffer(new Uint8Array(buf));
    setStep(2);
  };

  // Step 2: load & render template
  useEffect(() => {
    if (!templateBuffer) return;
    (async () => {
      const loading = pdfjsLib.getDocument({ data: templateBuffer });
      const pdf = await loading.promise;
      setPdfDoc(pdf);
      setNumPages(pdf.numPages);
      setCurrentPage(1);
      renderPage(pdf, 1, scale);
    })();
  }, [templateBuffer]);

  // rerender on page / scale change
  useEffect(() => {
    if (pdfDoc) renderPage(pdfDoc, currentPage, scale);
  }, [pdfDoc, currentPage, scale]);

    // Render a page into the main canvas and size the overlay
  const renderPage = async (pdf, pageNum, scale=1) => {
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale });

    // size the canvas
    const canvas = canvasRef.current;
    canvas.width  = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');

    // cancel any in-flight render
    if (renderTaskRef.current) {
      renderTaskRef.current.cancel();
    }

    // kick off this page’s render
    const task = page.render({ canvasContext: ctx, viewport });
    renderTaskRef.current = task;

    try {
      await task.promise;
    } catch (err) {
      // PDF.js throws a RenderingCancelledException when you cancel
      if (err && err.name !== 'RenderingCancelledException') {
        console.error('Render error:', err);
      }
    } finally {
      renderTaskRef.current = null;
    }

    // now size / redraw your overlay
    const overlay = overlayRef.current;
    overlay.width  = viewport.width;
    overlay.height = viewport.height;
    drawOverlay();
  };


  // drawing logic
  const isDrawingRef  = useRef(false);
  const startPosRef   = useRef({ x:0, y:0 });
  const currentPosRef = useRef({ x:0, y:0 });

  
function handleMouseDown(e) {
  const rect = overlayRef.current.getBoundingClientRect();
  isDrawingRef.current = true;
  startPosRef.current = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  };
}

function handleMouseMove(e) {
  if (!isDrawingRef.current) return;
  const rect = overlayRef.current.getBoundingClientRect();
  currentPosRef.current = {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top
  };
  drawOverlay();    // now uses the up-to-date refs immediately
}

function handleMouseUp() {
  if (!isDrawingRef.current) return;

  // compute a final box
  const { x: x1, y: y1 } = startPosRef.current;
  const { x: x2, y: y2 } = currentPosRef.current;
  const x = Math.min(x1, x2), y = Math.min(y1, y2);
  const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);

  if (w > 5 && h > 5) {
    setRedactionZones(zs => [
      ...zs,
      { x,y,width:w,height:h,page:currentPage }
    ]);
  }

  isDrawingRef.current = false;
}

// and tweak drawOverlay to read from the refs:

function drawOverlay() {
  const ctx = overlayRef.current.getContext('2d');
  ctx.clearRect(0,0,overlayRef.current.width,overlayRef.current.height);

  // redraw existing boxes
  redactionZones
    .filter(z => z.page === currentPage)
    .forEach(z => {
      ctx.fillStyle   = 'rgba(255,0,0,0.3)';
      ctx.strokeStyle = 'red';
      ctx.lineWidth   = 2;
      ctx.fillRect(z.x,z.y,z.width,z.height);
      ctx.strokeRect(z.x,z.y,z.width,z.height);
    });

  // draw the live “rubber-band” box
  if (isDrawingRef.current) {
    const { x: x1, y: y1 } = startPosRef.current;
    const { x: x2, y: y2 } = currentPosRef.current;
    const x = Math.min(x1, x2), y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);

    ctx.fillStyle   = 'rgba(255,0,0,0.3)';
    ctx.strokeStyle = 'red';
    ctx.lineWidth   = 2;
    ctx.fillRect(x,y,w,h);
    ctx.strokeRect(x,y,w,h);
  }
}

  // Step 3: send to backend
  async function runSanitization() {
    const form = new FormData();
    files.forEach(f => form.append('files', f));
    form.append('template_zones', JSON.stringify(
      redactionZones.map(z => ({ page: z.page, bbox: [z.x,z.y,z.x+z.width,z.y+z.height] }))
    ));
    form.append('manual_names', JSON.stringify(manualNames.split(',').map(s=>s.trim())));
    form.append('text_replacements', JSON.stringify(JSON.parse(replacementRules)));
    form.append('image_map', JSON.stringify(JSON.parse(imageMapRules)));
    form.append('threshold', threshold.toString());
    const resp = await fetch('/api/sanitize', { method: 'POST', body: form });
    const payload = await resp.json();
    setProcessed(payload.files.map(f => ({ name: f.originalName, url:`/api${f.path}` })));
    setStep(4);
  }

  // Step 4: download ZIP
  async function downloadZip() {
    const zip = new JSZip();
    for (let f of processed) {
      const blob = await fetch(f.url).then(r=>r.blob());
      zip.file(f.name, blob);
    }
    const content = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(content);
    const a = document.createElement('a');
    a.href = url; a.download = 'sanitized.zip'; a.click();
  }

  // — render our four steps
  return (
    <div className="p-6 space-y-6 max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold">PDF Sanitizer</h1>
      {/* nav */}
      <div className="flex space-x-2">
        {['Upload','Template','Params','Done'].map((lab,i)=>(
          <button
            key={i}
            className={`px-3 py-1 rounded ${step===i+1?'bg-blue-600 text-white':'bg-gray-200'}`}
            onClick={()=>setStep(i+1)}
          >{i+1}. {lab}</button>
        ))}
      </div>

      {step===1 && <>
        <input type="file" accept="application/pdf" multiple onChange={onFilesChange}/>
        {files.map((f,i)=>
          <div key={i} className="flex justify-between">
            <span>{f.name}</span>
            <button className="text-blue-600" onClick={()=>chooseTemplate(f)}>
              Use as Template
            </button>
          </div>
        )}
      </>}

      {step===2 && pdfDoc && <> 
        <div className="flex items-center space-x-2">
          <button onClick={()=> currentPage>1 && setCurrentPage(currentPage-1)}>‹</button>
          <span>Page {currentPage}/{numPages}</span>
          <button onClick={()=> currentPage<numPages && setCurrentPage(currentPage+1)}>›</button>
          <input
            type="range"
            min="0.5"
            max="3"
            step="0.1"
            value={scale}
            onChange={e => setScale(+e.target.value)}
          />
        </div>

        <div style={{ position: 'relative', border: '1px solid #ccc', display: 'inline-block' }}>
          {/* rendered PDF page */}
          <canvas ref={canvasRef} />

          {/* overlay for drawing */}
          <canvas
            ref={overlayRef}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              cursor: 'crosshair',
              zIndex: 10
            }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          />
        </div>

        <button
          className="mt-2 bg-green-600 text-white px-4 py-2 rounded"
          onClick={()=> setStep(3)}
        >
          Confirm Zones ({redactionZones.length})
        </button>
      </>}


      {step===3 && <>
        <label className="block">
          Manual names (comma-sep):
          <input
            className="border p-1 w-full"
            value={manualNames}
            onChange={e=>setManualNames(e.target.value)}
          />
        </label>
        <label className="block">
          Replacement rules (JSON):
          <textarea
            className="border p-1 w-full"
            rows={3}
            value={replacementRules}
            onChange={e=>setReplacementRules(e.target.value)}
          />
        </label>
        <label className="block">
          Image map rules (JSON):
          <textarea
            className="border p-1 w-full"
            rows={3}
            value={imageMapRules}
            onChange={e=>setImageMapRules(e.target.value)}
          />
        </label>
        <label className="block">
          Threshold:
          <input
            type="number" step="0.01" max="1" min="0"
            className="border p-1"
            value={threshold}
            onChange={e=>setThreshold(+e.target.value)}
          />
        </label>
        <button
          className="bg-blue-600 text-white px-4 py-2 rounded"
          onClick={runSanitization}
        >Run Sanitization</button>
      </>}

      {step===4 && <>
        <h2 className="text-xl">Download</h2>
        {processed.map((f,i)=>
          <div key={i}>
            <a href={f.url} target="_blank">{f.name}</a>
          </div>
        )}
        <button
          className="mt-4 bg-purple-600 text-white px-4 py-2 rounded"
          onClick={downloadZip}
        >Download All as ZIP</button>
      </>}
    </div>
  );
}

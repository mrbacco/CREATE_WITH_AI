let availableModels = []
let bacToolDefaultModel = "gemini-2.0-flash"
let compareDefaultModels = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
let runMode = "single"
let isReading = false

function escapeHtml(text){
return String(text)
  .replaceAll("&","&amp;")
  .replaceAll("<","&lt;")
  .replaceAll(">","&gt;")
  .replaceAll('"',"&quot;")
  .replaceAll("'","&#39;")
}

function trimText(text, maxLen){
const value=String(text||"")
if(value.length<=maxLen) return value
return `${value.slice(0,maxLen)}...`
}

function formatDateTime(value){
if(!value) return ""
const dt=new Date(value)
if(Number.isNaN(dt.getTime())) return String(value)
return dt.toLocaleString()
}

function buildWebpageBulletPoints(text, maxItems=14){
const clean=String(text||"")
  .replace(/\r/g, "")
  .replace(/\t/g, " ")
  .replace(/[ ]{2,}/g, " ")
  .trim()
if(!clean) return []

const points=[]
const seen=new Set()

const addPoint=(value)=>{
  const item=String(value||"").replace(/\s+/g, " ").trim()
  if(item.length<35) return
  const key=item.toLowerCase()
  if(seen.has(key)) return
  seen.add(key)
  points.push(item)
}

const paragraphs=clean.split(/\n{2,}/).map((p)=>p.trim()).filter(Boolean)
for(const paragraph of paragraphs){
  if(points.length>=maxItems) break
  const sentence=paragraph.split(/(?<=[.!?])\s+/)[0] || paragraph
  addPoint(sentence)
}

if(points.length<maxItems){
  const sentences=clean.split(/(?<=[.!?])\s+/)
  for(const sentence of sentences){
    if(points.length>=maxItems) break
    addPoint(sentence)
  }
}

return points
}

function renderWebpageExtraction(output, url, extractedText){
if(!output) return
const clean=String(extractedText||"").trim()
if(!clean){
  output.textContent="No extractable text was found on this web page."
  return
}

const points=buildWebpageBulletPoints(clean)
const bulletItems=points.length>0
  ? points.map((item)=>`<li>${escapeHtml(item)}</li>`).join("")
  : `<li>${escapeHtml(clean)}</li>`

output.innerHTML=`
<div class="web-extract-output">
  <div class="web-extract-title">Web Link Extraction</div>
  <div class="web-extract-source">Source: ${escapeHtml(url)}</div>
  <ul class="web-extract-list">${bulletItems}</ul>
</div>
`
}

function renderDocumentExtraction(output, label, extractedText){
if(!output) return
const clean=String(extractedText||"").trim()
if(!clean){
  output.textContent="No extractable content was found."
  return
}

const points=buildWebpageBulletPoints(clean)
const bulletItems=points.length>0
  ? points.map((item)=>`<li>${escapeHtml(item)}</li>`).join("")
  : `<li>${escapeHtml(clean)}</li>`

output.innerHTML=`
<div class="web-extract-output">
  <div class="web-extract-title">Document Extraction</div>
  <div class="web-extract-source">Source: ${escapeHtml(label || "uploaded file")}</div>
  <ul class="web-extract-list">${bulletItems}</ul>
</div>
`
}

function buildDocumentAnalysisBullets(analyzeData){
const bullets=[]
const files=Array.isArray(analyzeData && analyzeData.files) ? analyzeData.files : []

if(files.length===0) return bullets

for(const item of files){
  const name=(item && item.filename) || "document"
  const analysis=(item && item.analysis) || {}
  if(typeof analysis !== "object") continue

  const wc=Number(analysis.word_count || 0)
  const sc=Number(analysis.sentence_count || 0)
  const uc=Number(analysis.unique_words || 0)
  if(wc>0){
    bullets.push(`${name}: ${wc} words across ${sc} sentences, with ${uc} unique terms.`)
  }

  const topWords=Array.isArray(analysis.top_words) ? analysis.top_words : []
  if(topWords.length>0){
    const topTerms=topWords
      .slice(0,5)
      .map((entry)=>`${entry.word} (${entry.count})`)
      .join(", ")
    bullets.push(`${name}: Most frequent terms are ${topTerms}.`)
  }

  const desc=(analysis.analytical_description || analysis.description || "").trim()
  if(desc){
    bullets.push(`${name}: ${desc}`)
  }

  const summary=(analysis.summary || "").trim()
  if(summary){
    const lead=summary.split(/(?<=[.!?])\s+/)[0] || summary
    bullets.push(`${name}: Opening summary insight: ${lead}`)
  }

  const detailed=(analysis.detailed_description || "").trim()
  if(detailed && detailed !== desc){
    bullets.push(`${name}: Detailed analysis: ${detailed}`)
  }

  const visibleText=(analysis.visible_text || "").trim()
  if(visibleText){
    bullets.push(`${name}: Detected text in image: ${visibleText}`)
  }

  const errorText=(analysis.error || "").trim()
  if(errorText){
    bullets.push(`${name}: Analysis error: ${errorText}`)
  }
}

const deduped=[]
const seen=new Set()
for(const b of bullets){
  const key=String(b).toLowerCase()
  if(seen.has(key)) continue
  seen.add(key)
  deduped.push(b)
}
return deduped.slice(0,30)
}

function renderDocumentAnalysisBullets(output, sourceLabel, analyzeData){
if(!output) return
const bullets=buildDocumentAnalysisBullets(analyzeData)
if(bullets.length===0){
  const fallback=(analyzeData && analyzeData.overall_analysis) || JSON.stringify(analyzeData || {}, null, 2)
  renderDocumentExtraction(output, sourceLabel, fallback)
  return
}

const bulletItems=bullets.map((item)=>`<li>${escapeHtml(item)}</li>`).join("")
output.innerHTML=`
<div class="web-extract-output">
  <div class="web-extract-title">Document Analysis</div>
  <div class="web-extract-source">Source: ${escapeHtml(sourceLabel || "uploaded file")}</div>
  <ul class="web-extract-list">${bulletItems}</ul>
</div>
`
}

function renderExtractionLibrary(items){
const list=document.getElementById("extractionList")
if(!list) return
if(!Array.isArray(items) || items.length===0){
list.textContent="No extracted video info available yet."
return
}

list.innerHTML=items.map((item)=>{
const filename=escapeHtml((item && item.filename) || "(unknown file)")
const created=escapeHtml(formatDateTime(item && item.created_at))
const keyframes=Array.isArray(item && item.keyframes) ? item.keyframes : []
const transcriptUrl=escapeHtml((item && item.transcript_url) || "")
const keyframesUrl=escapeHtml((item && item.keyframes_url) || "")

return `
<article class="extraction-item">
<h4 class="extraction-item-title">${filename}</h4>
<div class="extraction-item-meta">${created} | ${keyframes.length} keyframe(s)</div>
<div class="extraction-item-links">
${transcriptUrl ? `<a href="${transcriptUrl}" target="_blank" rel="noopener">Open transcript</a>` : ""}
${keyframesUrl ? `<a href="${keyframesUrl}" target="_blank" rel="noopener">Open keyframes</a>` : ""}
</div>
</article>
`
}).join("")
}

async function loadVideoAnalyses(){
const extractionList=document.getElementById("extractionList")
if(!extractionList) return
extractionList.textContent="Loading extraction info..."
try{
const res=await fetch("/video_analyses?limit=12")
const data=await res.json()
if(!res.ok){
throw new Error(data.error || `Failed with status ${res.status}`)
}
renderExtractionLibrary(data.items || [])
}catch(err){
extractionList.textContent=`Failed to load extraction info: ${err.message}`
}
}

function renderCompareTable(data){
const rows=Object.entries(data).map(([model,response])=>`
<tr>
<td class="model-cell">${escapeHtml(model)}</td>
<td class="response-cell">${escapeHtml(response ?? "")}</td>
</tr>
`).join("")

return `
<table class="results-table">
<thead>
<tr>
<th>Model</th>
<th>Response</th>
</tr>
</thead>
<tbody>${rows}</tbody>
</table>
`
}

function renderModelSelectors(){
const bacToolSelect=document.getElementById("bacToolModel")
const comparePanel=document.getElementById("compareModels")
const modelSuffix=(modelId)=>{
const id=String(modelId||"").toLowerCase()
const tags=[]
if(id.includes(":free")) tags.push("free")
if(id.includes("gpt-oss")||id.includes("llama")||id.includes("gemma")) tags.push("open-source")
if(tags.length===0) return ""
return ` (${tags.join(", ")})`
}

if(availableModels.length===0){
bacToolSelect.innerHTML=""
comparePanel.innerHTML="<span>No models available. Add GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY.</span>"
return
}

bacToolSelect.innerHTML=availableModels.map((model)=>{
const selected=model.id===bacToolDefaultModel ? "selected" : ""
const label=`${model.id}${modelSuffix(model.id)}`
return `<option value="${escapeHtml(model.id)}" ${selected}>${escapeHtml(label)}</option>`
}).join("")

comparePanel.innerHTML=availableModels.map((model)=>{
const checked=compareDefaultModels.includes(model.id) ? "checked" : ""
const badgeClass=model.type==="remote" ? "badge-remote" : "badge-local"
const label=`${model.id}${modelSuffix(model.id)}`
return `
<label class="model-choice">
<input type="checkbox" class="compare-model" value="${escapeHtml(model.id)}" ${checked}>
<span>${escapeHtml(label)}</span>
<span class="model-badge ${badgeClass}">${escapeHtml(model.type)}</span>
</label>
`
}).join("")
renderModeSummary()
}

async function loadModels(){
try{
const res=await fetch("/models")
const data=await res.json()
availableModels=data.models||[]
bacToolDefaultModel=data.bac_tool_default||bacToolDefaultModel
compareDefaultModels=data.compare_default||compareDefaultModels
renderModelSelectors()
}catch{
const bacToolSelect=document.getElementById("bacToolModel")
bacToolSelect.innerHTML=`
<option value="gemini-2.0-flash" selected>gemini-2.0-flash</option>
<option value="gemini-2.0-flash-lite">gemini-2.0-flash-lite</option>
`
document.getElementById("compareModels").innerHTML=`
<label class="model-choice"><input type="checkbox" class="compare-model" value="gemini-2.0-flash" checked><span>gemini-2.0-flash</span><span class="model-badge badge-remote">remote</span></label>
<label class="model-choice"><input type="checkbox" class="compare-model" value="gemini-2.0-flash-lite" checked><span>gemini-2.0-flash-lite</span><span class="model-badge badge-remote">remote</span></label>
`
}
}

function selectedCompareModels(){
return [...document.querySelectorAll(".compare-model:checked")].map(el=>el.value)
}

function useFallbackEnabled(){
const toggle=document.getElementById("useFallback")
return toggle ? toggle.checked : true
}

function currentAttachedFiles(){
const fileInput=document.getElementById("file")
return fileInput && fileInput.files ? [...fileInput.files] : []
}

async function addLink(){
  const urlInput=document.getElementById("urlInput")
  const url = (urlInput && urlInput.value || "").trim()
  await addLinkByUrl(url, urlInput)
}

async function addRailLink(){
  const railInput=document.getElementById("railUrlInput")
  const url=(railInput && railInput.value || "").trim()
  await addLinkByUrl(url, railInput)
}

async function addLinkByUrl(url, sourceInput){
  const status=document.getElementById("uploadStatus")
  const output=document.getElementById("output")
  if(!url){
    status.textContent = "Enter a URL first."
    return
  }
  status.textContent = `Indexing URL: ${url}`
  if(output) output.textContent = "Scraping webpage and extracting content..."
  try {
    const res = await fetch("/index_url", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    })
    const data = await res.json()
    if(!res.ok){
      throw new Error(data.error || `Index URL failed: ${res.status}`)
    }
    const doc=data.document || {}
    const fileId=doc.file_id || ""
    status.textContent = `URL indexed: ${doc.name ? doc.name : url}`

    if(fileId){
      const readRes=await fetch(`/read_file?file_id=${encodeURIComponent(fileId)}`)
      const readData=await readRes.json()
      if(!readRes.ok){
        throw new Error(readData.error || `Read URL content failed: ${readRes.status}`)
      }
      const extractedText=(readData.text || "").trim()
      renderWebpageExtraction(output, url, extractedText)
      status.textContent = `URL extracted: ${doc.name ? doc.name : url}`
    } else if(output){
      output.textContent = "URL was indexed but no readable document ID was returned."
    }

    if(sourceInput) sourceInput.value = ""
    await loadDocuments()
  } catch(err){
    status.textContent = `URL indexing error: ${err.message}`
    if(output) output.textContent = `Web link extraction failed: ${err.message}`
  }
}

function renderFileChips(){
const container=document.getElementById("fileChips")
if(!container) return
const files=currentAttachedFiles()
if(files.length===0){
container.innerHTML=""
return
}
container.innerHTML=files.map((f)=>`<span class="file-chip">${escapeHtml(f.name)}</span>`).join("")
}

function submitPrompt(){
if(runMode==="multiple"){
multipleLlms()
return
}
runBacTool()
}

function applyModeVisibility(){
const panel=document.getElementById("singleModelPanel")
if(panel) panel.classList.toggle("hidden",runMode!=="single")
updateModeButtons()
}

function updateModeButtons(){
const singleBtn=document.getElementById("singleModeBtn")
const multipleBtn=document.getElementById("multipleModeBtn")
if(singleBtn) singleBtn.classList.toggle("active",runMode==="single")
if(multipleBtn) multipleBtn.classList.toggle("active",runMode==="multiple")
}

function renderModeSummary(){
const summary=document.getElementById("modeSummary")
if(!summary) return
if(runMode==="multiple"){
const count=selectedCompareModels().length
summary.textContent=`Multiple models (${count} selected)`
return
}
const model=document.getElementById("bacToolModel")
const value=model ? model.value : ""
summary.textContent=value ? `Single model (${value})` : "Single model"
}

function hideToolsMenu(){
const menu=document.getElementById("toolsMenu")
if(menu) menu.classList.add("hidden")
}

function toggleToolsMenu(){
const menu=document.getElementById("toolsMenu")
if(!menu) return
menu.classList.toggle("hidden")
}

function onSelectAddFiles(){
hideToolsMenu()
const fileInput=document.getElementById("file")
if(fileInput) fileInput.click()
}

async function uploadAndAnalyze(){
  const fileInput=document.getElementById("file")
  if(!fileInput) return

  const doAnalyze=async ()=>{
    if(fileInput.files && fileInput.files.length>0){
      await analyzeFile()
    }
  }

  fileInput.onchange = async ()=>{
    renderFileChips()
    await doAnalyze()
    fileInput.onchange = null
  }

  // If file already selected, just analyze it; else open dialog.
  if(fileInput.files && fileInput.files.length>0){
    await doAnalyze()
  } else {
    fileInput.click()
  }
}

function onSelectSingleMode(){
runMode="single"
hideToolsMenu()
closeMultiModelModal()
applyModeVisibility()
renderModeSummary()
}

function onSelectMultipleMode(){
runMode="multiple"
hideToolsMenu()
applyModeVisibility()
openMultiModelModal()
renderModeSummary()
}

function openMultiModelModal(){
const modal=document.getElementById("multiModelModal")
if(modal) modal.classList.remove("hidden")
}

function closeMultiModelModal(){
const modal=document.getElementById("multiModelModal")
if(modal) modal.classList.add("hidden")
renderModeSummary()
}

let indexedDocuments=[]

function renderDocumentsInfo(data){
const docs=(data&&data.documents)||[]
indexedDocuments = docs
}

async function loadDocuments(){
try{
const res=await fetch("/documents")
if(!res.ok) return
const data=await res.json()
renderDocumentsInfo(data)
}catch{
}
}

async function uploadFilesForPrompt(files){
const status=document.getElementById("uploadStatus")
const uploadedIds=[]
let imageCount=0
if(!files||files.length===0){
return uploadedIds
}

for(let i=0;i<files.length;i++){
const file=files[i]
const formData=new FormData()
formData.append("file",file)
status.textContent=`Uploading and indexing ${file.name} (${i+1}/${files.length})...`

const res=await fetch("/upload",{method:"POST",body:formData})
let data
try{
data=await res.json()
}catch{
throw new Error(`Upload returned ${res.status} ${res.statusText} and not JSON.`)
}
if(!res.ok){
throw new Error(data.error||`Upload failed with status ${res.status}.`)
}
const doc=data.document||{}
if(doc.file_id){
uploadedIds.push(doc.file_id)
}
if(doc.kind==="image"){
imageCount+=1
}
}

status.textContent=`Attached ${files.length} file(s) for this request (${uploadedIds.length} text-indexed${imageCount ? `, ${imageCount} image` : ""}).`
await loadDocuments()
return uploadedIds
}

function setLoadingState(isLoading,output,message){
const buttons=[...document.querySelectorAll("button")]
buttons.forEach((btn)=>{btn.disabled=isLoading})
if(isLoading){
output.textContent=message||"Fetching information from backend..."
}
}

async function runBacTool(){
const msg=document.getElementById("msg").value.trim()
const output=document.getElementById("output")
const model=document.getElementById("bacToolModel").value
const fileInput=document.getElementById("file")
const attachedFiles=currentAttachedFiles()
const outputType=document.getElementById("outputType").value

if(!msg){
output.textContent="Please enter a message first."
return
}

setLoadingState(true,output,"Fetching information from backend...")

try{
const fileIds=await uploadFilesForPrompt(attachedFiles)
const res=await fetch("/bac_tool",{
  method:"POST",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify({
    message:msg,
    model:model,
    use_fallback:useFallbackEnabled(),
    file_ids:fileIds,
    output_type:outputType
  })
})

let data
try{
data=await res.json()
}catch{
throw new Error(`Server returned ${res.status} ${res.statusText} and not JSON.`)
}

if(!res.ok){
throw new Error(data.error||`Request failed with status ${res.status}.`)
}

if(outputType==="video" && data.video_url){
  output.innerHTML = `<div class="video-result"><video controls src="${escapeHtml(data.video_url)}"></video></div>`;
} else if(outputType==="pdf" && data.pdf_url){
  output.innerHTML = `<div class="pdf-result"><a href="${escapeHtml(data.pdf_url)}" target="_blank">Download PDF</a></div>`;
} else if(outputType==="ppt" && data.ppt_url){
  output.innerHTML = `<div class="ppt-result"><a href="${escapeHtml(data.ppt_url)}" target="_blank">Download PPT</a></div>`;
} else {
output.innerHTML=`
<table class="results-table">
<thead>
<tr><th>Model</th><th>Response</th></tr>
</thead>
<tbody>
<tr>
<td class="model-cell">${escapeHtml(model)}</td>
<td class="response-cell">${escapeHtml(data.response ?? "")}
${typeof data.rag_hits==="number" ? `\n\n[Grounded with ${data.rag_hits} document snippet(s)]` : ""}
</td>
</tr>
</tbody>
</table>
`
}
if(fileInput){
fileInput.value=""
renderFileChips()
}
}catch(err){
output.textContent=`BAC_TOOL failed: ${err.message}`
}finally{
setLoadingState(false,output)
}
}

async function multipleLlms(){
const output=document.getElementById("output")
const msg=document.getElementById("msg").value.trim()
const models=selectedCompareModels()
const fileInput=document.getElementById("file")
const attachedFiles=currentAttachedFiles()

if(!msg){
output.textContent="Please enter a message first."
return
}

if(models.length===0){
output.textContent="Please select at least one model for compare."
return
}

setLoadingState(true,output,"Fetching responses from multiple models...")

try{
const fileIds=await uploadFilesForPrompt(attachedFiles)
const res=await fetch("/compare",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
message:msg,
models:models,
use_fallback:useFallbackEnabled(),
file_ids:fileIds
})
})

let data
try{
data=await res.json()
}catch{
throw new Error(`Server returned ${res.status} ${res.statusText} and not JSON.`)
}

if(!res.ok){
throw new Error(data.error||`Request failed with status ${res.status}.`)
}

output.innerHTML=renderCompareTable(data)
if(fileInput){
fileInput.value=""
renderFileChips()
}
}catch(err){
output.textContent=`Compare failed: ${err.message}`
}finally{
setLoadingState(false,output)
}
}

loadModels()
loadDocuments()
loadVideoAnalyses()
applyModeVisibility()
renderFileChips()
renderModeSummary()

const fileInput=document.getElementById("file")
if(fileInput){
fileInput.addEventListener("change", function() {
    renderFileChips();
    if (isReading) {
        readDocument();
        isReading = false;
    }
});
}

const singleModelSelect=document.getElementById("bacToolModel")
if(singleModelSelect){
singleModelSelect.addEventListener("change",renderModeSummary)
}

const comparePanel=document.getElementById("compareModels")
if(comparePanel){
comparePanel.addEventListener("change",renderModeSummary)
}

const refreshExtractionBtn=document.getElementById("refreshExtractionBtn")
if(refreshExtractionBtn){
refreshExtractionBtn.addEventListener("click",loadVideoAnalyses)
}

document.getElementById('readFileBtn').addEventListener('click', function() {
  const documentInput=document.getElementById("documentInput")
  if(documentInput) documentInput.value=""
    isReading = true;
    fileInput.click();
});

document.addEventListener("click",(event)=>{
const toolsMenu=document.getElementById("toolsMenu")
const toolsBtn=document.getElementById("toolsBtn")
if(toolsMenu && toolsBtn){
const inMenu=toolsMenu.contains(event.target)
const inBtn=toolsBtn.contains(event.target)
if(!inMenu && !inBtn) toolsMenu.classList.add("hidden")
}

})

window.closeMultiModelModal=closeMultiModelModal
window.addRailLink=addRailLink

async function readLocalFile(file){
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file)
  })
}

function isImageFile(file){
  if(!file) return false
  const name=(file.name||"").toLowerCase()
  const type=(file.type||"").toLowerCase()
  return type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|tiff)$/.test(name)
}

async function extractOcrHints(files, output){
  const hints=Array(files.length).fill("")
  const imageIndexes=[]
  for(let i=0;i<files.length;i++){
    if(isImageFile(files[i])) imageIndexes.push(i)
  }
  if(imageIndexes.length===0) return hints
  if(typeof Tesseract === "undefined") return hints

  for(let i=0;i<imageIndexes.length;i++){
    const idx=imageIndexes[i]
    output.textContent=`Running OCR on image ${i+1}/${imageIndexes.length}...`
    try{
      const result=await Tesseract.recognize(files[idx], "eng")
      hints[idx]=((result && result.data && result.data.text) || "").trim()
    }catch{
      hints[idx]=""
    }
  }
  return hints
}

async function readDocument(){
  const output=document.getElementById("output")
  const documentInput=document.getElementById("documentInput")
  const fileId=documentInput ? documentInput.value.trim() : ""
  const attachedFiles=currentAttachedFiles()

  setLoadingState(true, output, "Uploading and analyzing...")

  try{
    // Selected files always take priority over indexed document lookup.
    if(attachedFiles.length===0 && fileId){
      const res=await fetch(`/analyze_file?file_id=${encodeURIComponent(fileId)}`)
      const data=await res.json()
      if(!res.ok){
        throw new Error(data.error || `Failed with status ${res.status}`)
      }
      renderDocumentExtraction(output, data.name || fileId, JSON.stringify(data.analysis || data, null, 2))
      return
    }

    if(attachedFiles.length===0){
      output.textContent = "No index ID or files selected. Please select one or more files in the upload selector and try again."
      return
    }

    const ocrHints=await extractOcrHints(attachedFiles, output)

    // Upload selected files first to the index (one-by-one) for persistence.
    for(const file of attachedFiles){
      const formData=new FormData()
      formData.append("file", file)
      const uploadRes = await fetch("/upload", {method:"POST", body: formData})
      const uploadData = await uploadRes.json()
      if(!uploadRes.ok){
        throw new Error(uploadData.error || `Upload failed with status ${uploadRes.status}`)
      }
    }

    // Now run analysis across all selected files in one request.
    const analysisForm = new FormData()
    for(let i=0;i<attachedFiles.length;i++){
      analysisForm.append("file", attachedFiles[i])
      analysisForm.append("ocr_text", ocrHints[i] || "")
    }

    const analyzeRes = await fetch("/analyze_file", {method:"POST", body: analysisForm})
    const analyzeData = await analyzeRes.json()
    if(!analyzeRes.ok){
      throw new Error(analyzeData.error || `Failed with status ${analyzeRes.status}`)
    }

    const sourceLabel=attachedFiles.map((f)=>f.name).join(", ")
    renderDocumentAnalysisBullets(output, sourceLabel, analyzeData)
    await loadVideoAnalyses()

  } catch(err){
    output.textContent = `Read + Analyze failed: ${err.message}`
  } finally {
    setLoadingState(false, output)
  }
}

async function analyzeFile(){
  const output=document.getElementById("output")
  const select=document.getElementById("documentSelect")
  const fileId=select ? select.value : ""
  const files=currentAttachedFiles()

  setLoadingState(true, output, "Analyzing content...")
  try{
    if(fileId){
      const res=await fetch(`/analyze_file?file_id=${encodeURIComponent(fileId)}`)
      const data=await res.json()
      if(!res.ok){
        throw new Error(data.error || `Failed with status ${res.status}`)
      }
      output.textContent=JSON.stringify(data.analysis, null, 2)
      return
    }

    if(files.length>0){
      const formData=new FormData()
      for (let i=0; i<files.length; i++) {
        formData.append("file", files[i])
      }
      const res=await fetch("/analyze_file", {method:"POST", body:formData})
      const data=await res.json()
      if(!res.ok){
        throw new Error(data.error || `Failed with status ${res.status}`)
      }
      if(data.overall_analysis){
        output.textContent=`Overall analysis:\n${data.overall_analysis}`
      } else {
        output.textContent=JSON.stringify(data, null, 2)
      }
      return
    }

    output.textContent="No file selected. Please choose an indexed document or upload a file first."
  } catch(err){
    output.textContent=`Analyze failed: ${err.message}`
  } finally {
    setLoadingState(false, output)
  }
}

const msgInput=document.getElementById("msg")
if(msgInput){
msgInput.addEventListener("keydown",(event)=>{
if(event.key==="Enter" && !event.shiftKey){
event.preventDefault()
submitPrompt()
}
})
}

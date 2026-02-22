let availableModels = []
let bacToolDefaultModel = "gemini-2.0-flash"
let compareDefaultModels = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
let runMode = "single"

function escapeHtml(text){
return String(text)
  .replaceAll("&","&amp;")
  .replaceAll("<","&lt;")
  .replaceAll(">","&gt;")
  .replaceAll('"',"&quot;")
  .replaceAll("'","&#39;")
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

function renderDocumentsInfo(data){
const target=document.getElementById("documentsInfo")
if(!target) return
const docs=(data&&data.documents)||[]
if(docs.length===0){
target.textContent="No indexed documents yet."
return
}
const names=docs.slice(0,5).map((d)=>`${d.name} (${d.chunk_count} chunks)`).join(" | ")
const extra=docs.length>5 ? ` | +${docs.length-5} more` : ""
target.textContent=`Indexed documents: ${docs.length}. ${names}${extra}`
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
applyModeVisibility()
renderFileChips()
renderModeSummary()

const fileInput=document.getElementById("file")
if(fileInput){
fileInput.addEventListener("change",renderFileChips)
}

const singleModelSelect=document.getElementById("bacToolModel")
if(singleModelSelect){
singleModelSelect.addEventListener("change",renderModeSummary)
}

const comparePanel=document.getElementById("compareModels")
if(comparePanel){
comparePanel.addEventListener("change",renderModeSummary)
}

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

const msgInput=document.getElementById("msg")
if(msgInput){
msgInput.addEventListener("keydown",(event)=>{
if(event.key==="Enter" && !event.shiftKey){
event.preventDefault()
submitPrompt()
}
})
}

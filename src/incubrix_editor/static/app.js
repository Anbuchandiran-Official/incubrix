const $ = (selector) => document.querySelector(selector);
const sampleTranscript = [
  {id:"w1",text:"Pricing",start:8.0,end:8.4,speaker:"host",confidence:0.98},
  {id:"w2",text:"is",start:8.4,end:8.55,speaker:"host",confidence:0.99},
  {id:"w3",text:"flexible",start:8.55,end:9.0,speaker:"host",confidence:0.97},
  {id:"w4",text:"Pricing",start:36.0,end:36.4,speaker:"host",confidence:0.96},
  {id:"w5",text:"starts",start:36.4,end:36.75,speaker:"host",confidence:0.98},
  {id:"w6",text:"at",start:36.75,end:36.9,speaker:"host",confidence:0.98},
  {id:"w7",text:"ten",start:36.9,end:37.2,speaker:"host",confidence:0.98}
];
let sessionId = null;
let state = null;
let pendingQuestion = false;
let toastTimer;
let sourceVideoUrl = null;
let selectedVideoFile = null;
let selectedVideoDuration = null;
let renderedPreview = false;

function sampleText(duration=60){return JSON.stringify(sampleTranscript.filter(word=>word.end<=duration),null,2)}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}
function fmtTime(seconds){const s=Math.max(0,Math.floor(seconds));return `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`}
function toast(message){const el=$("#toast");el.textContent=message;el.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove("show"),2600)}
async function request(path,options={}){const headers={...(options.headers||{})};if(!(options.body instanceof FormData))headers["Content-Type"]="application/json";const response=await fetch(path,{...options,headers});const payload=await response.json().catch(()=>({}));if(!response.ok){const error=payload.error||payload.detail?.error;throw new Error(error?.message||payload.detail||`Request failed (${response.status})`)}return payload}

async function checkHealth(){const status=$("#apiStatus");try{await request("/health");status.classList.add("connected");status.querySelector("span").textContent="API connected"}catch{status.classList.remove("connected");status.querySelector("span").textContent="API unavailable"}}
function loadSample(){const duration=selectedVideoDuration||60;$("#durationInput").value=String(duration);$("#transcriptInput").value=sampleText(duration);$("#setupError").textContent=""}
function refreshVideoCaption(){const video=$("#sourceVideo"),caption=$("#videoCaption");if(!state||!video||video.classList.contains("hidden")||renderedPreview){caption.classList.add("hidden");return}const active=(state.annotations||[]).find(a=>a.kind==="caption"&&video.currentTime>=a.start&&video.currentTime<a.end);caption.textContent=active?.text||"";const allowedColors=new Set(["red","blue","green","white","yellow","black"]);caption.style.color=allowedColors.has(active?.style?.color)?active.style.color:"white";caption.classList.toggle("hidden",!active)}
function setEditorEnabled(enabled){$("#instructionInput").disabled=!enabled;$("#sendInstruction").disabled=!enabled;$("#instructionInput").placeholder=enabled?"Tell me what to change…":"Create a session to begin"}

async function createSession(){
  const error=$("#setupError");error.textContent="";
  try{
    const words=JSON.parse($("#transcriptInput").value);
    if(!Array.isArray(words))throw new Error("Transcript JSON must be an array of word objects.");
    if(selectedVideoFile&&!selectedVideoDuration)throw new Error("Wait for the selected video's duration to load, then create the session again.");
    const duration=selectedVideoDuration||Number($("#durationInput").value);
    if(!Number.isFinite(duration)||duration<=0)throw new Error("Enter a video length greater than zero.");
    $("#durationInput").value=String(duration);
    let result;
    if(selectedVideoFile){const form=new FormData();form.append("video",selectedVideoFile);form.append("source_duration_s",String(duration));form.append("words_json",JSON.stringify(words));result=await request("/api/v1/sessions/from-upload",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:form})}
    else result=await request("/api/v1/sessions",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({source_duration_s:duration,words})});
    sessionId=result.session_id;state=result.timeline;pendingQuestion=false;
    renderedPreview=false;$("#renderVideo").disabled=!selectedVideoFile;$("#downloadVideo").classList.add("hidden");const hash=result.source_sha256||"";$("#sourceHashLabel").textContent=hash?`SHA-256 ${hash.slice(0,12)}…`:"Source not uploaded";$("#sourceHashLabel").title=hash;if(sourceVideoUrl){const sourcePreview=$("#sourceVideo");sourcePreview.src=sourceVideoUrl;sourcePreview.load()}
    $("#sessionLabel").textContent=`${state.duration_s}s timeline`;
    $("#sourceDurationLabel").textContent=fmtTime(state.source_duration_s);
    $("#editorSection").classList.remove("hidden");setEditorEnabled(true);renderTimeline();renderHistory();refreshVideoCaption();
    $("#conversation").innerHTML='<div class="date-divider"><span>SESSION STARTED</span></div><div class="assistant-message"><div class="message-mark">✳</div><div class="message-body"><div class="message-author">Editor <time>now</time></div><div class="bubble assistant-bubble">Transcript loaded. Make an edit in plain language. I’ll ask you to choose if I find multiple matches.</div></div></div>';
    $("#pendingHint").textContent="";toast("Editing session ready");$("#instructionInput").focus();
  }catch(err){error.textContent=err.message}
}

function mapSourceTime(sourceTime){let offset=0;for(const segment of state.segments){if(sourceTime>=segment.source_start&&sourceTime<segment.source_end)return offset+(sourceTime-segment.source_start)/segment.speed;offset+=(segment.source_end-segment.source_start)/segment.speed}return null}
function renderTimeline(){
  if(!state)return;
  const total=state.duration_s||1;$("#currentDurationLabel").textContent=`${fmtTime(total)} current`;
  const track=$("#timelineTrack");track.innerHTML="";
  const pieces=[];let last=0;
  for(const segment of state.segments){
    if(segment.source_start>last)pieces.push({cut:true,start:last,end:segment.source_start,duration:segment.source_start-last});
    pieces.push({cut:false,...segment,duration:(segment.source_end-segment.source_start)/segment.speed});last=Math.max(last,segment.source_end)
  }
  if(last<state.source_duration_s)pieces.push({cut:true,start:last,end:state.source_duration_s,duration:state.source_duration_s-last});
  // Display the current order. Cut gaps are represented by small markers at
  // their nearest source boundary, while segment widths reflect edited time.
  for(const p of pieces){if(p.cut)continue;const el=document.createElement("div");el.className="timeline-segment";el.style.width=`${Math.max(2,p.duration/total*100)}%`;el.title=`Original ${fmtTime(p.source_start)}–${fmtTime(p.source_end)} · ${p.speed}×`;el.innerHTML=`<span>${fmtTime(p.source_start)}</span><span class="range-long">${p.speed!==1?`${p.speed}× · `:""}${fmtTime(p.source_end)}</span>`;track.appendChild(el)}
  const cuts=pieces.filter(p=>p.cut);for(const cut of cuts){const marker=document.createElement("div");marker.className="cut-marker";marker.title=`Cut source range ${fmtTime(cut.start)}–${fmtTime(cut.end)}`;marker.style.left=`${Math.min(98,Math.max(0,mapSourceTime(cut.start-0.001)||0)/total*100)}%`;track.appendChild(marker)}
  const ann=$("#annotationList");ann.innerHTML="";
  for(const annotation of state.annotations){const start=mapSourceTime(annotation.start),end=mapSourceTime(Math.max(annotation.start,annotation.end-0.001));const chip=document.createElement("span");chip.className=`annotation-chip ${annotation.kind!=="caption"?"effect":""}`;chip.textContent=`${annotation.kind.toUpperCase()} · ${annotation.text||annotation.word_ids.join(", ")} · ${start===null?"cut":fmtTime(start)}`;ann.appendChild(chip)}
}

function renderHistory(){const list=$("#historyList"),events=state?.history||[];$("#historyCount").textContent=`${events.length} edit${events.length===1?"":"s"}`;if(!events.length){list.innerHTML='<div class="empty-state">Your edits will appear here.</div>';return}list.innerHTML=events.slice(-8).reverse().map(item=>`<div class="history-row"><span class="history-bullet">${item.step}</span><div><p>${escapeHtml(item.label)}</p><small>Restore with “restore edit ${item.step}”</small></div></div>`).join("")}
function addUserMessage(text){const row=document.createElement("div");row.className="user-message";row.innerHTML=`<div class="bubble user-bubble">${escapeHtml(text)}</div>`;$("#conversation").appendChild(row);scrollConversation()}
function addAssistantMessage(message,options=[]){const row=document.createElement("div");row.className="assistant-message";const choices=options.length?`<div class="reply-options">${options.map((option,index)=>`<button data-choice="${index+1}">${index+1}. ${escapeHtml(option)}</button>`).join("")}</div>`:"";row.innerHTML=`<div class="message-mark">✳</div><div class="message-body"><div class="message-author">Editor <time>now</time></div><div class="bubble assistant-bubble">${escapeHtml(message)}</div>${choices}</div>`;$("#conversation").appendChild(row);for(const button of row.querySelectorAll("[data-choice]")){button.addEventListener("click",()=>sendInstruction(`the ${ordinal(Number(button.dataset.choice))} one`))}scrollConversation()}
function ordinal(n){return ["first","second","third","fourth","fifth","sixth"][n-1]||String(n)}
function scrollConversation(){$("#conversation").scrollTop=$("#conversation").scrollHeight}
function readableSummary(response){const s=response.summary||{};if(response.status==="needs_clarification")return response.question||"I need a little more detail.";if(response.status==="rejected")return response.message||"I couldn’t apply that instruction.";if(s.removed_s!==undefined)return `Applied. Removed ${s.removed_s} seconds. The timeline is now ${s.duration_s} seconds.`;if(response.operations?.[0]?.operation==="speed")return `Applied. Speed changed in original source time (${fmtTime(response.operations[0].start_s)}–${fmtTime(response.operations[0].end_s)}). Current length: ${s.duration_s}s.`;if(response.operations?.[0]?.operation==="update_caption")return `Applied. Caption color changed to ${response.operations[0].style?.color||"the selected color"}.`;if(s.annotation_id&&s.start_s!==undefined)return `Applied. Caption added at ${fmtTime(s.start_s)} for ${Math.round(s.end_s-s.start_s)} seconds.`;if(s.annotation_id)return "Applied. The edit is anchored to the selected transcript words.";if(s.reversed)return `Undid the latest edit: ${s.reversed}.`;if(s.restored)return `Redid the edit: ${s.restored}.`;if(s.reason)return s.reason;if(s.duration_s!==undefined)return `Applied. The timeline is now ${s.duration_s} seconds.`;return "Applied to the timeline."}
async function sendInstruction(text){
  if(!sessionId||!text.trim())return;
  const input=$("#instructionInput");const typed=text===input.value;addUserMessage(text);if(typed)input.value="";input.style.height="auto";$("#sendInstruction").disabled=true;
  try{
    const response=await request(`/api/v1/sessions/${sessionId}/instructions`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({text})});
    state=response.timeline;const status=response.status||"applied";
    if(status==="applied"&&renderedPreview&&sourceVideoUrl){const video=$("#sourceVideo");renderedPreview=false;video.src=sourceVideoUrl;video.load()}
    pendingQuestion=status==="needs_clarification";$("#pendingHint").textContent=pendingQuestion?"Choose an option above":"";renderTimeline();renderHistory();refreshVideoCaption();
    addAssistantMessage(`${readableSummary(response)} Time policy: ${response.time_policy||"original source time for edit ranges; reorder destinations use current timeline time"}.`,response.options||[]);
  }catch(err){addAssistantMessage(err.message);toast(err.message)}finally{$("#sendInstruction").disabled=false;input.focus()}
}

$("#loadSample").addEventListener("click",loadSample);
$("#videoInput").addEventListener("change",event=>{const file=event.target.files?.[0];if(sessionId){sessionId=null;state=null;pendingQuestion=false;$("#editorSection").classList.add("hidden");$("#sessionLabel").textContent="Not started";$("#renderVideo").disabled=true;$("#downloadVideo").classList.add("hidden");setEditorEnabled(false);$("#pendingHint").textContent="";toast("Video changed. Create a new editing session to edit this source.")}selectedVideoFile=file||null;selectedVideoDuration=null;if(!file)return;if(sourceVideoUrl)URL.revokeObjectURL(sourceVideoUrl);sourceVideoUrl=URL.createObjectURL(file);const video=$("#sourceVideo");renderedPreview=false;video.onloadedmetadata=()=>{if(Number.isFinite(video.duration)&&video.duration>0){selectedVideoDuration=video.duration;$("#durationInput").value=String(video.duration);$("#sourceDurationLabel").textContent=fmtTime(video.duration);if($("#transcriptInput").value.trim()===sampleText().trim())$("#transcriptInput").value=sampleText(video.duration)}refreshVideoCaption()};video.onerror=()=>{selectedVideoDuration=null;video.classList.add("hidden");$("#stagePlaceholder").classList.remove("hidden");toast("This video format could not be previewed by the browser")};video.src=sourceVideoUrl;video.classList.remove("hidden");$("#stagePlaceholder").classList.add("hidden");video.load();toast(`Loading ${file.name}`)});
$("#sourceVideo").addEventListener("timeupdate",refreshVideoCaption);
$("#createSession").addEventListener("click",createSession);
$("#sendInstruction").addEventListener("click",()=>sendInstruction($("#instructionInput").value));
$("#instructionInput").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();sendInstruction(event.currentTarget.value)}});
$("#instructionInput").addEventListener("input",event=>{event.currentTarget.style.height="auto";event.currentTarget.style.height=`${Math.min(event.currentTarget.scrollHeight,100)}px`});
$("#refreshTimeline").addEventListener("click",async()=>{if(!sessionId)return;try{const result=await request(`/api/v1/sessions/${sessionId}`);state=result.timeline;renderTimeline();renderHistory();refreshVideoCaption();toast("Timeline refreshed")}catch(err){toast(err.message)}});
$("#renderVideo").addEventListener("click",async()=>{if(!sessionId||!selectedVideoFile)return;const button=$("#renderVideo");button.disabled=true;button.textContent="Rendering…";try{const result=await request(`/api/v1/sessions/${sessionId}/render`,{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()}});const video=$("#sourceVideo");renderedPreview=true;video.src=result.url;video.load();const link=$("#downloadVideo");link.href=result.url;link.classList.remove("hidden");toast(`Render verified: ${result.render.duration_s.toFixed(3)}s; audio/video within one frame`)}catch(err){toast(err.message)}finally{button.disabled=false;button.textContent="Render edit"}});
document.querySelectorAll(".suggestion-chip").forEach(button=>button.addEventListener("click",()=>sendInstruction(button.textContent)));
loadSample();checkHealth();setInterval(checkHealth,15000);

// Run with: node tests/test_background_sync.js
const source=require('fs').readFileSync('static/app.js','utf8');
const detail=require('fs').readFileSync('static/task-detail.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
let taskDraft=null,requests=[],timers=[];
const pendingTaskDrafts=new Map(),document={activeElement:null},$=()=>null;
const clearTimeout=timer=>{if(timer)timer.cancelled=true};
const setTimeout=(fn,delay)=>{let timer={fn,delay};timers.push(timer);return timer};
const refresh=()=>Promise.resolve(),updateActivity=()=>Promise.resolve(),toast=()=>{},showTaskConflict=()=>{};
function setTaskSaveStatus(draft,message){draft.status.textContent=message}
function api(path,payload,background){return new Promise((resolve,reject)=>requests.push({payload,background,resolve,reject}))}
eval(source.slice(source.indexOf('function updateSyncIndicator('),source.indexOf('function showPendingTaskDrafts('))+'\n'+detail.slice(detail.indexOf('async function flushTaskDraft('),detail.indexOf('function showTaskConflict('))+`
  async function run(){
    let draft={id:1,ownerId:1,title:{value:'Tarea'},editor:{innerHTML:'Nuevo'},status:{},savedTitle:'Tarea',savedDescription:'Anterior',titleVersion:0,descriptionVersion:0};
    taskDraft=draft;queueTaskDraft(draft);taskDraft=null;
    assert(pendingTaskDrafts.get(1)===draft&&requests.length===1&&requests[0].background,'encola y envía sin esperar');
    let first=draft.pendingWork;requests[0].reject(Error('sin conexión'));await first;
    assert(pendingTaskDrafts.has(1)&&timers[0].delay===5000,'conserva y reintenta el fallo de red');
    timers[0].fn();assert(requests.length===2,'reintenta al vencer la espera');
    let second=draft.pendingWork;requests[1].resolve({description_version:1});await second;
    assert(!pendingTaskDrafts.has(1)&&draft.savedDescription==='Nuevo','retira solo después de confirmar');
    let conflict={id:2,ownerId:1,title:{value:'Otra'},editor:{innerHTML:'Cambio'},status:{},savedTitle:'Otra',savedDescription:'Anterior',titleVersion:0,descriptionVersion:0};
    taskDraft=conflict;queueTaskDraft(conflict);taskDraft=null;
    let failed=conflict.pendingWork,error=Error('conflicto');error.status=409;error.conflict=true;requests[2].reject(error);await failed;
    assert(pendingTaskDrafts.has(2)&&conflict.conflict&&!conflict.retryTimer,'conserva conflicto sin sobrescribir ni reintentar');
    let reopened={id:3,ownerId:1,title:{value:'Tercera'},editor:{innerHTML:'Primer cambio'},status:{},savedTitle:'Tercera',savedDescription:'Original',titleVersion:0,descriptionVersion:0};
    taskDraft=reopened;queueTaskDraft(reopened);taskDraft=null;
    let inFlight=reopened.pendingWork;pendingTaskDrafts.delete(3);taskDraft=reopened;reopened.reopenedDuringSave=true;
    reopened.editor.innerHTML='Edición posterior';requests[3].resolve({description_version:1});await inFlight;
    assert(requests.length===4&&reopened.savedDescription==='Primer cambio'&&reopened.editor.innerHTML==='Edición posterior','reabrir durante guardado conserva nuevas ediciones sin enviarlas');
    let repeated={id:4,ownerId:1,title:{value:'Cuarta'},editor:{innerHTML:'Primero'},status:{},savedTitle:'Cuarta',savedDescription:'Original',titleVersion:0,descriptionVersion:0};
    taskDraft=repeated;queueTaskDraft(repeated);taskDraft=null;
    let complete=repeated.pendingWork;pendingTaskDrafts.delete(4);taskDraft=repeated;repeated.reopenedDuringSave=true;repeated.editor.innerHTML='Segundo';
    queueTaskDraft(repeated);taskDraft=null;requests[4].resolve({description_version:1});
    for(let i=0;i<5&&requests.length<6;i++)await Promise.resolve();
    assert(requests.length===6&&requests[5].payload.description==='Segundo'&&requests[5].payload.description_version===1,'al cerrar otra vez conserva la edición posterior');
    requests[5].resolve({description_version:2});await complete;
    assert(!pendingTaskDrafts.has(4)&&repeated.savedDescription==='Segundo','confirma ambas ediciones en orden');
  }
  run().catch(error=>{if(typeof process!=='undefined'){console.error(error);process.exitCode=1}else{print(error);quit(1)}});
`);

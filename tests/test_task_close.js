// Run with: node tests/test_task_close.js
const source=require('fs').readFileSync('static/app.js','utf8');
let taskDraft=null,automationDraft=null,automationProject=null,descriptionCodeCleanup=null,taskId=4,data={},active=1,route='/board/a/task/b',queued=[],boardChanges=0;
let overlay;
const container={querySelector(selector){return selector==='.overlay'?overlay:overlay?.querySelector(selector)},innerHTML:''};
function makeOverlay(){return {dataset:{},parentNode:container,querySelector(s){return s==='#taskForm'?{}:null},remove(){overlay=null}}}
const $=()=>container,document={querySelector:()=>null},window={matchMedia:()=>({matches:true})};
function closeStatusMenu(){} function clearTimeout(){} function queueTaskDraft(draft){queued.push(draft)}
function isTaskPath(path){return path.includes('/task/')} function setBoardUrl(){boardChanges++;route='/board/a'}
const location={get pathname(){return route}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('async function closeModal('),source.indexOf('function descriptionText('))+`
  overlay=makeOverlay();taskDraft={title:{value:'  '},status:{}};
  closeModal(false,false).then(result=>{
    assert(!result&&overlay&&boardChanges===0&&!queued.length,'no cierra con título vacío');
    taskDraft.title.value='Título';return closeModal(false,false);
  }).then(result=>{
    assert(result&&!overlay&&taskId===null&&route==='/board/a'&&queued.length===1,'cierra y encola sin esperar');
    overlay=makeOverlay();taskId=4;route='/board/other';return closeModal(false,false,true);
  }).then(()=>assert(route==='/board/other'&&boardChanges===1,'respeta la navegación del historial'))
    .catch(error=>{if(typeof process!=='undefined'){console.error(error);process.exitCode=1}else{print(error);quit(1)}});
`);

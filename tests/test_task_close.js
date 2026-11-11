// Run with: node tests/test_task_close.js
const source=require('fs').readFileSync('static/app.js','utf8');
let taskDraft=null,automationDraft=null,automationProject=null,taskId=4,data={},active=1,route='/board/a/task/b',saved=true,boardChanges=0;
let overlay;
const container={querySelector(){return overlay},innerHTML:''};
function makeOverlay(){return {dataset:{},parentNode:container,querySelector(s){return s==='#taskForm'?{}:null},remove(){overlay=null}}}
const $=()=>container,document={querySelector:()=>null},window={matchMedia:()=>({matches:true})};
function closeStatusMenu(){} function clearTimeout(){} function flushTaskDraft(){return Promise.resolve(saved)}
function isTaskPath(path){return path.includes('/task/')} function setBoardUrl(){boardChanges++;route='/board/a'}
const location={get pathname(){return route}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('async function closeModal('),source.indexOf('function descriptionText('))+`
  overlay=makeOverlay();taskDraft={};saved=false;
  closeModal(false,false).then(result=>{
    assert(!result&&overlay&&boardChanges===0,'no cierra si falla el guardado');
    saved=true;return closeModal(false,false);
  }).then(result=>{
    assert(result&&!overlay&&taskId===null&&route==='/board/a','cierra y vuelve al tablero');
    overlay=makeOverlay();taskId=4;route='/board/other';return closeModal(false,false,true);
  }).then(()=>assert(route==='/board/other'&&boardChanges===1,'respeta la navegación del historial'));
`);

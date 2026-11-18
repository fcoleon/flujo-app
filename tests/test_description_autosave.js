// Run with: node tests/test_description_autosave.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
let taskDraft=null,timers=[],calls=[];
const setTimeout=(fn,delay)=>{let timer={fn,delay,cancelled:false};timers.push(timer);return timer};
const clearTimeout=timer=>{if(timer)timer.cancelled=true};
function element(value){return {value,innerHTML:value,handlers:{},addEventListener(name,fn){this.handlers[name]=fn}}}
let title=element('Título'),editor=element('<p>Original</p>'),status={textContent:''},form={elements:{title},addEventListener(){}};
const $=selector=>({'#taskForm':form,'#descriptionEditor':editor,'#taskSaveStatus':status})[selector];
function setupDescriptionCodeCopy(){} function flushTaskDraft(...args){calls.push(args)}
eval(source.slice(source.indexOf('function setupTaskAutosave('),source.indexOf('async function flushTaskDraft('))+`
  setupTaskAutosave(1,{title:'Título',title_version:0,description_version:0},false);
  editor.innerHTML='<p>Nuevo</p>';editor.handlers.input();
  assert(timers.length===0,'escribir no programa guardado');
  editor.handlers.blur();assert(timers[0].delay===2500,'perder foco espera 2,5 segundos');
  editor.handlers.focus();assert(timers[0].cancelled,'volver a editar cancela guardado');
  editor.innerHTML='<p>Original</p>';editor.handlers.input();editor.handlers.blur();
  assert(timers.length===1,'sin cambios no programa guardado');
  editor.innerHTML='<p>Nuevo</p>';editor.handlers.blur();timers[1].fn();
  assert(calls[0][0]===true&&calls[0][1]===false,'solo guarda descripción si no está enfocada');
  title.handlers.input();timers[2].fn();assert(calls[1][0]===false,'el título no dispara guardado de descripción');
`);

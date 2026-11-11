// Run with: node tests/test_drag_scroll.js
const source=require('fs').readFileSync('static/app.js','utf8');
const listeners={}, document={addEventListener:(name,fn)=>listeners[name]=fn,querySelectorAll:()=>[]};
let timer, cleared=false;
function setInterval(fn){timer=fn;return 1}
function clearInterval(){cleared=true;timer=null}
const cards={scrollTop:100,getBoundingClientRect:()=>({top:100,bottom:300}),querySelectorAll:()=>[]};
const column={classList:{contains:()=>false,add(){}},querySelector:()=>cards,contains:target=>target===column};
const placeholder={parentElement:cards,nextElementSibling:null};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function stopDragScroll()'),source.indexOf('function endTaskDrag()'))+`
  let dragged=1, dragPlaceholder=placeholder, dragSource={}, dragOffsetY=0;
  let dragScrollTimer=null, dragScrollColumn=null, dragScrollY=0;
  const event=y=>({currentTarget:column,clientY:y,preventDefault(){},dataTransfer:{}});
  dragOver(event(290)); timer(); assert(cards.scrollTop===108,'scroll down');
  dragOver(event(110)); timer(); assert(cards.scrollTop===100,'scroll up');
  dragOver(event(200)); assert(cleared&&!timer,'stop outside edge');
  dragOver(event(290)); listeners.dragover({target:{}}); assert(!timer,'stop outside column');
`);

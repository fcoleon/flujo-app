// Run with: node tests/test_card_open.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
let reduced=false,opened=[],animations=[];
const window={matchMedia:()=>({matches:reduced})};
function openTask(id){opened.push(id)}
eval(source.slice(source.indexOf('function openTaskFromCard('),source.indexOf('function card(t,'))+`
  let event={currentTarget:{animate:(frames,options)=>animations.push({frames,options})}};
  openTaskFromCard(event,3);
  assert(opened[0]===3&&animations.length===1,'anima la tarjeta y abre la tarea');
  reduced=true;openTaskFromCard(event,4);
  assert(opened[1]===4&&animations.length===1,'movimiento reducido abre sin animar');
`);

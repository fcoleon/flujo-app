// Run with: node tests/test_task_tab_animation.js
const source=require('fs').readFileSync('static/app.js','utf8');
let taskTab='detalle',reduced=false,animations=[],buttons={},panels={};
for(let name of ['detalle','subtareas','comentarios','actividad']){
  buttons[name]={id:'task-tab-'+name,selected:null,setAttribute(key,value){if(key==='aria-selected')this.selected=value},closest(){return dialog}};
  panels[name]={hidden:false};
}
const dialog={getBoundingClientRect(){let active=Object.keys(buttons).find(n=>buttons[n].selected==='true');return {height:({detalle:600,subtareas:450,comentarios:300,actividad:400})[active]||600}},animate(frames,options){let animation={frames,options,cancelled:false,cancel(){this.cancelled=true}};animations.push(animation);return animation}};
const $=selector=>selector.startsWith('#task-tab-')?buttons[selector.slice(10)]:selector.startsWith('#task-panel-')?panels[selector.slice(12)]:Object.values(buttons).find(button=>button.selected==='true')||null;
const window={matchMedia:()=>({matches:reduced})};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('const taskTabAnimations='),source.indexOf('function taskTabKeydown('))+`
  setTaskTab('detalle');assert(!animations.length&&!panels.detalle.hidden,'no anima la apertura inicial');
  setTaskTab('comentarios');assert(animations.length===1&&animations[0].frames[0].height==='600px'&&animations[0].frames[1].height==='300px','anima entre alturas');
  setTaskTab('actividad');assert(animations[0].cancelled&&animations.length===2&&!panels.actividad.hidden,'cancela la animación anterior');
  reduced=true;setTaskTab('subtareas');assert(animations[1].cancelled&&animations.length===2&&!panels.subtareas.hidden,'respeta movimiento reducido');
`);

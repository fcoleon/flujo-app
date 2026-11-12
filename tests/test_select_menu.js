// Run with: node tests/test_select_menu.js
const source=require('fs').readFileSync('static/app.js','utf8');
let statusMenu=null,statusMenuTrigger=null,menu,focused='',appended='',listeners={};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function normalizeSearch(s){return String(s).toLowerCase().trim()}
function clearTimeout(){} function setTimeout(){return 1}
const controls=['Uno','Tres'].map(text=>({textContent:text,focus(){focused=text}}));
const document={activeElement:controls[0],body:{appendChild(){appended='body'}},createElement(){return menu={style:{},offsetWidth:180,offsetHeight:130,handlers:{},setAttribute(){},addEventListener(name,fn){this.handlers[name]=fn},querySelector(){return controls[0]},querySelectorAll(){return controls}}},addEventListener(name,fn){(listeners[name]||=[]).push(fn)}};
const window={innerWidth:900,innerHeight:700,addEventListener(){}};
class Event{constructor(type,options){this.type=type;this.bubbles=options.bubbles}}
function closeStatusMenu(){statusMenu=null;statusMenuTrigger=null} function statusMenuOutside(){} function statusMenuScroll(){} function statusMenuResize(){}
const options=[{value:'1',textContent:'Uno',selected:true},{value:'2',textContent:'Dos',disabled:true},{value:'3',textContent:'Tres & más'}];
let events=[],value='1',disabled=false;
const anchor={getBoundingClientRect:()=>({left:20,top:10,bottom:30,width:180})},dialog={appendChild(){appended='dialog'}};
const select={options,multiple:false,size:0,labels:[],closest(s){return s==='select'?this:s==='.quick-control'?anchor:s==='dialog[open]'?dialog:null},matches:()=>disabled,getAttribute:()=> 'Tipo de tarea',setAttribute(){},dispatchEvent:e=>events.push(e.type),get value(){return value},set value(v){value=v;options.forEach(o=>o.selected=o.value===v)}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function openSelectMenu('),source.indexOf('function taskQuickControls('))+`
  let prevented=false;listeners.pointerdown[0]({target:select,button:0,preventDefault(){prevented=true},stopPropagation(){}});
  assert(prevented&&appended==='dialog'&&menu.className.includes('select-menu'),'abre dentro del diálogo');
  assert(menu.innerHTML.includes('Tres &amp; más')&&menu.innerHTML.includes('disabled')&&menu.innerHTML.includes('aria-checked="true"'),'opciones escapadas y estados');
  menu.handlers.keydown({key:'End',preventDefault(){}});assert(focused==='Tres','navegación por teclado');
  menu.handlers.click({stopPropagation(){},target:{closest:()=>({disabled:false,dataset:{index:'2'}})}});
  assert(select.value==='3'&&events.join(',')==='input,change','actualiza el select y emite eventos');
  openSelectMenu(select);menu.handlers.click({stopPropagation(){},target:{closest:()=>({disabled:false,dataset:{index:'2'}})}});
  assert(events.length===2,'no emite cambios si ya estaba seleccionado');
  disabled=true;assert(dropdownSelect(select)===null,'respeta selectores deshabilitados');
`);

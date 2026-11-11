// Run with: node tests/test_priority_menu.js
const source=require('fs').readFileSync('static/app.js','utf8');
let data={users:[],tasks:[{id:7,title:'Prueba',priority:'Normal'}]},statusMenu=null,statusMenuTrigger=null,menu,sent,saved;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const document={body:{appendChild(){}},activeElement:null,createElement(){return menu={style:{},offsetWidth:200,offsetHeight:150,handlers:{},setAttribute(){},addEventListener(name,fn){this.handlers[name]=fn},querySelector(){return {focus(){}}}}},addEventListener(){}};
const window={innerWidth:1000,innerHeight:800,addEventListener(){}};
function closeStatusMenu(){statusMenu=null;statusMenuTrigger=null} function statusMenuOutside(){} function statusMenuScroll(){} function statusMenuResize(){}
function api(path,body){sent={path,body};return new Promise(()=>{})} function saveTaskField(id,field,control){saved={id,field,value:control.value};return new Promise(()=>{})}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function taskPropertyVisual('),source.indexOf('function renderTaskProperties('))+
     source.slice(source.indexOf('function priorityButton('),source.indexOf('function taskQuickControls('))+`
  let task=data.tasks[0],button=priorityButton(task,true),readonly=priorityButton(task,false);
  assert(button.includes("'priority'")&&button.includes('aria-haspopup="menu"')&&!readonly.includes('<button'),'control editable y solo lectura');
  let trigger={setAttribute(){},getBoundingClientRect:()=>({left:10,top:10,bottom:30})};
  openStatusMenu({stopPropagation(){},currentTarget:trigger},7,false,'priority');
  assert(menu.innerHTML.includes('priority low')&&menu.innerHTML.includes('priority high')&&menu.innerHTML.includes('data-value="Normal"'),'opciones con colores existentes');
  menu.handlers.click({stopPropagation(){},target:{closest:()=>({dataset:{value:'Alta'}})}});
  assert(sent.path==='task/update'&&sent.body.priority==='Alta','guarda desde tarjeta o lista');
  let control={value:'Normal'},detail={setAttribute(){},getBoundingClientRect:()=>({left:10,top:10,bottom:30}),closest:()=>({querySelector:()=>control})};
  openStatusMenu({stopPropagation(){},currentTarget:detail},7,true,'priority');
  menu.handlers.click({stopPropagation(){},target:{closest:()=>({dataset:{value:'Baja'}})}});
  assert(saved.id===7&&saved.field==='priority'&&saved.value==='Baja','guarda desde detalle');
`);

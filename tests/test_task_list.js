// Run with: node tests/test_task_list.js
const source=require('fs').readFileSync('static/app.js','utf8');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let active=1,filter='',dragSaving=false,board={innerHTML:'',scrollTop:0,getBoundingClientRect:()=>({top:0,bottom:1000}),querySelectorAll:()=>[]},sent,reduced=true,scheduled;
let data={user:{id:1},projects:[{id:1,role:'editor',columns:[{id:10,name:'Primera'},{id:20,name:'Segunda'}]}],tasks:[
  {id:1,title:'Tercera',can_edit:true,placements:[{project_id:1,column_id:20,position:0}]},
  {id:2,title:'Segunda tarea',can_edit:true,placements:[{project_id:1,column_id:10,position:1}]},
  {id:3,title:'Primera tarea',can_edit:true,placements:[{project_id:1,column_id:10,position:0}]}
]};
const $=()=>board,normalizeSearch=s=>s.toLowerCase().trim(),matchesSearch=(t,q)=>t.title.toLowerCase().includes(q);
const document={addEventListener(){}};
const window={matchMedia:()=>({matches:reduced})};
function clearInterval(){} function clearTimeout(){scheduled=null} function setTimeout(fn){scheduled=fn;return 1}
function moveOrder(ids,id,next){ids=ids.filter(x=>x!==id);let i=ids.indexOf(next);ids.splice(i<0?ids.length:i,0,id);return ids}
function api(path,body){sent={path,body};return new Promise(()=>{})}
function taskQuickControls(t,editable){return {statusControl:editable?'EDITAR_ESTADO':'VER_ESTADO',personControl:'RESPONSABLE',dateControl:'FECHA',priorityControl:'PRIORIDAD'}}
function projectFilters(){return {}} function projectFilterDate(){return '2026-01-01'} function projectFilterDates(){return {}} function matchesProjectFilters(){return true} function projectFilterCount(){return 0}
const assert=(v,message)=>{if(!v)throw Error(message)};
eval(source.slice(source.indexOf('const listCollapsedState='),source.indexOf('function renderBoard()'))+`
  data.tasks[0].comment_count=2;data.tasks.push({id:4,parent_id:1,placements:[]});
  renderTaskList();let html=board.innerHTML;
  assert(html.indexOf('Primera <span class="count">')<html.indexOf('Primera tarea')&&html.indexOf('Primera tarea')<html.indexOf('Segunda tarea')&&html.indexOf('Segunda tarea')<html.indexOf('Segunda <span class="count">')&&html.indexOf('Segunda <span class="count">')<html.indexOf('Tercera'),'agrupa y ordena');
  assert(html.includes('newTask(10)')&&html.includes('moveListTask(2,10,-1,this)')&&html.includes('startListDrag(event,2)')&&html.includes('EDITAR_ESTADO'),'acciones de editor');
  assert(html.includes('width="14" height="14"')&&html.includes('Abrir 1 subtarea de Tercera')&&html.includes('Abrir 2 comentarios de Tercera'),'indicadores con iconos accesibles');
  listCollapsedState.set('flujo:list-collapsed:1:1',[10]);renderTaskList();html=board.innerHTML;
  assert(html.includes('aria-expanded="false"')&&html.includes('data-id="2" hidden'),'sección contraída');
  listCollapsedState.set('flujo:list-collapsed:1:1',[]);
  filter='segunda tarea';renderTaskList();html=board.innerHTML;
  assert(html.includes('Segunda tarea')&&!html.includes('Primera tarea')&&html.includes('Sin resultados'),'filtra sin ocultar columnas');
  assert(html.includes('moveListTask(2,10,-1,this)" aria-label="Subir Segunda tarea" '),'orden respecto a todas las tareas');
  filter='';data.projects[0].role='viewer';renderTaskList();html=board.innerHTML;
  assert(!html.includes('newTask(')&&!html.includes('moveListTask(')&&!html.includes('startListDrag(')&&!html.includes('EDITAR_ESTADO')&&html.includes('VER_ESTADO'),'permisos de lector');
  let sourceRow={hidden:true},targetRow={hidden:false,dataset:{id:'1'},getBoundingClientRect:()=>({top:200,height:40}),matches:()=>true};
  let placeholder={parentElement:null,nextElementSibling:null,replaceWith(row){this.replaced=row}};
  let section={dataset:{column:'20'},querySelectorAll:()=>[targetRow],querySelector:()=>({getAttribute:()=> 'true'}),insertBefore(node,next){node.parentElement=this;node.nextElementSibling=next}};
  listDrag={id:2,project:1,source:sourceRow,placeholder};
  dropListTask({currentTarget:section,clientX:20,clientY:180,preventDefault(){},dataTransfer:{}});
  assert(sent.path==='task/move'&&sent.body.column_id===20&&sent.body.order.join(',')==='2,1','mueve antes de la tarea de destino');
  dragSaving=false;targetRow.hidden=true;placeholder={parentElement:null,nextElementSibling:null,replaceWith(row){this.replaced=row}};
  section.querySelector=()=>({getAttribute:()=> 'false'});listDrag={id:2,project:1,source:sourceRow,placeholder};
  dropListTask({currentTarget:section,clientX:20,clientY:180,preventDefault(){},dataTransfer:{}});
  assert(sent.body.order.join(',')==='1,2'&&sourceRow.hidden,'agrega al final de sección contraída');
  let classes=new Set(),row={hidden:false,offsetHeight:60,classList:{add(...names){names.forEach(x=>classes.add(x))},remove(...names){names.forEach(x=>classes.delete(x))},toggle(name,on){if(on)classes.add(name);else classes.delete(name)}}},toggle={dataset:{name:'Primera'},setAttribute(k,v){this[k]=v},closest:()=>({querySelectorAll:()=>[row]})};
  toggleListColumn(toggle,10);assert(row.hidden&&toggle['aria-expanded']==='false','contrae sin animación si se prefiere movimiento reducido');
  toggleListColumn(toggle,10);assert(!row.hidden&&toggle['aria-expanded']==='true','expande sección');
  reduced=false;toggleListColumn(toggle,10);assert(!row.hidden&&classes.has('list-folded')&&row.inert,'contrae el espacio antes de ocultar');
  scheduled();assert(row.hidden&&!classes.has('list-folded'),'oculta al finalizar la transición');
  toggleListColumn(toggle,10);assert(!row.hidden&&!classes.has('list-folded')&&classes.has('list-folding'),'expande el espacio');
  scheduled();assert(!row.hidden&&!classes.has('list-folding'),'completa la expansión');
`);

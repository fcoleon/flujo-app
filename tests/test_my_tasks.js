// Run with: node tests/test_my_tasks.js
const source=require('fs').readFileSync('static/app.js','utf8');
let data={user:{id:1},projects:[{id:10,name:'A'},{id:20,name:'B'}],tasks:[
  {id:1,title:'Compartida',assignee_id:1,due_date:'2026-01-03',priority:'Normal',can_edit:true,has_projects:true,placements:[{project_id:10},{project_id:20}],inherited_project_ids:[],done:false},
  {id:2,title:'Subtarea',assignee_id:1,due_date:'2026-01-01',priority:'Alta',can_edit:false,has_projects:true,placements:[],inherited_project_ids:[10],parent_id:1,done:false},
  {id:3,title:'Sin proyecto',assignee_id:1,due_date:null,priority:'Baja',can_edit:true,has_projects:false,placements:[],done:false},
  {id:4,title:'Finalizada',assignee_id:1,due_date:'2026-01-02',priority:'Alta',can_edit:true,has_projects:true,placements:[{project_id:20}],done:true},
  {id:5,title:'De otra persona',assignee_id:2,can_edit:true,has_projects:false,placements:[],done:false}
]};
let results={innerHTML:''},count={textContent:''};
const $=s=>s==='#myTaskResults'?results:s==='#myTaskCount'?count:null;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function taskStatus(t){return {is_done:t.done}} function matchesSearch(t,q){return t.title.toLowerCase().includes(q.toLowerCase())}
function taskQuickControls(t,editable){return {statusControl:editable?'EDITAR':'LECTURA',dateControl:'FECHA',priorityControl:'PRIORIDAD'}}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function taskContextProjects('),source.indexOf('function taskPath('))+
     source.slice(source.indexOf("let mineBoard='all'"),source.indexOf('const renderBoardWithoutAutomations='))+`
  assert(myTasks().map(t=>t.id).join(',')==='2,1,3','solo asignadas y en curso, ordenadas por fecha');
  mineBoard='20';assert(myTasks().map(t=>t.id).join(',')==='1','tarea compartida aparece una sola vez');
  mineBoard='10';assert(myTasks().map(t=>t.id).join(',')==='2,1','incluye subtareas heredadas');
  mineBoard='none';assert(myTasks().map(t=>t.id).join(',')==='3','filtra tareas sin proyecto');
  mineBoard='all';mineIncludeDone=true;assert(myTasks().map(t=>t.id).join(',')==='2,4,1,3','incluye finalizadas');
  mineSearch='subtarea';renderMyTaskResults();assert(count.textContent==='1 tarea'&&results.innerHTML.includes('LECTURA')&&!results.innerHTML.includes('EDITAR'),'búsqueda y permisos');
`);

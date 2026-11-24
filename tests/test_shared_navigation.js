// Run with: node tests/test_shared_navigation.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
let app={innerHTML:''},active=null,boardView='board',filter='';
let data={user:{id:1,admin:true,name:'Ana'},workspace_name:'Equipo',tasks:[],projects:[{id:4,name:'Proyecto',description:'',role:'admin',members:[]}]};
const $=selector=>selector==='#app'?app:null,esc=value=>String(value),avatar=()=>'',icon=()=>'';
function saveBoardScroll(){} function updateSyncIndicator(){} function renderBoard(){} function renderStats(){}
function sidebarCollapsed(){return false} function taskStatus(){return {is_done:false}}
eval(source.slice(source.indexOf('function renderShell(){'),source.indexOf('const mobileSidebarMedia='))+`
  for(let [view,title] of [[null,'Inicio'],['mine','Mis tareas'],[0,'Sin proyecto'],[4,'Proyecto']]){
    active=view;renderShell();
    assert(app.innerHTML.includes('id="mainSidebar"')&&app.innerHTML.includes('class="topbar"'),'navegación compartida: '+title);
    assert(app.innerHTML.includes('aria-label="Mis tareas"')&&app.innerHTML.includes('aria-label="Sin proyecto"'),'opciones compartidas: '+title);
    assert(app.innerHTML.includes('<strong>'+title+'</strong>'),'título de vista: '+title);
    if(view==='mine')assert(!app.innerHTML.includes('class="welcome"'),'mis tareas no crea contenido de inicio');
  }
`);

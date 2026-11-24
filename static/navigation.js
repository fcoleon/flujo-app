let currentRoute=location.pathname+location.search,openingDeepLink=false;
function boardPath(id){return id==='mine'?'/my-tasks':id===0?'/board/no-project':data.projects.find(p=>p.id===id)?.public_id?'/board/'+data.projects.find(p=>p.id===id).public_id:'/'}
function setBoardUrl(id,replace=false){let url=boardPath(id);if(location.pathname+location.search!==url)history[replace?'replaceState':'pushState'](null,'',url);currentRoute=url}
function isTaskPath(path){return /^\/t\//.test(path)||/^\/board\/[^/]+\/task\//.test(path)}
function selectProject(id){active=id;filter='';boardView='board';statsType='';setBoardUrl(id);render()}
function setBoardView(view){closeStatusMenu();endListDrag();boardView=view;render()}
function taskContextProjects(task){
  if(!task.has_projects)return [];
  return [...new Set([...task.placements.map(x=>x.project_id),...(task.inherited_project_ids||[])])].filter(id=>data.projects.some(p=>p.id===id));
}
function taskPath(task){let projects=taskContextProjects(task),project=projects.includes(active)?active:(projects[0]??0);return boardPath(project)+'/task/'+task.public_id}
function taskLink(task){return new URL(taskPath(task),location.origin).href}
function setTaskUrl(task,replace=false){let url=taskPath(task),sameTask=isTaskPath(location.pathname)&&location.pathname.toLowerCase().endsWith('/'+task.public_id.toLowerCase());if(location.pathname+location.search!==url)history[replace||sameTask?'replaceState':'pushState'](active==='mine'?{returnTo:'mine'}:null,'',url);currentRoute=url}
async function copyTaskLink(id){
  let task=data.tasks.find(t=>t.id===id);if(!task?.public_id)return toast('El enlace no está disponible');let url=taskLink(task);
  try{if(!navigator.clipboard?.writeText)throw Error('Portapapeles no disponible');await navigator.clipboard.writeText(url)}
  catch(e){let field=document.createElement('textarea');field.value=url;field.style.cssText='position:fixed;left:-10000px;top:0';document.body.appendChild(field);field.select();let ok=false;try{ok=document.execCommand('copy')}catch{}field.remove();if(!ok)return toast('No se pudo copiar el enlace')}
  toast('Enlace copiado');
}
async function openDeepLink(){
  if(location.pathname==='/'){setBoardUrl(active,true);render();return}
  if(location.pathname==='/my-tasks'){active='mine';boardView='board';render();currentRoute=location.pathname+location.search;return}
  let taskMatch=location.pathname.match(/^\/board\/([^/]+)\/task\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
  let legacyMatch=location.pathname.match(/^\/t\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
  if(taskMatch||legacyMatch){
    let task=data.tasks.find(t=>t.public_id?.toLowerCase()===(taskMatch?taskMatch[2]:legacyMatch[1]).toLowerCase());
    if(!task){setBoardUrl(active,true);toast('Tarea no encontrada o sin acceso');return}
    let projects=taskContextProjects(task),raw=taskMatch?taskMatch[1]:new URLSearchParams(location.search).get('project');
    let requested=raw==='no-project'?0:raw&&/^\d+$/.test(raw)?Number(raw):data.projects.find(p=>p.public_id?.toLowerCase()===raw?.toLowerCase())?.id;
    let valid=requested===0?!task.has_projects:requested!=null&&projects.includes(requested);
    active=history.state?.returnTo==='mine'?'mine':valid?requested:(projects[0]??0);boardView='board';filter='';render();
    openingDeepLink=true;try{await openTask(task.id)}finally{openingDeepLink=false}
    if(raw&&!valid)toast('El tablero del enlace no está disponible; se abrió la tarea en una vista accesible');
    currentRoute=location.pathname+location.search;return;
  }
  let boardMatch=location.pathname.match(/^\/board\/([^/]+)$/i);
  if(boardMatch){
    let value=boardMatch[1].toLowerCase(),project=data.projects.find(p=>p.public_id?.toLowerCase()===value);
    if(value==='no-project'||project){active=project?.id??0;boardView='board';filter='';statsType='';render()}
    else{setBoardUrl(active,true);toast('Tablero no encontrado o sin acceso')}
    currentRoute=location.pathname+location.search;return;
  }
}
window.addEventListener('popstate',async()=>{if(!data)return;if(!await closeModal(false,false,true)){history.pushState(null,'',currentRoute);return}taskId=null;await openDeepLink();if(location.pathname==='/'){setBoardUrl(active,true);render()}currentRoute=location.pathname+location.search});


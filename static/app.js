const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data=null, active=null, taskId=null, dragged=null, filter='', typeDraft=null, statusDrag=null, dragPlaceholder=null, dragSource=null, dragOffsetY=0, dragSaving=false;
let dragScrollTimer=null, dragScrollColumn=null, dragScrollY=0;
let taskTab='detalle', boardView='board', statsMonth='', statsType='', statsRequest=0, taskDraft=null;
const cardMotion=new WeakMap();
let columnDrag=null, columnGhost=null, columnSource=null, columnSaving=false;
const columnMotion=new WeakMap();
let dataRequest=0, mutationEpoch=0, pendingMutations=0;
async function api(path,body){if(body){mutationEpoch++;pendingMutations++}try{let r=await fetch('/api/'+path,{method:body?'POST':'GET',cache:'no-store',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(r.status===429)throw Error('Demasiados intentos. Espera un momento y vuelve a intentarlo.');let d=await r.json();if(!r.ok){if((d.blocked||d.password_change_required||r.status===401)&&taskDraft){clearTimeout(taskDraft.timer);taskDraft.denied=true;taskDraft.status.textContent='Acceso perdido; conserva y copia tu borrador';}else if(d.blocked)showBlocked();else if(d.password_change_required)showPasswordChange();let error=Error(d.error||'Ocurrió un error');error.status=r.status;error.conflict=d.conflict;throw error}if(d.disabled_automations?.length)showDisabledAutomations(d.disabled_automations);return d}finally{if(body){pendingMutations--;mutationEpoch++}}}
let disabledAutomationQueue=[],disabledAutomationDialog=null;
function disabledAutomationItems(){return disabledAutomationQueue.map(rule=>`<li><strong>${esc(rule.name)}</strong> · ${esc(rule.project_name)}: ${esc(rule.reason)}</li>`).join('')}
function showDisabledAutomations(rules){
  disabledAutomationQueue.push(...rules);
  if(disabledAutomationDialog){disabledAutomationDialog.querySelector('ul').innerHTML=disabledAutomationItems();return}
  let dialog=disabledAutomationDialog=document.createElement('dialog');dialog.className='dialog automation-notice';dialog.setAttribute('aria-labelledby','automationNoticeTitle');
  dialog.innerHTML=`<h2 id="automationNoticeTitle">Automatizaciones desactivadas</h2><p>El cambio se guardó, pero estas reglas dejaron de funcionar y se desactivaron. Revísalas y corrígelas antes de volver a activarlas.</p><ul></ul><div class="dialog-actions"><button type="button" class="primary">Entendido</button></div>`;
  dialog.querySelector('ul').innerHTML=disabledAutomationItems();document.body.appendChild(dialog);dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{dialog.remove();disabledAutomationDialog=null;disabledAutomationQueue=[]});dialog.showModal();dialog.querySelector('button').focus();
}
function toast(s){$('#toast').textContent=s;$('#toast').classList.add('show');setTimeout(()=>$('#toast').classList.remove('show'),3000)}
function setupTaskTitle(input,task){
  let title=document.createElement('textarea');title.className=input.className;title.name=input.name;title.rows=1;title.required=input.required;title.disabled=input.disabled;title.setAttribute('aria-label','Nombre de la tarea');title.value=task.title;
  input.replaceWith(title);
  function resize(){title.style.height='auto';title.style.height=title.scrollHeight+'px'}
  title.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing)e.preventDefault()});
  title.addEventListener('input',()=>{
    let original=title.value,lineBreaks=/\r\n|[\r\n\x0b\x0c\x1c-\x1e\u0085\u2028\u2029]/g,clean=original.replace(lineBreaks,' ');
    if(clean!==original){let start=original.slice(0,title.selectionStart).replace(lineBreaks,' ').length,end=original.slice(0,title.selectionEnd).replace(lineBreaks,' ').length;title.value=clean;title.setSelectionRange(start,end)}
    resize();
  });resize();
}
function collapseActivity(list){
  let items=[...list.querySelectorAll(':scope > .activity')];if(items.length<8||list.dataset.expanded==='true')return;
  let hidden=items.slice(4,-1),button=document.createElement('button');button.type='button';button.className='text-button activity-show-previous';button.textContent=`Mostrar ${hidden.length} actualizaciones previas`;
  hidden.forEach(item=>item.hidden=true);hidden[0].before(button);
  button.onclick=()=>{hidden.forEach(item=>item.hidden=false);list.dataset.expanded='true';hidden[0].tabIndex=-1;hidden[0].focus({preventScroll:true});button.remove()};
}
function modal(html,wide=false){
  closeStatusMenu();
  const container=$('#modal'),replacing=!!container.querySelector('.overlay');
  container.innerHTML=`<div class="overlay${replacing?'':' modal-appear'}"><div class="dialog${wide?' task-dialog':''}">${html}</div></div>`;
  let title=container.querySelector('input.task-title'),task=data?.tasks.find(t=>t.id===taskId);if(title&&task)setupTaskTitle(title,task);
  let activity=container.querySelector('#activityList');if(activity)collapseActivity(activity);
  if(container.querySelector('#taskForm')){let task=data?.tasks.find(t=>t.id===taskId);if(task?.public_id)setTaskUrl(task,openingDeepLink)}
  const overlay=$('#modal .overlay');let startedOnOverlay=false,endedOnOverlay=false;
  overlay.addEventListener('pointerdown',e=>{startedOnOverlay=e.isPrimary&&e.button===0&&e.target===overlay},true);
  overlay.addEventListener('pointerup',e=>{endedOnOverlay=e.isPrimary&&e.button===0&&e.target===overlay},true);
  overlay.addEventListener('click',e=>{if(startedOnOverlay&&endedOnOverlay&&e.target===overlay)closeModal();startedOnOverlay=endedOnOverlay=false});
}
function waitForModalAnimation(element){return new Promise(resolve=>{let timer=setTimeout(finish,240);function finish(e){if(e&&e.target!==element)return;element.removeEventListener('animationend',finish);clearTimeout(timer);resolve()}element.addEventListener('animationend',finish)})}
async function closeModal(force=false,animate=!force,fromHistory=false){
  const container=$('#modal'),overlay=container.querySelector('.overlay'),draft=taskDraft;
  const taskOpen=!!overlay?.querySelector('#taskForm');
  if(!force&&automationDraft&&automationProject?.role==='admin'&&overlay?.querySelector('#automationForm')&&!confirm('¿Descartar los cambios de la automatización?'))return false;
  const automationReturn=overlay?.querySelector('.automations')?document.querySelector('.automation-entry'):null;
  if(overlay?.querySelector('.automations'))automationDraft=null;
  if(overlay?.dataset.closing){if(force&&!animate)overlay.remove();return true}
  if(draft&&!force&&!await flushTaskDraft())return false;
  if(overlay!==container.querySelector('.overlay'))return true;
  if(overlay?.dataset.closing){if(force&&!animate)overlay.remove();return true}
  closeStatusMenu();if(taskDraft===draft&&draft){clearTimeout(draft.timer);taskDraft=null}
  if(overlay&&animate&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    overlay.dataset.closing='true';overlay.inert=true;overlay.classList.add('is-closing');
    await waitForModalAnimation(overlay);
  }
  if(overlay?.parentNode===container)overlay.remove();
  else if(!overlay)container.innerHTML='';
  if(taskOpen){taskId=null;if(data&&!fromHistory&&isTaskPath(location.pathname))setBoardUrl(active,true)}
  automationReturn?.focus();
  return true;
}
function descriptionText(t){if(t.description_format!=='html')return t.description;let d=document.createElement('div');d.innerHTML=t.description;return d.textContent||''}
function normalizeSearch(value){return String(value??'').normalize('NFD').replace(/\p{M}/gu,'').toLocaleLowerCase('es').trim()}
function matchesSearch(t,query){let q=normalizeSearch(query);return !q||normalizeSearch(t.title).includes(q)||normalizeSearch(descriptionText(t)).includes(q)}
function avatar(name){return `<span class="avatar">${esc((name||'?').split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase())}</span>`}
function icon(name){return ({grid:'▦',home:'⌂',tasks:'☷',people:'♧',plus:'＋',search:'⌕',more:'···'})[name]||name}
async function refresh(){let request=++dataRequest,epoch=mutationEpoch,next=await api('data');if(request!==dataRequest||epoch!==mutationEpoch)return;let previous=active;data=next;if(active!==0&&active!=='mine'&&!data.projects.some(p=>p.id===active))active=data.projects[0]?.id??(data.tasks.some(t=>!t.has_projects&&!t.parent_id)?0:null);if(previous!==null&&previous!==active&&location.pathname.startsWith('/board/')){let task=data.tasks.find(t=>t.id===taskId);if(task&&isTaskPath(location.pathname))setTaskUrl(task,true);else setBoardUrl(active,true)}render()}
let pollTimer=null,polling=false,pollDelay=5000;
function schedulePoll(delay=5000){clearTimeout(pollTimer);if(data&&!document.hidden&&navigator.onLine!==false)pollTimer=setTimeout(poll,delay)}
async function poll(){
  if(polling||!data||document.hidden||navigator.onLine===false)return;
  polling=true;
  try{
    let revision=await api('revision');if(revision.revision==null||data.revision==null)throw Error('Revisión no disponible');
    if(revision.revision!==data.revision){
      let request=++dataRequest,epoch=mutationEpoch,next=await api('data');
      if(next.revision==null)throw Error('Revisión no disponible');
      if(request!==dataRequest||epoch!==mutationEpoch||pendingMutations||document.hidden)return;
      let task=next.tasks.find(t=>t.id===taskId),project=next.projects.find(p=>p.id===active);
      if(taskDraft&&(!task||!task.can_edit||project?.role==='viewer')){clearTimeout(taskDraft.timer);taskDraft.denied=true;taskDraft.status.textContent='Sin permiso para guardar; conserva y copia tu borrador'}
      // ponytail: postpone replacing interactive DOM until the user finishes; retry on the next poll.
      if(dragged||listDrag||columnDrag||statusDrag!==null||dragSaving||columnSaving||projectFilterPanel||statusMenu||document.querySelector('dialog[open]')||document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')&&$('#app')?.contains(document.activeElement))return;
      if($('#modal .overlay')&&!$('#taskForm'))return;
      if($('#task-panel-comentarios .comment-edit-form'))return;
      if(taskDraft&&task&&((document.activeElement===taskDraft.title&&task.title_version!==taskDraft.titleVersion)||(document.activeElement===taskDraft.editor&&task.description_version!==taskDraft.descriptionVersion)))return;
      let id=taskId,list=$('#activityList'),commentsPanel=$('#task-panel-comentarios');
      if(id&&task&&list&&!commentsPanel?.querySelector('.comment-edit-form')){
        let [history,comments]=await Promise.all([api('history/'+id),api('comments/'+id)]);
        if(request!==dataRequest||epoch!==mutationEpoch||taskId!==id||!list.isConnected)return;
        let commentsList=commentsPanel?.querySelector('.comment, .muted')?.parentElement;
        if(commentsList&&!commentsList.contains(document.activeElement))commentsList.innerHTML=renderComments(comments,!!task.can_edit&&project?.role!=='viewer');
        if(!list.contains(document.activeElement)){list.innerHTML=history.map(h=>`<div class="activity">${avatar(h.actor)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('');collapseActivity(list)}
      }
      if(request!==dataRequest||epoch!==mutationEpoch)return;
      if(taskDraft&&task&&!taskDraft.denied&&!taskDraft.saving&&!taskDraft.conflict){
        let draft=taskDraft;
        if(draft.title.value.trim()===draft.savedTitle&&document.activeElement!==draft.title){draft.title.value=task.title;draft.savedTitle=task.title;draft.titleVersion=task.title_version}
        if(draft.editor.innerHTML===draft.savedDescription&&document.activeElement!==draft.editor){let temp=document.createElement('div');if(task.description_format==='html')temp.innerHTML=task.description||'';else temp.textContent=task.description||'';draft.editor.innerHTML=temp.innerHTML;draft.savedDescription=temp.innerHTML;draft.descriptionVersion=task.description_version}
      }
      data=next;if(active!==0&&active!=='mine'&&!next.projects.some(p=>p.id===active))active=next.projects[0]?.id??0;render();
    }
    pollDelay=5000;
  }catch(e){pollDelay=Math.min(pollDelay*2,60000)}finally{polling=false;schedulePoll(pollDelay)}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(pollTimer);else schedulePoll(0)});
window.addEventListener('online',()=>schedulePoll(0));
window.addEventListener('offline',()=>clearTimeout(pollTimer));
async function start(){try{let s=await api('session');if(!s.user)return auth(s.needs_setup,s.allow_self_registration);if(s.blocked)return showBlocked();if(s.must_change_password)return showPasswordChange();await refresh();await openDeepLink();schedulePoll()}catch(e){toast(e.message)}}
function auth(setup,allowSignup=false,signup=false){
  let creating=setup||signup;
  $('#app').innerHTML=`<div class="auth"><div class="auth-art"><div class="brand light"><span class="brandmark">✳</span> flujo<span class="brand-dot">.</span></div><div><div class="art-label">UN LUGAR PARA AVANZAR</div><h1>El trabajo fluye<br>mejor en equipo<span>.</span></h1><p>Organiza las ideas, conecta a las personas y lleva cada proyecto hacia adelante.</p><div class="art-cards"><div>✳ <b>Grandes ideas</b><small>Todo empieza por aquí</small></div><div>✓ <b>Mejor trabajo</b><small>Un paso a la vez</small></div></div></div><small>Hecho para los equipos que hacen.</small></div><div class="auth-form"><div class="auth-box"><div class="eyebrow">TU ESPACIO DE TRABAJO</div><h2>${setup?'Comencemos':signup?'Crear cuenta':'Bienvenido de vuelta'}</h2><p>${setup?'Crea la primera cuenta administradora para empezar.':signup?'Crea tu cuenta para ingresar al workspace.':'Ingresa tus datos para continuar con tu trabajo.'}</p><form id="authForm">${creating?'<label>Nombre<input name="name" required autocomplete="name" placeholder="Tu nombre"></label>':''}<label>Usuario<input name="username" required autocomplete="username" placeholder="Tu usuario"></label><label>Contraseña<input name="password" type="password" required minlength="8" autocomplete="${creating?'new-password':'current-password'}" placeholder="Mínimo 8 caracteres"></label>${signup?'<label>Confirmar contraseña<input name="confirm_password" type="password" required minlength="8" autocomplete="new-password"></label>':''}<button class="primary wide">${setup?'Crear workspace':signup?'Crear cuenta':'Iniciar sesión'} <span>→</span></button></form>${allowSignup&&!setup?`<button type="button" class="text-button auth-switch" onclick="auth(false,true,${!signup})">${signup?'Ya tengo una cuenta':'Crear cuenta'}</button>`:''}</div></div></div>`;
  $('#authForm').onsubmit=async e=>{e.preventDefault();let form=e.target,b=Object.fromEntries(new FormData(form));if(signup&&b.password!==b.confirm_password)return toast('Las contraseñas no coinciden');let button=form.querySelector('button');button.disabled=true;try{await api(setup?'setup':signup?'register':'login',b);await start()}catch(err){button.disabled=false;toast(err.message)}}
}
function showPasswordChange(){
  if($('#passwordChangeForm'))return;
  data=null;active=null;closeModal(true);
  $('#app').innerHTML=`<div class="blocked-screen"><div class="blocked-card"><div class="brand"><span class="brandmark">✳</span> flujo<span class="brand-dot">.</span></div><h1>Cambia tu contraseña</h1><p>Tu cuenta tiene una contraseña temporal. Elige una nueva para acceder al workspace.</p><form id="passwordChangeForm" class="password-change-form"><label>Contraseña temporal<input name="current_password" type="password" required autocomplete="current-password"></label><label>Nueva contraseña<input name="new_password" type="password" required minlength="8" autocomplete="new-password" placeholder="Mínimo 8 caracteres"></label><label>Confirmar nueva contraseña<input name="confirm_password" type="password" required minlength="8" autocomplete="new-password"></label><button class="primary wide">Cambiar contraseña</button></form><button type="button" class="text-button" onclick="logout()">Cerrar sesión</button></div></div>`;
  let form=$('#passwordChangeForm');form.elements.current_password.focus();form.onsubmit=async e=>{e.preventDefault();let b=Object.fromEntries(new FormData(form));if(b.new_password!==b.confirm_password)return toast('Las contraseñas nuevas no coinciden');let button=form.querySelector('button');button.disabled=true;try{await api('password/change',b);await start();toast('Contraseña actualizada')}catch(err){button.disabled=false;toast(err.message)}};
}
function sidebarCollapsed(){try{let value=localStorage.getItem('flujo:sidebar:'+data.user.id);return value===null?window.matchMedia('(max-width:800px)').matches:value==='collapsed'}catch{return window.matchMedia('(max-width:800px)').matches}}
function toggleSidebar(){let sidebar=$('#mainSidebar'),button=$('#sidebarToggle'),collapsed=!sidebar.classList.contains('is-collapsed');sidebar.classList.toggle('is-collapsed',collapsed);button.setAttribute('aria-expanded',String(!collapsed));button.setAttribute('aria-label',collapsed?'Expandir menú':'Ocultar menú');button.dataset.tooltip=collapsed?'Expandir menú':'Ocultar menú';button.textContent=collapsed?'›':'‹';try{localStorage.setItem('flujo:sidebar:'+data.user.id,collapsed?'collapsed':'expanded')}catch{}}
function render(){saveBoardScroll();let p=data.projects.find(x=>x.id===active), orphan=active===0, writable=p&&p.role!=='viewer';let tasks=orphan?data.tasks.filter(t=>!t.has_projects&&!t.parent_id):data.tasks.filter(t=>t.placements.some(x=>x.project_id===active));let count=tasks.length;$('#app').innerHTML=`<div class="shell"><aside id="mainSidebar" class="sidebar ${sidebarCollapsed()?'is-collapsed':''}"><div class="sidebar-top"><div class="brand"><span class="brandmark">✳</span> flujo<span class="brand-dot">.</span></div><button id="sidebarToggle" class="sidebar-toggle" type="button" aria-controls="mainSidebar" aria-expanded="${!sidebarCollapsed()}" aria-label="${sidebarCollapsed()?'Expandir menú':'Ocultar menú'}" data-tooltip="${sidebarCollapsed()?'Expandir menú':'Ocultar menú'}" onclick="toggleSidebar()">${sidebarCollapsed()?'›':'‹'}</button></div><button type="button" class="workspace" onclick="workspaceSettings()" aria-label="Configurar workspace" data-tooltip="${esc(data.workspace_name)}"><span class="workspace-icon">${esc(data.workspace_name.charAt(0).toUpperCase())}</span><span class="workspace-label"><strong>${esc(data.workspace_name)}</strong><small>Espacio de trabajo</small></span><span class="chevron">⌄</span></button><div class="nav-caption">WORKSPACE</div><button class="nav-item ${orphan?'selected':''}" onclick="selectProject(0)" aria-label="Sin proyecto" data-tooltip="Sin proyecto"><span>${icon('tasks')}</span><span class="nav-label">Sin proyecto</span><em>${data.tasks.filter(t=>!t.has_projects&&!t.parent_id).length}</em></button><div class="nav-caption project-caption">PROYECTOS ${data.user.admin?'<button class="create-project-control" onclick="newProject()" aria-label="Crear proyecto" data-tooltip="Crear proyecto">＋</button>':''}</div>${data.user.admin?'<button class="sidebar-add-project create-project-control" onclick="newProject()" aria-label="Crear proyecto" data-tooltip="Crear proyecto">＋</button>':''}<div class="project-list">${data.projects.map(x=>`<button class="nav-item ${active===x.id?'selected':''}" aria-label="${esc(x.name)}" data-tooltip="${esc(x.name)}" onclick="selectProject(${x.id})"><i style="background:${esc(x.color)}"></i><span class="project-name">${esc(x.name)}</span></button>`).join('')||'<div class="empty-nav">Aún no hay proyectos</div>'}</div><div class="sidebar-bottom"><div class="profile"><button type="button" class="profile-control" onclick="editProfile()" aria-label="Mi perfil" data-tooltip="Mi perfil">${avatar(data.user.name)}<span class="profile-info"><strong>${esc(data.user.name)}</strong><small>${data.user.admin?'Admin del workspace':'Miembro'}</small></span></button><button class="logout-control" aria-label="Cerrar sesión" data-tooltip="Cerrar sesión" onclick="logout()">↪</button></div></div></aside><main class="${p?'board-main':''}"><header class="topbar"><div class="breadcrumb">${esc(data.workspace_name)} <span>/</span> <strong>${esc(orphan?'Sin proyecto':p?.name||'Inicio')}</strong></div><div class="top-actions">${(p||orphan)&&boardView==='board'?`<label class="search top-search"><span aria-hidden="true">⌕</span><input type="search" aria-label="Buscar tareas del tablero por nombre o descripción" placeholder="Buscar en este tablero" value="${esc(filter)}" oninput="filter=this.value; renderBoard()"></label>`:''}<span class="today">${new Intl.DateTimeFormat('es-CL',{day:'numeric',month:'long',year:'numeric'}).format(new Date())}</span>${avatar(data.user.name)}</div></header>${!p&&!orphan?`<div class="welcome"><div class="eyebrow">TU ESPACIO DE TRABAJO</div><h1>Todo empieza con un proyecto.</h1><p>Reúne las tareas y a tu equipo en un solo lugar.</p>${data.user.admin?'<button class="primary" onclick="newProject()">＋ Crear proyecto</button>':''}</div>`:`<section class="content ${p?(boardView==='board'?'board-layout':'stats-layout'):''}"><div class="project-heading"><div class="project-heading-main"><div class="project-title-row"><h1>${orphan?'Sin proyecto':esc(p.name)}${p?.role==='admin'?` <button class="project-settings-trigger" type="button" aria-label="Editar tablero" data-tooltip="Editar tablero" onclick="editProject()">⚙</button>`:''}</h1>${p?.role==='viewer'?`<span class="board-readonly"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg><span>Solo tienes acceso para ver este board, no puedes editar, crear ni borrar.</span></span>`:''}</div><p>${orphan?'Tareas que aún no pertenecen a un proyecto.':esc(p.description||'')}</p></div><div class="heading-actions">${orphan&&data.user.admin?'<button class="primary" onclick="newOrphan()">＋ &nbsp; Nueva tarea</button>':''}${p?`<div class="member-stack">${p.members.slice(0,3).map(m=>avatar(m.name)).join('')}</div>${writable?'<button class="primary" onclick="newTask()">＋ &nbsp; Nueva tarea</button>':''}`:''}</div></div><div class="tabs"><button class="tab ${boardView==='board'?'active':''}" onclick="setBoardView('board')">▦ &nbsp; Tablero</button>${p?.role==='admin'?`<button class="tab ${boardView==='stats'?'active':''}" onclick="setBoardView('stats')">▥ &nbsp; Estadísticas</button>`:''}${boardView==='board'?`<div class="tab-right"><span id="boardCount">${count} ${count===1?'tarea':'tareas'}</span></div>`:''}</div><div id="board" ${p&&boardView==='stats'?'tabindex="0" role="region" aria-label="Contenido de estadísticas"':''}></div></section>`}</main></div>`;if(p||orphan){if(boardView==='stats'&&p?.role==='admin')renderStats();else renderBoard()}}
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

function statsDefaultMonth(){let parts=new Intl.DateTimeFormat('en-US',{timeZone:data.stats_timezone||'America/Santiago',year:'numeric',month:'2-digit'}).formatToParts(new Date());return parts.find(x=>x.type==='year').value+'-'+parts.find(x=>x.type==='month').value}
function daysLabel(value){return value==null?'—':new Intl.NumberFormat('es-CL',{maximumFractionDigits:1}).format(value)+' días'}
async function renderStats(){
  let board=$('#board'),project=active,request=++statsRequest;if(!board)return;if(!statsMonth)statsMonth=statsDefaultMonth();
  board.innerHTML='<div class="stats-loading">Cargando estadísticas…</div>';
  let params=new URLSearchParams({project_id:project,month:statsMonth});if(statsType)params.set('type_id',statsType);
  try{
    let report=await api('analytics?'+params.toString());if(request!==statsRequest||active!==project||boardView!=='stats'||board!==$('#board'))return;
    let maxWeek=Math.max(1,...report.weeks.flatMap(w=>[w.created,w.completed])),maxBucket=Math.max(1,...report.durations.map(x=>x.count));
    let formatter=new Intl.DateTimeFormat('es-CL',{dateStyle:'long',timeZone:report.timezone}),started=formatter.format(new Date(report.started_at)),identityStarted=formatter.format(new Date(report.identity_started_at));
    board.innerHTML=`<div class="stats-page"><div class="stats-intro"><div><h2>Estadísticas del tablero</h2><p>Actividad mensual y tiempo de resolución de las tareas.</p></div><div class="stats-filters"><label>Mes<input type="month" value="${esc(statsMonth)}" onchange="if(this.value){statsMonth=this.value;renderStats()}"></label><label>Tipo de tarea<select onchange="statsType=this.value;renderStats()"><option value="">Todos los tipos</option>${report.types.map(t=>`<option value="${t.id}" ${String(t.id)===statsType?'selected':''}>${esc(t.name)}</option>`).join('')}</select></label></div></div><div class="stats-kpis"><div class="kpi"><span>Tareas creadas</span><strong>${report.totals.created}</strong><small>Durante el mes</small></div><div class="kpi"><span>Finalizaciones</span><strong>${report.totals.completed}</strong><small>Eventos durante el mes</small></div><div class="kpi"><span>Tiempo promedio</span><strong>${daysLabel(report.average_days)}</strong><small>${report.duration_count} ${report.duration_count===1?'finalización medida':'finalizaciones medidas'}</small></div><div class="kpi"><span>Tiempo mediano</span><strong>${daysLabel(report.median_days)}</strong><small>Desde la creación</small></div><div class="kpi"><span>Reaperturas</span><strong>${report.totals.reopened}</strong><small>Vuelven a estar en curso</small></div></div><div class="stats-charts"><section class="stats-chart"><h3>Actividad durante el mes</h3><p>Creaciones y finalizaciones por período de días</p><div class="chart-legend"><span><i class="legend-created"></i>Creadas</span><span><i class="legend-completed"></i>Finalizaciones</span></div><div class="week-chart">${report.weeks.map(w=>`<div class="week-group"><div class="bar-pair"><div class="chart-bar created" style="height:${w.created/maxWeek*100}%" title="${w.created} creadas" aria-label="${w.created} creadas"><span>${w.created}</span></div><div class="chart-bar completed" style="height:${w.completed/maxWeek*100}%" title="${w.completed} finalizaciones" aria-label="${w.completed} finalizaciones"><span>${w.completed}</span></div></div><small>${esc(w.label)}</small></div>`).join('')}</div></section><section class="stats-chart"><h3>Tiempo hasta finalizar</h3><p>Distribución de las finalizaciones medidas</p><div class="duration-chart">${report.durations.map(d=>`<div class="duration-row"><span>${esc(d.label)}</span><div class="duration-track"><div class="duration-fill" style="width:${d.count/maxBucket*100}%"></div></div><strong>${d.count}</strong></div>`).join('')}</div></section></div><section class="stats-chart stats-people"><h3>Desglose por persona</h3><p>Creaciones por autor y finalizaciones según el responsable asignado en ese momento.</p><div class="stats-table-wrap"><table class="stats-table"><thead><tr><th scope="col">Persona</th><th scope="col">Tareas creadas</th><th scope="col">Finalizaciones asignadas</th><th scope="col">Tiempo promedio</th></tr></thead><tbody>${report.people.length?report.people.map(person=>`<tr><th scope="row">${esc(person.name)}</th><td>${person.created}</td><td>${person.completed}</td><td>${daysLabel(person.average_days)}${person.duration_count?`<small>${person.duration_count} ${person.duration_count===1?'finalización medida':'finalizaciones medidas'}</small>`:''}</td></tr>`).join(''):'<tr><td colspan="4" class="stats-empty">No hay actividad para los filtros seleccionados.</td></tr>'}</tbody></table></div><p class="stats-identity-note">La identidad se registra desde el ${esc(identityStarted)}. Los eventos anteriores aparecen como «Sin datos históricos»; las tareas finalizadas sin persona asignada aparecen como «Sin responsable».</p></section><p class="stats-note">Los eventos se registran desde el ${esc(started)} (${esc(report.timezone)}); los anteriores no se reconstruyen. Una tarea finalizada varias veces cuenta en cada finalización. El tiempo se mide desde su creación, incluidos los períodos de reapertura. Si pertenece a varios proyectos, se registra en los que la contienen al ocurrir el evento.</p></div>`;
  }catch(e){if(request===statsRequest&&active===project&&boardView==='stats')board.innerHTML=`<div class="stats-loading">${esc(e.message)} <button class="text-button" onclick="renderStats()">Reintentar</button></div>`}
}

function collapseKey(p){return `flujo:collapsed:${data.user.id}:${p}`}
function collapsedColumns(p){try{return JSON.parse(localStorage.getItem(collapseKey(p))||'[]')}catch{return []}}
function toggleColumn(id){let list=collapsedColumns(active),next=list.includes(id)?list.filter(x=>x!==id):[...list,id];try{localStorage.setItem(collapseKey(active),JSON.stringify(next))}catch(e){toast('No se pudo guardar la vista contraída')}renderBoard()}
function editColumn(id){
  let p=data.projects.find(x=>x.id===active),col=p?.columns.find(x=>x.id===id);if(p?.role!=='admin'||!col)return;
  formModal('Editar columna',`<label>Nombre<input name="name" required maxlength="100" value="${esc(col.name)}"></label><label>Color del punto<input type="color" name="color" value="${esc(col.color||'#a5a3ab')}"></label>`,b=>api('columns/update',{column_id:id,...b}));
  let button=document.createElement('button');button.type='button';button.className='task-delete column-delete-button';button.textContent='Borrar columna';button.onclick=()=>confirmDeleteColumn(id);$('#modalForm .dialog-actions').prepend(button);
}
function confirmDeleteColumn(id){
  let project=data.projects.find(p=>p.id===active),column=project?.columns.find(c=>c.id===id);if(project?.role!=='admin'||!column)return;
  let tasks=listColumnTasks(project.id,id),others=project.columns.filter(c=>c.id!==id),dialog=document.createElement('dialog');
  dialog.className='dialog confirm-column-delete';dialog.setAttribute('aria-labelledby','deleteColumnTitle');dialog.setAttribute('aria-describedby','deleteColumnDescription');
  dialog.innerHTML=`<form><h2 id="deleteColumnTitle">¿Borrar columna?</h2><p id="deleteColumnDescription">Se eliminará la columna <strong>«${esc(column.name)}»</strong>${tasks.length?`, que contiene ${tasks.length} ${tasks.length===1?'tarea':'tareas'}`:''}.</p>${tasks.length?`<fieldset><legend>Elige qué hacer con sus tareas</legend><label class="column-delete-option"><input type="radio" name="action" value="move" required ${others.length?'':'disabled'}> Dejar las tareas en otra columna</label><label class="column-delete-destination" hidden>Columna de destino<select name="destination_id" disabled><option value="">Selecciona una columna</option>${others.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label>${others.length?'':'<p class="muted">No hay otra columna disponible en este tablero.</p>'}<label class="column-delete-option"><input type="radio" name="action" value="remove" required> Sacar las tareas de este proyecto</label><p class="muted">Quedarán sin proyecto si no pertenecen a otro tablero ni heredan uno de una tarea principal.</p><label class="column-delete-option"><input type="radio" name="action" value="delete" required> Eliminar las tareas de esta columna</label><p class="column-delete-warning">Las tareas se eliminarán de todos sus tableros y no podrán recuperarse. Sus subtareas se conservarán como tareas independientes.</p></fieldset>`:''}<p class="column-delete-error" role="alert"></p><div class="dialog-actions"><button type="button" class="outline" data-cancel>Cancelar</button><button type="submit" class="task-delete" ${tasks.length?'disabled':''}>Borrar columna</button></div></form>`;
  let warning=dialog.querySelector('.column-delete-warning');if(warning)warning.textContent='Las tareas se eliminarán de todos sus tableros y no podrán recuperarse. Las subtareas que no estén en esta columna se conservarán como tareas independientes.';
  $('#modal').appendChild(dialog);let form=dialog.querySelector('form'),cancel=dialog.querySelector('[data-cancel]'),submit=dialog.querySelector('[type=submit]'),destination=form.querySelector('[name=destination_id]'),destinationLabel=form.querySelector('.column-delete-destination'),error=dialog.querySelector('.column-delete-error');
  function update(){let action=form.querySelector('[name=action]:checked')?.value;if(destination){destination.disabled=action!=='move';destination.required=action==='move';destinationLabel.hidden=action!=='move'}submit.disabled=!!submit.dataset.stale||(!!tasks.length&&(!action||(action==='move'&&!destination.value)))}
  form.addEventListener('change',update);cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('cancel',e=>{if(submit.dataset.saving)e.preventDefault()});
  form.onsubmit=async e=>{
    e.preventDefault();let action=form.querySelector('[name=action]:checked')?.value;if(tasks.length&&(!action||(action==='move'&&!destination.value)))return;
    submit.disabled=cancel.disabled=true;submit.dataset.saving='true';error.textContent='';
    try{await api('columns/delete',{column_id:id,task_ids:tasks.map(t=>t.id),...(action?{action}:{}),...(action==='move'?{destination_id:Number(destination.value)}:{})})}
    catch(err){delete submit.dataset.saving;cancel.disabled=false;if(err.message.includes('Las tareas de la columna cambiaron')){submit.dataset.stale='true';try{await refresh()}catch(refreshError){toast('No se pudo actualizar el tablero')}}update();error.textContent=err.message;return}
    dialog.close();await closeModal(false,false);
    try{await refresh();toast('Columna eliminada')}catch(err){toast('Columna eliminada, pero no se pudo actualizar el tablero')}
  };
  dialog.showModal();cancel.focus();
}
const boardScrollPositions=new Map();
function saveBoardScroll(){
  let board=document.querySelector('.board[data-project]');if(!board)return;
  let key=board.dataset.project,state=boardScrollPositions.get(key)||{left:0,columns:new Map()};state.left=board.scrollLeft;
  board.querySelectorAll('.column[data-column] .cards').forEach(cards=>state.columns.set(cards.closest('.column').dataset.column,cards.scrollTop));
  boardScrollPositions.set(key,state);
}
function restoreBoardScroll(board){
  let state=boardScrollPositions.get(board.dataset.project);if(!state)return;board.scrollLeft=state.left;
  board.querySelectorAll('.column[data-column] .cards').forEach(cards=>{cards.scrollTop=state.columns.get(cards.closest('.column').dataset.column)||0});
}
const projectFilterCache=new Map();
function projectFilterDefaults(){return {completion:'all',period:'all',assignee:'',type:'',due:'',date:'',priority:'',status:''}}
function projectFilterKey(project){return `flujo:project-filters:${data.user.id}:${project.public_id||project.id}`}
function projectFilters(project){
  let key=projectFilterKey(project);if(projectFilterCache.has(key))return projectFilterCache.get(key);
  let saved={};try{saved=JSON.parse(sessionStorage.getItem(key)||'{}')}catch{}
  let result=projectFilterDefaults();if(saved&&typeof saved==='object')for(let field of Object.keys(result))if(typeof saved[field]==='string')result[field]=saved[field];
  if(!['all','open','done'].includes(result.completion))result.completion='all';
  if(!['all','today','yesterday','week','month'].includes(result.period))result.period='all';
  if(!['','none','overdue','today','week','month','date'].includes(result.due))result.due='';
  if(!['','Baja','Normal','Alta'].includes(result.priority))result.priority='';
  if(!/^(|none|[1-9]\d*)$/.test(result.assignee))result.assignee='';
  for(let field of ['type','status'])if(!/^(|[1-9]\d*)$/.test(result[field]))result[field]='';
  if(!/^\d{4}-\d{2}-\d{2}$/.test(result.date))result.date='';
  projectFilterCache.set(key,result);return result;
}
function saveProjectFilters(project,filters){try{sessionStorage.setItem(projectFilterKey(project),JSON.stringify(filters))}catch{}}
function projectFilterCount(filters){return ['assignee','type','due','priority','status'].filter(field=>filters[field]).length+(filters.completion!=='all'?1:0)}
let projectDateFormatter=null,projectDateTimezone='';
function projectFilterDate(value){
  let date=new Date(value);if(Number.isNaN(date.getTime()))return '';
  let timezone=data.stats_timezone||'America/Santiago';if(timezone!==projectDateTimezone){projectDateTimezone=timezone;projectDateFormatter=new Intl.DateTimeFormat('en-US',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'})}
  let parts=projectDateFormatter.formatToParts(date);
  return ['year','month','day'].map(type=>parts.find(part=>part.type===type).value).join('-');
}
function projectFilterDates(today){
  let day=new Date(today+'T00:00:00Z'),offset=(day.getUTCDay()+6)%7;
  function shift(days){let date=new Date(day);date.setUTCDate(date.getUTCDate()+days);return date.toISOString().slice(0,10)}
  return {today,yesterday:shift(-1),weekStart:shift(-offset),weekEnd:shift(6-offset),month:today.slice(0,7)};
}
function matchesProjectFilters(task,filters,dates){
  let done=!!taskStatus(task)?.is_done;
  if(filters.completion==='open'&&done||filters.completion==='done'&&!done)return false;
  if(filters.completion==='done'&&filters.period!=='all'){
    let completed=task.completed_at?projectFilterDate(task.completed_at):'';if(!completed)return false;
    if(filters.period==='today'&&completed!==dates.today||filters.period==='yesterday'&&completed!==dates.yesterday||filters.period==='week'&&(completed<dates.weekStart||completed>dates.weekEnd)||filters.period==='month'&&!completed.startsWith(dates.month+'-'))return false;
  }
  if(filters.assignee&&(filters.assignee==='none'?task.assignee_id!=null:String(task.assignee_id)!==filters.assignee))return false;
  if(filters.type&&String(task.type_id)!==filters.type||filters.status&&String(task.status_id)!==filters.status||filters.priority&&task.priority!==filters.priority)return false;
  let due=task.due_date||'';
  if(filters.due==='none'&&due||filters.due==='overdue'&&(!due||due>=dates.today)||filters.due==='today'&&due!==dates.today||filters.due==='week'&&(!due||due<dates.weekStart||due>dates.weekEnd)||filters.due==='month'&&(!due||!due.startsWith(dates.month+'-'))||filters.due==='date'&&(!filters.date||due!==filters.date))return false;
  return true;
}
let projectFilterPanel=null,projectFilterTrigger=null;
function closeProjectFilters(restoreFocus=false){
  if(!projectFilterPanel)return;
  if(statusMenu&&projectFilterPanel.contains(statusMenuTrigger))closeStatusMenu();
  let trigger=projectFilterTrigger;projectFilterPanel.remove();projectFilterPanel=null;projectFilterTrigger=null;trigger?.setAttribute('aria-expanded','false');
  document.removeEventListener('pointerdown',projectFilterOutside,true);document.removeEventListener('keydown',projectFilterKeydown,true);window.removeEventListener('resize',projectFilterResize);
  if(restoreFocus&&trigger?.isConnected)trigger.focus();
}
function projectFilterOutside(e){if(projectFilterPanel&&!projectFilterPanel.contains(e.target)&&!projectFilterTrigger?.contains(e.target)&&!(statusMenu?.contains(e.target)&&projectFilterPanel.contains(statusMenuTrigger)))closeProjectFilters()}
function projectFilterKeydown(e){if(e.key==='Escape'&&projectFilterPanel&&!(statusMenu?.contains(e.target))){e.preventDefault();closeProjectFilters(true)}}
function projectFilterResize(){closeProjectFilters()}
function projectFilterOptions(items,value){
  return items.map(([id,name])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(name)}</option>`).join('')+(value&&!items.some(([id])=>id===value)?`<option value="${esc(value)}" selected>No disponible</option>`:'');
}
function updateProjectFilterButton(project){let button=$('.project-filter-trigger');if(!button||active!==project.id)return;let count=projectFilterCount(projectFilters(project));button.textContent='⚑ Filtros'+(count?' · '+count:'');button.classList.toggle('active',!!count)}
function openProjectFilters(button){
  let project=data.projects.find(p=>p.id===active);if(!project)return;
  if(projectFilterTrigger===button){closeProjectFilters(true);return}closeProjectFilters();closeStatusMenu();
  let filters=projectFilters(project),types=[...project.types];
  for(let task of data.tasks)if(task.placements.some(place=>place.project_id===project.id)&&task.task_type&&!types.some(type=>type.id===task.task_type.id))types.push(task.task_type);
  let assignees=[['','Todos'],['none','Sin responsable'],...data.users.map(user=>[String(user.id),user.name])],typeOptions=[['','Todos'],...types.map(type=>[String(type.id),type.name])];
  let statuses=[['','Todos'],...types.flatMap(type=>type.statuses.map(status=>[String(status.id),type.name+' · '+status.name]))];
  let panel=document.createElement('div');panel.className='project-filter-panel';panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Filtros del proyecto');
  panel.innerHTML=`<div class="project-filter-head"><strong>Filtros</strong><button type="button" class="text-button" data-clear>Eliminar todos</button></div><div class="project-filter-quick"><button type="button" data-quick="open">Sin finalizar</button><button type="button" data-quick="mine">Asignadas a mí</button><button type="button" data-quick="done">Finalizadas</button></div><label data-period>Finalizadas<select data-field="period">${projectFilterOptions([['all','Todas'],['today','Hoy'],['yesterday','Ayer'],['week','Esta semana'],['month','Este mes']],filters.period)}</select><small>Las tareas antiguas sin fecha de finalización solo aparecen en «Todas».</small></label><div class="project-filter-fields"><label>Responsable<select data-field="assignee">${projectFilterOptions(assignees,filters.assignee)}</select></label><label>Tipo de tarea<select data-field="type">${projectFilterOptions(typeOptions,filters.type)}</select></label><label>Fecha límite<select data-field="due">${projectFilterOptions([['','Cualquiera'],['none','Sin fecha'],['overdue','Vencida'],['today','Hoy'],['week','Esta semana'],['month','Este mes'],['date','Fecha específica']],filters.due)}</select></label><label data-date>Fecha específica<input type="date" data-field="date" value="${esc(filters.date)}"></label><label>Prioridad<select data-field="priority">${projectFilterOptions([['','Todas'],['Baja','Baja'],['Normal','Normal'],['Alta','Alta']],filters.priority)}</select></label><label>Estado<select data-field="status">${projectFilterOptions(statuses,filters.status)}</select></label></div>`;
  document.body.appendChild(panel);projectFilterPanel=panel;projectFilterTrigger=button;button.setAttribute('aria-expanded','true');
  function sync(){
    panel.querySelectorAll('[data-field]').forEach(control=>{control.value=filters[control.dataset.field]});
    panel.querySelector('[data-period]').hidden=filters.completion!=='done';panel.querySelector('[data-date]').hidden=filters.due!=='date';
    panel.querySelectorAll('[data-quick]').forEach(control=>{let kind=control.dataset.quick,on=kind==='mine'?filters.assignee===String(data.user.id):filters.completion===kind;control.setAttribute('aria-pressed',String(on));control.classList.toggle('active',on)});
    updateProjectFilterButton(project);
    let rect=button.getBoundingClientRect(),height=panel.offsetHeight,width=panel.offsetWidth;panel.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';panel.style.top=Math.max(8,Math.min(rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6,window.innerHeight-height-8))+'px';
  }
  function apply(){saveProjectFilters(project,filters);sync();renderBoard()}
  panel.addEventListener('change',e=>{let field=e.target.dataset.field;if(!field)return;filters[field]=e.target.value;apply()});
  panel.addEventListener('click',e=>{
    let quick=e.target.closest('[data-quick]');
    if(quick){let kind=quick.dataset.quick;if(kind==='mine')filters.assignee=filters.assignee===String(data.user.id)?'':String(data.user.id);else filters.completion=filters.completion===kind?'all':kind;apply()}
    else if(e.target.closest('[data-clear]')){Object.assign(filters,projectFilterDefaults());apply()}
  });
  sync();
  document.addEventListener('pointerdown',projectFilterOutside,true);document.addEventListener('keydown',projectFilterKeydown,true);window.addEventListener('resize',projectFilterResize);panel.querySelector('[data-quick]')?.focus();
}
const listCollapsedState=new Map();
function listCollapsedColumns(project){
  let key=`flujo:list-collapsed:${data.user.id}:${project}`;
  if(!listCollapsedState.has(key)){let ids=[];try{let saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))ids=saved}catch{}listCollapsedState.set(key,ids)}
  return listCollapsedState.get(key);
}
function toggleListColumn(button,id){
  let key=`flujo:list-collapsed:${data.user.id}:${active}`,ids=listCollapsedColumns(active),collapsed=!ids.includes(id),next=collapsed?[...ids,id]:ids.filter(x=>x!==id);
  listCollapsedState.set(key,next);try{localStorage.setItem(key,JSON.stringify(next))}catch{}
  button.setAttribute('aria-expanded',String(!collapsed));button.setAttribute('aria-label',`${collapsed?'Expandir':'Contraer'} ${button.dataset.name}`);
  let rows=[...button.closest('tbody').querySelectorAll('.task-list-row,.task-list-empty-row')],reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  clearTimeout(button.foldTimer);
  for(let row of rows){
    row.inert=collapsed;
    if(reduced){row.hidden=collapsed;row.classList.remove('list-folding','list-folded');continue}
    row.classList.add('list-folding');
    if(!collapsed&&row.hidden){row.classList.add('list-folded');row.hidden=false;void row.offsetHeight}
    row.classList.toggle('list-folded',collapsed);
  }
  if(!reduced)button.foldTimer=setTimeout(()=>{for(let row of rows){row.hidden=collapsed;row.classList.remove('list-folding','list-folded')}},240);
}
function listColumnTasks(project,column){return data.tasks.filter(t=>t.placements.some(x=>x.project_id===project&&x.column_id===column)).sort((a,b)=>a.placements.find(x=>x.project_id===project).position-b.placements.find(x=>x.project_id===project).position)}
function taskListCount(task,kind,count){
  let subtask=kind==='subtareas',label=subtask?(count===1?'subtarea':'subtareas'):(count===1?'comentario':'comentarios');
  let path=subtask?'M9 6h12M9 12h12M9 18h12M3 6l1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2':'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H5l1.5-3A8.5 8.5 0 1 1 21 11.5Z';
  return `<button type="button" class="task-list-count" onclick="openTask(${task.id},'${kind}')" aria-label="Abrir ${count} ${label} de ${esc(task.title)}"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>${count}</button>`;
}
function renderTaskList(){
  let p=data.projects.find(x=>x.id===active),board=$('#board');if(!p||!board)return;
  let writable=p.role!=='viewer',query=normalizeSearch(filter),subtasks=new Map(),collapsed=listCollapsedColumns(p.id),filters=projectFilters(p),dates=projectFilterDates(projectFilterDate(new Date()));
  for(let task of data.tasks)if(task.parent_id)subtasks.set(task.parent_id,(subtasks.get(task.parent_id)||0)+1);
  board.innerHTML=`<div class="task-list-wrap"><table class="task-list"><thead><tr><th scope="col">Tarea</th><th scope="col">Estado</th><th scope="col">Responsable</th><th scope="col">Fecha límite</th><th scope="col">Prioridad</th>${writable?'<th scope="col">Orden y columna</th>':''}</tr></thead>${p.columns.map(column=>{
    let all=listColumnTasks(p.id,column.id),tasks=all.filter(t=>matchesSearch(t,query)&&matchesProjectFilters(t,filters,dates));
    let hidden=collapsed.includes(column.id);
    return `<tbody data-column="${column.id}" ondragover="listDragOver(event)" ondrop="dropListTask(event)"><tr class="task-list-group"><th colspan="${writable?6:5}" scope="colgroup"><button type="button" class="task-list-toggle" data-name="${esc(column.name)}" aria-label="${hidden?'Expandir':'Contraer'} ${esc(column.name)}" aria-expanded="${!hidden}" onclick="toggleListColumn(this,${column.id})">▾</button><span class="status-dot" style="background:${esc(column.color||'#a5a3ab')}"></span> ${esc(column.name)} <span class="count">${tasks.length}</span>${writable?` <button type="button" class="text-button" onclick="newTask(${column.id})" aria-label="Agregar tarea a ${esc(column.name)}">＋ Agregar tarea</button>`:''}</th></tr>${tasks.map(t=>{
      let editable=writable&&t.can_edit,controls=taskQuickControls(t,editable),index=all.findIndex(x=>x.id===t.id),subtaskCount=subtasks.get(t.id)||0;
      return `<tr class="task-list-row" data-id="${t.id}" ${hidden?'hidden':''}><td>${editable?`<span class="task-list-handle" draggable="true" ondragstart="startListDrag(event,${t.id})" ondragend="endListDrag()" title="Arrastrar tarea" aria-label="Arrastrar ${esc(t.title)}">⠿</span>`:''}<button type="button" class="task-list-title" onclick="openTask(${t.id})">${esc(t.title)}</button>${t.task_type?`<small>${esc(t.task_type.name)}</small>`:''}${subtaskCount?taskListCount(t,'subtareas',subtaskCount):''}${t.comment_count?taskListCount(t,'comentarios',t.comment_count):''}</td><td>${controls.statusControl}</td><td>${controls.personControl}</td><td>${controls.dateControl}</td><td>${controls.priorityControl}</td>${writable?`<td>${editable?`<div class="task-list-move"><button type="button" onclick="moveListTask(${t.id},${column.id},-1,this)" aria-label="Subir ${esc(t.title)}" ${index===0?'disabled':''}>↑</button><button type="button" onclick="moveListTask(${t.id},${column.id},1,this)" aria-label="Bajar ${esc(t.title)}" ${index===all.length-1?'disabled':''}>↓</button><select aria-label="Mover ${esc(t.title)} a columna" onchange="moveListTask(${t.id},Number(this.value),0,this)">${p.columns.map(c=>`<option value="${c.id}" ${c.id===column.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select></div>`:''}</td>`:''}</tr>`;
    }).join('')||`<tr class="task-list-empty-row" ${hidden?'hidden':''}><td colspan="${writable?6:5}" class="task-list-empty">${query||projectFilterCount(filters)?'Sin resultados en esta columna.':'Sin tareas en esta columna.'}</td></tr>`}</tbody>`;
  }).join('')}</table></div>`;
  for(let cell of board.querySelectorAll('.task-list-row > td, .task-list-empty-row > td')){
    let fold=document.createElement('div'),content=document.createElement('div');fold.className='task-list-cell-fold';
    while(cell.firstChild)content.appendChild(cell.firstChild);fold.appendChild(content);cell.appendChild(fold);
  }
}
let listDrag=null,listScrollTimer=null,listScrollX=0,listScrollY=0;
function stopListScroll(){clearInterval(listScrollTimer);listScrollTimer=null}
document.addEventListener('dragover',e=>{if(listDrag&&!$('#board')?.contains(e.target))stopListScroll()});
document.addEventListener('dragleave',e=>{if(listDrag&&!e.relatedTarget)stopListScroll()});
function startListDrag(e,id){
  if(dragSaving||columnSaving||dragged||listDrag){e.preventDefault();return}
  let source=e.currentTarget.closest('.task-list-row'),placeholder=document.createElement('tr'),cell=document.createElement('td');
  cell.colSpan=source.children.length;placeholder.className='task-list-placeholder';placeholder.setAttribute('aria-hidden','true');placeholder.appendChild(cell);source.after(placeholder);
  listDrag={id,project:active,source,placeholder};e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',String(id));
  let rect=source.getBoundingClientRect();e.dataTransfer.setDragImage(source,Math.max(0,e.clientX-rect.left),Math.max(0,e.clientY-rect.top));
  requestAnimationFrame(()=>{if(listDrag?.source===source)source.hidden=true});
}
function positionListPlaceholder(section,y){
  let rows=[...section.querySelectorAll('.task-list-row')].filter(row=>row!==listDrag.source&&!row.hidden);
  let next=rows.find(row=>{let rect=row.getBoundingClientRect();return y<rect.top+rect.height/2});
  if(listDrag.placeholder.parentElement!==section||listDrag.placeholder.nextElementSibling!==(next||null))section.insertBefore(listDrag.placeholder,next||null);
}
function listDragOver(e){
  if(!listDrag||listDrag.project!==active)return;
  e.preventDefault();e.dataTransfer.dropEffect='move';positionListPlaceholder(e.currentTarget,e.clientY);
  let board=$('#board'),rect=board.getBoundingClientRect();listScrollX=e.clientX;listScrollY=e.clientY;
  if(e.clientY>=rect.top&&e.clientY<=rect.bottom&&(e.clientY<rect.top+50||e.clientY>rect.bottom-50)){
    if(!listScrollTimer)listScrollTimer=setInterval(()=>{
      let bounds=board.getBoundingClientRect(),direction=listScrollY<bounds.top+50?-1:listScrollY>bounds.bottom-50?1:0,previous=board.scrollTop;
      board.scrollTop+=direction*8;
      if(board.scrollTop!==previous){let section=document.elementFromPoint(listScrollX,listScrollY)?.closest('tbody[data-column]');if(section&&board.contains(section))positionListPlaceholder(section,listScrollY)}
    },30);
  }else stopListScroll();
}
function endListDrag(){stopListScroll();if(!listDrag)return;listDrag.source.hidden=false;listDrag.placeholder.remove();listDrag=null}
async function dropListTask(e){
  if(!listDrag||listDrag.project!==active)return;
  listDragOver(e);let {id,project,source,placeholder}=listDrag,column=Number(e.currentTarget.dataset.column),next=placeholder.nextElementSibling;
  while(next&&(!next.matches('.task-list-row[data-id]')||next===source))next=next.nextElementSibling;
  let ids=listColumnTasks(project,column).map(t=>t.id),order=moveOrder(ids,id,next?Number(next.dataset.id):null);
  stopListScroll();source.hidden=e.currentTarget.querySelector('.task-list-toggle')?.getAttribute('aria-expanded')==='false';placeholder.replaceWith(source);listDrag=null;dragSaving=true;
  try{await api('task/move',{id,project_id:project,column_id:column,order});await refresh()}
  catch(err){toast(err.message);try{await refresh()}catch(refreshError){toast(refreshError.message)}}finally{dragSaving=false}
}
async function moveListTask(id,column,shift,control){
  if(dragSaving)return;let project=active,task=data.tasks.find(t=>t.id===id),placement=task?.placements.find(x=>x.project_id===project);if(!placement)return;
  let ids=listColumnTasks(project,column).map(t=>t.id),index=ids.indexOf(id);
  if(shift){let target=index+shift;if(index<0||target<0||target>=ids.length)return;[ids[index],ids[target]]=[ids[target],ids[index]]}
  else{if(placement.column_id===column)return;ids.push(id)}
  dragSaving=true;control.disabled=true;
  try{await api('task/move',{id,project_id:project,column_id:column,order:ids});await refresh()}
  catch(err){toast(err.message);try{await refresh()}catch(refreshError){toast(refreshError.message)}}finally{dragSaving=false}
}
function renderBoard(){
  if(boardView==='list')return renderTaskList();
  saveBoardScroll();
  let p=data.projects.find(x=>x.id===active),orphan=active===0,board=$('#board');if(!board)return;let subtaskCounts=new Map();for(let task of data.tasks)if(task.parent_id)subtaskCounts.set(task.parent_id,(subtaskCounts.get(task.parent_id)||0)+1);
  let query=normalizeSearch(filter),matched=new Map(),filters=p?projectFilters(p):null,dates=filters?projectFilterDates(projectFilterDate(new Date())):null,match=t=>{if(!matched.has(t.id))matched.set(t.id,matchesSearch(t,query)&&(!filters||matchesProjectFilters(t,filters,dates)));return matched.get(t.id)};
  let scoped=orphan?data.tasks.filter(t=>!t.has_projects&&!t.parent_id):data.tasks.filter(t=>t.placements.some(x=>x.project_id===active));let found=scoped.filter(match).length,count=$('#boardCount');if(count)count.textContent=filter.trim()||(filters&&projectFilterCount(filters))?`${found} de ${scoped.length} ${scoped.length===1?'tarea':'tareas'}`:`${scoped.length} ${scoped.length===1?'tarea':'tareas'}`;
  if(orphan){let ts=data.tasks.filter(t=>!t.has_projects&&!t.parent_id&&match(t));board.innerHTML=`<div class="orphan-list">${ts.map(t=>card(t,subtaskCounts.get(t.id)||0)).join('')||`<div class="empty-state">${filter.trim()?'No se encontraron tareas.':'No hay tareas sin proyecto.'}</div>`}</div>`;return}
  if(!p)return;let writable=p.role!=='viewer',collapsed=collapsedColumns(p.id);
  board.innerHTML=`<div class="board" data-project="${p.id}" tabindex="0" role="region" aria-label="Columnas del tablero" ondragover="columnDragOver(event)" ondrop="dropColumn(event)">${p.columns.map(c=>{
    let all=data.tasks.filter(t=>t.placements.some(x=>x.project_id===p.id&&x.column_id===c.id));
    let ts=all.filter(match).sort((a,b)=>a.placements.find(x=>x.project_id===p.id).position-b.placements.find(x=>x.project_id===p.id).position),isCollapsed=collapsed.includes(c.id);
    let handle=writable?`<span class="column-handle" draggable="true" ondragstart="startColumnDrag(event,${c.id})" ondragend="endColumnDrag()" data-tooltip="Arrastrar columna" aria-label="Arrastrar columna ${esc(c.name)}">⠿</span>`:'';
    if(isCollapsed)return `<div class="column column-collapsed" data-column="${c.id}"><div class="collapsed-controls">${handle}<button class="column-action" onclick="toggleColumn(${c.id})" data-tooltip="Expandir columna" aria-label="Expandir columna ${esc(c.name)}">›</button></div><div class="collapsed-label"><strong>${esc(c.name)}</strong><span class="count">${all.length}</span></div></div>`;
    return `<div class="column" data-column="${c.id}" ondragover="dragOver(event)" ondrop="dropTask(event,${c.id})"><div class="column-header"><div>${handle}<span class="status-dot" style="background:${esc(c.color||'#a5a3ab')}"></span><strong title="${esc(c.name)}">${esc(c.name)}</strong><span class="count">${ts.length}</span></div><div class="column-actions">${p.role==='admin'?`<button class="column-action" onclick="editColumn(${c.id})" data-tooltip="Editar columna" aria-label="Editar columna ${esc(c.name)}">✎</button>`:''}${writable?`<button class="column-action" onclick="newTask(${c.id})" data-tooltip="Agregar tarea" aria-label="Agregar tarea a ${esc(c.name)}">＋</button>`:''}<button class="column-action" onclick="toggleColumn(${c.id})" data-tooltip="Contraer columna" aria-label="Contraer columna ${esc(c.name)}">‹</button></div></div><div class="cards" tabindex="0" aria-label="Tareas de ${esc(c.name)}">${ts.map(t=>card(t,subtaskCounts.get(t.id)||0)).join('')}</div>${writable?`<button class="add-card" onclick="newTask(${c.id})">＋ &nbsp; Agregar tarea</button>`:''}</div>`
  }).join('')}${writable?'<button class="add-column" onclick="newColumn()">＋ &nbsp; Agregar columna</button>':''}</div>`;
  restoreBoardScroll(board.querySelector('.board'));
}
function startColumnDrag(e,id){
  if(dragSaving||columnSaving||dragged){e.preventDefault();return}
  e.stopPropagation();columnDrag=id;columnSource=e.currentTarget.closest('.column');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain','column:'+id);
  columnGhost=document.createElement('div');columnGhost.className='column-ghost';columnGhost.style.width=columnSource.offsetWidth+'px';columnGhost.style.height=columnSource.offsetHeight+'px';columnGhost.setAttribute('aria-hidden','true');
  columnSource.after(columnGhost);requestAnimationFrame(()=>{if(columnDrag===id&&columnSource)columnSource.classList.add('column-drag-source')});
}
function columnDragOver(e){
  if(columnDrag===null||!columnGhost||!columnSource)return;
  e.preventDefault();e.dataTransfer.dropEffect='move';let board=e.currentTarget;
  let columns=[...board.querySelectorAll(':scope > .column[data-column]')].filter(c=>c!==columnSource);
  let left=board.getBoundingClientRect().left-board.scrollLeft;
  let next=columns.find(c=>e.clientX<left+c.offsetLeft+c.offsetWidth/2)||board.querySelector('.add-column');
  if(columnGhost.parentElement===board&&columnGhost.nextElementSibling===(next||null))return;
  let before=new Map(columns.map(c=>[c,c.getBoundingClientRect().left]));board.insertBefore(columnGhost,next||null);
  for(let c of columns){columnMotion.get(c)?.cancel();let delta=before.get(c)-c.getBoundingClientRect().left;if(Math.abs(delta)>.5&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches)columnMotion.set(c,c.animate([{transform:`translateX(${delta}px)`},{transform:'translateX(0)'}],{duration:180,easing:'cubic-bezier(.2,.75,.3,1)'}))}
}
function endColumnDrag(){columnSource?.classList.remove('column-drag-source');columnGhost?.remove();columnDrag=null;columnGhost=null;columnSource=null}
async function dropColumn(e){
  if(columnDrag===null||!columnGhost||!columnSource)return;
  e.preventDefault();e.stopPropagation();columnDragOver(e);
  let board=e.currentTarget,id=columnDrag,source=columnSource,p=active;
  source.classList.remove('column-drag-source');columnGhost.replaceWith(source);columnGhost=null;
  let order=[...board.querySelectorAll(':scope > .column[data-column]')].map(c=>Number(c.dataset.column));
  columnSaving=true;endColumnDrag();source.classList.add('column-landed');source.addEventListener('animationend',()=>source.classList.remove('column-landed'),{once:true});
  try{await api('columns/reorder',{project_id:p,order});data=await api('data')}
  catch(err){toast(err.message);try{await refresh()}catch(refreshError){toast(refreshError.message)}}finally{columnSaving=false}
}

function taskStatus(t){return t.task_type?.statuses.find(s=>s.id===t.status_id)}
let statusMenu=null,statusMenuTrigger=null;
function closeStatusMenu(restoreFocus=false){
  if(!statusMenu)return;
  let trigger=statusMenuTrigger;statusMenu.remove();statusMenu=null;statusMenuTrigger=null;
  trigger?.setAttribute('aria-expanded','false');
  document.removeEventListener('pointerdown',statusMenuOutside,true);document.removeEventListener('scroll',statusMenuScroll,true);window.removeEventListener('resize',statusMenuResize);
  if(restoreFocus&&trigger?.isConnected)trigger.focus();
}
function statusMenuOutside(e){if(statusMenu&&!statusMenu.contains(e.target)&&!statusMenuTrigger?.contains(e.target))closeStatusMenu()}
function statusMenuScroll(e){if(statusMenu&&!statusMenu.contains(e.target))closeStatusMenu()}
function statusMenuResize(){closeStatusMenu()}
function statusButton(t,editable,detail=false){
  let visual=taskPropertyVisual('status',t,editable);
  if(!editable)return detail?`<span class="detail-value">${visual}</span>`:visual;
  return `<button type="button" class="quick-control status-trigger ${detail?'detail-control':''}" aria-label="Cambiar estado de ${esc(t.title)}" aria-haspopup="menu" aria-expanded="false" onclick="openStatusMenu(event,${t.id},${detail})" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation();event.preventDefault()"><span class="${detail?'detail-value':''}" aria-hidden="true">${visual}</span></button>`;
}
function assigneeButton(t,editable,detail=false){
  let user=data.users.find(u=>u.id===t.assignee_id),visual=detail?taskPropertyVisual('assignee_id',t,editable):user?avatar(user.name):'<span class="avatar empty-avatar">＋</span>';
  if(!editable)return detail?`<span class="detail-value">${visual}</span>`:visual;
  return `<button type="button" class="quick-control status-trigger ${detail?'detail-control':''}" aria-label="Cambiar responsable de ${esc(t.title)}" aria-haspopup="menu" aria-expanded="false" onclick="openStatusMenu(event,${t.id},${detail},'assignee')" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation();event.preventDefault()"><span class="${detail?'detail-value':''}" aria-hidden="true">${visual}</span></button>`;
}
function priorityButton(t,editable,detail=false){
  let visual=taskPropertyVisual('priority',t,editable);
  if(!editable)return detail?`<span class="detail-value">${visual}</span>`:visual;
  return `<button type="button" class="quick-control status-trigger ${detail?'detail-control':''}" aria-label="Cambiar prioridad de ${esc(t.title)}" aria-haspopup="menu" aria-expanded="false" onclick="openStatusMenu(event,${t.id},${detail},'priority')" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation();event.preventDefault()"><span class="${detail?'detail-value':''}" aria-hidden="true">${visual}</span></button>`;
}
function openStatusMenu(e,id,detail,field='status'){
  e.stopPropagation();let trigger=e.currentTarget,task=data.tasks.find(t=>t.id===id);
  if(statusMenuTrigger===trigger){closeStatusMenu();return}
  closeStatusMenu();if(!task||(field==='status'&&!task.task_type?.statuses?.length))return;
  let menu=document.createElement('div');menu.className='status-menu'+(field==='priority'?' priority-menu':'');menu.setAttribute('role','menu');menu.setAttribute('aria-label',field==='status'?'Cambiar estado':field==='priority'?'Cambiar prioridad':'Cambiar responsable');
  menu.innerHTML=field==='status'?task.task_type.statuses.map(st=>`<button type="button" role="menuitemradio" aria-checked="${st.id===task.status_id}" class="status-option ${st.id===task.status_id?'selected':''}" data-value="${st.id}"><i style="background:${esc(st.color)}"></i><span class="status-option-text">${esc(st.name)} <small>(${st.is_done?'Finalizado':'En curso'})</small></span>${st.id===task.status_id?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join(''):field==='priority'?['Baja','Normal','Alta'].map(value=>`<button type="button" role="menuitemradio" aria-checked="${value===task.priority}" class="status-option ${value===task.priority?'selected':''}" data-value="${value}"><span class="status-option-text">${taskPropertyVisual('priority',{priority:value},false)}</span>${value===task.priority?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join(''):[{id:null,name:'Sin asignar'},...data.users].map(user=>`<button type="button" role="menuitemradio" aria-checked="${user.id===(task.assignee_id??null)}" class="status-option ${user.id===(task.assignee_id??null)?'selected':''}" data-value="${user.id??''}">${user.id===null?'<span class="avatar empty-avatar">＋</span>':avatar(user.name)}<span class="status-option-text">${esc(user.name)}</span>${user.id===(task.assignee_id??null)?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join('');
  document.body.appendChild(menu);statusMenu=menu;statusMenuTrigger=trigger;trigger.setAttribute('aria-expanded','true');
  let rect=trigger.getBoundingClientRect(),width=menu.offsetWidth,height=menu.offsetHeight;
  menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
  menu.style.top=Math.max(8,Math.min(rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6,window.innerHeight-height-8))+'px';
  menu.addEventListener('click',async event=>{
    event.stopPropagation();let option=event.target.closest('.status-option');if(!option)return;
    let value=option.dataset.value,property=field==='status'?'status':field==='priority'?'priority':'assignee_id';closeStatusMenu(true);if(value===String((field==='status'?task.status_id:task[property])??''))return;
    if(detail){let control=trigger.closest('form')?.querySelector(field==='status'?'#taskStatusSelect':field==='priority'?'#taskPrioritySelect':'#taskAssigneeSelect');if(control){control.value=value;await saveTaskField(id,property,control)}}
    else{trigger.disabled=true;try{if(field==='status')await api('task/type-status',{id,type_id:task.type_id,status_id:Number(value)});else await api('task/update',{id,[property]:value});await refresh()}catch(err){trigger.disabled=false;toast(err.message)}}
  });
  menu.addEventListener('keydown',event=>{
    let options=[...menu.querySelectorAll('.status-option')],index=options.indexOf(document.activeElement);
    if(event.key==='Escape'){event.preventDefault();closeStatusMenu(true);return}
    if(event.key==='Tab'){closeStatusMenu(true);return}
    if(event.key==='ArrowDown')index=(index+1)%options.length;else if(event.key==='ArrowUp')index=(index-1+options.length)%options.length;else if(event.key==='Home')index=0;else if(event.key==='End')index=options.length-1;else return;
    event.preventDefault();options[index].focus();
  });
  document.addEventListener('pointerdown',statusMenuOutside,true);document.addEventListener('scroll',statusMenuScroll,true);window.addEventListener('resize',statusMenuResize);
  (menu.querySelector('[aria-checked="true"]')||menu.querySelector('.status-option')).focus();
}
function openSelectMenu(select){
  if(statusMenuTrigger===select){closeStatusMenu(true);return}
  closeStatusMenu();if(select.matches(':disabled'))return;
  let options=[...select.options].filter(option=>!option.hidden),anchor=select.closest('.quick-control')||select,rect=anchor.getBoundingClientRect();
  if(!options.length)return;
  let menu=document.createElement('div');menu.className='status-menu select-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label',select.getAttribute('aria-label')||select.labels?.[0]?.firstChild?.textContent?.trim()||'Seleccionar opción');
  menu.style.minWidth=Math.min(Math.max(160,rect.width),Math.max(0,Math.min(320,window.innerWidth-16)))+'px';
  menu.innerHTML=options.map((option,index)=>`<button type="button" role="menuitemradio" aria-checked="${option.selected}" class="status-option ${option.selected?'selected':''}" data-index="${index}" ${option.disabled||option.parentElement?.disabled?'disabled':''}><span class="status-option-text">${esc(option.textContent)}</span>${option.selected?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join('');
  (select.closest('dialog[open]')||document.body).appendChild(menu);statusMenu=menu;statusMenuTrigger=select;select.setAttribute('aria-expanded','true');
  let width=menu.offsetWidth,height=menu.offsetHeight;menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';menu.style.top=Math.max(8,Math.min(rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6,window.innerHeight-height-8))+'px';
  menu.addEventListener('click',event=>{
    let button=event.target.closest('.status-option');if(!button||button.disabled)return;event.stopPropagation();let option=options[Number(button.dataset.index)],changed=!option.selected;
    closeStatusMenu(true);if(changed){select.value=option.value;select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}))}
  });
  let typed='',typeTimer;
  menu.addEventListener('keydown',event=>{
    let buttons=[...menu.querySelectorAll('.status-option:not(:disabled)')],index=buttons.indexOf(document.activeElement);
    if(event.key==='Escape'){event.preventDefault();closeStatusMenu(true);return}if(event.key==='Tab'){closeStatusMenu(true);return}
    if(!buttons.length)return;
    if(event.key==='ArrowDown')index=(index+1)%buttons.length;else if(event.key==='ArrowUp')index=(index-1+buttons.length)%buttons.length;else if(event.key==='Home')index=0;else if(event.key==='End')index=buttons.length-1;
    else if(event.key.length===1&&event.key!==' '&&!event.ctrlKey&&!event.metaKey&&!event.altKey){typed+=normalizeSearch(event.key);clearTimeout(typeTimer);typeTimer=setTimeout(()=>{typed=''},700);let found=buttons.findIndex(button=>normalizeSearch(button.textContent).startsWith(typed));if(found<0)return;index=found}else return;
    event.preventDefault();buttons[index].focus();
  });
  document.addEventListener('pointerdown',statusMenuOutside,true);document.addEventListener('scroll',statusMenuScroll,true);window.addEventListener('resize',statusMenuResize);
  (menu.querySelector('[aria-checked="true"]:not(:disabled)')||menu.querySelector('.status-option:not(:disabled)'))?.focus();
}
function dropdownSelect(target){let select=target.closest?.('select');return select&&!select.multiple&&select.size<=1&&!select.matches(':disabled')?select:null}
document.addEventListener('pointerdown',e=>{let select=dropdownSelect(e.target);if(!select||e.button!==0)return;e.preventDefault();e.stopPropagation();openSelectMenu(select)},true);
document.addEventListener('click',e=>{let select=dropdownSelect(e.target);if(!select)return;e.preventDefault();e.stopPropagation();if(e.detail===0)openSelectMenu(select)},true);
document.addEventListener('keydown',e=>{let select=dropdownSelect(e.target);if(!select||!['Enter',' ','ArrowDown','ArrowUp'].includes(e.key)||e.altKey||e.ctrlKey||e.metaKey)return;e.preventDefault();e.stopPropagation();openSelectMenu(select)},true);
function taskQuickControls(t,editable){
  let priorityControl=priorityButton(t,editable);
  let st=taskStatus(t), statusControl=st?statusButton(t,editable):'';
  let dateLabel=t.due_date?'◷ &nbsp;'+esc(t.due_date):'◷ &nbsp; Sin fecha';
  let dateControl=editable?`<span class="quick-control date-control" title="Cambiar fecha límite" onclick="event.stopPropagation()" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation()"><span class="date-value" aria-hidden="true">${dateLabel}</span><input type="date" aria-label="Cambiar fecha límite de ${esc(t.title)}" value="${esc(t.due_date||'')}" onpointerdown="event.stopPropagation()" onclick="event.stopPropagation();if(typeof this.showPicker==='function'){try{this.showPicker();event.preventDefault()}catch(e){}}" onchange="quickUpdate(event,${t.id},'due_date')"></span>`:`<span class="date-value">${dateLabel}</span>`;
  let personControl=assigneeButton(t,editable);
  return {priorityControl,statusControl,dateControl,personControl};
}
function card(t,subtaskCount=0){
  let p=data.projects.find(x=>x.id===active), editable=t.can_edit&&(!p||p.role!=='viewer'), draggable=editable&&!!p,st=taskStatus(t);
  let {priorityControl,statusControl,dateControl,personControl}=taskQuickControls(t,editable);
  return `<article class="card" ${draggable?'draggable="true"':''} data-id="${t.id}" ondragstart="dragStart(event,${t.id})" ondragend="endTaskDrag()" onclick="openTask(${t.id})"><div class="card-top">${statusControl}</div><div class="card-title-row"><h3>${esc(t.title)}</h3>${subtaskCount?`<button type="button" class="card-comments" draggable="false" aria-label="Abrir ${subtaskCount} ${subtaskCount===1?'subtarea':'subtareas'} de ${esc(t.title)}" onclick="event.stopPropagation();openTask(${t.id},'subtareas')" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation();event.preventDefault()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6h12M9 12h12M9 18h12M3 6l1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2"/></svg>${subtaskCount}</button>`:''}${t.comment_count?`<button type="button" class="card-comments" draggable="false" aria-label="Abrir ${t.comment_count} ${t.comment_count===1?'comentario':'comentarios'} de ${esc(t.title)}" onclick="event.stopPropagation();openTask(${t.id},'comentarios')" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation();event.preventDefault()"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H5l1.5-3A8.5 8.5 0 1 1 21 11.5Z"/></svg>${t.comment_count}</button>`:''}</div>${st?`<div class="task-type-line">${esc(t.task_type.name)}</div>`:''}${t.description?`<p>${esc(descriptionText(t))}</p>`:''}<div class="card-bottom"><div class="card-meta">${dateControl}${priorityControl}</div>${personControl}</div></article>`
}
async function quickUpdate(e,id,field){
  e.stopPropagation();let select=e.currentTarget,t=data.tasks.find(task=>task.id===id),original=field==='status'?String(t.status_id):String(t[field]??'');select.disabled=true;
  try{if(field==='status')await api('task/type-status',{id,type_id:t.type_id,status_id:Number(select.value)});else await api('task/update',{id,[field]:select.value});await refresh()}
  catch(err){select.value=original;select.disabled=false;toast(err.message)}
}

function dragStart(e,id){
  if(dragSaving||columnSaving||e.target.closest('.quick-control')){e.preventDefault();return}
  dragged=id;dragSource=e.currentTarget;dragOffsetY=e.clientY-dragSource.getBoundingClientRect().top;e.dataTransfer.effectAllowed='move';
  e.dataTransfer.setData('text/plain',String(id));
  dragPlaceholder=dragSource.cloneNode(true);
  dragPlaceholder.classList.add('card-placeholder');dragPlaceholder.removeAttribute('draggable');
  dragPlaceholder.removeAttribute('onclick');dragPlaceholder.removeAttribute('ondragstart');dragPlaceholder.removeAttribute('ondragend');
  dragPlaceholder.setAttribute('aria-hidden','true');dragPlaceholder.removeAttribute('data-id');
  dragPlaceholder.style.height=dragSource.offsetHeight+'px';
  dragSource.after(dragPlaceholder);
  requestAnimationFrame(()=>{if(dragged===id&&dragSource)dragSource.classList.add('drag-source')});
}
function stopDragScroll(){clearInterval(dragScrollTimer);dragScrollTimer=null;dragScrollColumn=null}
document.addEventListener('dragover',e=>{if(dragScrollColumn&&!dragScrollColumn.contains(e.target))stopDragScroll()});
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)stopDragScroll()});
function dragOver(e,scroll=true){
  if(!dragged||!dragPlaceholder||e.currentTarget.classList.contains('column-collapsed'))return;
  e.preventDefault();e.dataTransfer.dropEffect='move';
  let column=e.currentTarget, cards=column.querySelector('.cards');
  if(scroll){
    let rect=cards.getBoundingClientRect();
    dragScrollY=e.clientY;
    if(e.clientY>=rect.top&&e.clientY<=rect.bottom&&(e.clientY<rect.top+50||e.clientY>rect.bottom-50)){
      if(dragScrollColumn!==column){
        stopDragScroll();dragScrollColumn=column;
        dragScrollTimer=setInterval(()=>{
          let rect=cards.getBoundingClientRect(), direction=dragScrollY<rect.top+50?-1:dragScrollY>rect.bottom-50?1:0, previous=cards.scrollTop;
          cards.scrollTop+=direction*8;
          if(cards.scrollTop!==previous)dragOver({currentTarget:column,clientY:dragScrollY,preventDefault(){},dataTransfer:{}},false);
        },30);
      }
    }else stopDragScroll();
  }
  // Use layout positions, not animated rectangles, to keep the insertion point stable.
  let top=cards.getBoundingClientRect().top-cards.scrollTop;
  let next=[...cards.querySelectorAll('.card:not(.card-placeholder):not(.drag-source)')].filter(card=>card!==dragSource).find(card=>{
    let sameColumn=dragPlaceholder.parentElement===cards, below=sameColumn&&(dragPlaceholder.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING);
    let edge=sameColumn?e.clientY-dragOffsetY+(below?dragPlaceholder.offsetHeight:0):e.clientY;
    return edge<top+card.offsetTop+card.offsetHeight*(sameColumn?(below?0.6:0.4):0.5);
  });
  if(dragPlaceholder.parentElement!==cards||dragPlaceholder.nextElementSibling!==(next||null)){
    let moving=[...document.querySelectorAll('.board .cards .card[data-id]')].filter(card=>card!==dragSource);
    let before=new Map(moving.map(card=>[card,card.getBoundingClientRect().top]));
    cards.insertBefore(dragPlaceholder,next||null);
    for(let card of moving){
      cardMotion.get(card)?.cancel();
      let delta=before.get(card)-card.getBoundingClientRect().top;
      if(Math.abs(delta)>0.5&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
        let animation=card.animate([{transform:`translateY(${delta}px)`},{transform:'translateY(0)'}],{duration:180,easing:'cubic-bezier(.2,.75,.3,1)'});
        cardMotion.set(card,animation);
      }
    }
  }
  document.querySelectorAll('.column.drag-target').forEach(el=>{if(el!==column)el.classList.remove('drag-target')});
  column.classList.add('drag-target');
}
function endTaskDrag(){
  stopDragScroll();
  dragSource?.classList.remove('drag-source');dragPlaceholder?.remove();
  document.querySelectorAll('.column.drag-target').forEach(el=>el.classList.remove('drag-target'));
  dragged=null;dragSource=null;dragPlaceholder=null;
}
function moveOrder(ids,id,nextId){let result=ids.filter(x=>x!==id);let index=nextId===null?-1:result.indexOf(nextId);result.splice(index<0?result.length:index,0,id);return result}
async function dropTask(e,col){
  e.preventDefault();if(!dragged||!dragPlaceholder||!dragSource)return;
  dragOver(e);
  let id=dragged,p=data.projects.find(x=>x.id===active),source=dragSource,original=data.tasks.find(t=>t.id===id);
  let ids=data.tasks.filter(t=>t.id!==id&&t.placements.some(x=>x.project_id===p.id&&x.column_id===col)).sort((a,b)=>a.placements.find(x=>x.project_id===p.id).position-b.placements.find(x=>x.project_id===p.id).position).map(t=>t.id);
  let next=dragPlaceholder.nextElementSibling;while(next&&(!next.matches('.card[data-id]')||next===source))next=next.nextElementSibling;
  let order=moveOrder(ids,id,next?Number(next.dataset.id):null);
  dragSaving=true;source.classList.remove('drag-source');dragPlaceholder.replaceWith(source);dragPlaceholder=null;
  source.classList.remove('card-landed');void source.offsetWidth;source.classList.add('card-landed');
  source.addEventListener('animationend',()=>source.classList.remove('card-landed'),{once:true});
  endTaskDrag();
  try{
    await api('task/move',{id,project_id:p.id,column_id:col,order});
    data=await api('data');
    let updated=data.tasks.find(t=>t.id===id);
    if(!updated||updated.placements.find(x=>x.project_id===p.id)?.column_id!==col||['assignee_id','due_date','priority','status_id','type_id'].some(field=>updated[field]!==original[field])){render();return}
    document.querySelectorAll('.column[data-column]').forEach(column=>{let count=column.querySelector('.count');if(count){let col=Number(column.dataset.column);count.textContent=column.classList.contains('column-collapsed')?data.tasks.filter(t=>t.placements.some(x=>x.project_id===p.id&&x.column_id===col)).length:column.querySelectorAll('.cards .card[data-id]').length}});
  }catch(err){toast(err.message);try{await refresh()}catch(refreshError){toast(refreshError.message)}}
  finally{dragSaving=false}
}

async function logout(){await api('logout',{});location.reload()}
function formModal(title,body,submit,footer='',taskCreation=false){
  modal(`<div class="dialog-head"><h2>${title}</h2><button onclick="closeModal()">×</button></div><form id="modalForm">${body}<div class="dialog-actions ${taskCreation?'task-create-actions':''}">${footer?`<span class="profile-version">${esc(footer)}</span>`:''}<button type="button" class="outline ${taskCreation?'task-create-cancel':''}" onclick="closeModal()">Cancelar</button>${taskCreation?'<button type="submit" value="create" class="outline task-create-button">Crear</button><button type="submit" value="edit" class="primary">Crear y editar</button>':'<button type="submit" class="primary">Guardar</button>'}</div></form>`);
  let form=$('#modalForm');form.querySelector('input:not([type=hidden]), textarea, select')?.focus();
  form.onsubmit=async e=>{
    e.preventDefault();let values=Object.fromEntries(new FormData(form));
    if(!taskCreation){try{await submit(values);closeModal();await refresh()}catch(err){toast(err.message)}return}
    let edit=e.submitter?.value==='edit',buttons=[...form.querySelectorAll('button[type="submit"]')];buttons.forEach(button=>button.disabled=true);
    let result;try{result=await submit(values)}catch(err){buttons.forEach(button=>button.disabled=false);toast(err.message);return}
    let open=edit&&form.isConnected;if(form.isConnected)await closeModal(false,false);
    try{await refresh()}catch(err){toast('Tarea creada, pero no se pudo actualizar el tablero');return}
    if(open&&!$('#modal .overlay')){if(data.tasks.some(t=>t.id===result.id))await openTask(result.id);else toast('Tarea creada, pero no está disponible para editar')}
  };
}
function newProject(){formModal('Nuevo proyecto','<label>Nombre del proyecto<input name="name" required placeholder="Ej. Lanzamiento de producto"></label><label>Descripción<textarea name="description" placeholder="¿De qué trata este proyecto?"></textarea></label><label>Color<input type="color" name="color" value="#7765d7"></label>',async b=>{let r=await api('projects',b);active=r.id;currentRoute='/board/'+r.public_id;history.pushState(null,'',currentRoute)})}
function editProject(){
  let project=data.projects.find(p=>p.id===active);if(!project||project.role!=='admin')return;
  modal(`<div class="dialog-head"><div><div class="eyebrow">CONFIGURACIÓN DEL PROYECTO</div><h2>Editar tablero</h2></div><button type="button" onclick="closeModal()" aria-label="Cerrar">×</button></div><div class="task-tabs project-settings-tabs" role="tablist" aria-label="Configuración del tablero" onkeydown="projectSettingsKeydown(event)"><button type="button" role="tab" id="project-tab-tablero" aria-controls="project-panel-tablero" onclick="setProjectSettingsTab('tablero')">Tablero</button><button type="button" role="tab" id="project-tab-usuarios" aria-controls="project-panel-usuarios" onclick="setProjectSettingsTab('usuarios')">Usuarios</button><button type="button" role="tab" id="project-tab-tipos" aria-controls="project-panel-tipos" onclick="setProjectSettingsTab('tipos')">Tipos de tarea</button></div><section id="project-panel-tablero" role="tabpanel" aria-labelledby="project-tab-tablero"><form id="projectSettingsForm"><label for="projectName">Nombre del tablero</label><div class="project-name-field"><input type="color" name="color" value="${esc(project.color)}" aria-label="Color del tablero" title="Color del tablero"><input id="projectName" name="name" required maxlength="120" value="${esc(project.name)}"></div><label>Descripción<textarea name="description" maxlength="5000" rows="4" placeholder="¿De qué trata este tablero?">${esc(project.description)}</textarea></label><div class="dialog-actions"><button type="button" class="outline" onclick="closeModal()">Cancelar</button><button class="primary">Guardar</button></div></form></section><section id="project-panel-usuarios" role="tabpanel" aria-labelledby="project-tab-usuarios"><p class="muted project-settings-note">Elige quién puede acceder a este tablero y qué puede hacer. Los cambios de acceso se guardan de inmediato.</p><div class="people-list">${data.users.map(u=>{let m=project.members.find(m=>m.id===u.id);return `<div class="person">${avatar(u.name)}<div><strong>${esc(u.name)}</strong><small>${u.admin?'Admin del workspace · acceso total':'@'+esc(u.username)}</small></div>${u.admin?'<span class="role-label">Admin</span>':`<select aria-label="Acceso de ${esc(u.name)}" onchange="setMember(${u.id},this.value,this)"><option value="remove" ${!m?'selected':''}>Sin acceso</option><option value="viewer" ${m?.role==='viewer'?'selected':''}>Solo lectura</option><option value="editor" ${m?.role==='editor'?'selected':''}>Editor</option><option value="admin" ${m?.role==='admin'?'selected':''}>Admin</option></select>`}</div>`}).join('')}</div></section><section id="project-panel-tipos" role="tabpanel" aria-labelledby="project-tab-tipos"></section>`);
  renderTypesList();setProjectSettingsTab('tablero');
  $('#modal .dialog-head h2').textContent='Editar proyecto';$('#project-tab-tablero').textContent='Proyecto';
  $('.project-settings-tabs').setAttribute('aria-label','Configuración del proyecto');
  $('#projectSettingsForm label[for="projectName"]').textContent='Nombre del proyecto';
  let color=$('#projectSettingsForm input[name="color"]');color.setAttribute('aria-label','Color del proyecto');color.title='Color del proyecto';
  $('#projectSettingsForm textarea[name="description"]').placeholder='¿De qué trata este proyecto?';
  $('#project-panel-usuarios .project-settings-note').textContent='Elige quién puede acceder a este proyecto y qué puede hacer. Los cambios de acceso se guardan de inmediato.';
  $('#projectSettingsForm').onsubmit=async e=>{e.preventDefault();try{await api('projects/update',{project_id:project.id,...Object.fromEntries(new FormData(e.target))});closeModal();await refresh();toast('Proyecto actualizado')}catch(err){toast(err.message)}};
}
function setProjectSettingsTab(name){for(let tab of ['tablero','usuarios','tipos']){let selected=tab===name,button=$('#project-tab-'+tab),panel=$('#project-panel-'+tab);if(!button||!panel)return;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;panel.hidden=!selected}}
function projectSettingsKeydown(e){let names=['tablero','usuarios','tipos'],i=names.findIndex(name=>$('#project-tab-'+name).getAttribute('aria-selected')==='true');if(e.key==='ArrowRight')i=(i+1)%names.length;else if(e.key==='ArrowLeft')i=(i+names.length-1)%names.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=names.length-1;else return;e.preventDefault();setProjectSettingsTab(names[i]);$('#project-tab-'+names[i]).focus()}

function newOrphan(){formModal('Nueva tarea sin proyecto','<label>Nombre de la tarea<input name="title" required></label>',b=>api('tasks',b),'',true)}
function newColumn(){formModal('Nueva columna','<label>Nombre<input name="name" required placeholder="Nombre de la columna"></label>',b=>api('columns',{...b,project_id:active}))}
function newTask(col){let p=data.projects.find(x=>x.id===active);formModal('Nueva tarea',`<label>Nombre de la tarea<input name="title" required placeholder="¿Qué hay que hacer?"></label><label>Columna<select name="column_id">${p.columns.map(c=>`<option value="${c.id}" ${c.id===col?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><label>Tipo de tarea<select name="type_id">${p.types.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></label>`,b=>api('tasks',{...b,project_id:p.id}),'',true)}
function slackIcon(){return '<svg class="slack-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="2" y="9" width="9" height="4" rx="2" fill="#36C5F0"/><rect x="7" y="2" width="4" height="9" rx="2" fill="#36C5F0"/><rect x="11" y="2" width="4" height="9" rx="2" fill="#2EB67D"/><rect x="13" y="7" width="9" height="4" rx="2" fill="#2EB67D"/><rect x="13" y="11" width="9" height="4" rx="2" fill="#ECB22E"/><rect x="13" y="13" width="4" height="9" rx="2" fill="#ECB22E"/><rect x="9" y="13" width="4" height="9" rx="2" fill="#E01E5A"/><rect x="2" y="13" width="9" height="4" rx="2" fill="#E01E5A"/></svg>'}
function editProfile(){
  formModal('Mi perfil',`<label>Nombre<input name="name" value="${esc(data.user.name)}" autocomplete="name" maxlength="120" required></label><p class="muted profile-password-note">Para cambiar tu contraseña, completa los siguientes campos.</p><label>Contraseña actual<input name="current_password" type="password" autocomplete="current-password"></label><label>Nueva contraseña<input name="new_password" type="password" minlength="8" autocomplete="new-password" placeholder="Mínimo 8 caracteres"></label><label>Confirmar nueva contraseña<input name="confirm_password" type="password" autocomplete="new-password"></label>`,async b=>{await api('profile',b);toast('Perfil actualizado')},'Versión: '+(data.version||'No disponible'))
  $('#modal .dialog').insertAdjacentHTML('beforeend',`<div class="detail-section"><div class="profile-entries"><button type="button" class="outline slack-entry" onclick="slackSettings()">${slackIcon()} Notificaciones de Slack</button><button type="button" class="outline slack-entry" onclick="mcpTokens()">Tokens de acceso (MCP)</button></div></div>`);
}
function mcpTokens(){
  let dialog=document.createElement('dialog'),returnFocus=document.activeElement,previous=document.querySelector('#modal .overlay'),busy=false;
  if(previous)previous.hidden=true;
  dialog.className='dialog automations slack-dialog';dialog.setAttribute('aria-labelledby','mcpTokensTitle');
  dialog.innerHTML='<div class="dialog-head"><h2 id="mcpTokensTitle">Tokens de acceso (MCP)</h2><button type="button" aria-label="Cerrar">×</button></div><p role="status">Cargando…</p>';
  document.body.appendChild(dialog);dialog.showModal();
  function close(){if(busy)return;dialog.close()}
  dialog.querySelector('button').onclick=close;
  dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
  dialog.addEventListener('close',()=>{dialog.remove();if(previous)previous.hidden=false;returnFocus?.focus()});
  const when=value=>value?new Date(value).toLocaleString('es-CL',{dateStyle:'short',timeStyle:'short'}):'sin uso';
  const item=t=>`<li${t.revoked_at?' class="revoked"':''}><div><strong>${esc(t.name)}</strong><small>Creado ${when(t.created_at)} · Último uso ${when(t.last_used_at)}</small></div>${t.revoked_at?'<span class="mcp-token-state">Revocado</span>':`<button type="button" class="outline" data-revoke="${t.id}">Revocar</button>`}</li>`;
  async function load(){
    try{
      let {tokens}=await api('tokens');if(!dialog.isConnected)return;
      dialog.innerHTML=`<div class="dialog-head"><h2 id="mcpTokensTitle">Tokens de acceso (MCP)</h2><button type="button" aria-label="Cerrar" data-action="close">×</button></div><p class="muted">Cada token da a un agente MCP el mismo acceso que tienes tú. Guárdalo al crearlo: solo se muestra una vez.</p><form id="mcpTokenForm"><label>Nombre del token<input name="name" required maxlength="60" placeholder="Ej. OpenCode en mi portátil"></label><div class="dialog-actions"><button class="primary" type="submit">Crear token</button></div></form><div class="mcp-token-created" hidden></div><ul class="mcp-token-list">${tokens.map(item).join('')||'<li class="muted">Aún no tienes tokens.</li>'}</ul>`;
      dialog.querySelector('#mcpTokenForm').onsubmit=async e=>{
        e.preventDefault();let form=e.target,button=form.querySelector('button[type=submit]');button.disabled=true;busy=true;
        try{
          let created=await api('tokens',Object.fromEntries(new FormData(form)));if(!dialog.isConnected)return;
          form.reset();
          let panel=dialog.querySelector('.mcp-token-created');panel.hidden=false;
          panel.innerHTML=`<p><strong>Token «${esc(created.name)}» creado</strong></p><label class="mcp-token-value">Cópialo ahora: no volverás a verlo<input readonly value="${esc(created.token)}"></label><button type="button" class="outline" data-copy="${created.token}">Copiar token</button>`;
          dialog.querySelector('.mcp-token-list').insertAdjacentHTML('afterbegin',item({id:created.id,name:created.name,created_at:new Date().toISOString(),last_used_at:null,revoked_at:null}));
          let empty=dialog.querySelector('.mcp-token-list .muted');if(empty)empty.remove();
        }catch(err){toast(err.message)}
        finally{busy=false;button.disabled=false}
      };
    }catch(err){if(dialog.isConnected)dialog.querySelector('[role=status]').textContent=err.message}
  }
  dialog.addEventListener('click',async e=>{
    let copy=e.target.closest('[data-copy]')?.dataset.copy;
    if(copy){try{await navigator.clipboard.writeText(copy);toast('Token copiado')}catch(err){toast(err.message)}return}
    let revoke=e.target.closest('[data-revoke]')?.dataset.revoke;
    if(revoke){
      if(!confirm('¿Revocar este token? El agente que lo use dejará de tener acceso.'))return;
      let button=e.target.closest('button');button.disabled=true;
      try{await api('tokens/revoke',{id:Number(revoke)});let li=button.closest('li');li.classList.add('revoked');button.outerHTML='<span class="mcp-token-state">Revocado</span>'}
      catch(err){button.disabled=false;toast(err.message)}
      return;
    }
    if(e.target.closest('[data-action=close]'))close();
  });
  load();
}
function showBlocked(){data=null;active=null;closeModal(true);$('#app').innerHTML=`<div class="blocked-screen"><div class="blocked-card"><div class="brand"><span class="brandmark">✳</span> flujo<span class="brand-dot">.</span></div><h1>Acceso bloqueado</h1><p>Has sido bloqueado de este workspace, contacta a tu admin para más información.</p><button class="outline" onclick="logout()">Cerrar sesión</button></div></div>`}
function workspaceSettings(){
  let admin=!!data.user.admin;
  modal(`<div class="dialog-head"><div><div class="eyebrow">WORKSPACE</div><h2>Configuración del workspace</h2></div><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div><form id="workspaceForm"><label>Nombre del workspace<input name="name" value="${esc(data.workspace_name)}" maxlength="120" required ${admin?'':'disabled'}></label>${admin?'<button class="primary" type="submit">Guardar nombre</button>':''}</form><div class="detail-section"><h3>Personas del workspace</h3><div class="people-list">${data.users.map(u=>`<div class="person">${avatar(u.name)}<div><strong>${esc(u.name)}</strong><small>@${esc(u.username)} ${u.admin?'· Admin del workspace':''}${u.blocked?' · Bloqueado':''}</small></div>${admin&&u.id!==data.user.id?`<button type="button" class="workspace-block ${u.blocked?'':'workspace-block-danger'}" onclick="setWorkspaceBlocked(${u.id},${!u.blocked},this)">${u.blocked?'Desbloquear':'Bloquear'}</button>`:''}</div>`).join('')}</div>${admin?'<button type="button" class="primary wide" onclick="newUser()">＋ Agregar persona</button>':''}</div>`);
  if(admin)$('#modal .dialog').insertAdjacentHTML('beforeend',`<div class="detail-section"><h3 class="slack-heading">${slackIcon()} Slack</h3><button type="button" class="outline slack-entry" onclick="slackSettings()">Configurar Slack</button></div>`);
  if(admin)$('#workspaceForm').onsubmit=async e=>{e.preventDefault();let button=e.target.querySelector('button');button.disabled=true;try{await api('workspace/update',Object.fromEntries(new FormData(e.target)));await refresh();workspaceSettings();toast('Nombre actualizado')}catch(err){button.disabled=false;toast(err.message)}};
}
function slackPreferenceFields(project,preference={}){
  let fields=[['assignment','Asignaciones'],['comment','Comentarios']];if(project.id!=null&&(data.user.admin||project.role==='admin'))fields.push(['completion','Tareas completadas']);
  return `<fieldset class="detail-section" data-project="${project.id??''}"><legend>${esc(project.name)}</legend>${fields.map(([key,label])=>`<label class="check"><input type="checkbox" name="${key}" ${preference[key]?'checked':''}> ${label}</label>`).join('')}</fieldset>`;
}
function slackPreferenceTable(projects,preferences){
  let columns=[['assignment','Asignaciones'],['comment','Comentarios'],['completion','Tareas completadas']];
  return `<div class="slack-table-wrap"><table class="slack-preferences"><thead><tr><th scope="col">Proyecto</th>${columns.map(([,label])=>`<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${projects.map(project=>{let pref=preferences.find(p=>p.project_id===project.id)||{};return `<tr data-project="${project.id??''}"><th scope="row">${esc(project.name)}</th>${columns.map(([key,label])=>`<td>${key==='completion'&&(project.id==null||!data.user.admin&&project.role!=='admin')?'':`<input type="checkbox" name="${key}" aria-label="${esc(label+' · '+project.name)}" ${pref[key]?'checked':''}>`}</td>`).join('')}</tr>`}).join('')}</tbody></table></div>`;
}
function slackSettings(){
  let dialog=document.createElement('dialog'),returnFocus=document.activeElement,previous=document.querySelector('#modal .overlay'),dirty=false,busy=false;
  if(previous)previous.hidden=true;
  dialog.className='dialog automations slack-dialog';dialog.setAttribute('aria-labelledby','slackTitle');
  dialog.innerHTML=`<div class="dialog-head"><h2 id="slackTitle" class="slack-heading">${slackIcon()} Slack</h2><button type="button" aria-label="Cerrar">×</button></div><p role="status">Cargando…</p>`;
  document.body.appendChild(dialog);dialog.showModal();
  function close(){if(busy)return;if(dirty&&!confirm('¿Descartar los cambios de Slack?'))return;dialog.close()}
  dialog.querySelector('button').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close()});dialog.addEventListener('close',()=>{dialog.remove();if(previous)previous.hidden=false;returnFocus?.focus()});
  async function load(){
    try{
      let status=await api('slack/status');if(!dialog.isConnected)return;
      let {configured,installation,account,preferences,summary}=status;
      let projects=[{id:null,name:'Sin proyecto'},...data.projects];
      dialog.innerHTML=`<div class="dialog-head"><h2 id="slackTitle">Slack</h2><button type="button" aria-label="Cerrar" data-action="close">×</button></div><p role="alert" id="slackError"></p><p>${!configured?'Slack no está configurado en el servidor.':installation?`Workspace conectado: <strong>${esc(installation.team_name)}</strong> (${esc(installation.team_id)})`:'Slack no está conectado al workspace.'}</p>${data.user.admin&&configured?`<button type="button" class="outline" data-action="${installation?'disconnect':'install/start'}">${installation?'Desconectar workspace':'Conectar workspace con Slack'}</button>`:''}${installation?`<div class="detail-section"><h3>Tu cuenta</h3><p>${account?`Vinculada: ${esc(account.slack_user_id)}`:'No vinculada a Slack.'}</p><button type="button" class="outline" data-action="${account?'unlink':'link/start'}">${account?'Desvincular cuenta':'Vincular cuenta'}</button> ${account?'<button type="button" class="outline" data-action="test">Enviar prueba</button>':''}</div>`:''}${account&&installation?`<form id="slackForm"><h3>Notificaciones por proyecto</h3>${projects.map(p=>slackPreferenceFields(p,preferences.find(x=>x.project_id===p.id))).join('')}<div class="detail-section"><h3>Resumen diario</h3><label class="check"><input type="checkbox" name="enabled" ${summary.enabled?'checked':''}> Enviar resumen diario</label><label>Hora del workspace<select name="hour">${Array.from({length:24},(_,h)=>`<option value="${h}" ${Number(summary.hour)===h?'selected':''}>${String(h).padStart(2,'0')}:00</option>`).join('')}</select></label><fieldset><legend>Días del resumen (hora del workspace)</legend>${['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo'].map((day,i)=>`<label class="check"><input type="checkbox" name="weekday" value="${i}" ${summary.weekdays.includes(i)?'checked':''}> ${day}</label>`).join('')}</fieldset></div><div class="dialog-actions"><button type="submit" class="primary">Guardar preferencias</button></div></form>`:''}`;
      let heading=dialog.querySelector('#slackTitle');heading.classList.add('slack-heading');heading.insertAdjacentHTML('afterbegin',slackIcon());
      let form=dialog.querySelector('#slackForm');if(form){let header=document.createElement('div');header.className='slack-fixed-header';while(dialog.firstElementChild!==form)header.appendChild(dialog.firstElementChild);form.before(header);let footer=form.querySelector('.dialog-actions');footer.classList.add('slack-fixed-footer');footer.insertAdjacentHTML('afterbegin','<button type="button" class="outline" data-action="close">Cancelar</button>');let groups=[...form.querySelectorAll('[data-project]')];groups[0].insertAdjacentHTML('beforebegin',slackPreferenceTable(projects,preferences));groups.forEach(group=>group.remove());form.addEventListener('change',()=>dirty=true);form.onsubmit=async e=>{e.preventDefault();let preferences=[...form.querySelectorAll('[data-project]')].map(group=>({project_id:group.dataset.project?Number(group.dataset.project):null,assignment:group.querySelector('[name=assignment]').checked,comment:group.querySelector('[name=comment]').checked,completion:group.querySelector('[name=completion]')?.checked||false}));let summary={enabled:form.elements.enabled.checked,hour:Number(form.elements.hour.value),weekdays:[...form.querySelectorAll('[name=weekday]:checked')].map(x=>Number(x.value))};await run('preferences',{preferences,summary},false)}}
    }catch(err){if(dialog.isConnected)dialog.querySelector('[role=status], #slackError').textContent=err.message}
  }
  async function run(action,body={},reload=true){
    if(busy)return;if(dirty&&action!=='preferences'&&action!=='test'&&!confirm('¿Descartar los cambios de Slack?'))return;
    busy=true;dialog.querySelectorAll('button, input, select').forEach(b=>b.disabled=true);let error=dialog.querySelector('#slackError');if(error)error.textContent='';
    try{let result=await api('slack/'+action,body);if(!dialog.isConnected)return;if(action.endsWith('/start')){location.assign(result.url);return}if(action==='preferences'){dirty=false;toast('Preferencias de Slack guardadas')}else if(action==='test')toast('Prueba de Slack encolada para envío');else{dirty=false;if(reload)await load()}}
    catch(err){if(dialog.isConnected){if(error)error.textContent=err.message;else toast(err.message)}}finally{busy=false;if(dialog.isConnected)dialog.querySelectorAll('button, input, select').forEach(b=>b.disabled=false)}
  }
  dialog.addEventListener('click',e=>{let action=e.target.closest('[data-action]')?.dataset.action;if(!action)return;if(action==='close')close();else if((action==='unlink'||action==='disconnect')&&!confirm(action==='unlink'?'¿Desvincular tu cuenta de Slack?':'¿Desconectar Slack del workspace?'))return;else run(action)});
  load();
}
async function setWorkspaceBlocked(id,blocked,button){let user=data.users.find(u=>u.id===id);if(!user)return;if(blocked&&!confirm(`¿Bloquear a «${user.name}» del workspace? No podrá acceder a sus datos hasta que lo desbloquees.`))return;button.disabled=true;try{await api('workspace/block',{user_id:id,blocked});await refresh();workspaceSettings();toast(blocked?'Usuario bloqueado':'Usuario desbloqueado')}catch(err){button.disabled=false;toast(err.message)}}
function newUser(){
  modal(`<div class="dialog-head"><h2>Agregar persona</h2><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div><form id="newUserForm"><label>Nombre<input name="name" required></label><label>Usuario<input name="username" required></label><label>Contraseña temporal<input name="password" type="password" minlength="8" required></label><p class="muted">Comparte esta contraseña con la persona; deberá cambiarla al ingresar.</p><label class="check"><input type="checkbox" name="admin"> Administrador del workspace</label><div class="dialog-actions"><button type="button" class="outline" onclick="workspaceSettings()">Cancelar</button><button class="primary">Crear persona</button></div></form>`);
  let form=$('#newUserForm');form.elements.name.focus();form.onsubmit=async e=>{e.preventDefault();let button=form.querySelector('button[type=submit],button:not([type])'),cancel=form.querySelector('.outline');button.disabled=true;cancel.disabled=true;
    try{await api('users',Object.fromEntries(new FormData(form)))}catch(err){button.disabled=false;cancel.disabled=false;toast(err.message);return}
    try{await refresh();if(form.isConnected)workspaceSettings();toast('Persona creada correctamente')}catch(err){if(form.isConnected)closeModal();toast('Persona creada, pero no se pudo actualizar la lista')}
  };
}
async function setMember(id,role,select){let projectId=active,p=data.projects.find(x=>x.id===projectId),previous=p.members.find(m=>m.id===id)?.role||'remove';select.disabled=true;try{await api('members',{project_id:projectId,user_id:id,role});await refresh();let updated=data.projects.find(x=>x.id===projectId);if(!updated||updated.role!=='admin'){boardView='board';closeModal();render();toast('Tu acceso al proyecto cambió')}else{select.disabled=false;toast('Acceso actualizado')}}catch(e){select.value=previous;select.disabled=false;toast(e.message)}}

function taskPropertyVisual(field,t,editable){
  let arrow=editable?' <span class="quick-chevron">⌄</span>':'';
  if(field==='assignee_id'){let user=data.users.find(u=>u.id===t.assignee_id);return `<span class="detail-person">${user?avatar(user.name):'<span class="avatar empty-avatar">＋</span>'}<span>${user?esc(user.name):'Sin asignar'}</span>${arrow}</span>`}
  if(field==='due_date')return `<span class="date-value">◷ &nbsp; ${esc(t.due_date||'Sin fecha')}</span>`;
  if(field==='priority')return `<span class="priority ${t.priority==='Alta'?'high':t.priority==='Baja'?'low':''}">${esc(t.priority)}${arrow}</span>`;
  if(field==='type')return `<span class="detail-type">${esc(taskTypeLabel(t.task_type,t))}${arrow}</span>`;
  let status=taskStatus(t);return status?`<span class="status-badge" style="--status-color:${esc(status.color)}"><i></i><span class="status-name">${esc(status.name)}</span>${arrow}</span>`:'';
}
function renderTaskProperties(t,editable){
  let disabled=editable?'':'disabled',change=field=>`onchange="saveTaskField(${t.id},'${field}',this)"`,visual=field=>`<span class="detail-value" aria-hidden="true">${taskPropertyVisual(field,t,editable)}</span>`;
  let fields=[
    ['assignee_id','Responsable',`<input type="hidden" id="taskAssigneeSelect" value="${t.assignee_id??''}">`],
    ['due_date','Fecha límite',`<input name="due_date" type="date" aria-label="Fecha límite" value="${esc(t.due_date||'')}" ${change('due_date')} ${disabled} onclick="if(typeof this.showPicker==='function'){try{this.showPicker();event.preventDefault()}catch(e){}}">`],
    ['priority','Prioridad',`<input type="hidden" id="taskPrioritySelect" value="${esc(t.priority)}">`],
    ['type','Tipo de tarea',`<select id="taskTypeSelect" aria-label="Tipo de tarea" ${change('type')} ${disabled}>${taskTypeOptions(t).map(type=>`<option value="${type.id}" ${type.id===t.type_id?'selected':''}>${esc(taskTypeLabel(type,t))}</option>`).join('')}</select>`],
    ['status','Estado',`<input type="hidden" id="taskStatusSelect" value="${t.status_id}">`]
  ];
  return `<div class="task-properties">${fields.map(([field,label,input])=>`<div class="task-property" data-property="${field}"><span class="task-property-label">${label}</span>${field==='status'?`${statusButton(t,editable,true)}${input}`:field==='assignee_id'?`${assigneeButton(t,editable,true)}${input}`:field==='priority'?`${priorityButton(t,editable,true)}${input}`:`<span class="quick-control detail-control ${editable?'':'read-only'}">${visual(field)}${input}</span>`}</div>`).join('')}</div>`;
}
function updateTaskPropertyVisuals(form,task){for(let field of ['assignee_id','due_date','priority','type','status']){let value=form.querySelector(`[data-property="${field}"] .detail-value`);if(value)value.innerHTML=taskPropertyVisual(field,task,true)}}
async function openTask(id,tab=null){if(taskDraft&&!await flushTaskDraft())return;if(taskDraft){clearTimeout(taskDraft.timer);taskDraft=null}if(tab)taskTab=tab;else if(taskId!==id)taskTab='detalle';taskId=id;let t=data.tasks.find(x=>x.id===id), history, comments;try{[history,comments]=await Promise.all([api('history/'+id),api('comments/'+id)])}catch(e){return toast(e.message)}let project=data.projects.find(p=>p.id===active),editable=t.can_edit&&(!project||project.role!=='viewer');let parent=data.tasks.find(x=>x.id===t.parent_id);let subs=data.tasks.filter(x=>x.parent_id===id);modal(`<div class="dialog-head"><div class="eyebrow">DETALLE DE LA TAREA &nbsp; / &nbsp; #${t.id}</div><div class="task-head-actions">${editable?`<button type="button" class="task-link-button task-remove-icon" data-tooltip="Eliminar tarea" aria-label="Eliminar tarea" onclick="confirmDeleteTask(${id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 4h4M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg></button>`:''}<button type="button" class="task-link-button" data-tooltip="Copiar link" aria-label="Copiar link" onclick="copyTaskLink(${t.id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L11 5"/><path d="M14 11a5 5 0 0 0-7.1 0L4.8 13.1a5 5 0 0 0 7.1 7.1L13 19"/></svg></button><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div></div>${parent?`<button type="button" class="parent-task-link" onclick="openTask(${parent.id},'detalle')">← Tarea principal: ${esc(parent.title)}</button>`:''}<form id="taskForm"><input class="task-title" name="title" value="${esc(t.title)}" ${editable?'':'disabled'} required>${renderTaskProperties(t,editable)}</form><div class="detail-section"><div class="project-section-head"><h3>Proyectos</h3>${editable?`<span class="quick-control project-add-control"><span aria-hidden="true">＋ Agregar a un proyecto <span class="quick-chevron">⌄</span></span><select class="project-select" aria-label="Agregar a un proyecto" onchange="if(this.value)taskProject(${id},+this.value,false)"><option value="">Agregar a un proyecto</option>${data.projects.filter(p=>p.role!=='viewer'&&!t.placements.some(x=>x.project_id===p.id)).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></span>`:''}</div><div class="chips">${t.placements.map(x=>{let p=data.projects.find(p=>p.id===x.project_id);return p?`<span class="chip"><i style="background:${esc(p.color)}"></i>${esc(p.name)} ${editable&&p.role!=='viewer'?`<button onclick="taskProject(${id},${p.id},true)" title="Quitar del proyecto">×</button>`:''}</span>`:''}).join('')+(t.inherited_project_ids||[]).map(projectId=>{let p=data.projects.find(p=>p.id===projectId);return p?`<span class="chip" title="Heredado de la tarea principal"><i style="background:${esc(p.color)}"></i>${esc(p.name)} · heredado</span>`:''}).join('')||'<span class="muted">Sin proyecto</span>'}</div></div>${editable?'<div id="taskSaveStatus" class="task-save-status" role="status" aria-live="polite"></div>':''}<div class="task-tabs" role="tablist" aria-label="Secciones de la tarea" onkeydown="taskTabKeydown(event)"><button type="button" role="tab" id="task-tab-detalle" aria-controls="task-panel-detalle" onclick="setTaskTab('detalle')">Detalle</button><button type="button" role="tab" id="task-tab-subtareas" aria-controls="task-panel-subtareas" onclick="setTaskTab('subtareas')">Subtareas (${subs.length})</button><button type="button" role="tab" id="task-tab-comentarios" aria-controls="task-panel-comentarios" onclick="setTaskTab('comentarios')">Comentarios (${comments.length})</button><button type="button" role="tab" id="task-tab-actividad" aria-controls="task-panel-actividad" onclick="setTaskTab('actividad')">Actividad</button></div><section id="task-panel-detalle" role="tabpanel" aria-labelledby="task-tab-detalle"><div class="description-field"><label id="descriptionLabel">Descripción</label>${editable?`<div class="editor-toolbar" role="toolbar" aria-label="Formato de descripción"><button type="button" title="Negrita" aria-label="Negrita" onmousedown="event.preventDefault()" onclick="formatDescription('bold')"><b>N</b></button><button type="button" title="Cursiva" aria-label="Cursiva" onmousedown="event.preventDefault()" onclick="formatDescription('italic')"><i>C</i></button><button type="button" title="Lista con viñetas" aria-label="Lista con viñetas" onmousedown="event.preventDefault()" onclick="formatDescription('insertUnorderedList')">☷</button><button type="button" title="Lista numerada" aria-label="Lista numerada" onmousedown="event.preventDefault()" onclick="formatDescription('insertOrderedList')">1.</button><button type="button" title="Insertar enlace" aria-label="Insertar enlace" onmousedown="event.preventDefault()" onclick="addDescriptionLink()">↗</button></div>`:''}<div id="descriptionEditor" class="description-editor ${editable?'':'read-only'}" role="textbox" aria-labelledby="descriptionLabel" aria-multiline="true" contenteditable="${editable}" data-placeholder="Agrega más detalles sobre esta tarea...">${t.description_format==='html'?t.description:esc(t.description).replace(/\n/g,'<br>')}</div></div></section><section id="task-panel-subtareas" role="tabpanel" aria-labelledby="task-tab-subtareas"><div class="detail-section"><h3>Subtareas</h3><div id="subtaskList">${renderSubtasks(subs)}</div>${editable?`<button type="button" id="addSubtask" class="text-button" onclick="newSubtask()">＋ Agregar subtarea</button><form id="subtaskForm" class="subtask-form" hidden><input name="title" aria-label="Nombre de la subtarea" placeholder="Nombre de la subtarea" required><button type="submit" class="primary">Crear</button></form>`:''}</div></section><section id="task-panel-comentarios" role="tabpanel" aria-labelledby="task-tab-comentarios"><div class="detail-section"><h3>Comentarios</h3><div id="commentsList">${renderComments(comments,editable)}</div>${editable?`<form id="commentForm"><label>Nuevo comentario<textarea name="body" rows="3" maxlength="10000" required placeholder="Escribe un comentario..."></textarea></label><button class="primary">Comentar</button></form>`:'<p class="muted">Tienes acceso de solo lectura.</p>'}</div></section><section id="task-panel-actividad" role="tabpanel" aria-labelledby="task-tab-actividad"><div class="detail-section"><h3>Actividad</h3><div id="activityList">${history.map(h=>`<div class="activity">${avatar(h.actor)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('')}</div></div></section>`,true);setTaskTab(taskTab);if(editable){setupSubtaskForm(id);$('#descriptionEditor').addEventListener('paste',pasteDescription);$('#commentForm').onsubmit=async e=>{e.preventDefault();let form=e.target;try{await api('comments',{task_id:id,body:form.elements.body.value});form.reset();let task=data.tasks.find(t=>t.id===id);if(task){task.comment_count=(task.comment_count||0)+1;$('#task-tab-comentarios').textContent=`Comentarios (${task.comment_count})`;renderBoard()}$('#commentsList').innerHTML=renderComments(await api('comments/'+id),editable);toast('Comentario agregado')}catch(err){toast(err.message)}};setupTaskAutosave(id,t)}}
function setupTaskAutosave(id,task){
  let form=$('#taskForm'),title=form.elements.title,editor=$('#descriptionEditor'),status=$('#taskSaveStatus');
  let draft=taskDraft={id,title,editor,status,savedTitle:task.title,savedDescription:editor.innerHTML,titleVersion:task.title_version,descriptionVersion:task.description_version,timer:null,saving:null,conflict:false,denied:false};
  function schedule(delay){clearTimeout(draft.timer);status.textContent='Cambios sin guardar';draft.timer=setTimeout(()=>flushTaskDraft(),delay)}
  title.addEventListener('input',()=>schedule(1000));
  title.addEventListener('blur',()=>flushTaskDraft());
  editor.addEventListener('input',()=>schedule(2500));
  form.addEventListener('submit',e=>{e.preventDefault();title.blur();flushTaskDraft()});
}
async function flushTaskDraft(){
  let draft=taskDraft;if(!draft)return true;
  clearTimeout(draft.timer);
  if(draft.conflict||draft.denied)return false;
  if(draft.saving){let ok=await draft.saving;return ok&&taskDraft===draft?flushTaskDraft():ok}
  let work=(async()=>{
    while(taskDraft===draft){
      let title=draft.title.value.trim(),description=draft.editor.innerHTML,payload={id:draft.id};
      if(title&&title!==draft.savedTitle)payload.title=title;
      if(description!==draft.savedDescription){payload.description=description;payload.description_format='html'}
      if(!('title' in payload)&&!('description' in payload)){
        if(!title){draft.status.textContent='El nombre de la tarea es obligatorio';return false}
        return true;
      }
      if('title' in payload){if(draft.titleVersion==null){draft.status.textContent='El servidor no admite versiones; conserva tu borrador';return false}payload.title_version=draft.titleVersion}
      if('description' in payload){if(draft.descriptionVersion==null){draft.status.textContent='El servidor no admite versiones; conserva tu borrador';return false}payload.description_version=draft.descriptionVersion}
      draft.status.textContent='Guardando…';
      let result;
      try{result=await api('task/update',payload)}catch(err){if(taskDraft===draft){if(err.conflict){draft.conflict=true;showTaskConflict(draft)}else{if(err.status===401||err.status===403)draft.denied=true;draft.status.textContent=draft.denied?'Sin acceso; conserva y copia tu borrador':'No se pudieron guardar los cambios';toast(err.message)}}return false}
      if(('title' in payload&&result.title_version==null)||('description' in payload&&result.description_version==null)){draft.denied=true;draft.status.textContent='El servidor no devolvió versiones; conserva tu borrador';return false}
      if('title' in payload)draft.titleVersion=result.title_version;
      if('description' in payload)draft.descriptionVersion=result.description_version;
      if('title' in payload)draft.savedTitle=title;
      if('description' in payload)draft.savedDescription=description;
      try{await refresh()}catch{}
      if(taskDraft!==draft)return true;
      draft.status.textContent='Guardado';
      try{await updateActivity(draft.id)}catch{}
    }
    return true;
  })();
  draft.saving=work;
  try{return await work}finally{if(draft.saving===work)draft.saving=null}
}
function showTaskConflict(draft){
  draft.status.textContent='Esta tarea cambió en el servidor. Elige cómo resolver el conflicto. ';
  for(let choice of ['Recargar','Sobrescribir']){
    let button=document.createElement('button');button.type='button';button.textContent=choice;draft.status.appendChild(button);
    button.onclick=async()=>{
      button.disabled=true;
      try{
        let snapshot=await api('data'),task=snapshot.tasks.find(t=>t.id===draft.id),project=snapshot.projects.find(p=>p.id===active);
        if(taskDraft!==draft)return;
        if(!task||!task.can_edit||project?.role==='viewer'||task.title_version==null||task.description_version==null)throw Error('Tarea sin acceso o versiones no disponibles');
        let titleDirty=draft.title.value.trim()!==draft.savedTitle,descriptionDirty=draft.editor.innerHTML!==draft.savedDescription;
        draft.titleVersion=task.title_version;draft.descriptionVersion=task.description_version;
        let temp=document.createElement('div');if(task.description_format==='html')temp.innerHTML=task.description||'';else temp.textContent=task.description||'';
        draft.savedTitle=task.title;draft.savedDescription=temp.innerHTML;
        if(choice==='Recargar'||!titleDirty)draft.title.value=task.title;
        if(choice==='Recargar'||!descriptionDirty)draft.editor.innerHTML=temp.innerHTML;
        draft.conflict=false;draft.status.textContent=choice==='Recargar'?'Actualizado':'Cambios sin guardar';
        if(choice==='Sobrescribir')await flushTaskDraft();
      }catch(err){button.disabled=false;toast(err.message)}
    };
  }
}
function confirmDeleteTask(id){
  let task=data.tasks.find(t=>t.id===id);if(!task?.can_edit)return;
  let dialog=document.createElement('dialog');dialog.className='dialog confirm-task-delete';dialog.setAttribute('aria-labelledby','deleteTaskTitle');dialog.setAttribute('aria-describedby','deleteTaskDescription');
  dialog.innerHTML=`<h2 id="deleteTaskTitle">¿Eliminar esta tarea?</h2><div id="deleteTaskDescription"><p>Se eliminará la tarea <strong>«${esc(task.title)}»</strong> de todos sus proyectos y se borrarán sus comentarios y actividad. Esta acción no se puede deshacer.</p><p>Sus subtareas se conservarán como tareas independientes y podrían dejar de ser visibles para algunos usuarios.</p></div><div class="dialog-actions"><button type="button" class="outline" data-cancel>Cancelar</button><button type="button" class="task-delete" data-delete>Eliminar tarea</button></div>`;
  $('#modal').appendChild(dialog);let cancel=dialog.querySelector('[data-cancel]'),button=dialog.querySelector('[data-delete]');
  async function cancelDialog(){if(dialog.dataset.closing)return;dialog.dataset.closing='true';dialog.inert=true;if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){dialog.classList.add('is-closing');await waitForModalAnimation(dialog)}if(dialog.open)dialog.close()}
  cancel.onclick=cancelDialog;dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('cancel',e=>{e.preventDefault();if(!button.disabled)cancelDialog()});
  button.onclick=async()=>{button.disabled=true;cancel.disabled=true;try{await api('task/delete',{id})}catch(err){button.disabled=false;cancel.disabled=false;toast(err.message);return}dialog.close();closeModal(true,true);taskId=null;if(/^\/t\//.test(location.pathname))setBoardUrl(active,true);try{await refresh();toast('Tarea eliminada')}catch(err){toast('Tarea eliminada, pero no se pudo actualizar el tablero')}};
  dialog.showModal();cancel.focus();
}
const taskTabAnimations=new WeakMap();
function setTaskTab(name){
  let previous=$('#modal .task-dialog [id^="task-tab-"][aria-selected="true"]'),dialog=previous?.closest('.task-dialog');
  let start=dialog?.getBoundingClientRect().height;taskTabAnimations.get(dialog)?.cancel();
  taskTab=name;
  for(let tab of ['detalle','subtareas','comentarios','actividad']){let selected=tab===name,button=$('#task-tab-'+tab),panel=$('#task-panel-'+tab);if(!button||!panel)return;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;panel.hidden=!selected}
  if(!dialog||previous.id==='task-tab-'+name||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  let end=dialog.getBoundingClientRect().height;
  if(Math.abs(start-end)>0.5){let animation=dialog.animate([{height:start+'px'},{height:end+'px'}],{duration:180,easing:'cubic-bezier(.2,.75,.3,1)'});taskTabAnimations.set(dialog,animation)}
}
function taskTabKeydown(e){let names=['detalle','subtareas','comentarios','actividad'],i=names.indexOf(taskTab);if(e.key==='ArrowRight')i=(i+1)%names.length;else if(e.key==='ArrowLeft')i=(i+names.length-1)%names.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=names.length-1;else return;e.preventDefault();setTaskTab(names[i]);$('#task-tab-'+names[i]).focus()}

function taskTypeOptions(t){let options=[t.task_type];if(!t.has_projects)options.push(data.base_type);for(let placement of t.placements){let p=data.projects.find(p=>p.id===placement.project_id);if(p&&p.role!=='viewer')options.push(...p.types)}return options.filter((x,i)=>x&&options.findIndex(y=>y?.id===x.id)===i)}
function taskTypeLabel(type,t){let options=taskTypeOptions(t),project=data.projects.find(p=>p.id===type.project_id),isCurrent=type.id===t.type_id;if(type.project_id==null)return type.name+(isCurrent&&t.has_projects?' · actual, tipo general':'');let linked=t.placements.some(p=>p.project_id===type.project_id);if(!linked&&isCurrent)return type.name+' · actual, de otro tablero';let distinctProjects=new Set(options.map(o=>o.project_id).filter(id=>id!=null));if(distinctProjects.size>1&&project)return type.name+' · '+project.name;return type.name}

async function saveTaskField(id,field,control){
  let task=data.tasks.find(t=>t.id===id);if(!task)return;
  let original=String(field==='type'?task.type_id:field==='status'?task.status_id:task[field]??''),value=control.value;
  let form=control.form,controls=[...form.querySelectorAll('.task-properties select,.task-properties input,.task-properties .status-trigger')];
  controls.forEach(item=>item.disabled=true);
  let draft=field==='assignee_id'?taskDraft:null;
  if(draft){draft.title.readOnly=true;draft.editor.contentEditable='false'}
  function unlock(){controls.forEach(item=>item.disabled=false);if(draft){draft.title.readOnly=false;draft.editor.contentEditable='true'}}
  if(draft&&!await flushTaskDraft()){control.value=original;unlock();return}
  try{
    if(field==='type')await api('task/type-status',{id,type_id:Number(value)});
    else if(field==='status')await api('task/type-status',{id,type_id:task.type_id,status_id:Number(value)});
    else await api('task/update',{id,[field]:value});
  }catch(err){control.value=original;unlock();toast(err.message);return}
  try{
    await refresh();let updated=data.tasks.find(t=>t.id===id);
    if(!updated){closeModal(true);toast('Cambio guardado; ya no tienes acceso a la tarea');return}
    if(!form.isConnected)return;
    if(field==='type'){
      form.querySelector('#taskTypeSelect').innerHTML=taskTypeOptions(updated).map(type=>`<option value="${type.id}" ${type.id===updated.type_id?'selected':''}>${esc(taskTypeLabel(type,updated))}</option>`).join('');
    }
    form.querySelector('#taskStatusSelect').value=String(updated.status_id);
    updateTaskPropertyVisuals(form,updated);
    unlock();
    try{await updateActivity(id)}catch{}
    toast(field==='type'?'Tipo cambiado; estado reiniciado':'Cambio guardado');
  }catch(err){unlock();toast('Cambio guardado, pero no se pudo actualizar el tablero')}
}
function renderTypesList(){let p=data.projects.find(x=>x.id===active),panel=$('#project-panel-tipos');if(!p||!panel)return;panel.innerHTML=`<p class="muted project-settings-note">Cada tipo tiene sus propios estados. Una tarea conserva su tipo y estado en todos sus proyectos.</p><div class="type-list">${p.types.map(t=>`<div class="type-list-item"><button type="button" class="type-row" onclick="editType(${t.id})"><span><strong>${esc(t.name)}</strong><small>${t.is_base?'Tipo base · ':''}${t.statuses.length} estados</small></span><span>Editar →</span></button>${t.is_base?'':`<button type="button" class="type-delete" aria-label="Eliminar tipo ${esc(t.name)}" title="Eliminar tipo" onclick="deleteType(${t.id},this)">×</button>`}</div>`).join('')}</div><button type="button" class="primary wide" onclick="newType()">＋ Crear tipo de tarea</button>`}
async function deleteType(id,button){let p=data.projects.find(x=>x.id===active),type=p?.types.find(t=>t.id===id);if(!type||type.is_base)return;if(!confirm(`¿Eliminar el tipo «${type.name}» y sus estados? Esta acción no se puede deshacer.`))return;button.disabled=true;try{await api('task-types/delete',{id});await refresh();renderTypesList();toast('Tipo eliminado')}catch(err){button.disabled=false;toast(err.message)}}
function newType(){let panel=$('#project-panel-tipos');panel.innerHTML=`<div class="type-editor-heading"><button type="button" class="text-button" onclick="renderTypesList()">← Tipos de tarea</button><h3>Nuevo tipo de tarea</h3></div><form id="newTypeForm"><label>Nombre del tipo<input name="name" required maxlength="100" placeholder="Ej. Incidencia"></label><div class="dialog-actions"><button type="button" class="outline" onclick="renderTypesList()">Cancelar</button><button class="primary">Crear</button></div></form>`;$('#newTypeForm input').focus();$('#newTypeForm').onsubmit=async e=>{e.preventDefault();try{let result=await api('task-types',{project_id:active,...Object.fromEntries(new FormData(e.target))});await refresh();editType(result.id)}catch(err){toast(err.message)}}}

function editType(id){let p=data.projects.find(x=>x.id===active);let original=p.types.find(x=>x.id===id);typeDraft=structuredClone(original);typeDraft.deleted_status_ids=[];renderTypeEditor()}
function renderTypeEditor(){let t=typeDraft;$('#project-panel-tipos').innerHTML=`<div class="type-editor-heading"><button type="button" class="text-button" onclick="renderTypesList()">← Tipos de tarea</button><h3>Editar tipo</h3></div><form id="typeForm"><label>Nombre del tipo<input name="name" required maxlength="100" value="${esc(t.name)}" oninput="typeDraft.name=this.value"></label><div class="status-heading"><div><strong>Estados</strong><p class="muted">Arrastra para cambiar el orden. Debe haber al menos un estado en curso y uno finalizado.</p></div></div><div class="status-list">${t.statuses.map((st,i)=>`<div class="status-row" draggable="true" ondragstart="statusDrag=${i};event.dataTransfer.effectAllowed='move'" ondragover="event.preventDefault()" ondrop="dropStatus(event,${i})"><span class="drag-handle" title="Arrastrar para ordenar">⠿</span><input aria-label="Nombre del estado" required maxlength="100" value="${esc(st.name)}" oninput="typeDraft.statuses[${i}].name=this.value"><input aria-label="Color del estado" type="color" value="${esc(st.color)}" oninput="typeDraft.statuses[${i}].color=this.value"><select aria-label="Categoría del estado" ${t.is_base?'disabled':''} onchange="typeDraft.statuses[${i}].is_done=+this.value"><option value="0" ${!st.is_done?'selected':''}>En curso</option><option value="1" ${st.is_done?'selected':''}>Finalizado</option></select><div class="status-arrows"><button type="button" title="Subir estado" aria-label="Subir estado" ${i===0?'disabled':''} onclick="moveStatus(${i},-1)">↑</button><button type="button" title="Bajar estado" aria-label="Bajar estado" ${i===t.statuses.length-1?'disabled':''} onclick="moveStatus(${i},1)">↓</button></div>${t.is_base?'':`<button type="button" class="status-delete" aria-label="Eliminar estado ${esc(st.name)}" title="Eliminar estado" onclick="removeStatus(${i})">×</button>`}</div>`).join('')}</div>${t.is_base?'':'<button type="button" class="text-button" onclick="addStatus()">＋ Agregar estado</button>'}<div class="dialog-actions"><button type="button" class="outline" onclick="renderTypesList()">Cancelar</button><button class="primary">Guardar</button></div></form>`;$('#typeForm').onsubmit=async e=>{e.preventDefault();try{await api('task-types/update',typeDraft);await refresh();renderTypesList();toast('Tipo actualizado')}catch(err){toast(err.message)}}}
function addStatus(){typeDraft.statuses.push({name:'Nuevo estado',color:'#8b85a3',is_done:0});renderTypeEditor()}
function removeStatus(index){let status=typeDraft.statuses[index];if(!status||typeDraft.is_base)return;let sameCategory=typeDraft.statuses.filter(s=>Boolean(s.is_done)===Boolean(status.is_done));if(sameCategory.length<=1){toast('Debe quedar al menos un estado en curso y uno finalizado');return}if(status.id)typeDraft.deleted_status_ids.push(status.id);typeDraft.statuses.splice(index,1);renderTypeEditor()}

function moveStatus(index,direction){let to=index+direction;if(to<0||to>=typeDraft.statuses.length)return;let [item]=typeDraft.statuses.splice(index,1);typeDraft.statuses.splice(to,0,item);renderTypeEditor()}
function dropStatus(e,index){e.preventDefault();e.stopPropagation();if(statusDrag===null||statusDrag===index)return;let [item]=typeDraft.statuses.splice(statusDrag,1);typeDraft.statuses.splice(index,0,item);statusDrag=null;renderTypeEditor()}

async function updateActivity(id){let list=$('#activityList');if(!list)return;let history=await api('history/'+id);if(!list.isConnected)return;list.innerHTML=history.map(h=>`<div class="activity">${avatar(h.actor)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('');collapseActivity(list)}

function renderComments(comments,editable=false){return comments.map(c=>`<div class="comment">${avatar(c.author)}<div class="comment-content"><div class="comment-heading"><span><strong>${esc(c.author)}</strong> <small>${new Date(c.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></span>${editable&&c.user_id===data.user.id?`<button type="button" class="comment-edit-button" onclick="editComment(this,${c.id})">Editar</button>`:''}</div><p class="comment-body">${esc(c.body)}</p></div></div>`).join('')||'<p class="muted">Aún no hay comentarios.</p>'}
function editComment(button,id){
  const content=button.closest('.comment-content'),body=content.querySelector('.comment-body'),editedTaskId=taskId;
  if(content.querySelector('.comment-edit-form'))return;
  const form=document.createElement('form');form.className='comment-edit-form';
  form.innerHTML='<textarea name="body" aria-label="Editar comentario" rows="3" maxlength="10000" required></textarea><div class="comment-edit-actions"><button type="button" class="outline">Cancelar</button><button type="submit" class="primary">Guardar</button></div>';
  const input=form.elements.body,cancel=form.querySelector('button[type=button]'),save=form.querySelector('button[type=submit]');
  input.value=body.textContent;body.hidden=true;button.hidden=true;body.after(form);input.focus();input.setSelectionRange(input.value.length,input.value.length);
  function finish(){form.remove();body.hidden=false;button.hidden=false;button.focus()}
  cancel.onclick=finish;
  form.onkeydown=e=>{if(e.key==='Escape'&&!save.disabled){e.preventDefault();e.stopPropagation();finish()}};
  form.onsubmit=async e=>{e.preventDefault();if(!input.value.trim()){input.focus();return}input.disabled=cancel.disabled=save.disabled=true;
    try{let result=await api('comments/update',{id,body:input.value});if(content.isConnected){body.textContent=result.body;finish()}toast('Comentario actualizado');if(taskId===editedTaskId)updateActivity(editedTaskId).catch(()=>{})}
    catch(err){input.disabled=cancel.disabled=save.disabled=false;toast(err.message);if(content.isConnected)input.focus()}
  };
}
function formatDescription(command,value=null){$('#descriptionEditor').focus();document.execCommand(command,false,value)}
function addDescriptionLink(){let url=prompt('Dirección del enlace (https://, http:// o mailto:)');if(!url)return;try{let parsed=new URL(url);if(!['https:','http:','mailto:'].includes(parsed.protocol))throw Error();formatDescription('createLink',url)}catch(e){toast('Ingresa una dirección válida que comience con https://, http:// o mailto:')}}
function pasteDescription(e){e.preventDefault();document.execCommand('insertText',false,e.clipboardData.getData('text/plain'))}

async function taskProject(id,p,remove){if(taskDraft&&!await flushTaskDraft())return;try{await api('task/project',{id,project_id:p,remove});await refresh();let task=data.tasks.find(t=>t.id===id);if(task){if(!task.has_projects&&active!=='mine'){active=0;boardView='board';filter='';if(!isTaskPath(location.pathname))setBoardUrl(0,true);render()}await openTask(id)}else{closeModal();toast('La tarea ya no está disponible para tu usuario')}}catch(e){toast(e.message)}}
function renderSubtasks(subs){return subs.map(s=>`<button type="button" class="subtask" onclick="openTask(${s.id})">◯ &nbsp; ${esc(s.title)} <span>→</span></button>`).join('')||'<p class="muted">Aún no hay subtareas.</p>'}
function newSubtask(){let form=$('#subtaskForm');if(!form)return;$('#addSubtask').hidden=true;form.hidden=false;form.elements.title.focus()}
function setupSubtaskForm(id){
  let form=$('#subtaskForm'),input=form.elements.title,button=form.querySelector('button'),add=$('#addSubtask');
  function cancel(){form.hidden=true;form.reset();add.hidden=false;add.focus()}
  form.addEventListener('keydown',e=>{if(e.key==='Escape'&&!button.disabled){e.preventDefault();e.stopPropagation();cancel()}});
  form.onsubmit=async e=>{e.preventDefault();let title=input.value.trim();if(!title){input.focus();return}button.disabled=true;input.disabled=true;
    try{await api('tasks',{title,parent_id:id})}catch(err){button.disabled=false;input.disabled=false;toast(err.message);return}
    form.reset();form.hidden=true;add.hidden=false;button.disabled=false;input.disabled=false;
    try{await refresh();if(form.isConnected&&taskId===id){let subs=data.tasks.filter(t=>t.parent_id===id);$('#subtaskList').innerHTML=renderSubtasks(subs);$('#task-tab-subtareas').textContent=`Subtareas (${subs.length})`;add.focus()}toast('Subtarea creada')}catch(err){toast('Subtarea creada, pero no se pudo actualizar el tablero')}
  };
}
let automationProject=null, automationRules=[], automationDraft=null;
const automationFields={column:'Columna',status:'Estado',type:'Tipo de tarea',completion:'Finalización',assignee_id:'Responsable',priority:'Prioridad',due_date:'Fecha de vencimiento',task_kind:'Clase de tarea'};
const automationTriggers={created:'Tarea creada',column:'Cambio de columna',status:'Cambio de estado',assignee_id:'Cambio de responsable',priority:'Cambio de prioridad',due_date:'Cambio de fecha de vencimiento'};
const automationActions={column:'Mover a columna',status:'Cambiar estado',assignee_id:'Asignar responsable',priority:'Cambiar prioridad',due_date:'Fijar vencimiento',clear_due_date:'Quitar vencimiento'};
const automationOps={eq:'es igual a',ne:'es distinto de',has:'tiene valor',empty:'está vacío',overdue:'está vencida',today:'es hoy',before:'es anterior a',after:'es posterior a'};
function automationOperators(field){return field==='due_date'?['eq','ne','has','empty','overdue','today','before','after']:field==='assignee_id'?['eq','ne','has','empty']:['eq','ne']}
function automationOptions(field){
  let p=automationProject;
  if(field==='column')return p.columns.map(x=>[String(x.id),x.name]);
  if(field==='type')return p.types.map(x=>[String(x.id),x.name]);
  if(field==='status')return p.types.flatMap(t=>t.statuses.map(s=>[String(s.id),`${t.name} · ${s.name}`]));
  if(field==='assignee_id')return [['none','Sin asignar'],...data.users.filter(u=>!u.blocked).map(u=>[String(u.id),u.name])];
  if(field==='priority')return ['Baja','Normal','Alta'].map(x=>[x,x]);
  if(field==='completion')return [['done','Finalizada'],['ongoing','En curso']];
  if(field==='task_kind')return [['parent','Tarea principal'],['subtask','Subtarea']];
  return [];
}
function automationSelect(options,value,attrs,placeholder='Selecciona una opción'){return `<select ${attrs}><option value="">${esc(placeholder)}</option>${options.map(([v,n])=>`<option value="${esc(v)}" ${String(value??'')===v?'selected':''}>${esc(n)}</option>`).join('')}${value&&!options.some(([v])=>v===String(value))?`<option value="${esc(value)}" selected>${esc(value)} (ya no disponible)</option>`:''}</select>`}
function automationValue(field,value,path,optional=false){
  let attrs=`aria-label="Valor de ${esc(automationFields[field]||field)}" onchange="${path}=this.value"`;
  if(field==='due_date')return `<input type="${optional?'text':'date'}" ${optional?'placeholder="Cualquier fecha, none o AAAA-MM-DD"':''} value="${esc(value)}" ${attrs}>`;
  return automationSelect(automationOptions(field),value,attrs,optional?'Cualquiera':'Selecciona una opción');
}
async function openAutomations(){
  let p=data.projects.find(x=>x.id===active);if(!p||!['admin','editor'].includes(p.role))return;
  try{let result=await api('automations?'+new URLSearchParams({project_id:p.id}));if(active!==p.id)return;automationProject=p;automationRules=result.rules;automationDraft=null;renderAutomations()}catch(e){toast(e.message)}
}
function automationHelp(){return `<details class="automation-help"><summary>Ayuda y referencia de automatizaciones</summary><div>
  <h3>Cómo funcionan</h3><p>Las reglas pertenecen a este tablero y se evalúan en el orden mostrado. Un gatillante inicia la regla; todas sus condiciones deben cumplirse y sus acciones se aplican en orden. Una acción puede gatillar otras reglas; cada regla se ejecuta como máximo una vez por cadena, con un límite de 100 eventos y 100 reglas por cadena.</p>
  <h3>Gatillantes</h3><p><b>Tarea creada:</b> al crear una tarea. <b>Columna, estado, responsable, prioridad y fecha:</b> al cambiar el campo. «Desde» y «Hasta» son opcionales: vacío significa cualquier valor. En responsable o fecha usa <code>none</code> para sin asignar o sin fecha; en fecha escribe AAAA-MM-DD.</p>
  <h3>Condiciones</h3><p>Columna, estado, tipo, finalización, responsable, prioridad, fecha y clase de tarea admiten igualdad y desigualdad. Finalización distingue finalizada (<code>done</code>) y en curso (<code>ongoing</code>) según el estado de la tarea. Responsable y fecha también admiten tiene valor y está vacío; fecha admite además vencida, hoy, anterior y posterior. Las condiciones sin valor no necesitan comparación. Clase distingue tarea principal (<code>parent</code>) y subtarea (<code>subtask</code>). Para comparar sin asignar o sin fecha usa <code>none</code>.</p>
  <h3>Acciones</h3><p>Mover de columna, cambiar estado, asignar responsable, cambiar prioridad, fijar o quitar vencimiento. Indica una fecha AAAA-MM-DD o un número entero de días desde hoy entre -3650 y 3650: 0 es hoy, positivo es futuro y negativo pasado. «Sin asignar» usa <code>none</code>.</p>
  <h3>Tableros, tipos y columnas</h3><p>Solo se evalúan las reglas del tablero al que pertenece el tipo de la tarea, y únicamente si la tarea tiene una ubicación explícita en ese tablero. Estar en otro tablero no activa sus reglas por sí solo. Las columnas pertenecen al tablero; sus identificadores son distintos aunque tengan el mismo nombre. Los estados pertenecen a un tipo: elige un estado compatible con el tipo de la tarea. El tipo y estado de una tarea se comparten entre sus tableros. Si se elimina una columna, tipo, estado o usuario, revisa las reglas que lo usaban.</p></div></details>`}
function renderAutomations(){
  let admin=automationProject.role==='admin';
  modal(`<div class="automations"><div class="dialog-head"><div><div class="eyebrow">${esc(automationProject.name)}</div><h2>Automatizaciones</h2></div><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div>${automationHelp()}<div id="automationContent">${automationDraft?automationEditor():`<p class="muted">${admin?'Administra las reglas de este tablero.':'Consulta las reglas de este tablero; solo un administrador puede modificarlas.'}</p><div class="automation-list">${automationRules.map((r,i)=>`<div class="automation-rule"><div><strong>${esc(r.name)}</strong><small>${r.enabled?'Activa':'Pausada'} · ${esc(automationTriggers[r.trigger?.type]||r.trigger?.type)}</small></div>${admin?`<div class="automation-controls"><button type="button" onclick="moveAutomation(${i},-1)" ${i===0?'disabled':''} aria-label="Subir ${esc(r.name)}">↑</button><button type="button" onclick="moveAutomation(${i},1)" ${i===automationRules.length-1?'disabled':''} aria-label="Bajar ${esc(r.name)}">↓</button><button type="button" onclick="editAutomation(${i})">Editar</button><button type="button" onclick="toggleAutomation(${i})">${r.enabled?'Pausar':'Activar'}</button><button type="button" onclick="deleteAutomation(${i})">Eliminar</button></div>`:`<button type="button" onclick="editAutomation(${i})">Ver</button>`}</div>`).join('')||'<p class="muted">Todavía no hay automatizaciones.</p>'}</div>${admin?'<button type="button" class="primary" onclick="editAutomation(-1)">＋ Crear automatización</button>':''}`}</div></div>`,true);
  let dialog=$('#modal .dialog');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label','Automatizaciones');dialog.querySelector('button, summary')?.focus();
  dialog.querySelectorAll('.automation-rule').forEach((element,i)=>{let reason=automationRules[i].disabled_reason;if(reason){let note=document.createElement('small');note.className='automation-disabled-reason';note.textContent='Desactivada automáticamente: '+reason;element.firstElementChild.appendChild(note)}});
  if(['admin','editor'].includes(automationProject.role)){
    let section=document.createElement('section');section.className='automation-runs';section.innerHTML='<h3>Últimas ejecuciones</h3><div aria-live="polite">Cargando ejecuciones…</div>';dialog.querySelector('.automations').appendChild(section);loadAutomationRuns(section,automationProject.id);
  }
  dialog.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();closeModal()}else if(e.key==='Tab'){let nodes=[...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, textarea:not(:disabled)')].filter(x=>x.getClientRects().length);if(!nodes.length)return;let first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};
}
async function loadAutomationRuns(section,projectId){
  let content=section.querySelector('[aria-live]');content.textContent='Cargando ejecuciones…';
  try{
    let result=await api('automations/runs?'+new URLSearchParams({project_id:projectId}));if(!section.isConnected)return;
    content.innerHTML=result.runs.map(run=>{let task=data?.tasks.find(t=>t.id===run.task_id),label=run.task_id==null?'Tarea eliminada':task?`${task.title} (#${run.task_id})`:`Tarea #${run.task_id}`;return `<div class="automation-run"><strong>${esc(run.rule_name)}</strong><span>${esc(label)} · ${esc(run.outcome==='success'?'Correcta':run.outcome==='error'?'Error':run.outcome)} · ${esc(run.created_at)}</span>${run.error?`<p>${esc(run.error)}</p>`:''}</div>`}).join('')||'<p class="muted">Todavía no hay ejecuciones.</p>';
  }catch(e){if(!section.isConnected)return;content.textContent='No se pudieron cargar las ejecuciones: '+e.message+' ';let retry=document.createElement('button');retry.type='button';retry.className='text-button';retry.textContent='Reintentar';retry.onclick=()=>loadAutomationRuns(section,projectId);content.appendChild(retry)}
}
function editAutomation(i){automationDraft=i<0?{project_id:automationProject.id,name:'',enabled:true,trigger:{type:'created'},conditions:[],actions:[],position:automationRules.length}:JSON.parse(JSON.stringify(automationRules[i]));automationDraft.project_id=automationProject.id;renderAutomationContent()}
function renderAutomationContent(){let el=$('#automationContent');el.innerHTML=automationDraft?automationEditor():'';if(automationDraft)el.querySelector('input, select, button')?.focus()}
function automationEditor(){
  let r=automationDraft,admin=automationProject.role==='admin',t=r.trigger,hasValue=op=>['eq','ne','before','after'].includes(op);
  return `<form id="automationForm" onsubmit="saveAutomation(event)"><fieldset ${admin?'':'disabled'}><label>Nombre<input required maxlength="120" value="${esc(r.name)}" oninput="automationDraft.name=this.value"></label><label class="check"><input type="checkbox" ${r.enabled?'checked':''} onchange="automationDraft.enabled=this.checked"> Regla activa</label><h3>Gatillante</h3><label>Cuando ${automationSelect(Object.entries(automationTriggers),t.type,'required onchange="automationDraft.trigger={type:this.value};renderAutomationContent()"')}</label>${t.type!=='created'?`<div class="automation-grid"><label>Desde ${automationValue(t.type,t.from||'','automationDraft.trigger.from',true)}</label><label>Hasta ${automationValue(t.type,t.to||'','automationDraft.trigger.to',true)}</label></div>`:''}<h3>Condiciones</h3><p class="muted">Deben cumplirse todas; sin condiciones se aplica siempre.</p>${r.conditions.map((c,i)=>`<div class="automation-row"><label>Campo ${automationSelect(Object.entries(automationFields),c.field,`required onchange="automationDraft.conditions[${i}]={field:this.value,op:'eq',value:''};renderAutomationContent()"`)}</label><label>Operador ${automationSelect(automationOperators(c.field).map(o=>[o,automationOps[o]]),c.op,`required onchange="automationDraft.conditions[${i}].op=this.value;automationDraft.conditions[${i}].value='';renderAutomationContent()"`)}</label>${hasValue(c.op)?`<label>Valor ${c.field==='due_date'?`<input type="text" required placeholder="AAAA-MM-DD o none" value="${esc(c.value)}" onchange="automationDraft.conditions[${i}].value=this.value">`:automationValue(c.field,c.value,`automationDraft.conditions[${i}].value`)}</label>`:''}<button type="button" onclick="automationDraft.conditions.splice(${i},1);renderAutomationContent()" aria-label="Eliminar condición ${i+1}">×</button></div>`).join('')}<button type="button" class="text-button" onclick="automationDraft.conditions.push({field:'column',op:'eq',value:''});renderAutomationContent()" ${r.conditions.length>=20?'disabled':''}>＋ Agregar condición</button><h3>Acciones</h3>${r.actions.map((a,i)=>`<div class="automation-row"><label>Acción ${automationSelect(Object.entries(automationActions),a.type,`required onchange="automationDraft.actions[${i}]={type:this.value,value:''};renderAutomationContent()"`)}</label>${a.type==='clear_due_date'?'':`<label>Valor ${a.type==='due_date'?`<input type="text" required placeholder="Días relativos o AAAA-MM-DD" value="${esc(typeof a.value==='object'&&a.value!==null?a.value.days:a.value)}" onchange="automationDraft.actions[${i}].value=this.value" aria-label="Días relativos o fecha">`:automationValue(a.type,a.value,`automationDraft.actions[${i}].value`)}</label>`}<button type="button" onclick="automationDraft.actions.splice(${i},1);renderAutomationContent()" aria-label="Eliminar acción ${i+1}">×</button></div>`).join('')}<button type="button" class="text-button" onclick="automationDraft.actions.push({type:'column',value:''});renderAutomationContent()" ${r.actions.length>=20?'disabled':''}>＋ Agregar acción</button></fieldset><div class="dialog-actions"><button type="button" class="outline" onclick="backAutomations()">${admin?'Cancelar':'Volver'}</button>${admin?'<button type="submit" class="primary">Guardar</button>':''}</div></form>`
}
function backAutomations(){if(automationProject.role==='admin'&&!confirm('¿Descartar los cambios de la automatización?'))return;automationDraft=null;renderAutomations()}
async function saveAutomation(e){e.preventDefault();let r=automationDraft;if(automationProject.role!=='admin')return;if(!r.name.trim())return toast('Escribe un nombre para la regla');if(!r.actions.length)return toast('Agrega al menos una acción');if(r.conditions.some(c=>['eq','ne','before','after'].includes(c.op)&&!c.value)||r.actions.some(a=>a.type!=='clear_due_date'&&!String(a.value??'').trim()))return toast('Completa todos los valores');for(let v of [...(r.trigger.type==='due_date'?[r.trigger.from,r.trigger.to]:[]),...r.conditions.filter(c=>c.field==='due_date'&&['eq','ne','before','after'].includes(c.op)).map(c=>c.value)])if(v&&v!=='none'&&(!/^\d{4}-\d{2}-\d{2}$/.test(v)||!Number.isFinite(Date.parse(v+'T00:00:00Z'))||new Date(v+'T00:00:00Z').toISOString().slice(0,10)!==v))return toast('Usa una fecha válida AAAA-MM-DD o none');
  let payload={...r,name:r.name.trim(),trigger:{...r.trigger},conditions:r.conditions.map(c=>({...c,value:['eq','ne','before','after'].includes(c.op)?String(c.value):''})),actions:r.actions.map(a=>({...a}))};if(!payload.id)delete payload.id;for(let k of ['from','to'])if(!payload.trigger[k]||payload.trigger[k]==='*')delete payload.trigger[k];let button=e.submitter||e.target.querySelector('[type=submit],button.primary');button.disabled=true;try{await api('automations/save',payload);if(automationDraft===r){automationDraft=null;await reloadAutomations()}}catch(err){button.disabled=false;toast(err.message)}}
async function reloadAutomations(){let project=automationProject,dialog=$('#modal .automations'),result=await api('automations?'+new URLSearchParams({project_id:project.id}));if(automationProject!==project||!dialog?.isConnected)return;automationRules=result.rules;renderAutomations()}
async function moveAutomation(i,delta){let order=automationRules.map(r=>r.id),j=i+delta;if(j<0||j>=order.length)return;[order[i],order[j]]=[order[j],order[i]];try{await api('automations/reorder',{project_id:automationProject.id,order});await reloadAutomations()}catch(e){toast(e.message)}}
async function toggleAutomation(i){let r=automationRules[i];try{await api('automations/save',{id:r.id,project_id:automationProject.id,name:r.name,enabled:!r.enabled,trigger:r.trigger,conditions:r.conditions,actions:r.actions,position:r.position??i});await reloadAutomations()}catch(e){toast(e.message)}}
async function deleteAutomation(i){let r=automationRules[i];if(!confirm(`¿Eliminar la automatización «${r.name}»?`))return;try{await api('automations/delete',{project_id:automationProject.id,id:r.id});await reloadAutomations()}catch(e){toast(e.message)}}
let mineBoard='all',mineSearch='',mineIncludeDone=false,mineGroup='none';
function myTasks(){
  let priority={Alta:0,Normal:1,Baja:2};
  return data.tasks.filter(t=>t.assignee_id===data.user.id&&(mineIncludeDone||!taskStatus(t)?.is_done)&&matchesSearch(t,mineSearch)&&
    (mineBoard==='all'||(mineBoard==='none'?!t.has_projects:taskContextProjects(t).includes(Number(mineBoard)))))
    .sort((a,b)=>Number(!a.due_date)-Number(!b.due_date)||(a.due_date||'').localeCompare(b.due_date||'')||((priority[a.priority]??3)-(priority[b.priority]??3))||b.id-a.id);
}
function myTasksToday(now=new Date()){
  let parts=new Intl.DateTimeFormat('en-US',{timeZone:data.stats_timezone||'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now);
  return ['year','month','day'].map(type=>parts.find(part=>part.type===type).value).join('-');
}
function groupMyTasks(tasks,today){
  let end=new Date(today+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);let limit=end.toISOString().slice(0,10);
  let groups=[{name:'Vencidas',tasks:[]},{name:'Próximos 7 días',tasks:[]},{name:'Más adelante',tasks:[]},{name:'Sin fecha',tasks:[]},{name:'Finalizadas',tasks:[]}];
  for(let task of tasks){let index=taskStatus(task)?.is_done?4:!task.due_date?3:task.due_date<today?0:task.due_date<=limit?1:2;groups[index].tasks.push(task)}
  return groups.filter(group=>group.tasks.length);
}
function renderMyTaskResults(){
  let container=$('#myTaskResults');if(!container)return;let tasks=myTasks();
  let groups=mineGroup==='due'?groupMyTasks(tasks,myTasksToday()):[{name:'',tasks}];
  container.innerHTML=tasks.length?`<div class="my-tasks-table-wrap"><table class="my-tasks-table"><thead><tr><th scope="col">Tarea</th><th scope="col">Tablero</th><th scope="col">Estado</th><th scope="col">Fecha límite</th><th scope="col">Prioridad</th></tr></thead>${groups.map(group=>`<tbody>${group.name?`<tr class="my-tasks-group"><th scope="rowgroup" colspan="5">${esc(group.name)} <span class="count">${group.tasks.length}</span></th></tr>`:''}${group.tasks.map(t=>{
    let controls=taskQuickControls(t,t.can_edit),projects=taskContextProjects(t).map(id=>data.projects.find(p=>p.id===id)).filter(Boolean);
    return `<tr><td><button type="button" class="task-list-title" onclick="openTask(${t.id})">${esc(t.title)}</button>${t.parent_id?'<small>Subtarea</small>':''}</td><td>${projects.length?projects.map(p=>`<button type="button" class="my-tasks-project" onclick="selectProject(${p.id})">${esc(p.name)}</button>`).join(' '):'<span class="muted">Sin proyecto</span>'}</td><td>${controls.statusControl}</td><td>${controls.dateControl}</td><td>${controls.priorityControl}</td></tr>`;
  }).join('')}</tbody>`).join('')}</table></div>`:'<div class="empty-state">No hay tareas que coincidan con los filtros.</div>';
  let count=$('#myTaskCount');if(count)count.textContent=`${tasks.length} ${tasks.length===1?'tarea':'tareas'}`;
}
function renderMyTasks(){
  if(mineBoard!=='all'&&mineBoard!=='none'&&!data.projects.some(p=>String(p.id)===mineBoard))mineBoard='all';
  let main=$('#app main');if(!main)return;main.classList.add('board-main');
  main.innerHTML=`<header class="topbar"><div class="breadcrumb">${esc(data.workspace_name)} <span>/</span> <strong>Mis tareas</strong></div></header><section class="content stats-layout my-tasks"><div class="project-heading"><div class="project-heading-main"><div class="project-title-row"><h1>Mis tareas</h1></div><p>Las tareas asignadas a ti, en todos tus tableros.</p></div></div><div class="my-tasks-filters"><label>Buscar<input type="search" id="myTaskSearch" placeholder="Nombre o descripción" value="${esc(mineSearch)}" oninput="mineSearch=this.value;renderMyTaskResults()"></label><label>Tablero<select aria-label="Filtrar por tablero" onchange="mineBoard=this.value;renderMyTaskResults()"><option value="all" ${mineBoard==='all'?'selected':''}>Todos los tableros</option><option value="none" ${mineBoard==='none'?'selected':''}>Sin proyecto</option>${data.projects.map(p=>`<option value="${p.id}" ${mineBoard===String(p.id)?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><label class="my-tasks-check"><input type="checkbox" ${mineIncludeDone?'checked':''} onchange="mineIncludeDone=this.checked;renderMyTaskResults()"> Incluir finalizadas</label><span id="myTaskCount" class="muted" aria-live="polite"></span></div><div id="board" tabindex="0" role="region" aria-label="Lista de mis tareas"><div id="myTaskResults"></div></div></section>`;
  let group=document.createElement('label');group.textContent='Agrupar por';let select=document.createElement('select');select.setAttribute('aria-label','Agrupar tareas');select.innerHTML='<option value="none">Sin agrupar</option><option value="due">Vencimiento</option>';select.value=mineGroup;select.onchange=()=>{mineGroup=select.value;renderMyTaskResults()};group.appendChild(select);main.querySelector('.my-tasks-filters').insertBefore(group,main.querySelector('.my-tasks-check'));
  renderMyTaskResults();
}
function projectMemberStack(project){
  let count=project.members.length,shown=count>6?4:5;
  return project.members.slice(0,shown).map(member=>avatar(member.name)).join('')+(count>5||count===0?'<span class="avatar member-more" aria-hidden="true">···</span>':'');
}
function projectMemberButton(project){return `<button type="button" class="member-stack-trigger" aria-label="Ver usuarios del proyecto" title="Ver usuarios del proyecto" onclick="openProjectMembers()">${projectMemberStack(project)}</button>`}
function openProjectMembers(){
  let project=data.projects.find(p=>p.id===active);if(!project)return;
  if(project.role==='admin'){editProject();setProjectSettingsTab('usuarios');$('#project-tab-usuarios')?.focus();return}
  let roles={admin:'Administrador',editor:'Editor',viewer:'Lector'};
  modal(`<div class="dialog-head"><div><div class="eyebrow">${esc(project.name)}</div><h2>Usuarios del proyecto</h2></div><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div><div class="people-list">${project.members.map(member=>`<div class="person">${avatar(member.name)}<div><strong>${esc(member.name)}</strong><small>@${esc(member.username)} · ${esc(roles[member.role]||member.role)}</small></div></div>`).join('')||'<p class="muted">No hay usuarios asociados.</p>'}</div>`);
  let dialog=$('#modal .dialog');dialog?.setAttribute('role','dialog');dialog?.setAttribute('aria-modal','true');dialog?.setAttribute('aria-label','Usuarios del proyecto');dialog?.querySelector('button')?.focus();
}
const renderBoardWithoutAutomations=render;
render=function(){
  closeProjectFilters();renderBoardWithoutAutomations();let p=data.projects.find(x=>x.id===active),heading=$('.project-title-row');
  let caption=$('#mainSidebar .nav-caption');if(caption){let button=document.createElement('button');button.type='button';button.className='nav-item'+(active==='mine'?' selected':'');button.setAttribute('aria-label','Mis tareas');button.innerHTML=`<span>${icon('tasks')}</span><span class="nav-label">Mis tareas</span><em>${data.tasks.filter(t=>t.assignee_id===data.user.id&&!taskStatus(t)?.is_done).length}</em>`;button.onclick=()=>selectProject('mine');caption.after(button)}
  if(active==='mine'){renderMyTasks();return}
  if(p){
    let settings=heading?.querySelector('.project-settings-trigger');if(settings){settings.setAttribute('aria-label','Editar proyecto');settings.dataset.tooltip='Editar proyecto'}
    let stack=$('.member-stack');if(stack)stack.innerHTML=projectMemberButton(p);
    let first=$('.tabs > .tab');if(first){let tab=document.createElement('button');tab.type='button';tab.className='tab'+(boardView==='list'?' active':'');tab.textContent='☷  Lista';tab.onclick=()=>setBoardView('list');first.after(tab)}
    if(boardView==='board'||boardView==='list'){let tabs=$('.tabs');if(tabs){let button=document.createElement('button');button.type='button';button.className='project-filter-trigger';button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-expanded','false');button.onclick=()=>openProjectFilters(button);let right=tabs.querySelector('.tab-right');tabs.insertBefore(button,right||null);updateProjectFilterButton(p)}}
    if(boardView==='list'){
      let label=document.createElement('label');label.className='search top-search';label.innerHTML='<span aria-hidden="true">⌕</span><input type="search" aria-label="Buscar tareas del tablero por nombre o descripción" placeholder="Buscar en este tablero">';
      let input=label.querySelector('input');input.value=filter;input.oninput=()=>{filter=input.value;renderTaskList()};$('.topbar .today')?.before(label);
    }
  }
  if(p&&['admin','editor'].includes(p.role)&&heading){let button=document.createElement('button');button.type='button';button.className='automation-entry';button.textContent='⚡ Automatizaciones';button.onclick=openAutomations;heading.appendChild(button)}
};
start();

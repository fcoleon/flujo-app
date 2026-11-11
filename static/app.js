const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data=null, active=null, taskId=null, dragged=null, filter='', typeDraft=null, statusDrag=null, dragPlaceholder=null, dragSource=null, dragOffsetY=0, dragSaving=false;
let dragScrollTimer=null, dragScrollColumn=null, dragScrollY=0;
let taskTab='detalle', boardView='board', statsMonth='', statsType='', statsRequest=0, taskDraft=null;
const cardMotion=new WeakMap();
let columnDrag=null, columnGhost=null, columnSource=null, columnSaving=false;
const columnMotion=new WeakMap();
async function api(path,body){let r=await fetch('/api/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});if(r.status===429)throw Error('Demasiados intentos. Espera un momento y vuelve a intentarlo.');let d=await r.json();if(!r.ok){if(d.blocked)showBlocked();else if(d.password_change_required)showPasswordChange();throw Error(d.error||'Ocurrió un error')}return d}
function toast(s){$('#toast').textContent=s;$('#toast').classList.add('show');setTimeout(()=>$('#toast').classList.remove('show'),3000)}
function modal(html,wide=false){
  closeStatusMenu();
  const container=$('#modal'),replacing=!!container.querySelector('.overlay');
  container.innerHTML=`<div class="overlay${replacing?'':' modal-appear'}"><div class="dialog${wide?' task-dialog':''}">${html}</div></div>`;
  const overlay=$('#modal .overlay');let startedOnOverlay=false,endedOnOverlay=false;
  overlay.addEventListener('pointerdown',e=>{startedOnOverlay=e.isPrimary&&e.button===0&&e.target===overlay},true);
  overlay.addEventListener('pointerup',e=>{endedOnOverlay=e.isPrimary&&e.button===0&&e.target===overlay},true);
  overlay.addEventListener('click',e=>{if(startedOnOverlay&&endedOnOverlay&&e.target===overlay)closeModal();startedOnOverlay=endedOnOverlay=false});
}
function waitForModalAnimation(element){return new Promise(resolve=>{let timer=setTimeout(finish,240);function finish(e){if(e&&e.target!==element)return;element.removeEventListener('animationend',finish);clearTimeout(timer);resolve()}element.addEventListener('animationend',finish)})}
async function closeModal(force=false,animate=!force){
  const container=$('#modal'),overlay=container.querySelector('.overlay'),draft=taskDraft;
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
  return true;
}
function descriptionText(t){if(t.description_format!=='html')return t.description;let d=document.createElement('div');d.innerHTML=t.description;return d.textContent||''}
function normalizeSearch(value){return String(value??'').normalize('NFD').replace(/\p{M}/gu,'').toLocaleLowerCase('es').trim()}
function matchesSearch(t,query){let q=normalizeSearch(query);return !q||normalizeSearch(t.title).includes(q)||normalizeSearch(descriptionText(t)).includes(q)}
function avatar(name){return `<span class="avatar">${esc((name||'?').split(' ').map(x=>x[0]).join('').slice(0,2).toUpperCase())}</span>`}
function icon(name){return ({grid:'▦',home:'⌂',tasks:'☷',people:'♧',plus:'＋',search:'⌕',more:'···'})[name]||name}
async function refresh(){data=await api('data');if(active!==0&&!data.projects.some(p=>p.id===active))active=data.projects[0]?.id??(data.tasks.some(t=>!t.has_projects&&!t.parent_id)?0:null);render()}
async function start(){try{let s=await api('session');if(!s.user)return auth(s.needs_setup,s.allow_self_registration);if(s.blocked)return showBlocked();if(s.must_change_password)return showPasswordChange();await refresh();await openDeepLink()}catch(e){toast(e.message)}}
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
function selectProject(id){active=id;filter='';boardView='board';statsType='';if(/^\/t\//.test(location.pathname))history.replaceState(null,'','/');render()}
function setBoardView(view){boardView=view;render()}
function taskContextProjects(task){
  if(!task.has_projects)return [];
  return [...new Set([...task.placements.map(x=>x.project_id),...(task.inherited_project_ids||[])])].filter(id=>data.projects.some(p=>p.id===id));
}
function taskLink(task){let link=new URL('/t/'+task.public_id,location.origin),projects=taskContextProjects(task),project=projects.includes(active)?active:projects[0];if(project)link.searchParams.set('project',String(project));return link.href}
async function copyTaskLink(id){
  let task=data.tasks.find(t=>t.id===id);if(!task?.public_id)return toast('El enlace no está disponible');let url=taskLink(task);
  try{if(!navigator.clipboard?.writeText)throw Error('Portapapeles no disponible');await navigator.clipboard.writeText(url)}
  catch(e){let field=document.createElement('textarea');field.value=url;field.style.cssText='position:fixed;left:-10000px;top:0';document.body.appendChild(field);field.select();let ok=false;try{ok=document.execCommand('copy')}catch{}field.remove();if(!ok)return toast('No se pudo copiar el enlace')}
  toast('Enlace copiado');
}
async function openDeepLink(){
  let match=location.pathname.match(/^\/t\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);if(!match)return;
  let task=data.tasks.find(t=>t.public_id?.toLowerCase()===match[1].toLowerCase());
  if(!task){toast('Tarea no encontrada o sin acceso');return}
  let projects=taskContextProjects(task),raw=new URLSearchParams(location.search).get('project'),requested=raw&&/^\d+$/.test(raw)?Number(raw):null;
  active=requested&&projects.includes(requested)?requested:(projects[0]??0);boardView='board';filter='';render();await openTask(task.id);
  if(requested&&!projects.includes(requested))toast('El tablero del enlace no está disponible; se abrió la tarea en una vista accesible');
}

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
function renameColumn(id){let p=data.projects.find(x=>x.id===active),col=p.columns.find(x=>x.id===id);formModal('Renombrar columna',`<label>Nombre<input name="name" required maxlength="100" value="${esc(col.name)}"></label>`,b=>api('columns/rename',{column_id:id,...b}))}
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
function renderBoard(){
  saveBoardScroll();
  let p=data.projects.find(x=>x.id===active),orphan=active===0,board=$('#board');if(!board)return;let subtaskCounts=new Map();for(let task of data.tasks)if(task.parent_id)subtaskCounts.set(task.parent_id,(subtaskCounts.get(task.parent_id)||0)+1);
  let query=normalizeSearch(filter),matched=new Map(),match=t=>{if(!matched.has(t.id))matched.set(t.id,matchesSearch(t,query));return matched.get(t.id)};
  let scoped=orphan?data.tasks.filter(t=>!t.has_projects&&!t.parent_id):data.tasks.filter(t=>t.placements.some(x=>x.project_id===active));let found=scoped.filter(match).length,count=$('#boardCount');if(count)count.textContent=filter.trim()?`${found} de ${scoped.length} ${scoped.length===1?'tarea':'tareas'}`:`${scoped.length} ${scoped.length===1?'tarea':'tareas'}`;
  if(orphan){let ts=data.tasks.filter(t=>!t.has_projects&&!t.parent_id&&match(t));board.innerHTML=`<div class="orphan-list">${ts.map(t=>card(t,subtaskCounts.get(t.id)||0)).join('')||`<div class="empty-state">${filter.trim()?'No se encontraron tareas.':'No hay tareas sin proyecto.'}</div>`}</div>`;return}
  if(!p)return;let writable=p.role!=='viewer',collapsed=collapsedColumns(p.id);
  board.innerHTML=`<div class="board" data-project="${p.id}" ondragover="columnDragOver(event)" ondrop="dropColumn(event)">${p.columns.map((c,i)=>{
    let all=data.tasks.filter(t=>t.placements.some(x=>x.project_id===p.id&&x.column_id===c.id));
    let ts=all.filter(match).sort((a,b)=>a.placements.find(x=>x.project_id===p.id).position-b.placements.find(x=>x.project_id===p.id).position),isCollapsed=collapsed.includes(c.id);
    let handle=writable?`<span class="column-handle" draggable="true" ondragstart="startColumnDrag(event,${c.id})" ondragend="endColumnDrag()" data-tooltip="Arrastrar columna" aria-label="Arrastrar columna ${esc(c.name)}">⠿</span>`:'';
    if(isCollapsed)return `<div class="column column-collapsed" data-column="${c.id}"><div class="collapsed-controls">${handle}<button class="column-action" onclick="toggleColumn(${c.id})" data-tooltip="Expandir columna" aria-label="Expandir columna ${esc(c.name)}">›</button></div><div class="collapsed-label"><strong>${esc(c.name)}</strong><span class="count">${all.length}</span></div></div>`;
    return `<div class="column" data-column="${c.id}" ondragover="dragOver(event)" ondrop="dropTask(event,${c.id})"><div class="column-header"><div>${handle}<span class="status-dot status-${i%4}"></span><strong title="${esc(c.name)}">${esc(c.name)}</strong><span class="count">${ts.length}</span></div><div class="column-actions">${writable?`<button class="column-action" onclick="renameColumn(${c.id})" data-tooltip="Renombrar columna" aria-label="Renombrar columna ${esc(c.name)}">✎</button><button class="column-action" onclick="newTask(${c.id})" data-tooltip="Agregar tarea" aria-label="Agregar tarea a ${esc(c.name)}">＋</button>`:''}<button class="column-action" onclick="toggleColumn(${c.id})" data-tooltip="Contraer columna" aria-label="Contraer columna ${esc(c.name)}">‹</button></div></div><div class="cards" tabindex="0" aria-label="Tareas de ${esc(c.name)}">${ts.map(t=>card(t,subtaskCounts.get(t.id)||0)).join('')}</div>${writable?`<button class="add-card" onclick="newTask(${c.id})">＋ &nbsp; Agregar tarea</button>`:''}</div>`
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
function openStatusMenu(e,id,detail,field='status'){
  e.stopPropagation();let trigger=e.currentTarget,task=data.tasks.find(t=>t.id===id);
  if(statusMenuTrigger===trigger){closeStatusMenu();return}
  closeStatusMenu();if(!task||(field==='status'&&!task.task_type?.statuses?.length))return;
  let menu=document.createElement('div');menu.className='status-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label',field==='status'?'Cambiar estado':'Cambiar responsable');
  menu.innerHTML=field==='status'?task.task_type.statuses.map(st=>`<button type="button" role="menuitemradio" aria-checked="${st.id===task.status_id}" class="status-option ${st.id===task.status_id?'selected':''}" data-value="${st.id}"><i style="background:${esc(st.color)}"></i><span class="status-option-text">${esc(st.name)} <small>(${st.is_done?'Finalizado':'En curso'})</small></span>${st.id===task.status_id?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join(''):[{id:null,name:'Sin asignar'},...data.users].map(user=>`<button type="button" role="menuitemradio" aria-checked="${user.id===(task.assignee_id??null)}" class="status-option ${user.id===(task.assignee_id??null)?'selected':''}" data-value="${user.id??''}">${user.id===null?'<span class="avatar empty-avatar">＋</span>':avatar(user.name)}<span class="status-option-text">${esc(user.name)}</span>${user.id===(task.assignee_id??null)?'<span class="status-option-check" aria-hidden="true">✓</span>':''}</button>`).join('');
  document.body.appendChild(menu);statusMenu=menu;statusMenuTrigger=trigger;trigger.setAttribute('aria-expanded','true');
  let rect=trigger.getBoundingClientRect(),width=menu.offsetWidth,height=menu.offsetHeight;
  menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
  menu.style.top=Math.max(8,Math.min(rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6,window.innerHeight-height-8))+'px';
  menu.addEventListener('click',async event=>{
    event.stopPropagation();let option=event.target.closest('.status-option');if(!option)return;
    let value=option.dataset.value;closeStatusMenu(true);if(value===String((field==='status'?task.status_id:task.assignee_id)??''))return;
    if(detail){let control=trigger.closest('form')?.querySelector(field==='status'?'#taskStatusSelect':'#taskAssigneeSelect');if(control){control.value=value;await saveTaskField(id,field==='status'?'status':'assignee_id',control)}}
    else{trigger.disabled=true;try{if(field==='status')await api('task/type-status',{id,type_id:task.type_id,status_id:Number(value)});else await api('task/update',{id,assignee_id:value});await refresh()}catch(err){trigger.disabled=false;toast(err.message)}}
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
function quickSelect(label,content,options,onchange){return `<span class="quick-control" title="${esc(label)}" onclick="event.stopPropagation()" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation()"><span aria-hidden="true">${content}</span><select aria-label="${esc(label)}" onclick="event.stopPropagation()" onpointerdown="event.stopPropagation()" onchange="${onchange}">${options}</select></span>`}
function card(t,subtaskCount=0){
  let p=data.projects.find(x=>x.id===active), editable=t.can_edit&&(!p||p.role!=='viewer'), draggable=editable&&!!p;
  let priority=`<span class="priority ${t.priority==='Alta'?'high':t.priority==='Baja'?'low':''}">${esc(t.priority)}${editable?' <span class="quick-chevron">⌄</span>':''}</span>`;
  let priorityControl=editable?quickSelect('Cambiar prioridad de '+t.title,priority,['Baja','Normal','Alta'].map(v=>`<option value="${v}" ${t.priority===v?'selected':''}>${v}</option>`).join(''),`quickUpdate(event,${t.id},'priority')`):priority;
  let st=taskStatus(t), statusControl=st?statusButton(t,editable):'';
  let dateLabel=t.due_date?'◷ &nbsp;'+esc(t.due_date):'◷ &nbsp; Sin fecha';
  let dateControl=editable?`<span class="quick-control date-control" title="Cambiar fecha límite" onclick="event.stopPropagation()" onpointerdown="event.stopPropagation()" ondragstart="event.stopPropagation()"><span class="date-value" aria-hidden="true">${dateLabel}</span><input type="date" aria-label="Cambiar fecha límite de ${esc(t.title)}" value="${esc(t.due_date||'')}" onpointerdown="event.stopPropagation()" onclick="event.stopPropagation();if(typeof this.showPicker==='function'){try{this.showPicker();event.preventDefault()}catch(e){}}" onchange="quickUpdate(event,${t.id},'due_date')"></span>`:`<span class="date-value">${dateLabel}</span>`;
  let personControl=assigneeButton(t,editable);
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
  let id=dragged,p=data.projects.find(x=>x.id===active),source=dragSource;
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
    document.querySelectorAll('.column[data-column]').forEach(column=>{let count=column.querySelector('.count');if(count){let col=Number(column.dataset.column);count.textContent=column.classList.contains('column-collapsed')?data.tasks.filter(t=>t.placements.some(x=>x.project_id===p.id&&x.column_id===col)).length:column.querySelectorAll('.cards .card[data-id]').length}});
  }catch(err){toast(err.message);try{await refresh()}catch(refreshError){toast(refreshError.message)}}
  finally{dragSaving=false}
}

async function logout(){await api('logout',{});location.reload()}
function formModal(title,body,submit,footer=''){modal(`<div class="dialog-head"><h2>${title}</h2><button onclick="closeModal()">×</button></div><form id="modalForm">${body}<div class="dialog-actions">${footer?`<span class="profile-version">${esc(footer)}</span>`:''}<button type="button" class="outline" onclick="closeModal()">Cancelar</button><button class="primary">Guardar</button></div></form>`);$('#modalForm').querySelector('input:not([type=hidden]), textarea, select')?.focus();$('#modalForm').onsubmit=async e=>{e.preventDefault();try{await submit(Object.fromEntries(new FormData(e.target)));closeModal();await refresh()}catch(err){toast(err.message)}}}
function newProject(){formModal('Nuevo proyecto','<label>Nombre del proyecto<input name="name" required placeholder="Ej. Lanzamiento de producto"></label><label>Descripción<textarea name="description" placeholder="¿De qué trata este proyecto?"></textarea></label><label>Color<input type="color" name="color" value="#7765d7"></label>',async b=>{let r=await api('projects',b);active=r.id})}
function editProject(){
  let project=data.projects.find(p=>p.id===active);if(!project||project.role!=='admin')return;
  modal(`<div class="dialog-head"><div><div class="eyebrow">CONFIGURACIÓN DEL PROYECTO</div><h2>Editar tablero</h2></div><button type="button" onclick="closeModal()" aria-label="Cerrar">×</button></div><div class="task-tabs project-settings-tabs" role="tablist" aria-label="Configuración del tablero" onkeydown="projectSettingsKeydown(event)"><button type="button" role="tab" id="project-tab-tablero" aria-controls="project-panel-tablero" onclick="setProjectSettingsTab('tablero')">Tablero</button><button type="button" role="tab" id="project-tab-usuarios" aria-controls="project-panel-usuarios" onclick="setProjectSettingsTab('usuarios')">Usuarios</button><button type="button" role="tab" id="project-tab-tipos" aria-controls="project-panel-tipos" onclick="setProjectSettingsTab('tipos')">Tipos de tarea</button></div><section id="project-panel-tablero" role="tabpanel" aria-labelledby="project-tab-tablero"><form id="projectSettingsForm"><label for="projectName">Nombre del tablero</label><div class="project-name-field"><input type="color" name="color" value="${esc(project.color)}" aria-label="Color del tablero" title="Color del tablero"><input id="projectName" name="name" required maxlength="120" value="${esc(project.name)}"></div><label>Descripción<textarea name="description" maxlength="5000" rows="4" placeholder="¿De qué trata este tablero?">${esc(project.description)}</textarea></label><div class="dialog-actions"><button type="button" class="outline" onclick="closeModal()">Cancelar</button><button class="primary">Guardar</button></div></form></section><section id="project-panel-usuarios" role="tabpanel" aria-labelledby="project-tab-usuarios"><p class="muted project-settings-note">Elige quién puede acceder a este tablero y qué puede hacer. Los cambios de acceso se guardan de inmediato.</p><div class="people-list">${data.users.map(u=>{let m=project.members.find(m=>m.id===u.id);return `<div class="person">${avatar(u.name)}<div><strong>${esc(u.name)}</strong><small>${u.admin?'Admin del workspace · acceso total':'@'+esc(u.username)}</small></div>${u.admin?'<span class="role-label">Admin</span>':`<select aria-label="Acceso de ${esc(u.name)}" onchange="setMember(${u.id},this.value,this)"><option value="remove" ${!m?'selected':''}>Sin acceso</option><option value="viewer" ${m?.role==='viewer'?'selected':''}>Solo lectura</option><option value="editor" ${m?.role==='editor'?'selected':''}>Editor</option><option value="admin" ${m?.role==='admin'?'selected':''}>Admin</option></select>`}</div>`}).join('')}</div></section><section id="project-panel-tipos" role="tabpanel" aria-labelledby="project-tab-tipos"></section>`);
  renderTypesList();setProjectSettingsTab('tablero');
  $('#projectSettingsForm').onsubmit=async e=>{e.preventDefault();try{await api('projects/update',{project_id:project.id,...Object.fromEntries(new FormData(e.target))});closeModal();await refresh();toast('Tablero actualizado')}catch(err){toast(err.message)}};
}
function setProjectSettingsTab(name){for(let tab of ['tablero','usuarios','tipos']){let selected=tab===name,button=$('#project-tab-'+tab),panel=$('#project-panel-'+tab);if(!button||!panel)return;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;panel.hidden=!selected}}
function projectSettingsKeydown(e){let names=['tablero','usuarios','tipos'],i=names.findIndex(name=>$('#project-tab-'+name).getAttribute('aria-selected')==='true');if(e.key==='ArrowRight')i=(i+1)%names.length;else if(e.key==='ArrowLeft')i=(i+names.length-1)%names.length;else if(e.key==='Home')i=0;else if(e.key==='End')i=names.length-1;else return;e.preventDefault();setProjectSettingsTab(names[i]);$('#project-tab-'+names[i]).focus()}

function newOrphan(){formModal('Nueva tarea sin proyecto','<label>Nombre de la tarea<input name="title" required></label>',b=>api('tasks',b))}
function newColumn(){formModal('Nueva columna','<label>Nombre<input name="name" required placeholder="Nombre de la columna"></label>',b=>api('columns',{...b,project_id:active}))}
function newTask(col){let p=data.projects.find(x=>x.id===active);formModal('Nueva tarea',`<label>Nombre de la tarea<input name="title" required placeholder="¿Qué hay que hacer?"></label><label>Columna<select name="column_id">${p.columns.map(c=>`<option value="${c.id}" ${c.id===col?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><label>Tipo de tarea<select name="type_id">${p.types.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></label>`,b=>api('tasks',{...b,project_id:active}))}
function editProfile(){
  formModal('Mi perfil',`<label>Nombre<input name="name" value="${esc(data.user.name)}" autocomplete="name" maxlength="120" required></label><p class="muted profile-password-note">Para cambiar tu contraseña, completa los siguientes campos.</p><label>Contraseña actual<input name="current_password" type="password" autocomplete="current-password"></label><label>Nueva contraseña<input name="new_password" type="password" minlength="8" autocomplete="new-password" placeholder="Mínimo 8 caracteres"></label><label>Confirmar nueva contraseña<input name="confirm_password" type="password" autocomplete="new-password"></label>`,async b=>{await api('profile',b);toast('Perfil actualizado')},'Versión: '+(data.version||'No disponible'))
}
function showBlocked(){data=null;active=null;closeModal(true);$('#app').innerHTML=`<div class="blocked-screen"><div class="blocked-card"><div class="brand"><span class="brandmark">✳</span> flujo<span class="brand-dot">.</span></div><h1>Acceso bloqueado</h1><p>Has sido bloqueado de este workspace, contacta a tu admin para más información.</p><button class="outline" onclick="logout()">Cerrar sesión</button></div></div>`}
function workspaceSettings(){
  let admin=!!data.user.admin;
  modal(`<div class="dialog-head"><div><div class="eyebrow">WORKSPACE</div><h2>Configuración del workspace</h2></div><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div><form id="workspaceForm"><label>Nombre del workspace<input name="name" value="${esc(data.workspace_name)}" maxlength="120" required ${admin?'':'disabled'}></label>${admin?'<button class="primary" type="submit">Guardar nombre</button>':''}</form><div class="detail-section"><h3>Personas del workspace</h3><div class="people-list">${data.users.map(u=>`<div class="person">${avatar(u.name)}<div><strong>${esc(u.name)}</strong><small>@${esc(u.username)} ${u.admin?'· Admin del workspace':''}${u.blocked?' · Bloqueado':''}</small></div>${admin&&u.id!==data.user.id?`<button type="button" class="workspace-block ${u.blocked?'':'workspace-block-danger'}" onclick="setWorkspaceBlocked(${u.id},${!u.blocked},this)">${u.blocked?'Desbloquear':'Bloquear'}</button>`:''}</div>`).join('')}</div>${admin?'<button type="button" class="primary wide" onclick="newUser()">＋ Agregar persona</button>':''}</div>`);
  if(admin)$('#workspaceForm').onsubmit=async e=>{e.preventDefault();let button=e.target.querySelector('button');button.disabled=true;try{await api('workspace/update',Object.fromEntries(new FormData(e.target)));await refresh();workspaceSettings();toast('Nombre actualizado')}catch(err){button.disabled=false;toast(err.message)}};
}
async function setWorkspaceBlocked(id,blocked,button){let user=data.users.find(u=>u.id===id);if(!user)return;if(blocked&&!confirm(`¿Bloquear a «${user.name}» del workspace? No podrá acceder a sus datos hasta que lo desbloquees.`))return;button.disabled=true;try{await api('workspace/block',{user_id:id,blocked});await refresh();workspaceSettings();toast(blocked?'Usuario bloqueado':'Usuario desbloqueado')}catch(err){button.disabled=false;toast(err.message)}}
function newUser(){
  modal(`<div class="dialog-head"><h2>Agregar persona</h2><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div><form id="newUserForm"><label>Nombre<input name="name" required></label><label>Usuario<input name="username" required></label><label>Contraseña temporal<input name="password" type="password" minlength="8" required></label><p class="muted">Comparte esta contraseña con la persona; deberá cambiarla al ingresar.</p><label class="check"><input type="checkbox" name="admin"> Administrador del workspace</label><div class="dialog-actions"><button type="button" class="outline" onclick="workspaceSettings()">Cancelar</button><button class="primary">Crear persona</button></div></form>`);
  let form=$('#newUserForm');form.elements.name.focus();form.onsubmit=async e=>{e.preventDefault();let button=form.querySelector('button[type=submit],button:not([type])'),cancel=form.querySelector('.outline');button.disabled=true;cancel.disabled=true;
    try{await api('users',Object.fromEntries(new FormData(form)))}catch(err){button.disabled=false;cancel.disabled=false;toast(err.message);return}
    try{await refresh();if(form.isConnected)workspaceSettings();toast('Persona creada correctamente')}catch(err){if(form.isConnected)closeModal();toast('Persona creada, pero no se pudo actualizar la lista')}
  };
}
async function setMember(id,role,select){let projectId=active,p=data.projects.find(x=>x.id===projectId),previous=p.members.find(m=>m.id===id)?.role||'remove';select.disabled=true;try{await api('members',{project_id:projectId,user_id:id,role});await refresh();let updated=data.projects.find(x=>x.id===projectId);if(!updated||updated.role!=='admin'){boardView='board';closeModal();render();toast('Tu acceso al tablero cambió')}else{select.disabled=false;toast('Acceso actualizado')}}catch(e){select.value=previous;select.disabled=false;toast(e.message)}}

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
    ['priority','Prioridad',`<select name="priority" aria-label="Prioridad" ${change('priority')} ${disabled}>${['Baja','Normal','Alta'].map(v=>`<option ${t.priority===v?'selected':''}>${v}</option>`).join('')}</select>`],
    ['type','Tipo de tarea',`<select id="taskTypeSelect" aria-label="Tipo de tarea" ${change('type')} ${disabled}>${taskTypeOptions(t).map(type=>`<option value="${type.id}" ${type.id===t.type_id?'selected':''}>${esc(taskTypeLabel(type,t))}</option>`).join('')}</select>`],
    ['status','Estado',`<input type="hidden" id="taskStatusSelect" value="${t.status_id}">`]
  ];
  return `<div class="task-properties">${fields.map(([field,label,input])=>`<div class="task-property" data-property="${field}"><span class="task-property-label">${label}</span>${field==='status'?`${statusButton(t,editable,true)}${input}`:field==='assignee_id'?`${assigneeButton(t,editable,true)}${input}`:`<span class="quick-control detail-control ${editable?'':'read-only'}">${visual(field)}${input}</span>`}</div>`).join('')}</div>`;
}
function updateTaskPropertyVisuals(form,task){for(let field of ['assignee_id','due_date','priority','type','status']){let value=form.querySelector(`[data-property="${field}"] .detail-value`);if(value)value.innerHTML=taskPropertyVisual(field,task,true)}}
async function openTask(id,tab=null){if(taskDraft&&!await flushTaskDraft())return;if(taskDraft){clearTimeout(taskDraft.timer);taskDraft=null}if(tab)taskTab=tab;else if(taskId!==id)taskTab='detalle';taskId=id;let t=data.tasks.find(x=>x.id===id), history, comments;try{[history,comments]=await Promise.all([api('history/'+id),api('comments/'+id)])}catch(e){return toast(e.message)}let project=data.projects.find(p=>p.id===active),editable=t.can_edit&&(!project||project.role!=='viewer');let parent=data.tasks.find(x=>x.id===t.parent_id);let subs=data.tasks.filter(x=>x.parent_id===id);modal(`<div class="dialog-head"><div class="eyebrow">DETALLE DE LA TAREA &nbsp; / &nbsp; #${t.id}</div><div class="task-head-actions">${editable?`<button type="button" class="task-link-button task-remove-icon" data-tooltip="Eliminar tarea" aria-label="Eliminar tarea" onclick="confirmDeleteTask(${id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 4h4M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg></button>`:''}<button type="button" class="task-link-button" data-tooltip="Copiar link" aria-label="Copiar link" onclick="copyTaskLink(${t.id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L11 5"/><path d="M14 11a5 5 0 0 0-7.1 0L4.8 13.1a5 5 0 0 0 7.1 7.1L13 19"/></svg></button><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div></div>${parent?`<button type="button" class="parent-task-link" onclick="openTask(${parent.id},'detalle')">← Tarea principal: ${esc(parent.title)}</button>`:''}<form id="taskForm"><input class="task-title" name="title" value="${esc(t.title)}" ${editable?'':'disabled'} required>${renderTaskProperties(t,editable)}</form><div class="detail-section"><div class="project-section-head"><h3>Proyectos</h3>${editable?`<span class="quick-control project-add-control"><span aria-hidden="true">＋ Agregar a un proyecto <span class="quick-chevron">⌄</span></span><select class="project-select" aria-label="Agregar a un proyecto" onchange="if(this.value)taskProject(${id},+this.value,false)"><option value="">Agregar a un proyecto</option>${data.projects.filter(p=>p.role!=='viewer'&&!t.placements.some(x=>x.project_id===p.id)).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></span>`:''}</div><div class="chips">${t.placements.map(x=>{let p=data.projects.find(p=>p.id===x.project_id);return p?`<span class="chip"><i style="background:${esc(p.color)}"></i>${esc(p.name)} ${editable&&p.role!=='viewer'?`<button onclick="taskProject(${id},${p.id},true)" title="Quitar del proyecto">×</button>`:''}</span>`:''}).join('')+(t.inherited_project_ids||[]).map(projectId=>{let p=data.projects.find(p=>p.id===projectId);return p?`<span class="chip" title="Heredado de la tarea principal"><i style="background:${esc(p.color)}"></i>${esc(p.name)} · heredado</span>`:''}).join('')||'<span class="muted">Sin proyecto</span>'}</div></div>${editable?'<div id="taskSaveStatus" class="task-save-status" role="status" aria-live="polite"></div>':''}<div class="task-tabs" role="tablist" aria-label="Secciones de la tarea" onkeydown="taskTabKeydown(event)"><button type="button" role="tab" id="task-tab-detalle" aria-controls="task-panel-detalle" onclick="setTaskTab('detalle')">Detalle</button><button type="button" role="tab" id="task-tab-subtareas" aria-controls="task-panel-subtareas" onclick="setTaskTab('subtareas')">Subtareas (${subs.length})</button><button type="button" role="tab" id="task-tab-comentarios" aria-controls="task-panel-comentarios" onclick="setTaskTab('comentarios')">Comentarios (${comments.length})</button><button type="button" role="tab" id="task-tab-actividad" aria-controls="task-panel-actividad" onclick="setTaskTab('actividad')">Actividad</button></div><section id="task-panel-detalle" role="tabpanel" aria-labelledby="task-tab-detalle"><div class="description-field"><label id="descriptionLabel">Descripción</label>${editable?`<div class="editor-toolbar" role="toolbar" aria-label="Formato de descripción"><button type="button" title="Negrita" aria-label="Negrita" onmousedown="event.preventDefault()" onclick="formatDescription('bold')"><b>N</b></button><button type="button" title="Cursiva" aria-label="Cursiva" onmousedown="event.preventDefault()" onclick="formatDescription('italic')"><i>C</i></button><button type="button" title="Lista con viñetas" aria-label="Lista con viñetas" onmousedown="event.preventDefault()" onclick="formatDescription('insertUnorderedList')">☷</button><button type="button" title="Lista numerada" aria-label="Lista numerada" onmousedown="event.preventDefault()" onclick="formatDescription('insertOrderedList')">1.</button><button type="button" title="Insertar enlace" aria-label="Insertar enlace" onmousedown="event.preventDefault()" onclick="addDescriptionLink()">↗</button></div>`:''}<div id="descriptionEditor" class="description-editor ${editable?'':'read-only'}" role="textbox" aria-labelledby="descriptionLabel" aria-multiline="true" contenteditable="${editable}" data-placeholder="Agrega más detalles sobre esta tarea...">${t.description_format==='html'?t.description:esc(t.description).replace(/\n/g,'<br>')}</div></div></section><section id="task-panel-subtareas" role="tabpanel" aria-labelledby="task-tab-subtareas"><div class="detail-section"><h3>Subtareas</h3><div id="subtaskList">${renderSubtasks(subs)}</div>${editable?`<button type="button" id="addSubtask" class="text-button" onclick="newSubtask()">＋ Agregar subtarea</button><form id="subtaskForm" class="subtask-form" hidden><input name="title" aria-label="Nombre de la subtarea" placeholder="Nombre de la subtarea" required><button type="submit" class="primary">Crear</button></form>`:''}</div></section><section id="task-panel-comentarios" role="tabpanel" aria-labelledby="task-tab-comentarios"><div class="detail-section"><h3>Comentarios</h3><div id="commentsList">${renderComments(comments,editable)}</div>${editable?`<form id="commentForm"><label>Nuevo comentario<textarea name="body" rows="3" maxlength="10000" required placeholder="Escribe un comentario..."></textarea></label><button class="primary">Comentar</button></form>`:'<p class="muted">Tienes acceso de solo lectura.</p>'}</div></section><section id="task-panel-actividad" role="tabpanel" aria-labelledby="task-tab-actividad"><div class="detail-section"><h3>Actividad</h3><div id="activityList">${history.map(h=>`<div class="activity">${avatar(h.actor)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('')}</div></div></section>`,true);setTaskTab(taskTab);if(editable){setupSubtaskForm(id);$('#descriptionEditor').addEventListener('paste',pasteDescription);$('#commentForm').onsubmit=async e=>{e.preventDefault();let form=e.target;try{await api('comments',{task_id:id,body:form.elements.body.value});form.reset();let task=data.tasks.find(t=>t.id===id);if(task){task.comment_count=(task.comment_count||0)+1;$('#task-tab-comentarios').textContent=`Comentarios (${task.comment_count})`;renderBoard()}$('#commentsList').innerHTML=renderComments(await api('comments/'+id),editable);toast('Comentario agregado')}catch(err){toast(err.message)}};setupTaskAutosave(id,t)}}
function setupTaskAutosave(id,task){
  let form=$('#taskForm'),title=form.elements.title,editor=$('#descriptionEditor'),status=$('#taskSaveStatus');
  let draft=taskDraft={id,title,editor,status,savedTitle:task.title,savedDescription:editor.innerHTML,timer:null,saving:null};
  function schedule(delay){clearTimeout(draft.timer);status.textContent='Cambios sin guardar';draft.timer=setTimeout(()=>flushTaskDraft(),delay)}
  title.addEventListener('input',()=>schedule(1000));
  title.addEventListener('blur',()=>flushTaskDraft());
  editor.addEventListener('input',()=>schedule(2500));
  form.addEventListener('submit',e=>{e.preventDefault();title.blur();flushTaskDraft()});
}
async function flushTaskDraft(){
  let draft=taskDraft;if(!draft)return true;
  clearTimeout(draft.timer);
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
      draft.status.textContent='Guardando…';
      try{await api('task/update',payload)}catch(err){if(taskDraft===draft){draft.status.textContent='No se pudieron guardar los cambios';toast(err.message)}return false}
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
function confirmDeleteTask(id){
  let task=data.tasks.find(t=>t.id===id);if(!task?.can_edit)return;
  let dialog=document.createElement('dialog');dialog.className='dialog confirm-task-delete';dialog.setAttribute('aria-labelledby','deleteTaskTitle');dialog.setAttribute('aria-describedby','deleteTaskDescription');
  dialog.innerHTML=`<h2 id="deleteTaskTitle">¿Eliminar esta tarea?</h2><div id="deleteTaskDescription"><p>Se eliminará la tarea <strong>«${esc(task.title)}»</strong> de todos sus proyectos y se borrarán sus comentarios y actividad. Esta acción no se puede deshacer.</p><p>Sus subtareas se conservarán como tareas independientes y podrían dejar de ser visibles para algunos usuarios.</p></div><div class="dialog-actions"><button type="button" class="outline" data-cancel>Cancelar</button><button type="button" class="task-delete" data-delete>Eliminar tarea</button></div>`;
  $('#modal').appendChild(dialog);let cancel=dialog.querySelector('[data-cancel]'),button=dialog.querySelector('[data-delete]');
  async function cancelDialog(){if(dialog.dataset.closing)return;dialog.dataset.closing='true';dialog.inert=true;if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){dialog.classList.add('is-closing');await waitForModalAnimation(dialog)}if(dialog.open)dialog.close()}
  cancel.onclick=cancelDialog;dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('cancel',e=>{e.preventDefault();if(!button.disabled)cancelDialog()});
  button.onclick=async()=>{button.disabled=true;cancel.disabled=true;try{await api('task/delete',{id})}catch(err){button.disabled=false;cancel.disabled=false;toast(err.message);return}dialog.close();closeModal(true,true);taskId=null;if(/^\/t\//.test(location.pathname))history.replaceState(null,'','/');try{await refresh();toast('Tarea eliminada')}catch(err){toast('Tarea eliminada, pero no se pudo actualizar el tablero')}};
  dialog.showModal();cancel.focus();
}
function setTaskTab(name){taskTab=name;for(let tab of ['detalle','subtareas','comentarios','actividad']){let active=tab===name,button=$('#task-tab-'+tab),panel=$('#task-panel-'+tab);if(!button||!panel)return;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;panel.hidden=!active}}
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

async function updateActivity(id){let list=$('#activityList');if(!list)return;let history=await api('history/'+id);if(!list.isConnected)return;list.innerHTML=history.map(h=>`<div class="activity">${avatar(h.actor)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('')}

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

async function taskProject(id,p,remove){if(taskDraft&&!await flushTaskDraft())return;try{await api('task/project',{id,project_id:p,remove});await refresh();let task=data.tasks.find(t=>t.id===id);if(task){if(!task.has_projects){active=0;boardView='board';filter='';render()}await openTask(id)}else{closeModal();toast('La tarea ya no está disponible para tu usuario')}}catch(e){toast(e.message)}}
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
start();

const projectFilterCache=new Map();
function projectFilterDefaults(){return {completion:'all',period:[],assignee:[],type:[],due:'',date:'',priority:[],status:[]}}
function projectFilterKey(project){return `flujo:project-filters:${data.user.id}:${project.public_id||project.id}`}
function projectFilters(project){
  let key=projectFilterKey(project);if(projectFilterCache.has(key))return projectFilterCache.get(key);
  let saved={};try{saved=JSON.parse(sessionStorage.getItem(key)||'{}')}catch{}
  let result=projectFilterDefaults();if(saved&&typeof saved==='object')for(let field of Object.keys(result)){
    if(Array.isArray(result[field])){
      let values=Array.isArray(saved[field])?saved[field]:typeof saved[field]==='string'?[saved[field]]:[];
      result[field]=[...new Set(values.filter(value=>typeof value==='string'&&value&&value!=='all'))];
    }else if(typeof saved[field]==='string')result[field]=saved[field];
  }
  if(!['all','open','done'].includes(result.completion))result.completion='all';
  result.period=result.period.filter(value=>['today','yesterday','week','month'].includes(value));
  if(!['','none','overdue','today','week','month','date'].includes(result.due))result.due='';
  result.priority=result.priority.filter(value=>['Baja','Normal','Alta'].includes(value));
  result.assignee=result.assignee.filter(value=>/^(none|[1-9]\d*)$/.test(value));
  for(let field of ['type','status'])result[field]=result[field].filter(value=>/^[1-9]\d*$/.test(value));
  if(!/^\d{4}-\d{2}-\d{2}$/.test(result.date))result.date='';
  projectFilterCache.set(key,result);return result;
}
function saveProjectFilters(project,filters){try{sessionStorage.setItem(projectFilterKey(project),JSON.stringify(filters))}catch{}}
function projectFilterCount(filters){return ['assignee','type','priority','status'].filter(field=>filters[field].length).length+(filters.due?1:0)+(filters.completion!=='all'?1:0)}
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
  if(filters.completion==='done'&&filters.period.length){
    let completed=task.completed_at?projectFilterDate(task.completed_at):'';if(!completed)return false;
    if(!filters.period.some(period=>period==='today'&&completed===dates.today||period==='yesterday'&&completed===dates.yesterday||period==='week'&&completed>=dates.weekStart&&completed<=dates.weekEnd||period==='month'&&completed.startsWith(dates.month+'-')))return false;
  }
  if(filters.assignee.length&&!filters.assignee.includes(task.assignee_id==null?'none':String(task.assignee_id)))return false;
  if(filters.type.length&&!filters.type.includes(String(task.type_id))||filters.status.length&&!filters.status.includes(String(task.status_id))||filters.priority.length&&!filters.priority.includes(task.priority))return false;
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
function projectFilterKeydown(e){if(e.key==='Escape'&&projectFilterPanel&&!(statusMenu?.contains(e.target))){e.preventDefault();let opened=projectFilterPanel.querySelector('.project-multifilter details[open]');if(opened){opened.open=false;opened.querySelector('summary').focus()}else closeProjectFilters(true)}}
function projectFilterResize(){closeProjectFilters()}
function projectFilterOptions(items,value){
  return items.map(([id,name])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(name)}</option>`).join('')+(value&&!items.some(([id])=>id===value)?`<option value="${esc(value)}" selected>No disponible</option>`:'');
}
function projectMultiFilter(field,label,items,selected){
  let options=[...items,...selected.filter(value=>!items.some(([id])=>id===value)).map(value=>[value,'No disponible'])];
  return `<div class="project-multifilter"><span id="filter-label-${field}">${esc(label)}</span><details><summary aria-labelledby="filter-label-${field} filter-summary-${field}"><span id="filter-summary-${field}">Todos</span></summary><fieldset><legend>${esc(label)}</legend>${options.map(([value,name])=>`<label><input type="checkbox" data-field="${field}" value="${esc(value)}" ${selected.includes(value)?'checked':''}> ${esc(name)}</label>`).join('')}</fieldset></details></div>`;
}
function positionProjectMultiFilter(details){
  let summary=details.querySelector('summary'),menu=details.querySelector('fieldset'),rect=summary.getBoundingClientRect();
  let width=Math.min(Math.max(rect.width,200),window.innerWidth-16);menu.style.width=width+'px';
  let height=menu.offsetHeight;menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';
  menu.style.top=Math.max(8,Math.min(rect.bottom+4+height<=window.innerHeight-8?rect.bottom+4:rect.top-height-4,window.innerHeight-height-8))+'px';
}
function updateProjectFilterButton(project){let button=$('.project-filter-trigger');if(!button||active!==project.id)return;let count=projectFilterCount(projectFilters(project));button.textContent='⚑ Filtros'+(count?' · '+count:'');button.classList.toggle('active',!!count)}
function openProjectFilters(button){
  let project=data.projects.find(p=>p.id===active);if(!project)return;
  if(projectFilterTrigger===button){closeProjectFilters(true);return}closeProjectFilters();closeStatusMenu();
  let filters=projectFilters(project),types=[...project.types];
  for(let task of data.tasks)if(task.placements.some(place=>place.project_id===project.id)&&task.task_type&&!types.some(type=>type.id===task.task_type.id))types.push(task.task_type);
  let assignees=[['none','Sin responsable'],...data.users.map(user=>[String(user.id),user.name])],typeOptions=types.map(type=>[String(type.id),type.name]);
  let statuses=types.flatMap(type=>type.statuses.map(status=>[String(status.id),type.name+' · '+status.name]));
  let multiOptions={period:[['today','Hoy'],['yesterday','Ayer'],['week','Esta semana'],['month','Este mes']],assignee:assignees,type:typeOptions,priority:[['Baja','Baja'],['Normal','Normal'],['Alta','Alta']],status:statuses};
  let panel=document.createElement('div');panel.className='project-filter-panel';panel.setAttribute('role','dialog');panel.setAttribute('aria-label','Filtros del proyecto');
  panel.innerHTML=`<div class="project-filter-head"><strong>Filtros</strong><button type="button" class="text-button" data-clear>Eliminar todos</button></div><div class="project-filter-quick"><button type="button" data-quick="open">Sin finalizar</button><button type="button" data-quick="mine">Asignadas a mí</button><button type="button" data-quick="done">Finalizadas</button></div><div data-period>${projectMultiFilter('period','Finalizadas',multiOptions.period,filters.period)}<small>Las tareas antiguas sin fecha de finalización solo aparecen sin filtrar el período.</small></div><div class="project-filter-fields">${projectMultiFilter('assignee','Responsable',assignees,filters.assignee)}${projectMultiFilter('type','Tipo de tarea',typeOptions,filters.type)}<label>Fecha límite<select data-field="due">${projectFilterOptions([['','Cualquiera'],['none','Sin fecha'],['overdue','Vencida'],['today','Hoy'],['week','Esta semana'],['month','Este mes'],['date','Fecha específica']],filters.due)}</select></label><label data-date>Fecha específica<input type="date" data-field="date" value="${esc(filters.date)}"></label>${projectMultiFilter('priority','Prioridad',multiOptions.priority,filters.priority)}${projectMultiFilter('status','Estado',statuses,filters.status)}</div>`;
  document.body.appendChild(panel);projectFilterPanel=panel;projectFilterTrigger=button;button.setAttribute('aria-expanded','true');
  function sync(){
    panel.querySelectorAll('[data-field]').forEach(control=>{let value=filters[control.dataset.field];if(Array.isArray(value))control.checked=value.includes(control.value);else control.value=value});
    for(let field of Object.keys(multiOptions)){let summary=panel.querySelector('#filter-summary-'+field),selected=filters[field];if(summary)summary.textContent=selected.length===0?'Todos':selected.length===1?(multiOptions[field].find(([id])=>id===selected[0])?.[1]||'No disponible'):`${selected.length} seleccionados`}
    panel.querySelector('[data-period]').hidden=filters.completion!=='done';panel.querySelector('[data-date]').hidden=filters.due!=='date';
    panel.querySelectorAll('[data-quick]').forEach(control=>{let kind=control.dataset.quick,on=kind==='mine'?filters.assignee.includes(String(data.user.id)):filters.completion===kind;control.setAttribute('aria-pressed',String(on));control.classList.toggle('active',on)});
    updateProjectFilterButton(project);
    let rect=button.getBoundingClientRect(),height=panel.offsetHeight,width=panel.offsetWidth;panel.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';panel.style.top=Math.max(8,Math.min(rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6,window.innerHeight-height-8))+'px';
    let opened=panel.querySelector('.project-multifilter details[open]');if(opened)positionProjectMultiFilter(opened);
  }
  function apply(){saveProjectFilters(project,filters);sync();renderBoard()}
  panel.addEventListener('change',e=>{let field=e.target.dataset.field;if(!field)return;if(Array.isArray(filters[field]))filters[field]=e.target.checked?[...filters[field].filter(value=>value!==e.target.value),e.target.value]:filters[field].filter(value=>value!==e.target.value);else filters[field]=e.target.value;apply()});
  panel.addEventListener('click',e=>{
    let quick=e.target.closest('[data-quick]');
    if(quick){let kind=quick.dataset.quick;if(kind==='mine'){let id=String(data.user.id);filters.assignee=filters.assignee.includes(id)?filters.assignee.filter(value=>value!==id):[...filters.assignee,id]}else filters.completion=filters.completion===kind?'all':kind;apply()}
    else if(e.target.closest('[data-clear]')){Object.assign(filters,projectFilterDefaults());apply()}
  });
  panel.querySelectorAll('.project-multifilter details').forEach(details=>details.addEventListener('toggle',()=>{
    if(!details.open)return;
    panel.querySelectorAll('.project-multifilter details[open]').forEach(other=>{if(other!==details)other.open=false});
    positionProjectMultiFilter(details);
  }));
  panel.addEventListener('pointerdown',e=>panel.querySelectorAll('.project-multifilter details[open]').forEach(details=>{if(!details.contains(e.target))details.open=false}));
  panel.addEventListener('scroll',e=>{if(e.target===panel)panel.querySelectorAll('.project-multifilter details[open]').forEach(details=>details.open=false)});
  sync();
  document.addEventListener('pointerdown',projectFilterOutside,true);document.addEventListener('keydown',projectFilterKeydown,true);window.addEventListener('resize',projectFilterResize);panel.querySelector('[data-quick]')?.focus();
}

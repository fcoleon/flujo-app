function taskPropertyVisual(field,t,editable){
  if(field==='assignee_id'){let user=data.users.find(u=>u.id===t.assignee_id);return `<span class="detail-person">${user?avatar(user.name,user.id):'<span class="avatar empty-avatar">＋</span>'}<span>${user?esc(user.name):'Sin asignar'}</span></span>`}
  if(field==='due_date')return `<span class="date-value"><span class="detail-date-icon">◷</span> &nbsp; ${esc(t.due_date||'Sin fecha')}</span>`;
  if(field==='priority')return `<span class="priority ${t.priority==='Alta'?'high':t.priority==='Baja'?'low':''}">${esc(t.priority)}</span>`;
  if(field==='type')return `<span class="detail-type">${esc(taskTypeLabel(t.task_type,t))}</span>`;
  let status=taskStatus(t);return status?`<span class="status-badge" style="--status-color:${esc(status.color)}"><i></i><span class="status-name">${esc(status.name)}</span></span>`:'';
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
  return `<div class="task-properties">${fields.map(([field,label,input])=>`<div class="task-property" data-property="${field}"><span class="task-property-label">${label}</span>${field==='status'?`${statusButton(t,editable,true)}${input}<span class="status-times">${detailStatusTimes(t)}</span>`:field==='assignee_id'?`${assigneeButton(t,editable,true)}${input}`:field==='priority'?`${priorityButton(t,editable,true)}${input}`:`<span class="quick-control detail-control ${editable?'':'read-only'}">${visual(field)}${input}</span>`}</div>`).join('')}</div>`;
}
function detailStatusTimes(t){if(!t.status_since||taskStatus(t)?.is_done)return '';let seconds=Math.max(0,(Date.now()-new Date(t.status_since).getTime())/1000);return `<small><span title="Tiempo desde el último cambio de estado${t.status_since_partial?' (como mínimo)':''}">Actual: ${statusDurationLabel(seconds)}</span><br><span title="Tiempo total en este estado${t.status_total_partial?' (como mínimo)':''}">Total: ${statusDurationLabel(seconds+(t.status_previous_seconds||0))}</span></small>`}
function updateTaskPropertyVisuals(form,task){for(let field of ['assignee_id','due_date','priority','type','status']){let value=form.querySelector(`[data-property="${field}"] .detail-value`);if(value)value.innerHTML=taskPropertyVisual(field,task,true)}let times=form.querySelector('.status-times');if(times)times.innerHTML=detailStatusTimes(task)}
async function openTask(id,tab=null){let previous=taskDraft;if(previous&&!previous.title.value.trim())return toast('El nombre de la tarea es obligatorio');let t=data.tasks.find(x=>x.id===id),history,comments;if(!t)return toast('La tarea ya no está disponible');try{[history,comments]=await Promise.all([api('history/'+id),api('comments/'+id)])}catch(e){return toast(e.message)}if(previous!==taskDraft)return;if(previous){queueTaskDraft(previous);taskDraft=null}if(tab)taskTab=tab;else if(taskId!==id)taskTab='detalle';taskId=id;let project=data.projects.find(p=>p.id===active),editable=t.can_edit&&(!project||project.role!=='viewer');let parent=data.tasks.find(x=>x.id===t.parent_id);let subs=data.tasks.filter(x=>x.parent_id===id);modal(`<div class="dialog-head"><div class="eyebrow">DETALLE DE LA TAREA &nbsp; / &nbsp; #${t.id}</div><div class="task-head-actions">${editable?`<button type="button" class="task-link-button task-remove-icon" data-tooltip="Eliminar tarea" aria-label="Eliminar tarea" onclick="confirmDeleteTask(${id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 4h4M6 7l1 13h10l1-13M10 11v5M14 11v5"/></svg></button>`:''}<button type="button" class="task-link-button" data-tooltip="Copiar link" aria-label="Copiar link" onclick="copyTaskLink(${t.id})"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L11 5"/><path d="M14 11a5 5 0 0 0-7.1 0L4.8 13.1a5 5 0 0 0 7.1 7.1L13 19"/></svg></button><button type="button" aria-label="Cerrar" onclick="closeModal()">×</button></div></div>${parent?`<button type="button" class="parent-task-link" onclick="openTask(${parent.id},'detalle')">← Tarea principal: ${esc(parent.title)}</button>`:''}<form id="taskForm"><input class="task-title" name="title" value="${esc(t.title)}" ${editable?'':'disabled'} required>${renderTaskProperties(t,editable)}</form><div class="detail-section"><div class="project-section-head"><h3>Proyectos</h3>${editable&&data.projects.some(p=>p.role!=='viewer'&&!t.placements.some(x=>x.project_id===p.id))?`<span class="quick-control project-add-control"><span aria-hidden="true">＋</span><select class="project-select" aria-label="Agregar a un proyecto" onchange="if(this.value)taskProject(${id},+this.value,false)"><option value="">Agregar a un proyecto</option>${data.projects.filter(p=>p.role!=='viewer'&&!t.placements.some(x=>x.project_id===p.id)).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></span>`:''}<div class="chips">${t.placements.map(x=>{let p=data.projects.find(p=>p.id===x.project_id);return p?`<span class="chip"><i style="background:${esc(p.color)}"></i><span class="project-chip-info"><strong>${esc(p.name)}</strong><small>${esc(p.columns.find(col=>col.id===x.column_id)?.name||'Sin columna')}</small></span>${editable&&p.role!=='viewer'?`<button data-tooltip="Sacar de este proyecto" aria-label="Sacar de este proyecto" onclick="taskProject(${id},${p.id},true)">×</button>`:''}</span>`:''}).join('')+(t.inherited_project_ids||[]).map(projectId=>{let p=data.projects.find(p=>p.id===projectId);return p?`<span class="chip" title="Heredado de la tarea principal"><i style="background:${esc(p.color)}"></i>${esc(p.name)} · heredado</span>`:''}).join('')||'<span class="muted">Sin proyecto</span>'}</div></div></div>${editable?'<div id="taskSaveStatus" class="task-save-status" role="status" aria-live="polite"></div>':''}<div class="task-tabs" role="tablist" aria-label="Secciones de la tarea" onkeydown="taskTabKeydown(event)"><button type="button" role="tab" id="task-tab-detalle" aria-controls="task-panel-detalle" onclick="setTaskTab('detalle')">Descripción</button><button type="button" role="tab" id="task-tab-subtareas" aria-controls="task-panel-subtareas" onclick="setTaskTab('subtareas')">Subtareas (${subs.length})</button><button type="button" role="tab" id="task-tab-comentarios" aria-controls="task-panel-comentarios" onclick="setTaskTab('comentarios')">Comentarios (${comments.length})</button><button type="button" role="tab" id="task-tab-actividad" aria-controls="task-panel-actividad" onclick="setTaskTab('actividad')">Actividad</button></div><section id="task-panel-detalle" role="tabpanel" aria-labelledby="task-tab-detalle"><div class="description-field">${editable?`<div class="editor-toolbar" role="toolbar" aria-label="Formato de descripción"><button type="button" data-tooltip="Negrita" aria-label="Negrita" onmousedown="event.preventDefault()" onclick="formatDescription('bold')"><b>N</b></button><button type="button" data-tooltip="Cursiva" aria-label="Cursiva" onmousedown="event.preventDefault()" onclick="formatDescription('italic')"><i>C</i></button><button type="button" data-tooltip="Lista con viñetas" aria-label="Lista con viñetas" onmousedown="event.preventDefault()" onclick="formatDescription('insertUnorderedList')"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/></svg></button><button type="button" data-tooltip="Lista numerada" aria-label="Lista numerada" onmousedown="event.preventDefault()" onclick="formatDescription('insertOrderedList')"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M10 6h10M10 12h10M10 18h10"/><path d="M4 5h1v3M4 8h2M4 11h2l-2 2h2M4 17c0-1 2-1 2 0s-2 1-2 2h2"/></svg></button><button type="button" data-tooltip="Insertar enlace" aria-label="Insertar enlace" onmousedown="event.preventDefault()" onclick="addDescriptionLink()"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L8 6"/><path d="M14 11a5 5 0 0 0-7.1 0l-2.1 2.1a5 5 0 0 0 7.1 7.1L16 18"/></svg></button></div>`:''}<div id="descriptionEditor" class="description-editor ${editable?'':'read-only'}" role="textbox" aria-label="Descripción" aria-multiline="true" contenteditable="${editable}" data-placeholder="Agrega más detalles sobre esta tarea...">${t.description_format==='html'?t.description:esc(t.description).replace(/\n/g,'<br>')}</div></div></section><section id="task-panel-subtareas" role="tabpanel" aria-labelledby="task-tab-subtareas"><div class="detail-section"><h3>Subtareas</h3><div id="subtaskList">${renderSubtasks(subs)}</div>${editable?`<button type="button" id="addSubtask" class="text-button" onclick="newSubtask()">＋ Agregar subtarea</button><form id="subtaskForm" class="subtask-form" hidden><input name="title" aria-label="Nombre de la subtarea" placeholder="Nombre de la subtarea" required><button type="submit" class="primary">Crear</button></form>`:''}</div></section><section id="task-panel-comentarios" role="tabpanel" aria-labelledby="task-tab-comentarios"><div class="detail-section"><h3>Comentarios</h3><div id="commentsList">${renderComments(comments,editable)}</div>${editable?`<form id="commentForm"><label>Nuevo comentario<textarea name="body" rows="3" maxlength="10000" required placeholder="Escribe un comentario..."></textarea></label><button class="primary">Comentar</button></form>`:'<p class="muted">Tienes acceso de solo lectura.</p>'}</div></section><section id="task-panel-actividad" role="tabpanel" aria-labelledby="task-tab-actividad"><div class="detail-section"><h3>Actividad</h3><div id="activityList">${history.map(h=>`<div class="activity">${avatar(h.actor,h.user_id)}<div><strong>${esc(h.actor)}</strong> ${esc(h.message)}<small>${new Date(h.created_at).toLocaleString('es-CL',{dateStyle:'medium',timeStyle:'short'})}</small></div></div>`).join('')}</div></div></section>`,true);setupAttachments(id,editable);setTaskTab(taskTab);if(editable){setupSubtaskForm(id);$('#descriptionEditor').addEventListener('paste',pasteDescription);$('#commentForm').onsubmit=async e=>{e.preventDefault();let form=e.target;try{await api('comments',{task_id:id,body:form.elements.body.value});form.reset();let task=data.tasks.find(t=>t.id===id);if(task){task.comment_count=(task.comment_count||0)+1;$('#task-tab-comentarios').textContent=`Comentarios (${task.comment_count})`;renderBoard()}$('#commentsList').innerHTML=renderComments(await api('comments/'+id),editable);toast('Comentario agregado')}catch(err){toast(err.message)}};setupTaskAutosave(id,t,editable)}}
function setTaskSaveStatus(draft,message){
  if(draft.conflict||draft.denied||draft.retryable===false)return;
  if(document.activeElement===draft.editor){
    if(['Guardado','Guardando…','Cambios sin guardar'].includes(draft.status.textContent))draft.status.textContent='';
  }else draft.status.textContent=message;
}
function setupTaskAutosave(id,task,editable){
  let form=$('#taskForm'),title=form.elements.title,editor=$('#descriptionEditor'),status=$('#taskSaveStatus');
  setupDescriptionCodeCopy(editor);
  if(editable)editor.previousElementSibling.insertAdjacentHTML('beforeend','<button type="button" data-tooltip="Bloque de código" aria-label="Bloque de código" onmousedown="event.preventDefault()" onclick="formatDescription(\'formatBlock\',\'pre\')">&lt;/&gt;</button>');
  let draft=pendingTaskDrafts.get(id);
  if(draft){
    clearTimeout(draft.retryTimer);pendingTaskDrafts.delete(id);title.value=draft.title.value;editor.innerHTML=draft.editor.innerHTML;
    if(draft.saving)draft.reopenedDuringSave=true;
    Object.assign(draft,{title,editor,status});updateSyncIndicator();
  }else draft={id,ownerId:data.user.id,title,editor,status,savedTitle:task.title,savedDescription:editor.innerHTML,titleVersion:task.title_version,descriptionVersion:task.description_version,timer:null,descriptionTimer:null,saving:null,conflict:false,denied:false};
  taskDraft=draft;
  if(draft.conflict)showTaskConflict(draft);
  else if(draft.denied||draft.retryable===false)status.textContent='No se pudo guardar; conserva y copia tu borrador';
  else if(draft.title.value.trim()!==draft.savedTitle||draft.editor.innerHTML!==draft.savedDescription)setTaskSaveStatus(draft,'Cambios sin guardar');
  title.addEventListener('input',()=>{if(taskDraft!==draft)return;draft.retryable=true;clearTimeout(draft.timer);setTaskSaveStatus(draft,'Cambios sin guardar');draft.timer=setTimeout(()=>{if(taskDraft===draft)flushTaskDraft(false)},1000)});
  title.addEventListener('blur',()=>{if(taskDraft===draft)flushTaskDraft(false)});
  editor.addEventListener('input',()=>{if(taskDraft!==draft)return;draft.retryable=true;clearTimeout(draft.descriptionTimer);setTaskSaveStatus(draft,editor.innerHTML===draft.savedDescription&&title.value.trim()===draft.savedTitle?'Guardado':'Cambios sin guardar')});
  editor.addEventListener('focus',()=>{clearTimeout(draft.descriptionTimer);setTaskSaveStatus(draft,'')});
  editor.addEventListener('blur',()=>{if(taskDraft!==draft)return;clearTimeout(draft.descriptionTimer);if(editor.innerHTML!==draft.savedDescription)draft.descriptionTimer=setTimeout(()=>{if(taskDraft===draft)flushTaskDraft(true,false)},2500)});
  if(editable)editor.addEventListener('keydown',descriptionCodeKeydown);
  if(editable){let linkWasEditing=false;editor.addEventListener('pointerdown',e=>{if(e.target.closest('a'))linkWasEditing=document.activeElement===editor});editor.addEventListener('click',e=>{let link=e.target.closest('a');if(link){e.preventDefault();if(linkWasEditing||e.detail===0&&document.activeElement===editor)addDescriptionLink(link);else{window.open(link.href,'_blank','noopener,noreferrer');editor.blur()}}linkWasEditing=false})}
  form.addEventListener('submit',e=>{e.preventDefault();title.blur();flushTaskDraft()});
}
async function flushTaskDraft(includeDescription=true,allowFocused=true,draft=taskDraft){
  if(!draft)return true;
  clearTimeout(draft.timer);if(includeDescription)clearTimeout(draft.descriptionTimer);
  if(draft.conflict||draft.denied)return false;
  if(draft.saving){let wasPending=pendingTaskDrafts.get(draft.id)===draft,ok=await draft.saving;if(wasPending&&pendingTaskDrafts.get(draft.id)!==draft)return ok;return ok&&(taskDraft===draft||pendingTaskDrafts.get(draft.id)===draft)?flushTaskDraft(includeDescription,allowFocused,draft):ok}
  let startedAsPending=pendingTaskDrafts.get(draft.id)===draft;
  let work=(async()=>{
    while(taskDraft===draft||pendingTaskDrafts.get(draft.id)===draft){
      let title=draft.title.value.trim(),description=draft.editor.innerHTML,payload={id:draft.id,expected_user_id:draft.ownerId};
      if(title&&title!==draft.savedTitle)payload.title=title;
      if(includeDescription&&(allowFocused||document.activeElement!==draft.editor)&&description!==draft.savedDescription){payload.description=description;payload.description_format='html'}
      if(!('title' in payload)&&!('description' in payload)){
        if(!title){draft.status.textContent='El nombre de la tarea es obligatorio';return false}
        return true;
      }
      if('title' in payload){if(draft.titleVersion==null){draft.retryable=false;draft.status.textContent='El servidor no admite versiones; conserva tu borrador';return false}payload.title_version=draft.titleVersion}
      if('description' in payload){if(draft.descriptionVersion==null){draft.retryable=false;draft.status.textContent='El servidor no admite versiones; conserva tu borrador';return false}payload.description_version=draft.descriptionVersion}
      setTaskSaveStatus(draft,'Guardando…');
      let result;
      try{result=await api('task/update',payload,true)}catch(err){
        draft.retryable=!err.status||err.status===408||err.status===429||err.status>=500;
        if(err.conflict){draft.conflict=true;if(taskDraft===draft)showTaskConflict(draft)}
        else{if(err.status===401||err.status===403)draft.denied=true;draft.status.textContent=draft.denied?'Sin acceso; conserva y copia tu borrador':'No se pudieron guardar los cambios';if(taskDraft===draft)toast(err.message)}
        return false
      }
      if(('title' in payload&&result.title_version==null)||('description' in payload&&result.description_version==null)){draft.denied=true;draft.status.textContent='El servidor no devolvió versiones; conserva tu borrador';return false}
      if('title' in payload)draft.titleVersion=result.title_version;
      if('description' in payload)draft.descriptionVersion=result.description_version;
      if('title' in payload)draft.savedTitle=title;
      if('description' in payload)draft.savedDescription=description;
      refresh().catch(()=>{});
      if(draft.reopenedDuringSave){draft.reopenedDuringSave=false;if(pendingTaskDrafts.get(draft.id)!==draft)return true}
      if(startedAsPending&&pendingTaskDrafts.get(draft.id)!==draft)return true;
      if(taskDraft!==draft&&pendingTaskDrafts.get(draft.id)!==draft)return true;
      setTaskSaveStatus(draft,draft.editor.innerHTML===draft.savedDescription&&draft.title.value.trim()===draft.savedTitle?'Guardado':'Cambios sin guardar');
      if(taskDraft===draft)updateActivity(draft.id).catch(()=>{});
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

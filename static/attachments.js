let attachmentQueue=[],attachmentUploading=false,attachmentItems={},attachmentGeneration={};
function updateAttachmentCount(id,count){let task=data?.tasks.find(t=>t.id===id);if(task&&Number.isInteger(count)){task.attachment_count=count;renderBoard()}}
function attachmentType(name){let ext=name.includes('.')?name.split('.').pop().toLowerCase():'';return ({doc:'Documento Word',docx:'Documento Word',pdf:'PDF',xls:'Planilla Excel',xlsx:'Planilla Excel',ppt:'Presentación PowerPoint',pptx:'Presentación PowerPoint',png:'Imagen PNG',jpg:'Imagen JPEG',jpeg:'Imagen JPEG',txt:'Texto',zip:'Archivo ZIP'})[ext]||(ext?'.'+ext:'Archivo')}
function isAttachmentImage(name){return /\.(png|jpe?g|gif|webp)$/i.test(name)}
function setupAttachmentPan(body,image){
  let drag=null;
  const scrollable=()=>body.scrollWidth>body.clientWidth||body.scrollHeight>body.clientHeight;
  image.draggable=false;
  image.addEventListener('pointerdown',e=>{
    if(e.pointerType!=='mouse'||e.button!==0||!scrollable())return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,left:body.scrollLeft,top:body.scrollTop};
    image.setPointerCapture(e.pointerId);image.classList.add('dragging');e.preventDefault();
  });
  image.addEventListener('pointermove',e=>{if(drag&&e.pointerId===drag.id){body.scrollLeft=drag.left+drag.x-e.clientX;body.scrollTop=drag.top+drag.y-e.clientY}});
  image.addEventListener('lostpointercapture',()=>{drag=null;image.classList.remove('dragging')});
  image.addEventListener('pointerup',e=>{if(drag&&e.pointerId===drag.id)image.releasePointerCapture(e.pointerId)});
  const update=()=>image.classList.toggle('pannable',scrollable());
  image.addEventListener('pointerenter',update);
  return update;
}
function viewAttachment(id,name){
  let dialog=document.createElement('dialog'),url='/api/attachments/'+encodeURIComponent(id),scale=1;
  dialog.className='attachment-viewer';dialog.setAttribute('aria-label','Vista de imagen: '+name);
  dialog.innerHTML=`<div class="attachment-viewer-bar"><strong></strong><button type="button" data-zoom="-1" aria-label="Reducir imagen">−</button><span data-scale>100%</span><button type="button" data-zoom="1" aria-label="Ampliar imagen">+</button><a href="${url}">Descargar</a><button type="button" data-close aria-label="Cerrar vista">×</button></div><div class="attachment-viewer-body"><img alt=""><p hidden>No se pudo mostrar esta imagen. Puedes descargar el archivo.</p></div>`;
  let image=dialog.querySelector('img'),body=dialog.querySelector('.attachment-viewer-body'),fit=0;dialog.querySelector('strong').textContent=name;image.alt=name;
  let updatePan=setupAttachmentPan(body,image);
  image.onload=()=>{fit=Math.min(1,body.clientWidth/image.naturalWidth,body.clientHeight/image.naturalHeight)*image.naturalWidth;image.style.width=fit+'px';updatePan()};
  image.src=url+'?preview=1';
  image.onerror=()=>{image.hidden=true;dialog.querySelector('p').hidden=false};
  dialog.querySelectorAll('[data-zoom]').forEach(button=>button.onclick=()=>{scale=Math.max(.25,Math.min(5,scale+(Number(button.dataset.zoom)*.25)));if(fit)image.style.width=fit*scale+'px';updatePan();dialog.querySelector('[data-scale]').textContent=Math.round(scale*100)+'%'});
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>dialog.remove());document.body.appendChild(dialog);dialog.showModal();
}
function renderAttachments(id){
  let list=$('#attachmentList');if(!list||Number(list.dataset.task)!==id)return;
  let task=data.tasks.find(t=>t.id===id),project=data.projects.find(p=>p.id===active),editable=task?.can_edit&&(!project||project.role!=='viewer');
  list.innerHTML=(attachmentItems[id]||[]).map(item=>`<details class="attachment-card"><summary data-tooltip="${esc(item.name)}"><span class="attachment-name">${esc(item.name)}</span><small>${esc(attachmentType(item.name))}</small></summary><div class="attachment-menu">${isAttachmentImage(item.name)?`<button type="button" data-view="${esc(item.id)}">Ver</button>`:''}<a href="/api/attachments/${esc(item.id)}">Descargar</a>${editable?`<button type="button" onclick="deleteAttachment('${esc(item.id)}',${id})">Eliminar</button>`:''}</div></details>`).join('')+attachmentQueue.filter(item=>item.task===id).map(item=>`<div class="attachment-card attachment-pending" data-tooltip="${esc(item.file.name)}"><span class="attachment-name">${esc(item.file.name)}</span><small><span class="attachment-spinner" aria-hidden="true"></span> ${item.loading?'Subiendo…':'En espera'}</small></div>`).join('');
  list.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{let item=attachmentItems[id].find(item=>item.id===button.dataset.view);if(item){button.closest('details').open=false;viewAttachment(item.id,item.name)}});
}
function attachmentRipple(hint,event,rect,state){
  let x=event.clientX,y=event.clientY,now=performance.now();
  if(!state)return {x,y,lastX:x,lastY:y,time:now};
  let moved=x!==state.lastX||y!==state.lastY;state.lastX=x;state.lastY=y;
  if(moved&&Math.hypot(x-state.x,y-state.y)>=12&&now-state.time>=300&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    let ripple=document.createElement('span');ripple.className='attachment-ripple';ripple.style.left=(x-rect.left)+'px';ripple.style.top=(y-rect.top)+'px';ripple.addEventListener('animationend',()=>ripple.remove());hint.appendChild(ripple);
    state.x=x;state.y=y;state.time=now;
  }
  return state;
}
function setupAttachments(id,editable){
  if(!data.attachments_enabled)return;
  $('#taskForm').nextElementSibling.insertAdjacentHTML('afterend',`<section class="detail-section attachments"><div class="project-section-head"><h3>Adjuntos</h3>${editable?'<button type="button" class="project-add-control attachment-add-control" id="attachmentChoose" aria-label="Adjuntar archivo" data-tooltip="Adjuntar archivo">＋</button><input id="attachmentInput" type="file" multiple hidden>':''}<div id="attachmentList" class="attachment-list" data-task="${id}"></div></div></section>`);
  let list=$('#attachmentList'),dialog=$('#modal .task-dialog'),generation=attachmentGeneration[id]||0;renderAttachments(id);
  dialog.addEventListener('click',e=>{list.querySelectorAll('details.attachment-card[open]').forEach(card=>{if(!card.contains(e.target))card.open=false})});
  api('tasks/'+id+'/attachments').then(result=>{if(generation!==(attachmentGeneration[id]||0)){if(list.isConnected)api('tasks/'+id+'/attachments').then(fresh=>{attachmentItems[id]=fresh.attachments;if(list.isConnected)renderAttachments(id)}).catch(err=>toast(err.message));return}attachmentItems[id]=result.attachments;if(list.isConnected)renderAttachments(id)}).catch(err=>toast(err.message));
  if(!editable)return;
  let input=$('#attachmentInput');$('#attachmentChoose').onclick=()=>input.click();
  let hint=document.createElement('div'),rippleState=null,hintTimer=null;hint.className='attachment-drop-hint';hint.hidden=true;hint.innerHTML='<span>Suelta el archivo para agregarlo a la tarea</span>';dialog.parentElement.appendChild(hint);
  function showHint(){if(!hint.hidden&&!hint.classList.contains('is-leaving')&&!hint.classList.contains('is-dropped'))return;clearTimeout(hintTimer);hint.hidden=false;hint.classList.remove('is-leaving','is-dropped');hint.classList.add('is-entering')}
  function hideHint(dropped){rippleState=null;clearTimeout(hintTimer);hint.classList.remove('is-entering','is-leaving','is-dropped');if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){hint.hidden=true;return}hint.classList.add(dropped?'is-dropped':'is-leaving');hintTimer=setTimeout(()=>{hint.hidden=true},dropped?400:200)}
  input.onchange=()=>{enqueueAttachments(id,input.files);input.value=''};
  dialog.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();e.dataTransfer.dropEffect='copy';let rect=dialog.getBoundingClientRect();Object.assign(hint.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});showHint();rippleState=attachmentRipple(hint,e,rect,rippleState)}});
  dialog.addEventListener('dragleave',e=>{if(!hint.hidden&&!hint.classList.contains('is-dropped')&&!dialog.contains(e.relatedTarget))hideHint(false)});
  dialog.addEventListener('drop',e=>{if(!e.dataTransfer.files.length)return;e.preventDefault();hideHint(true);enqueueAttachments(id,e.dataTransfer.files)});
}
function enqueueAttachments(id,files){for(let file of files)attachmentQueue.push({task:id,file});renderAttachments(id);uploadNextAttachment()}
async function uploadNextAttachment(){
  if(attachmentUploading||!attachmentQueue.length)return;
  attachmentUploading=true;let item=attachmentQueue[0];item.loading=true;renderAttachments(item.task);
  try{
    if(item.file.size>10*1024*1024)throw Error('El archivo supera 10 MiB');
    let body=new FormData();body.append('file',item.file);
    let response=await fetch('/api/tasks/'+item.task+'/attachments',{method:'POST',headers:{'X-Flujo-Request':'attachment'},body});
    if(response.status===413)throw Error('El archivo supera el límite de carga');
    let result=await response.json();if(!response.ok)throw Error(result.error||'No se pudo subir el archivo');
    attachmentGeneration[item.task]=(attachmentGeneration[item.task]||0)+1;attachmentItems[item.task]??=[];attachmentItems[item.task].push(result);updateAttachmentCount(item.task,result.attachment_count);toast('Archivo adjuntado');
  }catch(err){toast(`${item.file.name}: ${err.message}`)}
  finally{attachmentQueue.shift();attachmentUploading=false;renderAttachments(item.task);uploadNextAttachment()}
}
function deleteAttachment(id,task){
  let dialog=document.createElement('dialog');dialog.className='dialog confirm-task-delete';dialog.setAttribute('aria-labelledby','deleteAttachmentTitle');
  dialog.innerHTML='<h2 id="deleteAttachmentTitle">¿Eliminar adjunto?</h2><p>El archivo dejará de estar disponible en esta tarea.</p><div class="dialog-actions"><button type="button" class="outline cancel-button" data-cancel>Cancelar</button><button type="button" class="task-delete" data-delete>Eliminar</button></div>';
  $('#modal').appendChild(dialog);let cancel=dialog.querySelector('[data-cancel]'),remove=dialog.querySelector('[data-delete]');cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('cancel',e=>{if(remove.disabled)e.preventDefault()});
  remove.onclick=async()=>{cancel.disabled=remove.disabled=true;try{let result=await api('attachments/'+id,{});updateAttachmentCount(task,result.attachment_count);attachmentGeneration[task]=(attachmentGeneration[task]||0)+1;attachmentItems[task]=(attachmentItems[task]||[]).filter(item=>item.id!==id);dialog.close();renderAttachments(task);toast('Adjunto eliminado')}catch(err){cancel.disabled=remove.disabled=false;toast(err.message)}};
  dialog.showModal();cancel.focus();
}

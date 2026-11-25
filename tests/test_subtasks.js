// Run with: node tests/test_subtasks.js
const source=require('fs').readFileSync('static/app.js','utf8');
const detail=require('fs').readFileSync('static/task-detail.js','utf8');
const assert=require('assert');
let active=1,data={projects:[{id:1,role:'editor'}],users:[{id:2,name:'Ana'}]};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const avatar=name=>`<span>${esc(name)}</span>`;
eval(source.slice(source.indexOf('function taskStatus('),source.indexOf('let statusMenu='))+
     source.slice(source.indexOf('function statusButton('),source.indexOf('function priorityButton('))+
     detail.slice(detail.indexOf('function taskPropertyVisual('),detail.indexOf('function renderTaskProperties('))+
     source.slice(source.indexOf('function renderSubtasks('),source.indexOf('function newSubtask('))+`
  let task={id:3,title:'<Prueba>',assignee_id:2,can_edit:true,status_id:4,task_type:{statuses:[{id:4,name:'En curso',color:'#123'}]}};
  let html=renderSubtasks([task]);
  assert(html.includes('&lt;Prueba&gt;')&&html.includes('Ana')&&html.includes('En curso')&&html.includes('style="background:#123"'));
  assert(html.includes("false,'assignee'")&&html.includes('Cambiar estado de')&&html.includes('openTask(3)'));
  data.projects[0].role='viewer';
  html=renderSubtasks([task]);
  assert(html.includes('Ana')&&!html.includes('Cambiar responsable')&&!html.includes('Cambiar estado de'));
  task.assignee_id=null;task.status_id=null;
  assert(renderSubtasks([task]).includes('Sin asignar')&&!renderSubtasks([task]).includes('En curso')&&!renderSubtasks([task]).includes('style="background:'));
`);

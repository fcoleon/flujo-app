// Run with: node tests/test_delete_column.js
const source=require('fs').readFileSync('static/app.js','utf8');
let active=1,action=null,sent=null,change;
let data={projects:[{id:1,role:'admin',columns:[{id:10,name:'Origen'},{id:20,name:'Destino'}]}]};
const tasks=[{id:3},{id:4}],destination={disabled:true,value:''},label={hidden:true},error={},warning={};
const cancel={disabled:false,focus(){}},submit={disabled:true,dataset:{}};
const form={querySelector(selector){return selector==='[name=action]:checked'?(action?{value:action}:null):selector==='[name=destination_id]'?destination:label},addEventListener(name,fn){if(name==='change')change=fn}};
const dialog={setAttribute(){},querySelector(selector){return selector==='form'?form:selector==='[data-cancel]'?cancel:selector==='[type=submit]'?submit:selector==='.column-delete-warning'?warning:error},addEventListener(){},showModal(){},close(){},remove(){}};
const document={createElement:()=>dialog},$=()=>({appendChild(){}}),esc=s=>s,listColumnTasks=()=>tasks;
function api(path,body){sent={path,body};return new Promise(()=>{})}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function confirmDeleteColumn('),source.indexOf('const boardScrollPositions='))+`
  confirmDeleteColumn(10);
  assert(dialog.innerHTML.includes('value="move"')&&dialog.innerHTML.includes('value="remove"')&&dialog.innerHTML.includes('value="delete"'),'muestra las tres opciones');
  form.onsubmit({preventDefault(){}});assert(!sent,'exige elegir una opción');
  action='move';change();assert(!destination.disabled&&!label.hidden&&submit.disabled,'requiere destino');
  destination.value='20';change();assert(!submit.disabled,'permite confirmar con destino');
  form.onsubmit({preventDefault(){}});
  assert(sent.path==='columns/delete'&&sent.body.action==='move'&&sent.body.destination_id===20&&sent.body.task_ids.join(',')==='3,4','envía la selección y tareas confirmadas');
`);

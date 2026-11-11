// Run with: node tests/test_create_task.js
const source=require('fs').readFileSync('static/app.js','utf8');
let html='',opened=null,created=0,refreshed=0,closed=0;
let buttons=[{disabled:false},{disabled:false}],form={isConnected:true,querySelector:()=>({focus(){}}),querySelectorAll:()=>buttons};
const data={tasks:[{id:7}]},$=selector=>selector==='#modalForm'?form:null,esc=s=>s;
function modal(content){html=content} function closeModal(){closed++;form.isConnected=false;return Promise.resolve(true)}
function refresh(){refreshed++;return Promise.resolve()} function openTask(id){opened=id;return Promise.resolve()}
function toast(message){throw Error(message)}
class FormData{constructor(){return [['title','Prueba']][Symbol.iterator]()}}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function formModal('),source.indexOf('function newProject()'))+`
  formModal('Nueva tarea','',()=>{created++;return Promise.resolve({id:7})},'',true);
  assert(html.includes('>Crear</button>')&&html.includes('>Crear y editar</button>'),'dos opciones');
  let promise=form.onsubmit({preventDefault(){},submitter:{value:'edit'}});
  assert(buttons.every(button=>button.disabled),'evita envíos duplicados');
  promise.then(()=>{
    assert(created===1&&closed===1&&refreshed===1&&opened===7,'crea y abre el detalle');
    form.isConnected=true;buttons.forEach(button=>button.disabled=false);opened=null;
    return form.onsubmit({preventDefault(){},submitter:{value:'create'}});
  }).then(()=>assert(created===2&&opened===null,'crear no abre el detalle'));
`);

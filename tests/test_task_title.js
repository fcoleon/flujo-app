// Run with: node tests/test_task_title.js
const source=require('fs').readFileSync('static/app.js','utf8');
let title,handlers={};
const document={createElement:()=>title={style:{},scrollHeight:72,setAttribute(){},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end},addEventListener:(name,fn)=>handlers[name]=fn}};
const input={className:'task-title',name:'title',required:true,disabled:false,replaceWith(element){this.replacement=element}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function setupTaskTitle('),source.indexOf('function modal('))+`
  setupTaskTitle(input,{title:'Primera línea\\nSegunda línea'});
  assert(input.replacement===title&&title.value.includes('\\n')&&title.style.height==='72px','título multilínea inicial');
  let prevented=false;handlers.keydown({key:'Enter',isComposing:false,preventDefault(){prevented=true}});assert(prevented,'bloquea Enter');
  prevented=false;handlers.keydown({key:'Enter',isComposing:true,preventDefault(){prevented=true}});assert(!prevented,'permite finalizar composición');
  title.value='Uno'+String.fromCharCode(10)+'Dos';title.selectionStart=title.selectionEnd=title.value.length;
  title.scrollHeight=96;handlers.input();assert(title.value==='Uno Dos'&&title.style.height==='96px','elimina saltos al pegar y ajusta altura');
  assert(title.required&&!title.disabled&&title.name==='title','conserva la validación y edición');
`);

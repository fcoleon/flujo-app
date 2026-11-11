// Run with: node tests/test_task_title.js
const source=require('fs').readFileSync('static/app.js','utf8');
let title,handler;
const document={createElement:()=>title={style:{},scrollHeight:72,setAttribute(){},addEventListener:(name,fn)=>handler=fn}};
const input={className:'task-title',name:'title',required:true,disabled:false,replaceWith(element){this.replacement=element}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function setupTaskTitle('),source.indexOf('function modal('))+`
  setupTaskTitle(input,{title:'Primera línea\\nSegunda línea'});
  assert(input.replacement===title&&title.value.includes('\\n')&&title.style.height==='72px','título multilínea inicial');
  title.scrollHeight=96;handler();assert(title.style.height==='96px','ajusta la altura al editar');
  assert(title.required&&!title.disabled&&title.name==='title','conserva la validación y edición');
`);

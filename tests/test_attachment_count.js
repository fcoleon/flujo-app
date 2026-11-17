// Run with: node tests/test_attachment_count.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
const esc=value=>String(value),taskStatus=()=>null,taskQuickControls=()=>({});
let data={projects:[],attachments_enabled:true},active=0;
eval(source.slice(source.indexOf('function card(t,'),source.indexOf('async function quickUpdate('))+`
  let task={id:1,title:'Prueba',attachment_count:2,comment_count:1};
  let html=card(task,1);
  assert(html.includes('Abrir 2 adjuntos de Prueba')&&html.includes('stroke-linejoin="round"'),'muestra contador de adjuntos');
  task.attachment_count=0;assert(!card(task).includes('adjunto'),'sin adjuntos no muestra contador');
  task.attachment_count=1;data.attachments_enabled=false;assert(!card(task).includes('adjunto'),'sin almacenamiento configurado no muestra contador');
`);

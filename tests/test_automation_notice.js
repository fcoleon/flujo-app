// Run with: node tests/test_automation_notice.js
const source=require('fs').readFileSync('static/app.js','utf8');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let created=0, dialog;
const document={body:{appendChild(){}},createElement(){
  created++;let ul={},button={focus(){}};
  return dialog={setAttribute(){},querySelector:s=>s==='ul'?ul:button,addEventListener:(name,fn)=>dialog.onclose=fn,
    showModal(){},close(){dialog.onclose()},remove(){}};
}};
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('let disabledAutomationQueue='),source.indexOf('function toast('))+`
  showDisabledAutomations([{name:'<Regla>',project_name:'A',reason:'Estado eliminado'}]);
  assert(dialog.querySelector('ul').innerHTML.includes('&lt;Regla&gt;'),'escapa nombres');
  showDisabledAutomations([{name:'Otra',project_name:'B',reason:'Sin usuario'}]);
  assert(created===1 && dialog.querySelector('ul').innerHTML.includes('Otra'),'agrupa avisos');
  dialog.close();
  showDisabledAutomations([{name:'Nueva',project_name:'A',reason:'Error'}]);
  assert(created===2 && !dialog.querySelector('ul').innerHTML.includes('Otra'),'limpia al cerrar');
`);

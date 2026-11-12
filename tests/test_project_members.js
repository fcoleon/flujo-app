// Run with: node tests/test_project_members.js
const source=require('fs').readFileSync('static/app.js','utf8');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function avatar(name){return '<span class="avatar">'+esc(name)+'</span>'}
let active=1,data={projects:[]},html='',edited=false,tab='';
function modal(value){html=value} function editProject(){edited=true} function setProjectSettingsTab(value){tab=value}
const $=()=>({setAttribute(){},querySelector:()=>({focus(){}}),focus(){}});
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function projectMemberStack('),source.indexOf('const renderBoardWithoutAutomations='))+`
  let members=Array.from({length:7},(_,i)=>({id:i+1,name:'Persona '+(i+1),username:'user'+(i+1),role:'editor'}));
  let project={id:1,name:'Tablero',role:'viewer',members:members.slice(0,5)};data.projects=[project];
  let stack=projectMemberStack(project);assert((stack.match(/class="avatar"/g)||[]).length===5&&!stack.includes('member-more'),'hasta cinco avatares');
  project.members=members.slice(0,6);stack=projectMemberStack(project);
  assert((stack.match(/class="avatar"/g)||[]).length===5&&stack.includes('member-more'),'seis muestran cinco y acceso al resto');
  project.members=members;stack=projectMemberStack(project);
  assert((stack.match(/class="avatar"/g)||[]).length===4&&stack.includes('member-more'),'más de seis muestran cuatro y acceso');
  openProjectMembers();assert(html.includes('Persona 7')&&!html.includes('<select')&&!edited,'vista de solo lectura');
  project.role='admin';openProjectMembers();assert(edited&&tab==='usuarios','administrador abre edición de usuarios');
`);

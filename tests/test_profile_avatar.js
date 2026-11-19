// Run with: node tests/test_profile_avatar.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
let data={user:{id:1,name:'Ana'},users:[{id:1}],version:'1'};
let elements={};
function element(){return {hidden:false,disabled:false,files:[],focus(){},click(){},insertAdjacentHTML(position,html){this.inserted={position,html}},insertAdjacentElement(position,element){this.moved={position,element}},querySelector(){return element()}}}
for(let key of ['#modalForm','#avatarFile','.profile-avatar-picker','#avatarSave','#avatarCancel','#avatarRemove','#avatarPreview','#modal .dialog'])elements[key]=element();
Object.defineProperty(elements['#avatarFile'],'value',{set(value){if(value==='')this.files=[]}});
const $=key=>elements[key], esc=x=>x, avatar=()=>'<span>avatar</span>', formModal=()=>{}, slackIcon=()=>'', toast=()=>{};
const URL={createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}};
eval(source.slice(source.indexOf('function editProfile(){'),source.indexOf('function mcpTokens(){'))+`
  editProfile();
  assert($('#modalForm').moved.position==='afterend','versión al final del perfil');
  let input=$('#avatarFile'),save=$('#avatarSave'),cancel=$('#avatarCancel'),remove=$('#avatarRemove');
  assert(save.hidden&&cancel.hidden&&remove.hidden,'sin foto no hay acciones');
  input.files=[{size:10}];input.onchange();
  assert(!save.hidden&&!cancel.hidden&&remove.hidden,'selección muestra subir y cancelar');
  cancel.onclick();assert(save.hidden&&cancel.hidden&&remove.hidden,'cancelar restaura estado sin foto');
  data.users[0].avatar_version='actual';editProfile();
  assert(save.hidden&&cancel.hidden&&!remove.hidden,'foto existente permite quitar');
  input.files=[{size:10}];input.onchange();
  assert(!save.hidden&&!cancel.hidden&&remove.hidden,'foto nueva oculta quitar');
  cancel.onclick();assert(save.hidden&&cancel.hidden&&!remove.hidden,'cancelar restaura foto anterior');
`);

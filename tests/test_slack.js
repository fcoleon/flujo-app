// Run with: jsc tests/test_slack.js
const assert=(value,message)=>{if(!value)throw Error(message||'Assertion failed')};
assert.strictEqual=(a,b)=>assert(a===b,`${a} !== ${b}`);
assert.deepStrictEqual=(a,b)=>assert(JSON.stringify(a)===JSON.stringify(b),JSON.stringify(a));
const source=readFile('static/app.js');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data={user:{admin:true},projects:[{id:1,name:'Admin <proyecto>',role:'admin'},{id:2,name:'Lector',role:'viewer'}]};
let requests=[],html='',form,dialog,handlers={},messages=[],destination,previous={hidden:false},focused=false;
const group=(id,assignment,comment,completion)=>({dataset:{project:id},insertAdjacentHTML(position,value){html+=value},remove(){},querySelector(s){return s.includes('assignment')?{checked:assignment}:s.includes('comment')?{checked:comment}:completion==null?null:{checked:completion}}});
form={elements:{enabled:{checked:true},hour:{value:'9'}},addEventListener(){},querySelectorAll(s){return s==='[data-project]'?[group('',true,false,null),group('1',false,true,true),group('2',true,true,null)]:[{value:'0'},{value:'4'}]}};
dialog={isConnected:true,setAttribute(){},set innerHTML(value){html=value},get innerHTML(){return html},showModal(){},close(){handlers.close()},remove(){},addEventListener(name,fn){handlers[name]=fn},querySelector(s){if(s==='button')return {};if(s==='#slackForm')return form;if(s==='#slackError')return {textContent:''};if(s==='#slackTitle')return {classList:{add(){}},insertAdjacentHTML(position,icon){html+=icon}}},querySelectorAll(){return []}};
const document={activeElement:{focus(){focused=true}},querySelector(){return previous},createElement(){return dialog},body:{appendChild(){}}};
function slackIcon(){return '<svg class="slack-icon"></svg>'}
const status={configured:true,installation:{team_name:'Equipo',team_id:'T1'},account:{slack_user_id:'U1'},preferences:[{project_id:null,assignment:true},{project_id:1,completion:true}],summary:{enabled:true,hour:9,weekdays:[0,4]}};
async function api(path,body){requests.push({path,body});return path==='slack/status'?status:path.endsWith('/start')?{url:'https://slack.com/oauth'}:{}}
function toast(message){messages.push(message)} function confirm(){return true}
const location={assign(url){destination=url}};
eval(source.slice(source.indexOf('function slackPreferenceFields('),source.indexOf('async function setWorkspaceBlocked('))+`
  (async()=>{
    slackSettings();await Promise.resolve();
    assert(previous.hidden,'oculta el modal anterior');
    assert(html.includes('class="slack-icon"'),'muestra el icono de Slack');
    assert(html.includes('Admin &lt;proyecto&gt;'));
    assert(html.includes('<table class="slack-preferences">'),'muestra preferencias en tabla');
    assert(html.includes('scope="col">Proyecto'),'muestra el encabezado de proyecto');
    let table=slackPreferenceTable([{id:null,name:'Sin proyecto'}],[]);
    assert(!table.includes('name="completion"'),'sin proyecto no tiene opción de finalización');
    assert(html.includes('data-action="disconnect"')&&html.includes('data-action="unlink"'));
    await form.onsubmit({preventDefault(){}});
    assert.deepStrictEqual(requests[1],{path:'slack/preferences',body:{preferences:[
      {project_id:null,assignment:true,comment:false,completion:false},
      {project_id:1,assignment:false,comment:true,completion:true},
      {project_id:2,assignment:true,comment:true,completion:false}
    ],summary:{enabled:true,hour:9,weekdays:[0,4]}}});
    const click=action=>handlers.click({target:{closest:()=>({dataset:{action}})}});
    click('test');await Promise.resolve();await Promise.resolve();
    assert.deepStrictEqual(requests[2],{path:'slack/test',body:{}});
    assert.strictEqual(messages.pop(),'Prueba de Slack encolada para envío');
    click('link/start');await Promise.resolve();await Promise.resolve();
    assert.deepStrictEqual(requests[3],{path:'slack/link/start',body:{}});
    assert.strictEqual(destination,'https://slack.com/oauth');
    dialog.close();assert(!previous.hidden&&focused,'restaura el modal y el foco');
    data.user.admin=false;slackSettings();await Promise.resolve();
    assert(!html.includes('data-action="disconnect"'));
    assert(html.includes('<table class="slack-preferences">'));
  })().catch(err=>{print(err.stack);quit(1)});
`);

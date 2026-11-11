// Run with: node tests/test_board_links.js
const source=require('fs').readFileSync('static/app.js','utf8');
const uuid='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', other='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
let data={projects:[{id:1,public_id:uuid},{id:2,public_id:other}],tasks:[]},active=1,filter='',boardView='board',statsType='',taskId=null;
let location={pathname:'/',search:'',origin:'https://example.test'},pop;
const history={pushState(a,b,path){setPath(path)},replaceState(a,b,path){setPath(path)}};
function setPath(path){let parts=path.split('?');location.pathname=parts[0];location.search=parts[1]?'?'+parts[1]:''}
const window={addEventListener(name,fn){if(name==='popstate')pop=fn}};
class URLSearchParams{constructor(s=''){this.items={};if(s)for(let pair of s.replace(/^\?/,'').split('&')){let [k,v]=pair.split('=');this.items[k]=v}}set(k,v){this.items[k]=v}get(k){return this.items[k]||null}toString(){return Object.entries(this.items).map(([k,v])=>k+'='+v).join('&')}}
class URL{constructor(path,origin){this.path=path;this.origin=origin;this.searchParams=new URLSearchParams()}get href(){let q=this.searchParams.toString();return this.origin+this.path+(q?'?'+q:'')}}
function render(){} function toast(){} async function closeModal(){return true}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('let currentRoute='),source.indexOf('function statsDefaultMonth()'))+`
  async function openTask(id){taskId=id;setTaskUrl(data.tasks.find(t=>t.id===id),openingDeepLink)}
  selectProject(2);assert(location.pathname==='/board/'+other,'cambio de tablero');
  selectProject(0);assert(location.pathname==='/board/no-project','sin proyecto');
  let task={id:9,public_id:uuid,has_projects:true,placements:[{project_id:1},{project_id:2}]};
  data.tasks=[task];active=2;
  assert(taskLink(task)==='https://example.test/board/'+other+'/task/'+uuid,'enlace con UUID');
  setPath('/board/'+uuid);openDeepLink().then(()=>{
    assert(active===1,'apertura directa');
    setPath('/t/'+uuid+'?project=2');return openDeepLink();
  }).then(()=>{
    assert(active===2&&taskId===9&&location.pathname==='/board/'+other+'/task/'+uuid,'enlace antiguo');
    setPath('/t/'+uuid+'?project='+uuid);return openDeepLink();
  }).then(()=>{
    assert(active===1&&taskId===9&&location.pathname==='/board/'+uuid+'/task/'+uuid,'enlace anterior con UUID');
    setPath('/board/'+other+'/task/'+uuid);return openDeepLink();
  }).then(()=>{
    assert(active===2&&taskId===9,'enlace directo');
    setPath('/board/no-project');return pop();
  }).then(()=>{
    assert(active===0,'volver en historial');
    let orphan={id:10,public_id:other,has_projects:false,placements:[]};data.tasks.push(orphan);
    assert(taskPath(orphan)==='/board/no-project/task/'+other,'tarea sin proyecto');
    setPath('/board/no-project/task/'+other);return openDeepLink();
  }).then(()=>assert(active===0&&taskId===10,'apertura de tarea sin proyecto'));
`);

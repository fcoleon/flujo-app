// Run with: node tests/test_project_filter_views.js
const source=require('fs').readFileSync('static/app.js','utf8');
let active=1,filter='',boardView='board',dragSaving=false,board={innerHTML:'',querySelector:()=>({}),querySelectorAll:()=>[]},count={};
let data={user:{id:9},stats_timezone:'UTC',projects:[{id:1,public_id:'project',role:'editor',columns:[{id:10,name:'Pendientes'}]}],tasks:[
  {id:1,title:'Mía',assignee_id:9,can_edit:true,done:false,placements:[{project_id:1,column_id:10,position:0}]},
  {id:2,title:'De otra persona',assignee_id:8,can_edit:true,done:false,placements:[{project_id:1,column_id:10,position:1}]},
  {id:3,title:'Finalizada',assignee_id:9,can_edit:true,done:true,placements:[{project_id:1,column_id:10,position:2}]}
]};
const document={addEventListener(){}},sessionStorage={getItem:()=>null,setItem(){}};
const $=selector=>selector==='#board'?board:selector==='#boardCount'?count:null,esc=s=>String(s);
function taskStatus(t){return {is_done:t.done}} function normalizeSearch(s){return s.toLowerCase().trim()} function matchesSearch(t,q){return t.title.toLowerCase().includes(q)}
function saveBoardScroll(){} function restoreBoardScroll(){} function collapsedColumns(){return []} function card(t){return '<article>'+t.title+'</article>'}
function taskQuickControls(){return {statusControl:'',personControl:'',dateControl:'',priorityControl:''}}
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('const projectFilterCache='),source.indexOf('let projectFilterPanel='))+
     source.slice(source.indexOf('const listCollapsedState='),source.indexOf('function startColumnDrag('))+`
  let f=projectFilters(data.projects[0]);f.completion='open';f.assignee='9';
  renderBoard();assert(board.innerHTML.includes('Mía')&&!board.innerHTML.includes('De otra persona')&&!board.innerHTML.includes('Finalizada')&&count.textContent.startsWith('1 de 3'),'filtra tarjetas');
  boardView='list';renderBoard();assert(board.innerHTML.includes('Mía')&&!board.innerHTML.includes('De otra persona')&&!board.innerHTML.includes('Finalizada'),'aplica los mismos filtros en lista');
`);

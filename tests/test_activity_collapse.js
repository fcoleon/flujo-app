// Run with: node tests/test_activity_collapse.js
const source=require('fs').readFileSync('static/app.js','utf8');
let button;
const document={createElement:()=>button={remove(){this.removed=true}}};
const assert=(value,message)=>{if(!value)throw Error(message)};
function activityList(count,expanded=false){
  let list={dataset:expanded?{expanded:'true'}:{},items:[],querySelectorAll(){return this.items}};
  for(let i=0;i<count;i++)list.items.push({hidden:false,before(element){list.buttonBefore=i;list.button=element},focus(){this.focused=true}});
  return list;
}
eval(source.slice(source.indexOf('function collapseActivity('),source.indexOf('function modal('))+`
  let short=activityList(7);collapseActivity(short);assert(!short.button&&short.items.every(item=>!item.hidden),'hasta siete visibles');
  let list=activityList(8);collapseActivity(list);
  assert(list.button.textContent==='Mostrar 3 actualizaciones previas'&&list.buttonBefore===4,'enlace entre recientes y anteriores');
  assert(list.items.map(item=>item.hidden?1:0).join('')==='00001110','cuatro recientes y primera actividad visibles');
  list.button.onclick();assert(list.items.every(item=>!item.hidden)&&list.dataset.expanded==='true'&&list.button.removed&&list.items[4].focused,'despliega y conserva foco');
  let expanded=activityList(12,true);collapseActivity(expanded);assert(!expanded.button&&expanded.items.every(item=>!item.hidden),'no vuelve a contraer tras actualizar');
`);

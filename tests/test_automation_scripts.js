// Run with: node tests/test_automation_scripts.js
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const template=fs.readFileSync('templates/index.html','utf8');
const scripts=[['navigation','navigation'],['slack','slack'],['project-filters','project_filters'],['attachments','attachments'],['task-detail','task_detail'],['automations','automations'],['app','script']];
let previous=-1;
for(const [name,version] of scripts){
  let index=template.indexOf(`/static/${name}.js?v={{ ${version}_version }}`);
  assert(index>previous,`${name}.js debe cargarse en orden y con versión`);
  previous=index;
}
const context=vm.createContext({
  document:{addEventListener(){},querySelector(){return null}},
  window:{addEventListener(){},matchMedia(){return {addEventListener(){}}}},
  location:{pathname:'/',search:''},URLSearchParams,console
});
for(const [name] of scripts.slice(0,-1))vm.runInContext(fs.readFileSync(`static/${name}.js`,'utf8'),context);
vm.runInContext(fs.readFileSync('static/app.js','utf8').replace(/start\(\);\s*$/,''),context);
assert.strictEqual(typeof context.openAutomations,'function');
assert.strictEqual(typeof context.saveAutomation,'function');
for(const name of ['selectProject','openDeepLink','slackSettings','openProjectFilters','setupAttachments','openTask','flushTaskDraft'])assert.strictEqual(typeof context[name],'function');
vm.runInContext(`
  data={projects:[{id:1,role:'viewer'},{id:2,role:'admin'}],users:[]};
  let requests=0,rendered=0;
  api=async()=>{requests++;return {rules:[]}};
  renderAutomations=()=>rendered++;
`,context);
(async()=>{
  vm.runInContext('active=1',context);
  await context.openAutomations();
  assert.strictEqual(vm.runInContext('requests',context),0);
  vm.runInContext('active=2',context);
  await context.openAutomations();
  assert.strictEqual(vm.runInContext('requests',context),1);
  assert.strictEqual(vm.runInContext('rendered',context),1);
  vm.runInContext("automationDraft={name:'anterior'};automationDraft.name='inline'",context);
  assert.strictEqual(vm.runInContext('automationDraft.name',context),'inline');
})().catch(err=>{console.error(err.stack);process.exitCode=1});

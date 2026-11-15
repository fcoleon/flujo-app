// Run with: node tests/test_mobile_sidebar.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
function element(){
  let classes=new Set();
  return {hidden:false,inert:false,attrs:{},dataset:{},handlers:{},classList:{
    add(name){classes.add(name)},remove(name){classes.delete(name)},
    contains(name){return classes.has(name)},toggle(name,value){value?classes.add(name):classes.delete(name)}
  },setAttribute(name,value){this.attrs[name]=value},addEventListener(name,fn){this.handlers[name]=fn},focus(){document.activeElement=this}};
}
let sidebar=element(),button=element(),topbar=element(),shell=element(),main=element(),trigger,backdrop;
topbar.prepend=el=>trigger=el;shell.insertBefore=el=>backdrop=el;
const mobileSidebarMedia={matches:true};
const document={activeElement:null,createElement:element};
const $=selector=>({'#mainSidebar':sidebar,'#sidebarToggle':button,'.topbar':topbar,'.shell':shell,'.shell>main':main,'#mobileSidebarToggle':trigger,'#mobileSidebarBackdrop':backdrop}[selector]);
function sidebarCollapsed(){return true}
function toggleSidebar(){button.desktopToggled=true}
eval(source.slice(source.indexOf('function closeMobileSidebar('),source.indexOf('const renderWithoutMobileSidebar='))+`
  setupMobileSidebar();
  assert(sidebar.inert&&!sidebar.classList.contains('is-collapsed'),'menú móvil cerrado y expandido al abrir');
  trigger.onclick();
  assert(sidebar.classList.contains('mobile-open')&&!sidebar.inert&&main.inert&&!backdrop.hidden,'apertura móvil');
  assert(trigger.attrs['aria-expanded']==='true'&&document.activeElement===button,'accesibilidad al abrir');
  backdrop.onclick();
  assert(sidebar.inert&&!main.inert&&backdrop.hidden&&document.activeElement===trigger,'cierre y foco');
  trigger.onclick();
  sidebar.handlers.click({target:{closest:()=>({})}});
  assert(sidebar.inert&&!main.inert,'cerrar al elegir una opción');
  mobileSidebarMedia.matches=false;syncMobileSidebar();
  assert(!sidebar.inert&&sidebar.classList.contains('is-collapsed'),'preferencia de escritorio');
  button.onclick();
  assert(button.desktopToggled,'control de escritorio intacto');
`);

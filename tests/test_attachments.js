// Run with: node tests/test_attachments.js
const source=require('fs').readFileSync('static/attachments.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function attachmentType('),source.indexOf('function renderAttachments('))+`
  assert(attachmentType('documento.docx')==='Documento Word','documento Word');
  assert(attachmentType('informe.PDF')==='PDF','extensiones sin distinción de mayúsculas');
  assert(attachmentType('archivo.xyz')==='.xyz','extensión desconocida');
  assert(attachmentType('archivo')==='Archivo','sin extensión');
  assert(isAttachmentImage('foto.JPEG')&&isAttachmentImage('foto.webp'),'reconoce imágenes');
  assert(!isAttachmentImage('foto.svg')&&!isAttachmentImage('foto.png.html'),'excluye imágenes no admitidas');
`);
eval(source.slice(source.indexOf('function setupAttachmentPan('),source.indexOf('function viewAttachment('))+`
  let handlers={},classes=new Set(),captured=null;
  let image={classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle:(x,on)=>on?classes.add(x):classes.delete(x)},addEventListener:(name,fn)=>handlers[name]=fn,setPointerCapture:id=>captured=id,releasePointerCapture:id=>{if(captured===id){captured=null;handlers.lostpointercapture()}}};
  let body={clientWidth:100,clientHeight:100,scrollWidth:100,scrollHeight:100,scrollLeft:20,scrollTop:30};
  let update=setupAttachmentPan(body,image),event={pointerType:'mouse',button:0,pointerId:1,clientX:50,clientY:50,preventDefault(){}};
  update();handlers.pointerdown(event);assert(!classes.has('pannable')&&captured===null,'sin scroll no arrastra');
  body.scrollWidth=300;update();handlers.pointerdown(event);
  handlers.pointermove({...event,clientX:40,clientY:35});
  assert(classes.has('pannable')&&classes.has('dragging')&&body.scrollLeft===30&&body.scrollTop===45,'arrastra imagen ampliada');
  handlers.pointerup(event);assert(!classes.has('dragging')&&captured===null,'suelta el arrastre');
  handlers.pointerdown({...event,pointerType:'touch'});assert(captured===null,'conserva desplazamiento táctil nativo');
`);
let inside={},outside={},card={open:true,contains:target=>target===inside},handler;
let list={querySelectorAll:()=>[card]},dialog={addEventListener:(event,callback)=>{if(event==='click')handler=callback}};
eval(source.match(/dialog\.addEventListener\('click',e=>\{list\.querySelectorAll\('details\.attachment-card\[open\]'\).*?\}\);/)[0]);
handler({target:inside});assert(card.open,'clic dentro conserva el menú');
handler({target:outside});assert(!card.open,'clic fuera cierra el menú');
let clock=0,ripples=[],reduced=false;
const performance={now:()=>clock},window={matchMedia:()=>({matches:reduced})};
const document={createElement:()=>({style:{},addEventListener(){},remove(){}})};
let hint={appendChild:item=>ripples.push(item)},rect={left:0,top:0},state=null;
eval(source.slice(source.indexOf('function attachmentRipple('),source.indexOf('function setupAttachments('))+`
  state=attachmentRipple(hint,{clientX:0,clientY:0},rect,state);
  clock=700;state=attachmentRipple(hint,{clientX:3,clientY:2},rect,state);
  assert(ripples.length===0,'temblor no genera ondas');
  state=attachmentRipple(hint,{clientX:20,clientY:0},rect,state);
  assert(ripples.length===1,'movimiento suficiente genera onda');
  clock=999;state=attachmentRipple(hint,{clientX:40,clientY:0},rect,state);
  assert(ripples.length===1,'espera al menos 300 ms');
  clock=1000;state=attachmentRipple(hint,{clientX:41,clientY:0},rect,state);
  assert(ripples.length===2,'genera otra onda después de 300 ms');
  clock=1400;state=attachmentRipple(hint,{clientX:41,clientY:0},rect,state);
  assert(ripples.length===2,'cursor inmóvil no genera ondas');
  reduced=true;state=attachmentRipple(hint,{clientX:60,clientY:0},rect,state);
  assert(ripples.length===2,'movimiento reducido desactiva ondas');
`);
eval(`{
  let classes=new Set(),hint={hidden:true,classList:{contains:x=>classes.has(x),add:x=>classes.add(x),remove:(...xs)=>xs.forEach(x=>classes.delete(x))}},rippleState=null,hintTimer=null,timer=null;
  const setTimeout=(fn,delay)=>{timer={fn,delay};return timer},clearTimeout=value=>{if(value===timer)timer=null};
  ${source.slice(source.indexOf('  function showHint(){'),source.indexOf('  input.onchange=',source.indexOf('  function showHint(){')))}
  reduced=false;showHint();assert(!hint.hidden&&classes.has('is-entering'),'entrada visible');
  hideHint(false);assert(classes.has('is-leaving')&&timer.delay===200,'salida breve');
  showHint();assert(!hint.hidden&&classes.has('is-entering')&&!timer,'reingresar cancela salida');
  hideHint(true);assert(classes.has('is-dropped')&&timer.delay===400,'soltar anima 400 ms');
  timer.fn();assert(hint.hidden,'oculta tras animación');
  reduced=true;showHint();hideHint(true);assert(hint.hidden,'movimiento reducido oculta inmediatamente');
}`);

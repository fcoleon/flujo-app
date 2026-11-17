// Run with: node tests/test_attachments.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=(value,message)=>{if(!value)throw Error(message)};
eval(source.slice(source.indexOf('function attachmentType('),source.indexOf('function renderAttachments('))+`
  assert(attachmentType('documento.docx')==='Documento Word','documento Word');
  assert(attachmentType('informe.PDF')==='PDF','extensiones sin distinción de mayúsculas');
  assert(attachmentType('archivo.xyz')==='.xyz','extensión desconocida');
  assert(attachmentType('archivo')==='Archivo','sin extensión');
`);
let inside={},outside={},card={open:true,contains:target=>target===inside},handler;
let list={querySelectorAll:()=>[card]},dialog={addEventListener:(event,callback)=>{if(event==='click')handler=callback}};
eval(source.match(/dialog\.addEventListener\('click',e=>\{list\.querySelectorAll\('details\.attachment-card\[open\]'\).*?\}\);/)[0]);
handler({target:inside});assert(card.open,'clic dentro conserva el menú');
handler({target:outside});assert(!card.open,'clic fuera cierra el menú');

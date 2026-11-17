// Run with: node tests/test_description_copy.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=require('assert');
let copied,cleanup,button,layer,notices=[];
const block={innerText:'  línea 1\nlínea 2',getBoundingClientRect:()=>({top:20,right:90})};
const field={getBoundingClientRect:()=>({top:10,right:100}),appendChild(node){layer=node}};
const editor={parentElement:field,querySelectorAll:()=>[block]};
const document={createElement(tag){let node={style:{},children:[],setAttribute(){},appendChild(child){this.children.push(child);button=child},replaceChildren(){this.children=[]}};return node}};
const navigator={clipboard:{async writeText(text){copied=text}}};
const toast=message=>notices.push(message);
class MutationObserver{constructor(fn){this.fn=fn}observe(){}disconnect(){}}
class ResizeObserver{constructor(fn){this.fn=fn}observe(){}disconnect(){}}
let descriptionCodeCleanup;
eval(source.slice(source.indexOf('function setupDescriptionCodeCopy('),source.indexOf('function formatDescription('))+`
  setupDescriptionCodeCopy(editor);
  assert.strictEqual(layer.children.length,1);
  assert.strictEqual(button.style.top,'16px');
  cleanup=descriptionCodeCleanup;
  button.onclick().then(()=>{assert.strictEqual(copied,block.innerText);assert.deepStrictEqual(notices,['Código copiado']);cleanup()});
`);

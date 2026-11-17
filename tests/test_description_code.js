// Run with: node tests/test_description_code.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=require('assert');
let command,selected,dispatched=false,prevented=false,remaining='',extra=false;
const block={nodeType:1,parentNode:null,nextSibling:null,childNodes:[],closest:()=>block,after:line=>{block.nextSibling=line}};
const editor={focus(){},contains:node=>node===block,dispatchEvent(){dispatched=true}};
block.parentNode=editor;
const range={cloneRange(){return this},setEnd(){},toString(){return remaining},cloneContents(){return {querySelector:()=>extra}},selectNodeContents(node){selected=node},collapse(){}};
const selection={anchorNode:block,isCollapsed:true,rangeCount:1,getRangeAt:()=>range,removeAllRanges(){},addRange(){}};
const getSelection=()=>selection,$=()=>editor;
const document={createRange:()=>range,execCommand:(...args)=>{command=args},createElement:()=>({appendChild(){}})};
eval(source.slice(source.indexOf('function formatDescription('),source.indexOf('function addDescriptionLink('))+`
  formatDescription('formatBlock','pre');
  assert.strictEqual(selected,block);assert.strictEqual(command[2],'div');
  let event={key:'ArrowDown',currentTarget:editor,preventDefault(){prevented=true}};
  remaining='\\nnext';descriptionCodeKeydown(event);assert(!prevented);
  remaining='';descriptionCodeKeydown(event);assert(prevented&&dispatched&&block.nextSibling);
  prevented=false;descriptionCodeKeydown(event);assert(!prevented);
`);

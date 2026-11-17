// Run with: node tests/test_status_duration.js
const source=require('fs').readFileSync('static/app.js','utf8');
const assert=require('assert');
eval(source.slice(source.indexOf('function statusDurationLabel('),source.indexOf('\n',source.indexOf('function statusDurationLabel(')))+`
  assert.strictEqual(statusDurationLabel(60),'1 min');
  assert.strictEqual(statusDurationLabel(90000),'1 día 1 hora');
`);

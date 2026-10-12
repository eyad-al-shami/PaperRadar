'use strict';
// Execute the actual translator against synthetic Zotero/DOM APIs; no library writes or downloads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('research_tagger/static/zotero/Research Tagger.js', 'utf8');
const split = source.indexOf('\n}\n') + 2;
const metadata = JSON.parse(source.slice(0, split));
assert.equal(metadata.translatorType, 4);
assert.match('http://127.0.0.1:8000/', new RegExp(metadata.target));
assert.match('http://localhost:8123/', new RegExp(metadata.target));
assert.doesNotMatch('https://example.org/', new RegExp(metadata.target));
let state = null, response, selection, choices, requests = [], saved = [];
const doc = {querySelector: () => ({textContent:JSON.stringify(state)})};
const runtime = vm.createContext({
  requestJSON: async url => { requests.push(url); return response; },
  Zotero:{
    selectItems: async items => { choices = items; return selection; },
    Item: function(type) { this.itemType = type; this.complete = async () => saved.push(JSON.parse(JSON.stringify(this))); }
  }
});
vm.runInContext(source.slice(split), runtime);
const paper = {id:1,title:'Example paper',abstract:'Saved abstract',conference:'ICML',year:2026,
  source_url:'https://icml.cc/paper?x=1&y=2',pdf_url:'https://openreview.net/pdf?id=example',arxiv_id:'2401.12345v1',tags:['World models']};
(async () => {
  assert.equal(runtime.detectWeb(doc, 'http://localhost:8000/'), false);
  state = {version:1, mode:'single', endpoint:'/api/conferences/1/zotero?paper_id=1', count:1};
  assert.equal(runtime.detectWeb(doc, 'http://localhost:8000/'), 'conferencePaper');
  assert.equal(runtime.detectWeb(doc, 'https://example.org/'), false);
  response = {version:1, papers:[paper]};
  await runtime.doWeb(doc, 'http://localhost:8000/');
  assert.deepEqual(requests, ['http://localhost:8000/api/conferences/1/zotero?paper_id=1']);
  assert.equal(saved[0].itemType, 'conferencePaper');
  assert.equal(saved[0].abstractNote, paper.abstract);
  assert.equal(saved[0].url, paper.pdf_url);
  assert.deepEqual(saved[0].tags, [{tag:'World models'}]);
  assert.deepEqual(saved[0].attachments, [{title:'Full Text PDF',url:paper.pdf_url,mimeType:'application/pdf',proxy:false}]);
  assert.match(saved[0].notes[0].note, /&amp;/);
  assert.equal(saved[0].extra, 'arXiv: 2401.12345v1');
  state = {version:1, mode:'multiple', endpoint:'/api/conferences/1/zotero?domain=world_models&domain=other', count:35};
  response = {version:1, papers:Array.from({length:35}, (_,i) => ({...paper,id:i+1,pdf_url:i === 34 ? null : paper.pdf_url}))};
  selection = {'1':'Example paper','35':'Example paper'};
  saved = [];
  await runtime.doWeb(doc, 'http://127.0.0.1:8123/');
  assert.equal(Object.keys(choices).length, 35, 'Batch selection includes records beyond the table page');
  assert.match(choices['35'], /no PDF link/);
  assert.equal(saved.length, 2);
  assert.deepEqual(saved[1].attachments, []);
  assert.equal(saved[1].url, 'https://arxiv.org/abs/2401.12345v1');
  assert.equal(requests.at(-1), 'http://127.0.0.1:8123'+state.endpoint);
  selection = false; saved = [];
  await runtime.doWeb(doc, 'http://localhost:8000/');
  assert.equal(saved.length, 0, 'Cancelling selection writes no items');
  // The context and response are captured before the picker opens.
  runtime.Zotero.selectItems = async () => { state = null; return {'1':'Example paper'}; };
  await runtime.doWeb(doc, 'http://localhost:8000/');
  assert.equal(saved.length, 1);
  state = {version:1,mode:'single',endpoint:'https://example.org/steal',count:1};
  assert.throws(() => runtime.detectWeb(doc,'http://localhost:8000/'), /Unsupported/);
  state.endpoint = '/api/settings?';
  assert.throws(() => runtime.detectWeb(doc,'http://localhost:8000/'), /Unsupported/);
  state.endpoint = '/api/conferences/1/zotero?paper_id=1';
  response = {version:2,papers:[paper]};
  await assert.rejects(runtime.doWeb(doc,'http://localhost:8000/'), /Invalid/);
  response = {version:1,papers:[]};
  await assert.rejects(runtime.doWeb(doc,'http://localhost:8000/'), /no longer available/);
  console.log('Zotero translator checks passed: single, batch, cancellation, missing PDFs, scope and version validation.');
})().catch(error => { console.error(error); process.exitCode = 1; });

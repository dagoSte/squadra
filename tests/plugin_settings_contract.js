// Check plugin settings with the shared DOM model and synthetic server replies.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const before="fetch:async path=>{app.fetched.push(path);return path.startsWith('/api/artifact/')?{ok:true,text:async()=>'artifact'}:{ok:true,json:async()=>clone(app.fixture)}}";
if(!prefix.includes(before))throw Error('base harness seam changed');
prefix=prefix.replace(before,`fetch:async(path,options)=>{app.fetched.push(path);if(options?.method==='POST'){(app.posts||(app.posts=[])).push([path,options]);return app.post(path,options)}return {ok:true,json:async()=>clone(path.startsWith('/api/plugin/')?{name:path.split('/').at(-1),key:app.fixture.plugins.key,panel:app.payload||null}:app.fixture)}}`);
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
prefix=prefix.replace('sessionStorage:{setItem(){},getItem()', 'sessionStorage:{setItem(k,v){(app.sessionWrites||(app.sessionWrites=[])).push([k,v])},getItem()');
const cases=String.raw`
Element.prototype.setSelectionRange=function(start,end){this.selectionStart=start;this.selectionEnd=end};
const SECRET='Synthetic-Only!Key-123';
const content=app=>app.doc.getElementById('content');
const field=(app,id)=>app.find('settings:'+id+':input');
const change=(app,id,value)=>{const input=field(app,id);assert.ok(input);input.value=value;input.oninput();return input};
const response=(fields,ok=true,status=200)=>({ok,status,json:async()=>({settings:fields})});
const saved=()=>[{id:'address',set:true,value:'https://example.invalid/'},{id:'agent_key',set:true},{id:'label',set:true,value:'Saved note'}];
async function setup(opts={}){
 const app=await boot(opts);
 app.post=async()=>response(saved());
 app.select=async(name='example')=>{const b=app.doc.getElementById('plugin-links').children.find(b=>key(b)==='nav:plugin:'+name);assert.ok(b);b.onclick();await settle()};
 await app.select();return app;
}
function privateUI(app){
 assert.ok(!JSON.stringify(app.writes).includes(SECRET),'no localStorage secret');
 assert.ok(!JSON.stringify(app.sessionWrites).includes(SECRET),'no sessionStorage secret');
 assert.ok(!text(content(app)).includes(SECRET),'no visible secret echo');
 assert.equal(all(content(app),e=>e.tag==='form').length,0,'fetch submission outside a form');
}
const tests={
 async card(){
  const app=await setup({stored:{'squadra-theme':'dark','squadra-sidebar':'collapsed'}});
  const card=all(content(app),e=>e.className==='settings-card')[0];assert.ok(card,'card despite absent panel');
  assert.match(text(content(app)),/Impostazioni/);assert.match(text(content(app)),/Nessun dato/);
  assert.equal(field(app,'agent_key').getAttribute('type'),'password');
  assert.equal(field(app,'agent_key').getAttribute('autocomplete'),'new-password');
  assert.equal(field(app,'agent_key').value,'');assert.equal(all(content(app),e=>e.tag==='input').length,3);
  assert.equal(all(content(app),e=>['img','script','iframe','a'].includes(e.tag)).length,0);
  assert.ok(all(card,e=>e.tag==='label').every(e=>e.getAttribute('for')),'associated labels');
  assert.ok(all(card,e=>e.getAttribute('aria-live')==='polite').length);
  app.run("setLanguage('en')");await settle();assert.match(text(content(app)),/Settings.*Save/);
  app.doc.getElementById('theme').children[1].onclick();await settle();assert.equal(app.doc.documentElement.getAttribute('data-theme'),'light');
  const next=clone(app.fixture);next.plugins.plugins[0].settings[1].set=true;await app.refresh(next);
  assert.match(text(content(app)),/Saved ✓/);assert.equal(field(app,'agent_key').value,'','saved secret never prefilled');privateUI(app);
 },
 async save(){
  const app=await setup({language:'en-US'});let finish;app.post=()=>new Promise(resolve=>{finish=resolve});
  change(app,'address','https://example.invalid/');change(app,'label','Saved note');const input=change(app,'agent_key',SECRET);
  let prevented=false;input.onkeydown({key:'Enter',preventDefault(){prevented=true}});assert.ok(prevented);
  assert.equal(input.value,'','cleared when handed to fetch');assert.equal(app.find('settings:save').disabled,true);
  app.find('settings:save').onclick();assert.equal(app.posts.length,1,'duplicate submission refused');
  const [path,options]=app.posts[0];assert.equal(path,'/api/plugin/example/settings');
  assert.deepEqual(clone(options.headers),{'X-Squadra-Token':'token','Content-Type':'application/json'});
  assert.deepEqual(JSON.parse(options.body),{values:{address:'https://example.invalid/',agent_key:SECRET,label:'Saved note'}});
  assert.match(text(content(app)),/Saving/);finish(response(saved()));await settle();
  assert.match(text(content(app)),/Settings saved/);assert.equal(input.value,'');assert.equal(app.find('settings:save').disabled,false);privateUI(app);
  app.post=async()=>response(saved());app.find('settings:save').onclick();await settle();
  assert.deepEqual(JSON.parse(app.posts.at(-1)[1].body),{values:{}},'blank untouched password means keep');
  app.find('settings:agent_key:clear').onclick();assert.match(text(content(app)),/Will clear on save/);
  app.post=async()=>response(saved().map(f=>f.id==='agent_key'?{id:f.id,set:false}:f));app.find('settings:save').onclick();await settle();
  assert.deepEqual(JSON.parse(app.posts.at(-1)[1].body),{values:{agent_key:null}});assert.match(text(content(app)),/Not set/);privateUI(app);
 },
 async poll(){
  const app=await setup();const password=change(app,'agent_key',SECRET),input=change(app,'label','unsaved');
  input.focus();input.setSelectionRange(2,5);
  const card=all(content(app),e=>e.className==='settings-card')[0];
  const next=clone(app.fixture);next.plugins.plugins[0].settings[2].value='server value';next.plugins.plugins[0].settings[2].set=true;
  await app.refresh(next);assert.equal(field(app,'label'),input);assert.equal(input.value,'unsaved');assert.equal(app.doc.activeElement,input);
  assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,5);assert.equal(field(app,'agent_key'),password);assert.equal(password.value,SECRET);
  assert.equal(all(content(app),e=>e.className==='settings-card')[0],card,'live node retained, no draft password cache');
  app.run("setLanguage('en')");await settle();assert.equal(field(app,'label'),input);assert.equal(input.value,'unsaved');assert.equal(password.value,SECRET);
  privateUI(app);await app.tab('Quadro');assert.equal(password.value,'','leave clears password');
  const before=app.fetched.filter(p=>p.startsWith('/api/plugin/')).length;await app.refresh();assert.equal(app.fetched.filter(p=>p.startsWith('/api/plugin/')).length,before);
 },
 async failure(){
  const app=await setup({language:'en-US'});
  for(const status of [400,403,409,500]){
   app.post=async()=>({ok:false,status,text:async()=>{assert.fail('never render error body '+SECRET)}});
   change(app,'agent_key',SECRET);app.find('settings:save').onclick();await settle();
   assert.equal(field(app,'agent_key').value,'');assert.match(text(content(app)),status<500?/Save refused/:/not confirmed/);privateUI(app);
  }
  app.post=async()=>{throw Error(SECRET)};change(app,'agent_key',SECRET);app.find('settings:save').onclick();await settle();assert.match(text(content(app)),/not confirmed/);privateUI(app);
  app.post=async()=>({ok:true,json:async()=>{throw Error(SECRET)}});app.find('settings:save').onclick();await settle();assert.match(text(content(app)),/not confirmed/);
  let finish;app.post=()=>new Promise(resolve=>{finish=resolve});change(app,'agent_key',SECRET);app.find('settings:save').onclick();
  await app.tab('Quadro');finish(response(saved()));await settle();assert.doesNotMatch(text(content(app)),/Settings saved/);privateUI(app);
 },
 async schema(){
  const app=await setup({language:'en-US'});const password=change(app,'agent_key',SECRET);
  const next=clone(app.fixture);next.plugins.plugins[0].settings_error='settings_invalid';await app.refresh(next);
  assert.match(text(content(app)),/Settings unavailable/);assert.match(text(content(app)),/Status unavailable/);
  const changed=clone(next);delete changed.plugins.plugins[0].settings_error;changed.plugins.plugins[0].settings[1].id='new_key';await app.refresh(changed);
  assert.equal(password.value,'','schema replacement discards typed secret');assert.equal(field(app,'new_key').value,'');assert.equal(field(app,'agent_key'),null);
  delete changed.plugins.plugins[0].settings;await app.refresh(changed);assert.equal(all(content(app),e=>e.className==='settings-card').length,0);
  privateUI(app);
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('settings contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

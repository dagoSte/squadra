// Check plugin introductions with the page scripts, shared DOM model and synthetic replies.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const before='json:async()=>clone(app.fixture)';
if(!prefix.includes(before))throw Error('base harness seam changed');
prefix=prefix.replace(before,"json:async()=>clone(path.startsWith('/api/plugin/')?await app.reply(path):app.fixture)");
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const content=app=>app.doc.getElementById('content');
const local=(it,en)=>({it,en});
const hostile='<img src=x onerror=alert(1)> https://example.invalid/ <script>literal</script>';
const about=()=>({publisher:'Example <b>team</b>',version:'1.2.3',tagline:local('Ricorda il lavoro '+hostile,'Remember the work '+hostile),sections:[
 {id:'purpose',title:local('A cosa serve '+hostile,'What it does '+hostile),text:local('Un paragrafo\n'+hostile,'A paragraph\n'+hostile),items:[local('Prima voce '+hostile,'First item '+hostile),local('Seconda voce','Second item')]},
 {id:'steps',title:local('Come iniziare','Getting started'),items:[local('Apri le impostazioni','Open settings')]},
 {id:'limits',title:local('Limiti','Limits'),text:local('Nessuna approvazione automatica','No automatic approval')}]});
const fields=()=>[{id:'address',kind:'url',label:local('Indirizzo','Address'),set:false},
 {id:'key',kind:'secret',label:local('Chiave','Key'),set:false}];
const panel=()=>({schema:1,updated:'2026-10-11T12:00:00Z',summary:local('Il lavoro conservato','Saved work'),items:[{id:'one',title:'Recorded title',text:'Recorded body',status:'draft',meta:[]}]});
const tabs=app=>all(content(app),e=>e.getAttribute('role')==='tab');
const pane=app=>one(content(app),e=>e.getAttribute('role')==='tabpanel'&&!e.hidden,'active panel');
const tabButton=(app,name)=>one(content(app),e=>key(e)==='plugin:tab:'+name,'tab '+name);
const panelRequests=app=>app.fetched.filter(p=>p.startsWith('/api/plugin/')).length;
async function choose(app,name){tabButton(app,name).onclick();await settle()}
async function setup({withAbout=true,withSettings=true,...opts}={}){
 const fixture=clone(base);fixture.plugins={key:'a'.repeat(64),refused:[],plugins:[{schema:1,name:'example',title:local('Esempio','Example'),description:local('Dati locali','Local data'),...(withAbout?{about:about()}:{}),...(withSettings?{settings:fields()}: {})}]};
 const app=await boot({...opts,fixture});app.reply=async()=>({name:'example',key:fixture.plugins.key,panel:panel()});
 app.select=async()=>{one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:example').onclick();await settle()};
 await app.select();return app;
}
function selection(app,name){
 const selected=tabs(app).filter(b=>b.getAttribute('aria-selected')==='true');assert.equal(selected.length,1,'one selected tab');assert.equal(key(selected[0]),'plugin:tab:'+name);
 assert.equal(selected[0].getAttribute('tabindex'),'0');assert.ok(tabs(app).filter(b=>b!==selected[0]).every(b=>b.getAttribute('tabindex')==='-1'));
 const panels=all(content(app),e=>e.getAttribute('role')==='tabpanel');assert.equal(panels.length,tabs(app).length);
 for(const b of tabs(app)){const p=panels.find(p=>p.id===b.getAttribute('aria-controls'));assert.ok(p,'tab controls a real panel');assert.equal(p.getAttribute('aria-labelledby'),b.id);assert.equal(p.hidden,b!==selected[0])}
 assert.equal(pane(app).getAttribute('tabindex'),'0');
}
const tests={
 async defaults(){
  const app=await setup();assert.equal(all(content(app),e=>e.getAttribute('role')==='tablist').length,1);
  assert.deepEqual(tabs(app).map(b=>b.textContent),['Panoramica','Impostazioni','Memorie']);selection(app,'overview');
  assert.match(text(content(app)),/Esempio.*Example <b>team<\/b> · versione 1.2.3.*Ricorda il lavoro/);
  assert.equal(all(pane(app),e=>e.tag==='section').length,3);assert.equal(all(pane(app),e=>e.tag==='ul').length,2);assert.equal(all(pane(app),e=>e.tag==='li').length,3);
  assert.equal(panelRequests(app),0,'overview entry needs no panel request');assert.equal(all(content(app),e=>e.tag==='input').length,0);
  const plain=await setup({withSettings:false});assert.deepEqual(tabs(plain).map(b=>b.textContent),['Panoramica','Memorie']);selection(plain,'overview');
 },
 async keyboard(){
  const app=await setup();tabButton(app,'overview').focus();
  for(const [keyName,expected] of [['ArrowRight','settings'],['ArrowRight','memories'],['ArrowRight','overview'],['ArrowLeft','memories'],['Home','overview'],['End','memories']]){
   let prevented=false;app.doc.activeElement.onkeydown({key:keyName,preventDefault(){prevented=true}});await settle();
   assert.ok(prevented);selection(app,expected);assert.equal(key(app.doc.activeElement),'plugin:tab:'+expected,'arrow activation moves focus');
  }
  let prevented=false;app.doc.activeElement.onkeydown({key:'Tab',preventDefault(){prevented=true}});assert.equal(prevented,false,'Tab remains native');
  await choose(app,'settings');await app.refresh();assert.equal(key(app.doc.activeElement),'plugin:tab:settings');selection(app,'settings');
  const missing=await setup({withSettings:false});tabButton(missing,'overview').onkeydown({key:'ArrowRight',preventDefault(){}});await settle();selection(missing,'memories');
 },
 async literal_languages(){
  const app=await setup({stored:{'squadra-theme':'dark'}});
  for(const lang of ['it','en']){
   app.run('setLanguage('+JSON.stringify(lang)+')');await settle();selection(app,'overview');
   const a=about();assert.ok(text(content(app)).includes(a.tagline[lang]));
   for(const section of a.sections){assert.ok(text(pane(app)).includes(section.title[lang]));if(section.text)assert.ok(text(pane(app)).includes(section.text[lang]));for(const item of section.items||[])assert.ok(text(pane(app)).includes(item[lang]))}
   assert.equal(all(content(app),e=>['img','script','iframe','a'].includes(e.tag)).length,0,'supplied text is never markup or links');
   assert.doesNotMatch(text(content(app)),/⟦/);assert.match(text(content(app)),lang==='en'?/version 1.2.3/:/versione 1.2.3/);
   assert.deepEqual(tabs(app).map(b=>b.textContent),lang==='en'?['Overview','Settings','Memories']:['Panoramica','Impostazioni','Memorie']);
  }
  assert.equal(app.doc.documentElement.getAttribute('data-theme'),'dark');app.doc.getElementById('theme').children[1].onclick();await settle();assert.equal(app.doc.documentElement.getAttribute('data-theme'),'light');
  const phone=await setup({phone:true});assert.doesNotMatch(phone.doc.getElementById('app').className,/drawer-open/);selection(phone,'overview');
 },
 async polling(){
  const app=await setup();for(let i=0;i<3;i++)await app.refresh();assert.equal(panelRequests(app),0,'overview polling does not fetch panel');
  await choose(app,'settings');for(let i=0;i<2;i++)await app.refresh();assert.equal(panelRequests(app),0,'settings polling does not fetch panel');
  await choose(app,'memories');assert.equal(panelRequests(app),1,'one request on entering memories');assert.match(text(pane(app)),/Il lavoro conservato.*Recorded title.*Recorded body/);
  assert.equal(all(pane(app),e=>e.className==='settings-card').length,0);await app.refresh();assert.equal(panelRequests(app),2,'one panel request per state refresh');
  app.run("setLanguage('en')");await settle();assert.equal(panelRequests(app),2,'language switch adds no request');
  await choose(app,'overview');await app.refresh();assert.equal(panelRequests(app),2,'leaving memories stops panel polling');
  let finish;app.reply=()=>new Promise(resolve=>{finish=resolve});await choose(app,'memories');await choose(app,'overview');
  const late=panel();late.items[0].title='Late hidden response';finish({name:'example',key:'a'.repeat(64),panel:late});await settle();selection(app,'overview');assert.doesNotMatch(text(pane(app)),/Recorded title/);assert.doesNotMatch(app.run('JSON.stringify(pluginView.panel)'),/Late hidden response/,'late reply from hidden tab discarded');
  await app.tab('Quadro');const n=panelRequests(app);await app.refresh();assert.equal(panelRequests(app),n,'closed plugin stays quiet');
  assert.ok(app.fetched.every(p=>p==='/api/state'||p==='/api/plugin/example'),'no discovery or external requests');
 },
 async settings(){
  const app=await setup();await choose(app,'settings');const password=app.find('settings:key:input'),address=app.find('settings:address:input');
  assert.ok(password&&address,'existing settings controls remain');password.value='Synthetic-Only-Key';password.oninput();address.value='https://example.invalid';address.oninput();address.focus();
  await app.refresh();assert.equal(app.find('settings:address:input'),address);assert.equal(app.doc.activeElement,address);assert.equal(password.value,'Synthetic-Only-Key');
  app.run("setLanguage('en')");await settle();assert.equal(app.find('settings:address:input'),address);assert.match(text(pane(app)),/Settings.*Address.*Key.*Save/);
  await choose(app,'overview');assert.equal(password.value,'','leaving settings clears the typed secret');assert.equal(app.find('settings:key:input'),null,'no hidden field focus target');
  await choose(app,'settings');assert.equal(app.find('settings:key:input').value,'');assert.equal(panelRequests(app),0);
 },
 async fallback(){
  const app=await setup({withAbout:false});assert.equal(tabs(app).length,0,'legacy plugin has no new tabs');
  assert.match(text(content(app)),/Esempio.*Impostazioni.*Il lavoro conservato.*Recorded title/);assert.equal(panelRequests(app),1);
  await app.refresh();assert.equal(panelRequests(app),2);assert.ok(app.find('settings:key:input'));
  const plain=await setup({withAbout:false,withSettings:false});assert.equal(tabs(plain).length,0);assert.equal(all(content(plain),e=>e.tag==='input').length,0);assert.match(text(content(plain)),/Recorded title/);
 },
 async lifecycle(){
  const app=await setup();await choose(app,'settings');const next=clone(app.fixture);delete next.plugins.plugins[0].settings;await app.refresh(next);selection(app,'overview');
  assert.equal(tabs(app).length,2);assert.equal(key(app.doc.activeElement),'plugin:heading','removed tab has a local focus fallback');
  await choose(app,'memories');await app.tab('Quadro');await app.select();selection(app,'overview','re-entry starts at the description');
  const legacy=clone(app.fixture);delete legacy.plugins.plugins[0].about;await app.refresh(legacy);assert.equal(tabs(app).length,0);assert.match(text(content(app)),/Recorded title/);
  const restored=clone(legacy);restored.plugins.plugins[0].about=about();await app.refresh(restored);selection(app,'overview');
  const gone=clone(restored);gone.plugins.plugins=[];await app.refresh(gone);assert.match(text(content(app)),/Plugin non più disponibile/);assert.equal(tabs(app).length,0);
 }
};
(async()=>{assert.ok(tests[scenario],'known scenario');await tests[scenario]();console.log('plugin about contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

// Exercise the shipped scripts using the existing focus-aware DOM harness.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/skills'?await app.reply():app.fixture)");
prefix=prefix.replace('const context={console,Date:PageDate','const context={URL,console,Date:PageDate');
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
async function setup(opts={}){
 const app=await boot(opts);app.data=clone(payload);app.reply=async()=>clone(app.data);app.content=app.doc.getElementById('content');
 app.select=async()=>{const links=app.doc.getElementById('skills-links');assert.equal(links.children.length,1,'System skills has its own sidebar entry');links.children[0].onclick();await settle()};
 app.open=async id=>{assert.ok(app.find('skills:open:'+id),'skill row '+id);app.find('skills:open:'+id).onclick();await settle()};
 return app;
}
const tests={
 async directory(){
  const app=await setup();await app.select();
  assert.equal(app.doc.getElementById('skills-links').children[0].getAttribute('aria-current'),'page');
  assert.deepEqual(all(app.content,e=>e.getAttribute('data-skill')).map(e=>e.getAttribute('data-skill')),['errata','previsione','rasoio','bilanciamento']);
  assert.equal(all(app.content,e=>hasClass(e,'sq-skill-spark')).length,2,'read-only forecasts have no invented sparkline');
  assert.equal(all(app.content,e=>e.textContent==='squadra built-in').length,4);
  assert.match(text(app.content),/Calcolata su richiesta/);
  for(const row of all(app.content,e=>e.getAttribute('data-skill'))){assert.ok(one(row,e=>e.tag==='h3'));assert.ok(all(row,e=>e.tag==='p').length>=2)}
  app.find('skills:range:day').onclick();await settle();assert.equal(app.find('skills:range:day').getAttribute('aria-selected'),'true');
  app.find('skills:range:day').focus();app.find('skills:range:day').onkeydown({key:'ArrowLeft'});await settle();assert.equal(key(app.doc.activeElement),'skills:range:all');assert.equal(app.find('skills:range:all').getAttribute('tabindex'),'0');
  const search=app.find('skills:search');search.value='semplific';search.oninput({target:search});await settle();
  assert.deepEqual(all(app.content,e=>e.getAttribute('data-skill')).map(e=>e.getAttribute('data-skill')),['rasoio']);
  app.doc.activeElement=null;await app.keydown('/');assert.equal(key(app.doc.activeElement),'skills:search');
  const textarea=app.doc.createElement('textarea');textarea.focus();await app.keydown('/');assert.equal(app.doc.activeElement,textarea);
  app.run("skillsUI.query='';setLanguage('en')");await settle();assert.match(text(app.content),/System skills/);assert.match(text(app.content),/Last 24 h/);assert.doesNotMatch(text(app.content),/⟦/);
  unique(app,'skills list');
 },
 async detail(){
  const app=await setup();await app.select();await app.open('errata');
  assert.equal(key(app.doc.activeElement),'skills:title');assert.match(text(app.content),/Incarico di origine: Validate the example/);
  assert.match(text(app.content),/coppie distinte/);assert.match(text(app.content),/Eventi di richiamo/);assert.match(text(app.content),/Fonte esterna/);
  assert.equal(app.find('skills:mode:trigger').getAttribute('aria-selected'),'true');
  app.find('skills:mode:trigger').focus();app.find('skills:mode:trigger').onkeydown({key:'End'});await settle();assert.equal(key(app.doc.activeElement),'skills:mode:prompt');
  app.find('skills:mode:prompt').onclick();await settle();assert.equal(app.find('skills:mode:prompt').getAttribute('aria-selected'),'true');
  app.find('skills:event:recent:5').onclick();assert.equal(app.doc.getElementById('artifact').open,true);
  assert.equal(JSON.parse(app.doc.getElementById('artifact-body').textContent).kind,'lesson.retire');app.doc.getElementById('artifact').close();
  app.find('skills:task:recent:5').onclick();await settle();assert.equal(app.run('panelUI.story'),'T');app.doc.getElementById('story').close();app.run('panelStoryClosed()');await settle();
  app.find('skills:more:previsione').onclick();await settle();assert.match(text(app.content),/Calcolata su richiesta/);
  assert.equal(all(app.content,e=>String(key(e)).startsWith('skills:event:')).length,0);
  app.find('skills:back').onclick();await settle();assert.equal(key(app.doc.activeElement),'skills:search');unique(app,'skill detail');
 },
 async sidebarBack(){
  const ids=['errata','previsione','rasoio','bilanciamento'];
  for(const language of ['it-IT','en-US'])for(const phone of [false,true]){
   const app=await setup({language,phone});await app.select();
   for(const id of ids){
    await app.open(id);app.run("skillsUI.expanded=true;skillsUI.mode='prompt';render()");
    const fetched=app.fetched.filter(p=>p==='/api/skills').length;
    if(phone)app.doc.getElementById('menu').onclick();
    await app.select();
    assert.deepEqual(all(app.content,e=>e.getAttribute('data-skill')).map(e=>e.getAttribute('data-skill')),ids,'sidebar returns from '+id+' to the list');
    assert.equal(app.run('skillsUI.selected'),null);assert.equal(app.run('skillsUI.expanded'),false);assert.equal(app.run('skillsUI.mode'),'trigger');
    assert.equal(key(app.doc.activeElement),'skills:search','same focus as breadcrumb');
    assert.equal(app.fetched.filter(p=>p==='/api/skills').length,fetched,'same fresh data as breadcrumb');
    assert.equal(app.run('panelUI.drawer'),false);
   }
   await app.open('errata');await app.tab('Finestre');
   assert.equal(app.run('tab'),'Finestre');assert.equal(app.run('skillsUI.selected'),'errata','other menu entries keep their behaviour');
   await app.select();assert.equal(app.run('skillsUI.selected'),null);assert.ok(app.find('skills:open:errata'));
   app.run("skillsUI.query='rasoio';skillsUI.range='day';render()");await app.open('rasoio');await app.select();
   assert.equal(app.find('skills:search').value,'rasoio');assert.equal(app.find('skills:range:day').getAttribute('aria-selected'),'true');
   assert.deepEqual(all(app.content,e=>e.getAttribute('data-skill')).map(e=>e.getAttribute('data-skill')),['rasoio'],'list filters retained like breadcrumb');
  }
 },
 async safeGuide(){
  const app=await setup();const guide='# Synthetic guide\n<script>evil()</script>\n![pixel](https://example.invalid/pixel.png)\n[bad](javascript:evil)\n[good](https://example.invalid/help)\n'+Array.from({length:30},(_,i)=>'Line '+i).join('\n');
  app.data.skills[0].guide=guide;await app.select();await app.open('errata');
  assert.equal(all(app.content,e=>['script','img','iframe'].includes(e.tag)).length,0);
  assert.match(text(app.content),/<script>evil/);assert.doesNotMatch(text(app.content),/Line 29/);
  const link=one(app.content,e=>e.tag==='a'&&e.textContent==='good');assert.equal(link.getAttribute('rel'),'noopener noreferrer');
  assert.equal(all(app.content,e=>e.getAttribute('href')?.startsWith('javascript:')).length,0);
  app.find('skills:guide').onclick();await settle();assert.match(text(app.content),/Line 29/);
  assert.equal(app.run('skillsUI.expanded'),true);await app.refresh();assert.equal(app.run('skillsUI.expanded'),true);
  assert.equal(app.fetched.filter(p=>!p.startsWith('/api/')).length,0,'no remote guide asset fetches');
 },
 async audits(){
  const app=await setup();for(const [i,status] of ['pass','warn','fail'].entries())app.data.skills[i].audit={status,scanner:'cisco-ai-skill-scanner',version:'2.2.2',scanned_at:'2026-10-14T00:00:00Z',findings:{critical:0,high:status==='fail'?1:0,medium:status==='warn'?1:0,low:0}};
  await app.select();for(const status of ['pass','warn','fail'])assert.equal(all(app.content,e=>hasClass(e,status)).length,1);
  await app.open('rasoio');assert.match(text(app.content),/cisco-ai-skill-scanner 2.2.2/);assert.match(text(app.content),/Alti: 1/);
  app.data.skills[2].audit={status:'not_audited',reason:'files_changed'};await app.refresh();assert.match(text(app.content),/Non verificata per questa versione/);assert.equal(all(app.content,e=>hasClass(e,'fail')||hasClass(e,'pass')).length,0);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/Not audited for this version/);assert.doesNotMatch(text(app.content),/Socket|Snyk|⟦/);
 },
 async polling(){
  const app=await setup();await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/skills').length,0);
  let release;app.reply=()=>new Promise(r=>{release=r});await app.select();await app.refresh();await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/skills').length,1);
  app.reply=async()=>clone(app.data);release(clone(payload));await settle();
  const search=app.find('skills:search');search.focus();const box=app.content.children[0];await app.refresh();
  assert.equal(app.content.children[0],box,'unchanged response retains DOM');assert.equal(key(app.doc.activeElement),'skills:search');
  const count=app.fetched.filter(p=>p==='/api/skills').length;await app.tab('Quadro');await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/skills').length,count);
  await app.select();app.doc.hidden=true;await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/skills').length,count+1);
 },
 async failureAndZero(){
  const app=await setup();app.reply=async()=>{throw Error('offline')};await app.select();assert.match(text(app.content),/non sono disponibili/);assert.ok(app.find('skills:retry'));
  app.reply=async()=>clone(app.data);app.find('skills:retry').onclick();await settle();assert.equal(all(app.content,e=>e.getAttribute('data-skill')).length,4);
  for(const s of app.data.skills)if(s.mode==='recorded'){s.total=0;s.last_24h=0;s.daily=Array(56).fill(0);s.first=null;s.last=null;s.recent=[]}
  await app.refresh();const paths=all(app.content,e=>e.tag==='path'&&e.getAttribute('d')?.startsWith('M1.00,38.00'));
  assert.equal(paths.length,2);for(const p of paths)assert.equal((p.getAttribute('d').match(/,38.00/g)||[]).length,56,'flat real zero');
  await app.open('errata');assert.match(text(app.content),/Nessuna attivazione registrata/);
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('system skills contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(prefix+cases);

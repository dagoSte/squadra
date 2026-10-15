'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
harness=harness.replace('this.children=[];this.append(...children)',"this.textContent='';this.children=[];this.append(...children)");
harness=harness.replace('phone=false,clock}', 'phone=false,clock,http}');
harness=harness.replace('fixture,fetched:[],clock}', 'fixture,fetched:[],clock,http}');
harness=harness.replace('const context={console,', 'const context={AbortController,console,');
harness=harness.replace('setTimeout:fn=>{timers.push(fn)},',
 'setTimeout:(fn,ms)=>{const t={fn,ms};timers.push(t);return t},clearTimeout:t=>{const i=timers.indexOf(t);if(i>=0)timers.splice(i,1)},');
harness=harness.replace("fetch:async path=>{app.fetched.push(path);return", "fetch:async(path,options)=>{app.fetched.push(path);if(app.http)return app.http(path,options);return");
harness=harness.replace("timers.findIndex(fn=>fn.name==='refresh'),fn=i<0?undefined:timers.splice(i,1)[0]", "timers.findIndex(t=>t.fn.name==='refresh'),fn=i<0?undefined:timers.splice(i,1)[0].fn");
const cases=String.raw`
const reply=value=>({ok:true,json:async()=>clone(value),text:async()=>JSON.stringify(value)});
const poll=app=>app.timers.filter(t=>t.fn.name==='refresh');
const deadline=app=>app.timers.find(t=>t.ms===15000);
const visibility=async(app,hidden)=>{app.doc.hidden=hidden;for(const f of app.doc.listeners.visibilitychange||[])f();await settle()};
const tests={
 async audit(){
  for(const language of ['it-IT','en-US']){
   const fixture=clone(base);fixture.audit={status:'running'};
   const app=await boot({language,fixture});const banner=app.doc.getElementById('registry-audit');
   assert.equal(banner.hidden,false);assert.match(page,/id="registry-audit"[^>]*role="status"[^>]*aria-live="polite"/);
   assert.equal(text(banner),language==='it-IT'?'Verifica completa del registro in corso':'Full registry verification in progress');
   assert.ok(app.doc.getElementById('content').children.length,'ordinary verified data is usable during audit');
   app.run("setLanguage('en')");assert.equal(text(banner),'Full registry verification in progress');
   app.run("setLanguage('it')");assert.equal(text(banner),'Verifica completa del registro in corso');
   const complete=clone(fixture);complete.audit.status='complete';app.http=()=>reply(complete);await app.refresh();
   assert.equal(banner.hidden,true,'completed audit removes the pending notice');
   app.doc.getElementById('artifact-body').textContent='OLD-DETAIL-CANARY';app.doc.getElementById('artifact').showModal();
   let late;app.http=()=>new Promise(r=>{late=r});
   const detail=app.run("api('/api/detail/task').then(r=>r.json())");
   const rejected=assert.rejects(detail,/integrità|integrity/);await settle();
   app.http=()=>Promise.resolve({ok:false,status:409,text:async()=> 'integrity_failure'});await app.refresh();
   assert.equal(app.run('state'),null);assert.equal(app.run('loadUI.integrity'),true);
   for(const id of ['content','charts','metrics','connected-hero','plugin-links','artifact-body'])assert.equal(text(app.doc.getElementById(id)),'',id+' must not retain old data');
   assert.equal(app.doc.getElementById('artifact').open,false);assert.equal(banner.hidden,true);
   assert.equal(app.doc.getElementById('loading').hidden,false);
   assert.match(text(app.doc.getElementById('loading-title')),/integrità/);
   assert.match(text(app.doc.getElementById('loading-title')),/squadra doctor/);
   app.run("setLanguage('en')");assert.match(text(app.doc.getElementById('loading-title')),/integrity/);
   assert.match(text(app.doc.getElementById('loading-title')),/squadra doctor/);
   assert.equal(app.doc.getElementById('loading-retry').hidden,true);
   late(reply({body:'OLD-DETAIL-CANARY'}));await rejected;
   assert.equal(poll(app).length,0,'a failed integrity audit stops polling');
   const requests=app.fetched.length;app.http=()=>reply(complete);await app.run('refresh()');
   assert.equal(app.fetched.length,requests,'failed integrity cannot be replaced by a stale success');
  }
 },
 async sections(){
  assert.match(page,/<div\b[^>]*\bid="legacy"[^>]*\bhidden\b/,'overview is hidden before scripts run');
  assert.match(page,/<details\b[^>]*\bid="registry-details"[^>]*\bhidden\b/,'registry is hidden before scripts run');
  for(const language of ['it-IT','en-US']){
   let reject;const app=await boot({language,http:()=>new Promise((resolve,r)=>{reject=r})});
   const legacy=app.doc.getElementById('legacy'),registry=app.doc.getElementById('registry-details');
   const hidden=()=>{assert.equal(legacy.hidden,true);assert.equal(registry.hidden,true)};
   hidden();app.run("setLanguage('en')");hidden();app.run("setLanguage('it')");hidden();
   reject(Error('first request failed'));await settle();hidden();
   assert.equal(app.doc.getElementById('loading-retry').hidden,false);
   app.http=()=>reply(app.fixture);await app.doc.getElementById('loading-retry').onclick();await settle();
   assert.equal(legacy.hidden,false);assert.equal(registry.hidden,false);
   await app.tab('Posta');assert.equal(legacy.hidden,true);assert.equal(registry.hidden,false);
   await app.tab('Quadro');assert.equal(legacy.hidden,false);
   let release;app.http=()=>new Promise(r=>{release=r});const update=app.refresh();await settle();
   assert.equal(legacy.hidden,false);assert.equal(registry.hidden,false,'refresh preserves loaded sections');
   release(reply(app.fixture));await update;
   app.http=()=>Promise.reject(Error('later request failed'));await app.refresh();
   assert.equal(legacy.hidden,false);assert.equal(registry.hidden,false,'failure preserves loaded sections');
   const reload=await boot({language,http:()=>new Promise(()=>{})});
   assert.equal(reload.doc.getElementById('legacy').hidden,true);assert.equal(reload.doc.getElementById('registry-details').hidden,true);
  }
 },
 async initial(){
  let release;const app=await boot({http:()=>new Promise(r=>{release=r})});
  assert.equal(app.doc.getElementById('loading').hidden,false);
  assert.equal(app.doc.getElementById('loading-title').textContent,'Caricamento del progetto…');
  assert.match(text(app.doc.getElementById('footer')),/Sviluppato da Stefano/,'static labels are ready before the response');
  assert.equal(app.doc.getElementById('main').getAttribute('aria-busy'),'true');
  assert.equal(app.fetched.length,1);assert.ok(deadline(app));
  app.run("setLanguage('en')");assert.equal(app.doc.getElementById('loading-title').textContent,'Loading the project…');
  release(reply(app.fixture));await settle();
  assert.equal(app.doc.getElementById('loading').hidden,true);
  assert.equal(app.doc.getElementById('main').getAttribute('aria-busy'),'false');
  assert.equal(deadline(app),undefined,'consuming the body clears the deadline');assert.equal(poll(app).length,1);
  assert.match(page,/prefers-reduced-motion:reduce/);assert.match(page,/id="loading"[^>]+role="status"[^>]+aria-live="polite"/);
 },
 async timeout(){
  let signal;const app=await boot({http:(path,options)=>{signal=options.signal;return Promise.resolve({ok:true,json:()=>new Promise(()=>{})})}});
  assert.ok(deadline(app),'body stalls are covered after headers arrive');
  deadline(app).fn();await settle();
  assert.equal(signal.aborted,true);assert.equal(app.doc.getElementById('loading-retry').hidden,false);
  assert.match(app.doc.getElementById('loading-help').textContent,/15 secondi/);
  assert.equal(app.doc.getElementById('loading-title').textContent,'Il progetto non è stato caricato');
  assert.equal(app.run('state'),null);assert.equal(poll(app).length,1);
  app.http=()=>reply(app.fixture);await app.doc.getElementById('loading-retry').onclick();await settle();
  assert.equal(app.doc.getElementById('loading').hidden,true);assert.equal(poll(app).length,1);
  app.http=()=>Promise.resolve({ok:true,text:()=>new Promise(()=>{})});
  const artifact=app.run("api('/api/artifact/'+ 'a'.repeat(64)).then(r=>r.text())");
  const failed=assert.rejects(artifact,/15 secondi/);await settle();deadline(app).fn();await failed;
 },
 async refresh(){
  assert.match(page,/#live\{[^}]*font-variant-numeric:\s*tabular-nums(?:;|\})/,'the status clock uses tabular figures');
  for(const language of ['it-IT','en-US']){
  const app=await boot({language}),content=app.doc.getElementById('content'),before=content.children.slice();
  const live=app.doc.getElementById('live'),connected=text(live),children=live.children.slice(),label=live.getAttribute('aria-label');
  assert.match(connected,language==='it-IT'?/^Registro connesso · /:/^Registry connected · /);
  let release;app.http=()=>new Promise(r=>{release=r});const update=app.refresh();await settle();
  assert.equal(app.doc.getElementById('loading').hidden,true);
  assert.equal(text(live),connected,'a background poll keeps the connected text');
  assert.equal(live.getAttribute('aria-label'),label);assert.equal(live.className,'status');
  assert.equal(live.children.length,children.length);
  children.forEach((child,index)=>assert.equal(live.children[index],child,'a background poll leaves the status elements in place'));
  assert.doesNotMatch(text(live),/Aggiornamento|Updating/);
  assert.deepEqual(content.children,before,'loaded content remains while refreshing');
  const calls=app.fetched.length;await app.run('refresh()');assert.equal(app.fetched.length,calls,'no overlapping GET');
  await visibility(app,true);release(reply(app.fixture));await update;assert.equal(poll(app).length,0,'hidden page schedules no state polls');
  app.http=()=>reply(app.fixture);await visibility(app,false);assert.equal(app.fetched.length,calls+1);assert.equal(poll(app).length,1);
  app.http=()=>Promise.reject(Error('network unavailable'));await app.refresh();
  assert.match(text(app.doc.getElementById('notice')),language==='it-IT'?/Ultima vista conservata/:/Last view kept/);
  assert.equal(text(live),language==='it-IT'?'Connessione non disponibile':'Connection unavailable');
  assert.equal(app.doc.getElementById('loading').hidden,true);assert.ok(app.run('state'));
  const retry=one(app.doc.getElementById('notice'),e=>e.tag==='button');app.http=()=>reply(app.fixture);await retry.onclick();await settle();
  assert.equal(app.doc.getElementById('notice').children.length,0);assert.equal(poll(app).length,1);
  }
 },
 async search(){
  const fixture=clone(base);fixture.search={query:'',tasks:[],runs:[]};const app=await boot({fixture});
  let release,query;app.http=(path,options)=>{query=decodeURIComponent(options.headers['X-Squadra-Search']);return new Promise(r=>{release=r})};
  const input=app.doc.getElementById('search');input.value='private-file.py';input.oninput({target:input});
  assert.equal(app.run('filter'),'','do not show incomplete technical search results');
  const update=app.refresh();await settle();assert.equal(query,'private-file.py');
  assert.equal(text(app.doc.getElementById('live')),'Ricerca in corso…');
  const response=clone(fixture);response.search={query,tasks:['TASK-KQ1'],runs:[]};
  release(reply(response));await update;
  assert.equal(app.run('matches(state.tasks["TASK-KQ1"])'),true,'hidden inventory match survives compact projection');
  assert.equal(app.run('matches(state.tasks["TASK-KQ3"])'),false);
  input.value='older-query';input.oninput({target:input});const old=app.refresh();await settle();
  input.value='latest-query';input.oninput({target:input});release(reply(response));await old;
  assert.equal(app.run('filter'),'private-file.py','a stale reply cannot replace the last complete result');
  assert.equal(poll(app).length,1);assert.equal(poll(app)[0].ms,0,'the latest query is requested next');
 },
 async details(){
  const app=await boot();let seen;const full={id:'TASK-KQ1',candidate:{files:{'source.py':{sha256:'a'.repeat(64)}}}};
  app.http=(path,options)=>{seen={path,options};return reply(full)};
  const link=app.run("recordLink('task',state.tasks['TASK-KQ1'])");await link.onclick();await settle();
  assert.equal(seen.path,'/api/detail/task');assert.equal(seen.options.headers['X-Squadra-Record'],'TASK-KQ1');
  assert.equal(app.doc.getElementById('artifact').open,true);assert.deepEqual(JSON.parse(app.doc.getElementById('artifact-body').textContent),full);
  assert.equal(link.disabled,false);
 }
};
(async()=>{assert.ok(tests[scenario],scenario);await tests[scenario]();console.log('loading contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+cases);

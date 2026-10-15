// Exercise the shipped Balance page with the existing focus-aware DOM harness.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='fetch:async path=>{app.fetched.push(path);return path.startsWith(\'/api/artifact/\')?{ok:true,text:async()=>\'artifact\'}:{ok:true,json:async()=>clone(app.fixture)}}';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"fetch:async(path,init)=>{app.fetched.push(path);(app.sent=app.sent||[]).push({path,headers:init?.headers||{}});if(path==='/api/balance'){const body=await app.reply(init?.headers||{});return {ok:true,json:async()=>body}}return path.startsWith('/api/artifact/')?{ok:true,text:async()=>'artifact'}:{ok:true,json:async()=>clone(app.fixture)}}");
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const motionSeam=' focus(){this.doc.activeElement=this}';
if(!prefix.includes(motionSeam))throw Error('base harness element seam changed');
prefix=prefix.replace(motionSeam,' animate(frames,options){(this.doc.animations||(this.doc.animations=[])).push({el:this,frames,options});return {cancel(){},finished:Promise.resolve()}}\n'+motionSeam);
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
async function setup(opts={}){
 const app=await boot(opts);app.data=clone(payload.full);app.reply=async()=>clone(app.data);app.content=app.doc.getElementById('content');
 app.select=async()=>{const links=app.doc.getElementById('balance-links');assert.ok(links,'Balance has its own sidebar entry');assert.equal(links.children.length,1);links.children[0].onclick();await settle()};
 return app;
}
const balanceFetches=app=>app.fetched.filter(p=>p==='/api/balance').length;
const tests={
 async page(){
  const app=await setup();await app.refresh();assert.equal(balanceFetches(app),0,'no request before the page opens');
  await app.select();const page=one(app.content,e=>e.getAttribute('data-balance')==='page');assert.ok(page,'Balance page');
  assert.match(text(app.content),/Bilanciamento/);assert.match(text(app.content),/Non assegna nulla/);
  for(const id of ['balance-load','balance-tokens','balance-quota']){const card=one(app.content,e=>e.getAttribute('data-chart')===id);assert.ok(card,id);assert.ok(one(card,e=>e.tag==='table'),id+' table view')}
  const cards=all(app.content,e=>e.getAttribute('data-balance-model')!==null&&hasClass(e,'sq-balance-card'));assert.ok(cards.length>=2,'suggestions grouped by model');
  assert.match(text(app.content),/w-cy ha 2 letture in attesa/);assert.match(text(app.content),/96% della quota settimanale, si azzera/);
  assert.match(text(app.content),/stima 85% delle 5 ore/);
  const est=all(app.content,e=>e.getAttribute('data-balance-estimate')!==null);assert.equal(est.length,2,'two estimate slots');
  assert.ok(all(app.content,e=>hasClass(e,'sq-viz-hatched')).length>=1,'estimate bars are hatched');
  assert.match(text(app.content),/condividano un account/);
  const load=all(app.content,e=>e.getAttribute('data-balance-window')!==null).map(e=>e.getAttribute('data-balance-window'));assert.deepEqual(load,['w-cl','w-coord','w-cx','w-cy']);
  const quota=all(app.content,e=>e.getAttribute('data-balance-quota')==='w-cx');assert.equal(quota.length,2,'both Codex slots shown');
  unique(app,'balance page');
 },
 async zero(){
  const app=await setup();app.data=clone(payload.empty);await app.select();
  assert.match(text(app.content),/Nessuna finestra registrata/);assert.match(text(app.content),/Token e quota non raccolti/);
  assert.match(text(app.content),/serie oraria non disponibile/);assert.equal(one(app.content,e=>e.getAttribute('data-balance-verdict')!==null).getAttribute('data-balance-verdict'),'incomplete');assert.match(text(app.content),/Nessun consiglio con i dati disponibili/);
  assert.equal(all(app.content,e=>e.getAttribute('data-balance-window')!==null||e.getAttribute('data-balance-quota')!==null||e.getAttribute('data-balance-estimate')!==null).length,0,'no bar drawn for missing data');
  app.data=clone(payload.noReference);app.run('balanceUI.immediate=true');await app.refresh();
  assert.match(text(app.content),/Stima non disponibile: nessun riferimento dichiarato\. Ultime 5 ore: 85\.000 token/);
  assert.equal(all(app.content,e=>e.getAttribute('data-balance-estimate')!==null).length,0,'no estimate bar without a reference');
  app.data=clone(payload.partial);app.run('balanceUI.immediate=true');await app.refresh();
  assert.match(text(app.content),/Stima non completa: alcune finestre Claude non sono ancora lette\. Somme note, parziali: ultime 5 ore 0 token/);
  assert.equal(all(app.content,e=>e.getAttribute('data-balance-estimate')!==null).length,2,'slots listed');assert.equal(all(app.content,e=>hasClass(e,'sq-viz-hatched')).length,0,'no hatched bar for a partial estimate');
 },
 async task(){
  const app=await setup();await app.select();const select=app.find('balance:task');assert.ok(select,'open-task selector');
  select.value='T';select.onchange({target:select});await settle();
  const last=app.sent.filter(s=>s.path==='/api/balance').at(-1);assert.equal(last.headers['X-Squadra-Record'],'T');
  const slots=all(app.content,e=>e.getAttribute('data-balance-slot')!==null).map(e=>e.getAttribute('data-balance-slot'));assert.deepEqual(slots,['build','read']);
  const read=one(app.content,e=>e.getAttribute('data-balance-slot')==='read');
  assert.deepEqual(all(read,e=>e.getAttribute('data-balance-rank')!==null).map(e=>e.getAttribute('data-balance-rank')).includes('w-cl'),false,'the author never appears as reader');
  assert.ok(one(read,e=>e.getAttribute('data-balance-excluded')==='w-cl'));assert.match(text(read),/Esclusa: autore dell’incarico/);
  assert.match(text(read),/Criterio «quota» non usato: manca per w-cy/);
  assert.match(text(read),/Ordine usato: passi in attesa > incarichi aperti/);
 },
 async reference(){
  const app=await setup({stored:{'squadra.usage.settings.v1':JSON.stringify({schema:1,prices:{currency:'EUR',models:{}},balance:{}})}});await app.select();
  const h5=app.find('balance:ref:h5'),week=app.find('balance:ref:week');h5.value='abc';week.value='5';app.find('balance:ref:save').onclick();await settle();
  assert.match(text(app.content),/due numeri interi positivi/);
  app.find('balance:ref:h5').value='100000';app.find('balance:ref:week').value='1000000';app.find('balance:ref:save').onclick();await settle();
  const stored=JSON.parse(app.run("localStorage.getItem('squadra.usage.settings.v1')"));assert.deepEqual(stored.prices,{currency:'EUR',models:{}},'price settings preserved');assert.equal(stored.schema,1);
  assert.deepEqual(stored.balance.reference,{h5:100000,week:1000000});
  const last=app.sent.filter(s=>s.path==='/api/balance').at(-1);assert.equal(last.headers['X-Squadra-Balance-Reference'],'{"claude":{"h5":100000,"week":1000000}}');
  app.find('balance:ref:clear').onclick();await settle();const cleared=JSON.parse(app.run("localStorage.getItem('squadra.usage.settings.v1')"));assert.equal('reference' in cleared.balance,false);assert.ok(cleared.prices);
  const after=app.sent.filter(s=>s.path==='/api/balance').at(-1);assert.equal('X-Squadra-Balance-Reference' in after.headers,false);
  const blocked=await setup({storage:'throws'});await blocked.select();assert.ok(one(blocked.content,e=>e.getAttribute('data-balance')==='page'),'works without storage');
 },
 async english(){
  const app=await setup();await app.select();app.run("setLanguage('en')");await settle();
  assert.match(text(app.content),/Balance/);assert.match(text(app.content),/Suggestions per model/);assert.match(text(app.content),/Tokens per hour, last 24 h/);
  assert.match(text(app.content),/estimate 85% of the 5 hours/);assert.doesNotMatch(text(app.content),/⟦/);
 },
 async polling(){
  const app=await setup();let release;app.reply=()=>new Promise(r=>{release=r});await app.select();await app.refresh();await app.refresh();
  assert.equal(balanceFetches(app),1,'serial: no second request while one is pending');app.reply=async()=>clone(app.data);release(clone(payload.full));await settle();
  const select=app.find('balance:task');select.focus();const box=app.content.children[0];await app.refresh();
  assert.equal(app.content.children[0],box,'unchanged response retains DOM');assert.equal(key(app.doc.activeElement),'balance:task');
  const count=balanceFetches(app);await app.tab('Quadro');await app.refresh();assert.equal(balanceFetches(app),count,'no request while the page is closed');
  app.reply=async()=>{throw Error('offline')};await app.select();assert.match(text(app.content),/non è disponibile/);assert.ok(app.find('balance:retry'));
 },
 async injection(){
  const app=await setup();app.data.windows[0].identity='<img src=x onerror=evil()>';app.data.suggestions[0].window='<script>evil()</script>';await app.select();
  assert.equal(all(app.content,e=>['script','img','iframe'].includes(e.tag)).length,0);assert.match(text(app.content),/<script>evil/);
 },
 async models(){
  const app=await setup();app.data=clone(payload.multi);await app.select();
  const series=all(app.content,e=>hasClass(e,'sq-crow')&&e.getAttribute('data-balance-model')!==null).map(e=>e.getAttribute('data-balance-model'));
  assert.ok(series.includes('gpt-5.4')&&series.includes('gpt-5.4-mini'),'both measured series are charted: '+series.join(','));
  assert.match(text(app.content),/gpt-5\.4, il più usato tra quelli misurati: gpt-5\.4, gpt-5\.4-mini/);
  const select=app.find('balance:task');select.value='T';select.onchange({target:select});await settle();
  const build=one(app.content,e=>e.getAttribute('data-balance-slot')==='build');assert.match(text(build),/w-cx · gpt-5\.4, il più usato/);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/gpt-5\.4, the most used of the measured models: gpt-5\.4, gpt-5\.4-mini/);
 },
 async verdicts(){
  for(const [name,kind,pattern] of [['full','advice',/\d+ consigli\./],['balanced','balanced',/La squadra è in equilibrio: oggi nessun consiglio\./],['unknownQuota','incomplete',/Nessun consiglio con i dati disponibili/],['blocked','blocked',/w-cy ha 2 letture in attesa, ma nessun lettore indipendente è libero/],['empty','incomplete',/Nessun consiglio con i dati disponibili/]]){
   const app=await setup();app.data=clone(payload[name]);await app.select();const v=one(app.content,e=>e.getAttribute('data-balance-verdict')!==null,'verdict '+name);
   assert.equal(v.getAttribute('data-balance-verdict'),kind,name);assert.match(text(v),pattern,name);
   if(kind==='advice')assert.ok(one(v,e=>hasClass(e,'sq-balance-cards')),'the advice sits right under the verdict');
   if(kind!=='advice')assert.equal(all(v,e=>hasClass(e,'sq-balance-card')).length,0,name+': no advice card');
  }
 },
 async rules(){
  const app=await setup();await app.select();const table=one(app.content,e=>e.getAttribute('data-balance-rules')!==null);
  const rows=all(table,e=>e.getAttribute('data-balance-rule')!==null);assert.deepEqual(rows.map(r=>r.getAttribute('data-balance-rule')),['reads','quota','tasks','idle']);
  for(const r of rows){assert.equal(r.children.length,4,'four columns');assert.deepEqual(r.children.map(c=>c.getAttribute('data-label')),['Quando scatta','Esempio di consiglio','Oggi','Stato'],'each cell names its column for the phone layout')}
  assert.match(text(rows[0]),/Un lettore ha 2 o più letture in attesa/);assert.match(text(rows[0]),/al massimo 2, w-cy/);assert.equal(rows[0].getAttribute('data-balance-state'),'fires');
  assert.match(text(rows[1]),/Una quota arriva all’80%/);assert.match(text(rows[1]),/al massimo 96%, w-cx/);assert.match(text(rows[1]),/non misurata per 1 finestra\b/);
  const blocked=await setup();blocked.data=clone(payload.blocked);await blocked.select();
  const row=one(blocked.content,e=>e.getAttribute('data-balance-rule')==='reads');assert.equal(row.getAttribute('data-balance-state'),'blocked');assert.match(text(row),/oltre la soglia, nessuna destinazione libera/);
 },
 async plain(){
  const app=await setup();await app.select();const list=one(app.content,e=>e.getAttribute('data-balance-windows')!==null);
  const cxItem=one(list,e=>e.getAttribute('data-balance-plain')==='w-cx'),cx=text(cxItem);assert.equal(all(cxItem,e=>hasClass(e,'sq-balance-line')).length,3,'who, quota and work on three short lines');
  assert.match(cx,/96% della quota settimanale, si azzera alle/);assert.match(cx,/1 incarico aperto, 0 letture in attesa/,'Italian singular and plural');assert.doesNotMatch(text(list),/\b1 (incarichi|letture|finestre)\b/,'no plural after 1');assert.match(cx,/10% della quota delle 5 ore/);assert.match(cx,/600 token all’ora/);assert.match(cx,/legge/);
  const missing=text(one(app.content,e=>e.getAttribute('data-balance-missing')!==null));assert.match(missing,/Quota di w-cy non ancora osservata\./);
  const none=await setup();none.data=clone(payload.noReference);await none.select();assert.match(text(one(none.content,e=>e.getAttribute('data-balance-missing')!==null)),/manca il riferimento/);
  const multi=await setup();multi.data=clone(payload.multi);await multi.select();assert.match(text(one(multi.content,e=>e.getAttribute('data-balance-plain')==='w-cx')),/gpt-5\.4, il più usato tra quelli misurati: gpt-5\.4, gpt-5\.4-mini/);
 },
 async noInternal(){
  const banned=[/\bS[1-4]\b/,/\b(model_source|used_percent|read_steps|build_steps|owned_open|window_minutes|resets_at|tokens_per_hour|measured_models|declared_models|claude_estimate)\b/,
   /\b(not_collected|collector_busy|no_observation|no_reference|partial_series|observation_stale|not_measured|quota_observation|no_source)\b/,/\b(measured|partial|near|clear|unknown|stale|expired|busy|estimate)\b/i,/\b[hd][57]\b/];
  for(const name of ['full','partial','multi','empty','balanced','blocked','unknownQuota','noReference']){
   const app=await setup();app.data=clone(payload[name]);await app.select();for(const d of all(app.content,e=>e.tag==='details'))d.open=true;
   const page=text(app.content);for(const b of banned)assert.doesNotMatch(page,b,name+': '+b);
  }
 },
 async cadence(){
  const app=await setup({clock:Date.parse('2026-10-15T12:00:00Z')});const n=()=>balanceFetches(app);
  await app.select();assert.equal(n(),1,'opening the page fetches');
  await app.refresh();await app.refresh();assert.equal(n(),1,'a fresh state object does not bypass the cadence');
  app.run('balanceFetch();balanceFetch();balancePoll()');await settle();assert.equal(n(),1,'every 2-second caller, old name or new, goes through the same gate');
  app.clock+=16000;await app.refresh();assert.equal(n(),2,'due after 15 s with an idle registry');
  let release;app.reply=()=>new Promise(r=>{release=r});app.clock+=16000;await app.refresh();assert.equal(n(),3);app.clock+=16000;await app.refresh();assert.equal(n(),3,'single flight');
  app.reply=async()=>clone(app.data);release(clone(payload.full));await settle();
  const before=text(one(app.content,e=>e.getAttribute('data-balance-verdict')!==null));
  app.reply=async()=>clone(payload.busy);app.clock+=16000;await app.refresh();await settle();assert.equal(n(),4);
  assert.equal(text(one(app.content,e=>e.getAttribute('data-balance-verdict')!==null)),before,'busy keeps the last figures');
  const fresh=one(app.content,e=>e.getAttribute('data-balance-freshness')!==null);assert.equal(fresh.getAttribute('data-balance-freshness'),'busy');assert.match(text(fresh),/Ultimo aggiornamento alle .*nuovo tentativo alle/);
  app.reply=async()=>{throw Error('offline')};app.clock+=16000;await app.refresh();await settle();assert.equal(n(),5);
  assert.equal(text(one(app.content,e=>e.getAttribute('data-balance-verdict')!==null)),before,'an error keeps the last figures');assert.ok(app.find('balance:retry'));
  app.reply=async()=>clone(app.data);const select=app.find('balance:task');select.value='T';select.onchange({target:select});await settle();assert.equal(n(),6,'a task change is an explicit immediate fetch');
  await app.tab('Quadro');app.clock+=16000;await app.refresh();assert.equal(n(),6,'closed page: no request');
  await app.select();assert.equal(n(),7,'opening the page again fetches at once');
  app.doc.hidden=true;app.clock+=16000;await app.refresh();assert.equal(n(),7,'hidden page: no request');
 },
 async chartsMotion(){
  const app=await setup();await app.select();const first=(app.doc.animations||[]).length;assert.ok(first>0,'balance charts animate on first draw');
  app.run('balanceUI.immediate=true');await app.refresh();await settle();assert.equal((app.doc.animations||[]).length,first,'same figures: no replay');
  const still=await setup();still.run("window.matchMedia=q=>({matches:/prefers-reduced-motion: reduce/.test(q),addEventListener(){}})");await still.select();assert.equal((still.doc.animations||[]).length,0,'reduced motion');
  const load=one(app.content,e=>e.getAttribute('data-chart')==='balance-load'),marks=all(load,e=>e.getAttribute('data-viz-key')!==null&&e.getAttribute('tabindex')==='0');assert.ok(marks.length>=4);
  marks[0].onfocus();assert.match(text(one(load,e=>hasClass(e,'sq-viz-readout'))),/incarichi aperti, \d+ letture in attesa/);
  const strip=one(app.content,e=>e.tag==='g'&&e.getAttribute('data-balance-model')==='gpt');strip.onfocus();strip.onkeydown({key:'ArrowLeft'});assert.match(text(one(app.content,e=>e.getAttribute('data-chart')==='balance-tokens')),/gpt · \d\d:\d\d: (\d|non misurato)/);
 },
 async css(){
  const block=page.match(/<style id="squadra-balance-style">([\s\S]*?)<\/style>/);assert.ok(block,'Balance style block');const css=block[1];
  const breakpoint=Number(css.match(/@media\(max-width:([\d.]+)px\)/)[1]);for(const width of [360,375,390])assert.ok(width<=breakpoint,'phone covered: '+width);
  assert.match(css,/\.sq-balance-cards\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(min\(320px,100%\),1fr\)\)/,'cards shrink to the phone width');
  assert.match(css,/overflow-wrap:anywhere/,'long identities wrap');assert.match(css,/\.sq-balance \.sq-crow>\.sq-viz\{grid-column:1\/-1\}/,'the hourly strip spans the card under its model name');assert.match(css,/\[data-balance-rules\] :is\(table,tbody,tr,td\)\{display:block/,'the rules table becomes cards on a phone');assert.match(css,/var\(--sq-border/,'theme tokens, light and dark');
  assert.doesNotMatch(css,/#[0-9a-f]{3,6}\b/i,'no fixed colours outside the theme tokens');
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('balance contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(prefix+cases);

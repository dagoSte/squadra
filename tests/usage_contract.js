// Run the shipped Usage view in the existing focus-aware DOM harness.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/usage'?await app.reply():app.fixture)");
prefix=prefix.replace('fetch:async path=>{app.fetched.push(path);',"fetch:async (path,options)=>{(app.requests||(app.requests=[])).push({path,options});app.fetched.push(path);");
prefix=prefix.replace('const context={console,Date:PageDate','const context={URL,console,Date:PageDate');
const cases=String.raw`
const totals=(input,cache_read,cache_creation,output,reasoning=0)=>({input,cache_read,cache_creation,output,reasoning,total:input+cache_read+cache_creation+output});
function payload(){
 const a=totals(100,800,0,100,20),b=totals(500,0,0,600,200),at='2026-10-15T11:30:00Z';
 const q={identity:'first',model:'model-a',limit:'primary',used_percent:85,window_minutes:300,resets_at:'2026-10-15T13:00:00Z',observed_at:at,status:'measured'};
 return {schema:1,observed_at:at,status:'measured',coverage:{status:'ready',sources:2,measured:2,reasons:[]},
 windows:[{identity:'first',actors:[],client:'claude',status:'measured',reasons:[],totals:a,models:[],quota:[q]},{identity:'second',actors:[],client:'codex',status:'measured',reasons:[],totals:b,models:[],quota:[]}],
 models:[{model:'model-a',status:'measured',totals:a,windows:['first']},{model:'model-b',status:'measured',totals:b,windows:['second']}],
 tasks:[{task:null,status:'measured',totals:totals(600,800,0,700,220),estimated:true,reasons:['overlapping_tasks']}],
 days:[{day:'2026-10-15',status:'measured',totals:totals(600,800,0,700,220)}],quota_history:[{...q,observed_at:'2026-10-15T10:00:00Z',used_percent:75},q],
 suggestions:[{id:'cache',message_key:'usage.adviceCache',model:'model-a',window:'first',metrics:{total:1000,cache_read:800},vars:{total:1000,share:80}},{id:'output',message_key:'usage.adviceOutput',model:'model-b',window:'second',metrics:{total:1100,output:600},vars:{total:1100,share:54.5}},{id:'quota',message_key:'usage.adviceQuota',model:'model-a',window:'first',metrics:{used_percent:85},vars:{percent:85,minutes:300}}],cost:null};
}
async function setup(opts={}){
 const app=await boot({...opts,clock:Date.parse('2026-10-15T11:30:00Z')});app.data=payload();app.reply=async()=>clone(app.data);app.content=app.doc.getElementById('content');
 app.select=async()=>{const links=app.doc.getElementById('usage-links');assert.equal(links.children.length,1,'Usage has its own visible navigation entry');links.children[0].onclick();await settle()};return app;
}
const chart=(app,id)=>one(app.content,e=>e.getAttribute('data-usage-chart')===id,'chart '+id);
const rows=(app,id)=>all(chart(app,id),e=>e.tag==='tr').slice(1).map(r=>r.children.map(c=>c.textContent));
const requests=app=>app.fetched.filter(p=>p==='/api/usage');
const tests={
 async charts(){
  for(const language of ['it-IT','en-US'])for(const theme of ['light','dark'])for(const phone of [false,true]){
   const app=await setup({language,stored:{'squadra-theme':theme},phone});await app.select();
   assert.equal(app.doc.getElementById('usage-links').children[0].getAttribute('aria-current'),'page');
   assert.deepEqual(all(app.content,e=>e.getAttribute('data-usage-chart')).map(e=>e.getAttribute('data-usage-chart')),['daily','byWindow','byModel','byTask','quota']);
   assert.equal(all(app.content,e=>e.getAttribute('role')==='img').length,5,'five accessible graphics');
   assert.equal(all(app.content,e=>e.tag==='table').length,5,'every chart has a table');
   for(const c of all(app.content,e=>e.getAttribute('data-usage-chart'))){assert.ok(one(c,e=>e.getAttribute('role')==='img').getAttribute('aria-label'));assert.ok(one(c,e=>e.tag==='th').getAttribute('scope'));assert.ok(one(c,e=>e.tag==='summary'))}
   assert.equal(rows(app,'daily')[0].length,7,'reasoning is not a fifth stacked kind');
   assert.deepEqual(rows(app,'daily')[0].slice(1,5),['600','800','0','700']);
   assert.equal(rows(app,'byWindow').length,2);assert.equal(rows(app,'byModel')[0][0],'model-b');assert.equal(rows(app,'quota').length,2);
   assert.match(text(chart(app,'quota')),language==='it-IT'?/non si sommano/:/not added/);
   assert.doesNotMatch(text(app.content),/⟦|\{[A-Za-z]+\}/);assert.equal(all(app.content,e=>['img','iframe','script'].includes(e.tag)).length,0);
   unique(app,'usage charts');
  }
 },
 async coverage(){
  const app=await setup();app.data.status='partial';app.data.coverage.status='collecting';app.data.windows[1].totals=null;app.data.windows[1].status='not_measured';app.data.windows[1].reasons=['source_missing'];app.data.models[1].totals=totals(0,0,0,0);app.data.days.push({day:'2026-10-14',status:'not_measured',totals:null});
  await app.select();assert.match(text(app.content),/Raccolta in corso/);assert.match(text(chart(app,'byWindow')),/Non misurato/);assert.match(text(chart(app,'byTask')),/Attribuzione stimata/);
  assert.ok(rows(app,'byModel').some(r=>r[0]==='model-b'&&r[1]==='0'),'observed zero remains zero');assert.equal(rows(app,'daily')[1][5],'Non misurato','unknown is never a zero');
  app.data={...payload(),windows:[],models:[],tasks:[],days:[],quota_history:[],suggestions:[],status:'not_measured',coverage:{status:'limited',sources:1,measured:0,reasons:['source_missing']}};await app.refresh();assert.match(text(app.content),/non significa consumo zero/);assert.equal(all(app.content,e=>e.getAttribute('role')==='img').length,0);
  assert.match(text(app.content),/Non ci sono dati sufficienti/);assert.match(text(app.content),/Stima disattivata/);
 },
 async polling(){
  const app=await setup();await app.refresh();assert.equal(requests(app).length,0,'closed Usage causes no collection');
  let release;app.reply=()=>new Promise(r=>{release=r});await app.select();assert.match(text(app.content),/Raccolta dei contatori/);assert.equal(requests(app).length,1);
  app.run('render();render()');await settle();assert.equal(requests(app).length,1,'repeated render does not overlap collection');
  app.reply=async()=>clone(app.data);release(clone(app.data));await settle();
  app.find('usage:refresh').focus();await app.refresh();assert.equal(key(app.doc.activeElement),'usage:refresh');
  const count=requests(app).length;await app.tab('Quadro');await app.refresh();assert.equal(requests(app).length,count);
  await app.select();app.doc.hidden=true;app.run('usageUI.attempt=null;usageFetch()');await settle();assert.equal(requests(app).length,count+1,'hidden view does not collect');app.doc.hidden=false;
  app.reply=async()=>{throw Error('PRIVATE-EXCEPTION-TEXT')};app.find('usage:refresh').onclick();await settle();assert.match(text(app.content),/Non è possibile leggere/);assert.doesNotMatch(text(app.content),/PRIVATE-EXCEPTION-TEXT/);assert.ok(chart(app,'daily'),'old counters remain on failure');
  app.reply=async()=>clone(app.data);app.find('usage:retry').onclick();await settle();assert.equal(all(app.content,e=>e.getAttribute('role')==='alert').length,0);unique(app,'usage refreshed');
  for(const request of app.requests.filter(r=>r.path==='/api/usage')){assert.equal(request.options.headers['X-Squadra-Token'],'token');assert.deepEqual(Object.keys(request.options.headers),['X-Squadra-Token']);assert.equal(request.options.method,undefined);assert.equal(request.options.body,undefined)}
 },
 async prices(){
  const app=await setup();await app.select();const cases=JSON.parse(fs.readFileSync(casesPath,'utf8'));
  for(const c of cases){if(c.error)assert.throws(()=>app.run('usageEstimate('+JSON.stringify(c.data)+','+JSON.stringify(c.prices)+')'),/price_table_invalid/,c.name);else assert.deepEqual(JSON.parse(app.run('JSON.stringify(usageEstimate('+JSON.stringify(c.data)+','+JSON.stringify(c.prices)+'))')),c.expected,c.name)}
  const balance={claude_reference:{h5:123,d7:456},future:{keep:true}};
  app.run('localStorage.setItem(usageSettingsKey,'+JSON.stringify(JSON.stringify({schema:1,prices:null,balance}))+')');
  const prices={currency:'EUR',models:{'model-a':{input:2,cache_read:0.2,cache_creation:3,output:8}}};assert.equal(app.run('usageWritePrices('+JSON.stringify(prices)+')'),true);assert.deepEqual(JSON.parse(app.run('JSON.stringify(usageReadSettings().balance)')),balance);
  app.run('usageUI.signature=null;render()');await settle();assert.match(text(app.content),/Stima parziale/);assert.match(text(app.content),/model-b.*Prezzo mancante/);
  const count=requests(app).length;app.find('usage:clearPrices').onclick();await settle();assert.equal(requests(app).length,count,'local prices do not refetch');assert.match(text(app.content),/Stima disattivata/);assert.deepEqual(JSON.parse(app.run('JSON.stringify(usageReadSettings().balance)')),balance);
 },
 async quotaHistory(){
  const app=await setup({language:'en-US'});const q=app.data.quota_history[0];app.data.quota_history=[{...q,status:'stale',used_percent:75},{...q,status:'expired',used_percent:50},{...q,status:'stale',used_percent:-1},{...q,status:'expired',used_percent:101},{...q,status:'stale',used_percent:'85'},{...q,status:'not_measured',used_percent:85}];await app.select();
  assert.deepEqual(rows(app,'quota').map(r=>r[3]),['75','50','Not measured','Not measured','Not measured','Not measured'],'historical percentages remain visible; invalid/unknown values do not become measurements');
  assert.match(text(chart(app,'quota')),/Past observation, not current quota/);assert.match(text(chart(app,'quota')),/historical observations, not the current quota/);
  assert.equal(all(chart(app,'quota'),e=>e.tag==='rect').length,2,'two valid historical points');
  app.data.windows[0].quota[0].status='expired';await app.refresh();assert.equal(all(app.content,e=>e.getAttribute('data-usage-advice')==='quota').length,0,'history display never authorizes stale quota advice');
 },
 async privacy(){
  const app=await setup();app.data.private_text='PRIVATE_CANARY_sk-sensitive';app.data.windows[0].conversation='PRIVATE_CANARY_sk-sensitive';app.data.suggestions.push({id:'unknown',message_key:'usage.evil',vars:{text:'PRIVATE_CANARY_sk-sensitive'}});await app.select();
  assert.equal(all(app.content,e=>e.getAttribute('data-usage-advice')).length,3);assert.doesNotMatch(app.run('JSON.stringify(usageUI.data)'),/PRIVATE_CANARY/);assert.doesNotMatch(text(app.content),/PRIVATE_CANARY|sk-sensitive|⟦/);
  assert.match(text(app.content),/1000 token, 80%/);assert.match(text(app.content),/85%.*300/);assert.match(text(app.content),/Ripristino/);
  app.data.models[0].status='partial';app.data.models[1].status='not_measured';app.data.windows[0].quota[0].observed_at='2026-10-14T00:00:00Z';await app.refresh();assert.equal(all(app.content,e=>e.getAttribute('data-usage-advice')).length,0,'partial/missing model and stale quota give no advice');
  app.data.models[0].model='<img src=x onerror=alert(1)>';app.data.suggestions=[];await app.refresh();assert.equal(all(app.content,e=>e.tag==='img').length,0);assert.match(text(app.content),/<img src=x onerror=alert\(1\)>/,'normalized identifiers are still rendered as text');
  assert.ok(app.requests.every(r=>r.path.startsWith('/api/')));assert.ok(app.writes.every(r=>!String(r[1]).includes('PRIVATE_CANARY')));
 },
 async form(){
  const app=await setup();await app.select();const currency=app.find('usage:currency');currency.value='eur';currency.oninput();const price=app.find('usage:price:model-a:input');price.value='1.5';price.oninput();price.focus();
  await app.refresh();assert.equal(app.find('usage:currency').value,'EUR');assert.equal(app.find('usage:price:model-a:input').value,'1.5');assert.equal(key(app.doc.activeElement),'usage:price:model-a:input');
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/Estimate with your prices/);assert.equal(app.find('usage:price:model-a:input').value,'1.5');app.find('usage:savePrices').onclick();await settle();assert.match(text(app.content),/Prices saved in this browser/);
  const saved=JSON.parse(app.writes.filter(r=>r[0]==='squadra.usage.settings.v1').at(-1)[1]);assert.equal(saved.prices.models['model-a'].input,1.5);
  const blocked=await setup({storage:'throws'});await blocked.select();blocked.find('usage:currency').value='EUR';blocked.find('usage:currency').oninput();blocked.find('usage:savePrices').onclick();await settle();assert.match(text(blocked.content),/non consente il salvataggio/);unique(app,'usage form');
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('usage contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(prefix+cases);

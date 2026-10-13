// Run the actual page scripts with the existing focus-aware DOM and synthetic API data.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/reads'?await app.reply():app.fixture)");
prefix=prefix.replace('const context={console,Date:PageDate','const context={scrollBy:(x,y)=>{doc.scroll=(doc.scroll||0)+y},console,Date:PageDate');
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
Element.prototype.getBoundingClientRect=function(){
 const rows=all(this.doc.getElementById('content'),e=>e.getAttribute('data-read-id'));
 const card=rows.find(e=>e===this||[...walk(e)].includes(this)),i=rows.indexOf(card);
 return i<0?{top:0,bottom:0}:{top:i*300-(this.doc.scroll||0),bottom:(i+1)*300-(this.doc.scroll||0)};
};
async function setup(opts={}){
 const app=await boot(opts);app.data=clone(payload);app.reply=async()=>clone(app.data);app.content=app.doc.getElementById('content');
 app.select=()=>app.tab('Letture');app.count=k=>text(one(app.content,e=>e.getAttribute('data-read-count')===k));app.value=k=>one(one(app.content,e=>e.getAttribute('data-read-count')===k),e=>e.tag==='strong').textContent;
 app.bump=async()=>{const next=clone(app.fixture);next.event_count++;next.registry_head={seq:next.event_count,hash:'new-'+next.event_count};await app.refresh(next)};
 return app;
}
const tests={
 async filterSizing(){
  const app=await setup({phone:true});await app.select();
  const selector=app.find('reads:task');assert.equal(selector.tag,'select');
  const styles=page.match(/<style id="squadra-reads">([\s\S]*?)<\/style>/)[1];
  const sizing=styles.match(/\.sq-reads-bar select\{([^}]+)\}/)[1];
  assert.match(sizing,/min-width:0(?:;|$)/,'native select must shrink below its intrinsic option width');
  assert.match(sizing,/(?:^|;)width:100%(?:;|$)/,'select stays within its label grid');
  assert.doesNotMatch(styles,/overflow-x:(?:hidden|clip)/,'do not conceal page overflow');
  assert.match(styles,/\.sq-reads \.sq-table-wrap\{overflow:auto\}/,'wide table remains locally scrollable');
 },
 async readIcon(){
  const app=await setup();await app.select();
  const nav=app.find('nav:Letture'),glyph=one(nav,e=>e.tag==='svg');
  assert.equal(one(glyph,e=>e.tag==='path').getAttribute('d'),app.run('PANEL_ICONS.search[0][0]'));
  assert.equal(nav.getAttribute('aria-current'),'page');
 },
 async missingCategoryStyle(){
  const app=await setup();await app.select();
  for(const [lang,label] of [['it','Non registrato'],['en','Not recorded']]){
   app.run('setLanguage('+JSON.stringify(lang)+')');await settle();
   const card=one(app.content,e=>e.getAttribute('data-read-count')==='conditions');
   const missing=one(card,e=>e.tag==='strong');
   assert.equal(missing.textContent,label);
   assert.match(missing.className,/sq-read-unrecorded/,'missing category is a quiet label, not a count');
  }
  assert.match(page,/\.sq-reads-card strong\.sq-read-unrecorded\{font-size:14px;font-weight:400;color:var\(--sq-muted\)\}/);
 },
 async counts(){
  const app=await setup();await app.select();
  assert.match(text(app.content),/Cosa hanno fermato le letture/);
  for(const [k,n] of Object.entries({reads:3,accepted:2,'changes-required':1,blocked:0,planRevisions:1,codeRevisions:0,findings:1}))assert.equal(app.value(k),String(n),k);
  assert.match(app.count('conditions'),/Non registrato/);assert.match(app.count('findings'),/2 letture su 3/);
  assert.ok(text(app.content).includes('<img src=x onerror=alert(1)> & synthetic'),'untrusted title stays text');
  assert.equal(all(app.content,e=>e.tag==='img').length,0);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/What the reads caught/);
  assert.match(app.count('conditions'),/Not recorded/);assert.match(app.count('findings'),/2 of 3 reads/);
  assert.match(text(app.content),/Repeated observations are not distinct defects/);
 },
 async filters(){
  const app=await setup();await app.select();const stage=app.find('reads:stage');stage.value='code';stage.onchange();await settle();
  assert.match(app.count('reads'),/0/);assert.match(text(app.content),/Nessuna lettura/);
  app.find('reads:reset').onclick();await settle();assert.match(app.count('reads'),/3/);
  app.find('reads:task:T').onclick();await settle();assert.equal(app.find('reads:task').value,'T');
  const read=payload.reads[0];app.find('reads:report:'+read.id).onclick();await settle();assert.ok(app.fetched.includes('/api/artifact/'+read.report.sha256));
  app.doc.getElementById('artifact').close();app.find('reads:story:'+read.id).onclick();await settle();
  assert.equal(app.run('panelUI.story'),'T');assert.equal(app.doc.getElementById('story').open,true);
 },
 async fetches(){
  const app=await setup();await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,0);
  let release;app.reply=()=>new Promise(resolve=>{release=resolve});await app.select();
  await app.refresh();await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,1,'one in flight');
  release(clone(payload));await settle();await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,1,'same head cached');
  app.reply=async()=>clone(app.data);await app.bump();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,2);
  await app.tab('Quadro');await app.bump();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,2,'hidden view never fetched');
  await app.select();assert.equal(app.fetched.filter(p=>p==='/api/reads').length,3);
 },
 async states(){
  const app=await setup();await app.select();app.reply=async()=>{throw Error('offline')};await app.bump();
  assert.match(text(app.content),/Letture non aggiornate/);assert.match(app.count('reads'),/3/);
  const n=app.fetched.length;await app.refresh();assert.equal(app.fetched.length,n+1,'no blind retry');
  app.data={...clone(payload),head:{seq:999,hash:'empty'},reads:[],revisions:[]};app.reply=async()=>app.data;
  app.find('reads:retry').onclick();await settle();assert.match(text(app.content),/Nessuna lettura/);assert.match(app.count('findings'),/Non registrato/);
  const missing=clone(payload);missing.head={seq:1000,hash:'missing'};missing.reads[0].report=null;app.data=missing;await app.bump();
  assert.match(text(app.content),/Rapporto non registrato/);
 },
 async focus(){
  const app=await setup();await app.select();const read=payload.reads[0],summary=app.find('reads:read:'+read.id);
  const detail=one(app.content,e=>e.getAttribute('data-read-id')===read.id);detail.open=true;detail.ontoggle();summary.focus();
  const before=app.content.children[0];await app.refresh();assert.equal(app.content.children[0],before,'stable DOM');assert.equal(key(app.doc.activeElement),'reads:read:'+read.id);
  app.data.head={seq:500,hash:'changed'};await app.bump();assert.equal(key(app.doc.activeElement),'reads:read:'+read.id);
  assert.equal(one(app.content,e=>e.getAttribute('data-read-id')===read.id).open,true);
  app.doc.activeElement=null;app.doc.scroll=350;const first=all(app.content,e=>e.getAttribute('data-read-id'))[1],top=first.getBoundingClientRect().top;
  const extra={...clone(payload.reads[0]),id:'new-read',seq:600};app.data.reads.push(extra);app.data.head={seq:600,hash:'inserted'};await app.bump();
  const retained=one(app.content,e=>e.getAttribute('data-read-id')===first.getAttribute('data-read-id'));assert.equal(retained.getBoundingClientRect().top,top,'reader place survives insertion');
  unique(app,'reads');
 },
 async shell(){
  const app=await setup({phone:true});await app.select();
  assert.equal(app.find('nav:Letture').getAttribute('aria-current'),'page');assert.equal(app.find('reads:stage').getAttribute('aria-label'),'Fase della lettura');
  app.run("panelUI.collapsed=true;render()");await settle();
  for(const theme of ['dark','light']){app.run('panelTheme('+JSON.stringify(theme)+')');await settle();assert.equal(app.doc.documentElement.getAttribute('data-theme'),theme)}
  assert.match(page,/@media\(max-width:600px\).*sq-reads-cards/);
  app.doc.getElementById('menu').onclick();await settle();assert.match(app.doc.getElementById('app').className,/drawer-open/);
  await app.keydown('Escape');assert.doesNotMatch(app.doc.getElementById('app').className,/drawer-open/);
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('reads contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

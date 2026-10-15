// Exercise conditional Usage reads with the shipped view and existing DOM harness.
'use strict';
const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'usage_contract.js'),'utf8');
const base=source.split('const tests={')[0];
const live=String.raw`
const revision='a'.repeat(64),nextRevision='b'.repeat(64);
const tests={
 async independent(){
  const app=await setup();app.data.revision=revision;await app.select();
  app.run('globalThis.originalState=state');const before=requests(app).length;
  app.data.revision=nextRevision;app.data.windows[0].totals=totals(120,800,0,100);
  await app.run('usageFetch()');await settle();
  assert.equal(app.run('state===originalState'),true,'Usage advances without replacing the registry state');
  assert.equal(requests(app).length,before+1);assert.equal(app.run('usageUI.data.windows[0].totals.total'),1020);
  assert.equal(app.run('usageUI.revision'),nextRevision);
  app.run('render();render();render()');await settle();assert.equal(requests(app).length,before+1,'render never starts collection');
  const last=app.requests.filter(r=>r.path==='/api/usage').at(-1);
  assert.deepEqual(clone(last.options.headers),{'X-Squadra-Token':'token','X-Squadra-Usage-View':'compact','X-Squadra-Usage-Revision':revision});
  assert.equal(last.options.body,undefined);assert.equal(last.options.method,undefined);assert.equal(last.options.cache,'no-store');
 },
 async envelopes(){
  const app=await setup();app.data.revision=revision;await app.select();
  const element=app.content.children[0],price=app.find('usage:price:model-a:input'),table=app.find('usage:table:daily');
  price.value='1.75';price.oninput();price.focus();table.scrollLeft=91;table.scrollTop=37;
  app.run('globalThis.originalUsage=usageUI.data');
  for(const envelope of [{schema:1,unchanged:true,revision},{schema:1,busy:true},{schema:1,unchanged:true,revision}]){
   app.reply=async()=>envelope;await app.run('usageFetch()');await settle();
   assert.equal(app.run('usageUI.data===originalUsage'),true,'small responses keep the measured snapshot');
   assert.equal(app.run('usageUI.revision'),revision);assert.equal(app.content.children[0],element);
   assert.equal(app.find('usage:price:model-a:input'),price);assert.equal(price.value,'1.75');assert.equal(app.doc.activeElement,price);
   assert.equal(table.scrollLeft,91);assert.equal(table.scrollTop,37);assert.ok(chart(app,'daily'));
   assert.equal(all(app.content,e=>e.getAttribute('role')==='alert').length,0);
   assert.match(text(one(app.content,e=>e.getAttribute('role')==='status')),envelope.busy?/Aggiornamento dei contatori/:/Osservato alle/);
  }
  app.reply=async()=>({...app.data,revision:nextRevision});await app.run('usageFetch()');await settle();
  assert.equal(app.find('usage:table:daily').scrollLeft,91);assert.equal(app.find('usage:table:daily').scrollTop,37);
  assert.equal(app.find('usage:price:model-a:input').value,'1.75');assert.equal(key(app.doc.activeElement),'usage:price:model-a:input');
 },
 async firstBusy(){
  for(const language of ['it-IT','en-US']){
   const app=await setup({language});app.reply=async()=>({schema:1,busy:true});await app.select();
   assert.match(text(app.content),language==='it-IT'?/Raccolta dei contatori/:/Collecting local counters/);
   assert.equal(app.run('usageUI.data'),null);assert.equal(app.run('usageUI.revision'),null);
   assert.equal(all(app.content,e=>e.getAttribute('data-usage-chart')).length,0);
   assert.equal(all(app.content,e=>e.getAttribute('role')==='alert').length,0);
   const count=requests(app).length;app.run('render();render()');await settle();assert.equal(requests(app).length,count);
   app.data.revision=revision;app.reply=async()=>app.data;app.find('usage:refresh').onclick();await settle();
   assert.equal(app.run('usageUI.data.windows[0].totals.total'),1000);assert.ok(chart(app,'daily'));
  }
 },
 async validationAndSerial(){
  const app=await setup();app.data.revision=revision;await app.select();
  let release;app.reply=()=>new Promise(r=>{release=r});const count=requests(app).length;
  app.run('usageFetch();usageFetch();render()');await settle();assert.equal(requests(app).length,count+1,'one in-flight Usage read');
  assert.match(text(app.content),/Aggiornamento dei contatori/);
  release({schema:1,unchanged:true,revision:nextRevision});await settle();
  assert.equal(app.run('usageUI.revision'),revision);assert.ok(chart(app,'daily'));assert.match(text(app.content),/Non è possibile leggere/);
  app.reply=async()=>({...app.data,revision:[nextRevision]});await app.run('usageFetch()');await settle();
  assert.equal(app.run('usageUI.error'),true);assert.equal(app.run('usageUI.revision'),revision,'only an opaque string becomes the next request token');
  app.reply=async()=>({schema:1,unchanged:true,revision});app.find('usage:retry').onclick();await settle();assert.equal(all(app.content,e=>e.getAttribute('role')==='alert').length,0);
  const last=requests(app).length;app.doc.hidden=true;await app.run('usageFetch()');app.doc.hidden=false;await app.tab('Quadro');await app.run('usageFetch()');assert.equal(requests(app).length,last,'hidden or closed Usage never reads');
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('usage live contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(base+live+'`;\neval(prefix+cases);');

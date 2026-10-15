// Exercise the shipped information labels and cache notice with the retained DOM harness.
'use strict';
const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'usage_contract.js'),'utf8');
const base=source.split('const tests={')[0];
const checks=String.raw`
(async()=>{
 for(const language of ['it-IT','en-US']){
  const app=await setup({language});
  app.data.coverage.notices=['cache_off_unsafe_path','PRIVATE_CANARY'];
  await app.select();
  const shown=text(app.content);
  assert.match(shown,language==='it-IT'?/collegamento simbolico/:/symbolic link/);
  assert.doesNotMatch(shown,/PRIVATE_CANARY|cache_off_unsafe_path/);
  assert.deepEqual(clone(app.run('usageUI.data.coverage.notices')),['cache_off_unsafe_path']);
  assert.equal(app.run('usageUI.data.status'),'measured');
  const saved=app.run('JSON.stringify(usageUI.data.windows.map(w=>[w.status,w.totals,w.reasons]))');
  app.data.coverage.notices=[];await app.run('usageFetch()');await settle();
  assert.doesNotMatch(text(app.content),/collegamento simbolico|symbolic link/);
  assert.equal(app.run('JSON.stringify(usageUI.data.windows.map(w=>[w.status,w.totals,w.reasons]))'),saved);
  const at='2026-10-15T11:00:00Z';
  const message=(id,info,ack=null)=>({id,from:'C',to:'A',body:id,utc:at,ack,...(info?{info:true}:{})});
  const mail={normal:message('normal',false),information:message('information',true),read:message('read',true,{agent:'A',utc:at})};
  app.run('state.messages='+JSON.stringify(mail)+';render()');
  const metrics=app.doc.getElementById('metrics');
  assert.equal(metrics.children[2].children[1].textContent,1,'one required ACK, not three');
  const table=app.run('mail()');
  const label=language==='it-IT'?'Informazione · conferma non richiesta':'Information · no confirmation required';
  assert.equal(all(table,e=>e.tag==='span'&&e.textContent===label).length,2);
  const filter=one(table,e=>e.tag==='select'&&e.children.some(c=>c.getAttribute('value')==='info'||c.value==='info'));
  filter.value='waiting';filter.onchange({target:filter});
  const waiting=app.run('mail()');assert.doesNotMatch(text(waiting),/information|Progress only/);
  const edge={kind:'message',state:'info',flags:['manual'],ack_utc:null};
  assert.match(app.run('commsResult('+JSON.stringify(edge)+')'),language==='it-IT'?/nessuna conferma richiesta/:/no confirmation required/);
  edge.ack_utc=at;assert.doesNotMatch(app.run('commsResult('+JSON.stringify(edge)+')'),/pending|attesa/);
 }
 console.log('small improvements contract passed');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(base+checks+'`;\neval(prefix+cases);');

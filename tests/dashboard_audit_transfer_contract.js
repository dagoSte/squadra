'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'dashboard_loading_contract.js'),'utf8').split('const cases=String.raw`')[0];
const extension=String.raw`
harness=harness.replace(' focus(){this.doc.activeElement=this}', ' animate(frames,options){(this.doc.animations||(this.doc.animations=[])).push({el:this,frames,options});return {cancel(){},finished:Promise.resolve()}}\n focus(){this.doc.activeElement=this}');
harness=harness.replace('const context={AbortController,console,', 'const context={URL,AbortController,console,');
`;
const cases=String.raw`
const reply=value=>({ok:true,json:async()=>clone(value),text:async()=>JSON.stringify(value)});
function fixture(status='running'){
 const value=clone(base);value.transfer=1;value.change_token='a'.repeat(64);value.server_time='2026-10-15T12:00:00+00:00';value.registry_head={hash:'b'.repeat(64),seq:200};value.audit={status};return value;
}
const unchanged=value=>({not_modified:true,change_token:value.change_token,server_time:value.server_time});
const tests={
 async transition(){
  for(const language of ['it-IT','en-US']){
   const value=fixture(),app=await boot({language,fixture:value});
   const banner=app.doc.getElementById('registry-audit');assert.equal(banner.hidden,false);
   assert.match(text(banner),language==='it-IT'?/Verifica completa/:/Full registry verification/);
   app.run("transferUI.sections.set('mail:',{messages:{MERGED:{id:'MERGED',body:'Hydrated body'}}});transferSync()");
   const held=app.run('state');app.http=()=>reply(unchanged(value));await app.refresh();
   assert.equal(app.run('state'),held);assert.equal(banner.hidden,false);
   const done=clone(value);done.change_token='c'.repeat(64);done.audit={status:'complete'};
   app.http=()=>reply(done);await app.refresh();assert.equal(banner.hidden,true);
   assert.equal(app.run('state.registry_head.hash'),value.registry_head.hash);
   assert.equal(app.run('state.messages.MERGED.body'),'Hydrated body');
   assert.equal(app.run("transferHas('mail')"),true,'audit completion does not discard same-head immutable details');
   app.run("globalThis.usageFetch=async()=>{globalThis.usagePolls=(globalThis.usagePolls||0)+1};globalThis.balanceFetch=async()=>{globalThis.balancePolls=(globalThis.balancePolls||0)+1};tab='Usage'");
   await app.run('transferIndependentPoll()');app.run("tab='Bilanciamento'");await app.run('transferIndependentPoll()');
   assert.equal(app.run('globalThis.usagePolls'),1);assert.equal(app.run('globalThis.balancePolls'),1);
  }
 },
 async late_story(){
  const value=fixture('complete'),app=await boot({fixture:value});let release;
  app.run("transferUI.sections.set('mail:',{messages:{}})");
  app.http=()=>new Promise(resolve=>{release=resolve});app.run("panelOpenStory('TASK-KQ1')");await settle();
  assert.equal(app.doc.getElementById('story').open,true);
  app.http=()=>Promise.resolve({ok:false,status:409,text:async()=> '{"error":"registry_invalid"}'});await app.refresh();
  assert.equal(app.run('state'),null);assert.equal(app.run('loadUI.integrity'),true);
  assert.equal(app.doc.getElementById('story').open,false);assert.equal(text(app.doc.getElementById('content')),'');
  release(reply({head:value.registry_head.hash,section:'story',record:'TASK-KQ1',patch:{tasks:{'TASK-KQ1':base.tasks['TASK-KQ1']}}}));await settle();await settle();
  assert.equal(app.run('state'),null);assert.equal(app.run('transferUI.sections.size'),0);
  assert.equal(app.doc.getElementById('story').open,false);
  assert.equal(app.timers.filter(t=>t.fn.name==='refresh').length,0);
  const count=app.fetched.length;await app.run('refresh()');assert.equal(app.fetched.length,count);
 },
 async charts_poll(){
  const value=fixture('complete'),app=await boot({fixture:value,clock:Date.parse(value.server_time)});
  const counts={input:100,cache_read:800,cache_creation:0,output:100,reasoning:0,total:1000};
  const data={schema:1,revision:'d'.repeat(64),observed_at:value.server_time,status:'measured',coverage:{status:'ready',sources:1,measured:1,reasons:[]},
   windows:[{identity:'first',actors:[],client:'claude',status:'measured',reasons:[],totals:counts,models:[],quota:[]}],
   models:[{model:'model-a',status:'measured',totals:counts,windows:['first']}],tasks:[],days:[{day:'2026-10-15',status:'measured',totals:counts}],quota_history:[],suggestions:[],cost:null};
  let requests=0,mode='full';app.http=path=>{if(path==='/api/usage'){requests++;return reply(mode==='full'?data:mode==='busy'?{schema:1,busy:true}:{schema:1,unchanged:true,revision:data.revision})}return reply(unchanged(value))};
  app.run("tab='Usage'");await app.run('usageFetch()');await settle();
  const moved=(app.doc.animations||[]).length;assert.ok(moved>0,'the actual chart renderer animated measured data');
  const retained=app.run('usageUI.data');mode='unchanged';const before=requests;await app.refresh();
  assert.ok(requests>before,'Usage polls despite the unchanged registry envelope');
  assert.equal(app.run('usageUI.data'),retained);assert.equal((app.doc.animations||[]).length,moved);
  mode='busy';await app.refresh();assert.equal(app.run('usageUI.data'),retained);assert.equal((app.doc.animations||[]).length,moved);
  app.run("tab='Bilanciamento';globalThis.balanceRequest=()=>{globalThis.balanceRequests=(globalThis.balanceRequests||0)+1;balanceUI.immediate=false;balanceUI.lastDone=balanceNow()};balanceUI.immediate=true");
  await app.run('transferIndependentPoll()');assert.equal(app.run('globalThis.balanceRequests'),1);
  app.clock+=2000;await app.run('transferIndependentPoll()');assert.equal(app.run('globalThis.balanceRequests'),1);
  app.clock+=13000;await app.run('transferIndependentPoll()');assert.equal(app.run('globalThis.balanceRequests'),2,'the actual 15-second gate remains effective');
 }
};
(async()=>{await tests[scenario]();console.log('audit transfer contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+extension+'\neval(harness+\'\\n\'+cases);');

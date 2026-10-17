'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'dashboard_loading_contract.js'),'utf8').split('const cases=String.raw`')[0];
const cases=String.raw`
const reply=value=>({ok:true,json:async()=>clone(value),text:async()=>JSON.stringify(value)});
const refused=body=>Promise.resolve({ok:false,status:409,text:async()=>body});
const plainCopy=value=>JSON.parse(JSON.stringify(value));
function landing(){
 const value=clone(base);value.transfer=1;value.change_token='a'.repeat(64);value.server_time='2026-10-15T12:00:00+00:00';value.registry_head={hash:'b'.repeat(64),seq:200};return value;
}
const empty={set:{},remove:[],maps:{},lists:{}};
const tests={
 async delta(){
  const first=landing(),headers=[];
  const app=await boot({fixture:first,http:(path,options)=>{headers.push(options.headers);return reply(first)}});
  const second=clone(first),ids=Object.keys(second.messages),[gone,changed]=ids;
  delete second.messages[gone];
  second.messages[changed]={...second.messages[changed],body:'Changed'};
  second.messages.NEW={id:'NEW',to:'A',from:'C',body:'New',utc:'2026-10-15T12:01:00+00:00',ack:null};
  const added={...second.events[0],seq:999,id:'e999'};
  second.events=[...second.events.slice(1),added];
  second.registry_head={hash:'c'.repeat(64),seq:201};second.change_token='d'.repeat(64);second.server_time='2026-10-15T12:01:00+00:00';
  delete second.activity;
  const delta={set:{registry_head:second.registry_head,server_time:second.server_time},remove:['activity'],
   maps:{messages:{order:Object.keys(second.messages),set:{[changed]:second.messages[changed],NEW:second.messages.NEW}}},
   lists:{events:{by:'seq',order:second.events.map(e=>e.seq),set:{'999':added}}}};
  app.http=(path,options)=>{headers.push(options.headers);return reply({delta,base_token:first.change_token,change_token:second.change_token,server_time:second.server_time,registry_head:second.registry_head})};
  await app.refresh();
  assert.equal(headers.at(-1)['X-Squadra-Delta'],'1');assert.equal(headers.at(-1)['X-Squadra-State-Token'],first.change_token);
  assert.deepEqual(plainCopy(app.run('transferLanding')),plainCopy(second),'the delta rebuilds the new landing exactly');
  assert.deepEqual(Object.keys(app.run('state.messages')),Object.keys(second.messages),'map order follows the server');
  assert.equal(app.run('state.change_token'),second.change_token);
 },
 async fallback(){
  const first=landing(),tokens=[];
  const app=await boot({fixture:first,http:(path,options)=>{tokens.push(options.headers['X-Squadra-State-Token']);return reply(first)}});
  const whole=landing();whole.change_token='e'.repeat(64);whole.registry_head={hash:'f'.repeat(64),seq:300};
  let n=0;
  app.http=(path,options)=>{tokens.push(options.headers['X-Squadra-State-Token']);n++;
   return reply(n===1?{delta:empty,base_token:'0'.repeat(64),change_token:'9'.repeat(64),server_time:first.server_time,registry_head:whole.registry_head}:whole)};
  await app.refresh();
  assert.deepEqual(tokens.slice(-2),[first.change_token,''],'a delta for another base is replaced by a whole reply');
  assert.equal(app.run('state.registry_head.hash'),whole.registry_head.hash);
  const third=landing();third.change_token='8'.repeat(64);third.registry_head={hash:'7'.repeat(64),seq:301};n=0;
  app.http=(path,options)=>{tokens.push(options.headers['X-Squadra-State-Token']);n++;
   return reply(n===1?{delta:empty,base_token:whole.change_token,change_token:'6'.repeat(64),server_time:first.server_time,registry_head:{hash:'5'.repeat(64),seq:302}}:third)};
  await app.refresh();
  assert.deepEqual(tokens.slice(-2),[whole.change_token,''],'a delta that lands on another head is replaced');
  assert.equal(app.run('state.registry_head.hash'),third.registry_head.hash);
 },
 async conflict(){
  const first=landing();const app=await boot({fixture:first,http:()=>reply(first)});
  app.http=()=>refused('{"error":"state_changed"}');await app.refresh();
  assert.equal(app.run('loadUI.integrity'),false,'an ordinary conflict does not end polling');
  assert.ok(app.run('state'),'the view keeps its data');
  assert.ok(app.timers.some(t=>t.fn.name==='refresh'),'the next poll is scheduled');
  app.http=()=>refused('{"error":"registry_invalid"}');await app.refresh();
  assert.equal(app.run('loadUI.integrity'),true,'a registry failure stops polling');
  assert.equal(app.run('state'),null);
 },
 async ownkeys(){
  const first=landing();const app=await boot({fixture:first,http:()=>reply(first)});
  const id=Object.keys(first.tasks)[0],named=(key,title)=>JSON.stringify({...first.tasks[id],id:key,title});
  const records=JSON.parse('{"__proto__":'+named('__proto__','Prototype-named task')+',"PLAIN":'+named('PLAIN','Ordinary task')+'}');
  const head={hash:'c'.repeat(64),seq:201},order=[...Object.keys(first.tasks),'__proto__','PLAIN'];
  const delta={set:{registry_head:head,server_time:'2026-10-15T12:00:02+00:00'},remove:[],lists:{},maps:{tasks:{order,set:records}}};
  app.http=()=>reply({delta,base_token:first.change_token,change_token:'d'.repeat(64),server_time:'2026-10-15T12:00:02+00:00',registry_head:head});
  await app.refresh();
  for(const name of ['transferLanding.tasks','state.tasks']){
   assert.deepEqual(plainCopy(app.run('Object.keys('+name+')')),order,name+' keeps every id as an own key, in the server order');
   assert.equal(app.run(name+'["__proto__"].title'),'Prototype-named task');
   assert.equal(app.run('Object.getPrototypeOf('+name+')===Object.prototype'),true,name+' keeps an ordinary prototype');
  }
  const patch='transferMerge(JSON.parse(\'{"tasks":{"__proto__":{"id":"__proto__","title":"Detail"},"PLAIN":{"id":"PLAIN","title":"Plain detail"}}}\'))';
  app.run(patch);
  assert.equal(app.run('state.tasks["__proto__"].title'),'Detail','a detail patch keeps a prototype-named record');
  assert.equal(app.run('state.tasks.PLAIN.title'),'Plain detail','a detail patch keeps an ordinary record');
  assert.deepEqual(plainCopy(app.run('Object.keys(state.tasks)')),order);
  assert.equal(app.run('Object.getPrototypeOf(state.tasks)===Object.prototype'),true);
  // A patch merges by own key even into a view that does not hold that id yet.
  const fresh=await boot({fixture:first,http:()=>reply(first)});fresh.run(patch);
  assert.equal(fresh.run('Object.hasOwn(state.tasks,"__proto__")&&state.tasks["__proto__"].title'),'Detail','a merged id is an own key');
  assert.equal(fresh.run('Object.getPrototypeOf(state.tasks)===Object.prototype'),true,'a merge never replaces the map prototype');
  assert.equal(fresh.run('state.tasks.PLAIN.title'),'Plain detail');
 },
 async aging(){
  const first=landing();first.waits=[{kind:'ack',id:'M-AGE',task:null,who:['A'],seconds:900,threshold:900,as_of:first.server_time}];
  const app=await boot({fixture:first,http:()=>reply(first)});
  let token=first.change_token;const shown=[];
  for(const [n,stamp] of [[1,'2026-10-15T12:00:20+00:00'],[2,'2026-10-15T12:00:40+00:00']]){
   const next='e'.repeat(63)+n,search={...(first.search||{}),results:[],total:0};
   app.http=()=>reply({delta:{set:{search},remove:[],maps:{},lists:{}},base_token:token,change_token:next,server_time:stamp,registry_head:first.registry_head});
   await app.refresh();token=next;
   assert.equal(app.run('state.change_token'),next,'the search-only delta is applied');
   assert.equal(app.run('transferLanding.server_time'),first.server_time,'a reused landing keeps the time its rows were computed');
   assert.equal(app.run('transferLanding.waits[0].seconds'),900,'the landing itself is never aged');
   assert.equal(app.run('state.server_time'),stamp);
   shown.push(app.run('state.waits[0].seconds'));
  }
  assert.deepEqual(shown,[920,940],'rows age from the landing time to each reply, without drift');
 },
 async names(){
  const first=landing();const app=await boot({fixture:first,http:()=>reply(first)});
  assert.equal(app.run("searchRecord({kind:'decision',seq:42,actor:'C',data:{reason:'Synthetic'}}).id"),'dec-e42');
  assert.equal(app.run("searchRecord({kind:'decision',seq:43,actor:'C',data:{id:'D-NAMED',reason:'Synthetic'}}).id"),'D-NAMED');
  const id=Object.keys(first.tasks)[0];
  app.run('state.tasks['+JSON.stringify(id)+'].brief={text:"Brief heading\\nmore"}');
  app.run('loadUI.query="zz";state.search={query:"zz",results:[{kind:"tasks",id:'+JSON.stringify(id)+',text:"Raw title",seq:1,utc:state.server_time,actor:"C",outcome:""}],total:1};searchUI.open=true;searchUI.signature=null;renderSearch()');
  const option=app.doc.getElementById('search-results').children.find(child=>child.getAttribute('role')==='option');assert.ok(option,'one search option is rendered');
  assert.equal(option.children[0].textContent,'Brief heading','a task result carries the name the lists use');
  const row=app.run("commsPairRow({lanes:['A','C'],messages:3,acked:1,info:1,sent:0,queued:0,overdue:0,pending:1,oldest_pending_seconds:null,tasks:[]})");
  assert.equal(row.children.length,7);
  assert.equal(row.children[6].textContent,'1','information messages have their own column');
 }
};
(async()=>{await tests[scenario]();console.log('dashboard perf contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
// The loading harness already defines and adapts the common panel harness.
eval(harness+'\neval(harness+\'\\n\'+cases);');

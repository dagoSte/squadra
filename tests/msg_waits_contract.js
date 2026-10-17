// Use real producer rows with the existing DOM harness, without external services.
'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const prefix=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const checks=String.raw`
(async()=>{
 let count=0;
 for(const language of ['it-IT','en-US'])for(const phone of [false,true])for(const theme of ['light','dark']){
  const app=await boot({language,phone,stored:{'squadra.theme':theme}});
  const forward=app.fixture.attention.waiting.find(w=>w.kind==='to-forward');
  assert.ok(forward&&forward.ack_command,'fixture uses a real forward item');
  const expected=language.startsWith('it')?'Da inoltrare':'To forward';
  for(const section of ['ovWaiting()','recordedWaiting()']){
   const panel=app.run(section),words=text(panel);
   assert.match(words,new RegExp(expected));
   assert.equal(words.split(forward.ack_command).length-1,1,'one original ACK command per message');
   assert.ok(words.includes(forward.to)&&words.includes(forward.who[0]),'recipient and coordinator are explicit');
   assert.doesNotMatch(words,/⟦/,'both languages have labels');
   const first=section==='ovWaiting()'?one(panel,e=>e.tag==='ul').children[0]:panel.children[1];
   assert.ok(text(first).includes(forward.ack_command),'forwarding precedes other waits');
  }
  const ids=JSON.parse(app.run('JSON.stringify(compactWaits(state.attention.waiting).map(w=>[w.kind,w.id]))'));
  assert.equal(ids.filter(([kind,id])=>id===forward.id&&kind==='ack').length,0);
  assert.equal(ids.filter(([kind,id])=>id===forward.id&&kind==='to-forward').length,1);
  const delivered=clone(app.fixture);
  delivered.attention.waiting=delivered.attention.waiting.filter(w=>w.kind!=='to-forward');
  delivered.waits=delivered.waits.filter(w=>w.kind!=='to-forward');
  await app.refresh(delivered);
  assert.doesNotMatch(text(app.run('ovWaiting()')),new RegExp(expected),'true delivery ends forwarding, ACK still pending');
  assert.equal(delivered.messages[forward.id].ack,null);
  const acked=clone(delivered);acked.messages[forward.id].ack={agent:forward.to,utc:'2026-10-16T15:00:01Z'};
  acked.attention.waiting=acked.attention.waiting.filter(w=>w.id!==forward.id);
  acked.waits=acked.waits.filter(w=>w.id!==forward.id);
  acked.messages.STALE={...acked.messages[forward.id],id:'STALE',ack:null};
  acked.comms={lanes:['C','R'].map(id=>({identity:id,registered:true,seats:[{id,role:id==='C'?'coordinator':'reviewer'}],work:[]})),edges:[],pairs:[]};
  await app.refresh(acked);
  assert.ok(!text(app.run('ovWaiting()')).includes(forward.ack_command),'ACK of original ends the remaining receipt wait');
  const metric=app.doc.getElementById('metrics').children[2];
  assert.equal(String(one(metric,e=>e.tag==='strong').textContent),'0','headline uses the operational ACK rows, not historical missing ACKs');
  const pair=app.run('commsPairRow('+JSON.stringify({lanes:['C','R'],messages:11,acked:0,sent:9,queued:2,pending:0,overdue:0,oldest_pending_seconds:null,tasks:[],info:0,same_window:false})+')');
  assert.equal(text(pair.children[3]),'0','Communications uses measured pending count even when it is zero');
  count++;
 }
 console.log('message waits contract passed: '+count+' combinations');
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(prefix+checks);

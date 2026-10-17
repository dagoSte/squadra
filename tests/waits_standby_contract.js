// Mail to a window in stand-by shows as one counted row, from the real server's rows, in both languages.
// Usage: node waits_standby_contract.js <dashboard.html> <state.json> <recorded reason>
'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const prefix=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const checks=String.raw`
const reason=process.argv[4];
(async()=>{
 let count=0;
 for(const language of ['it-IT','en-US'])for(const phone of [false,true])for(const theme of ['light','dark']){
  const app=await boot({language,phone,stored:{'squadra.theme':theme}});
  const held=app.fixture.attention.waiting.filter(w=>w.kind==='stand-by');
  assert.equal(held.length,1,'the server folds the stand-by mail into one recorded row');
  const n=held[0].messages.length,seat=app.run('seatName('+JSON.stringify(held[0].id)+')');
  const sentence=language.startsWith('it')?n+' messaggi a '+seat+' in stand-by ('+reason+')':n+' messages to '+seat+' on stand-by ('+reason+')';
  const overview=app.run('ovWaiting()'),list=one(overview,e=>e.tag==='ul').children;
  const rows=list.filter(li=>text(li).includes(sentence));
  assert.equal(rows.length,1,'one counted row in «What is waiting»: '+text(overview));
  assert.ok(list.length>1&&rows[0]===list.at(-1),'the counted row comes after every wait, even when newer');
  const over=app.run("tr('ov.wait.over',{minutes:'@'})").split('@')[0];
  assert.ok(!text(rows[0]).includes(over),'a stand-by row is not an overdue wait');
  const recorded=app.run('recordedWaiting()'),words=text(recorded);
  assert.equal(words.split(sentence).length-1,1,'one counted row among the recorded waits');
  assert.ok(words.includes(app.run("tr('page.wait.standby')")),'the row says how the mail comes back');
  assert.ok(words.includes(app.run("tr('status.stand-by')")),'the row carries the stand-by label');
  assert.ok(!words.includes(app.run("tr('page.wait.over',{kind:descriptions['stand-by']})")),'the timed row is not repeated as overdue');
  assert.doesNotMatch(words+' '+text(overview),/⟦/,'both languages have labels');
  const pending=new Set(app.fixture.attention.pending_ack);
  assert.ok(held[0].messages.every(id=>pending.has(id)),'fixture: the stand-by mail is still pending');
  app.run('render()');
  const metric=app.doc.getElementById('metrics').children[2];
  assert.equal(String(one(metric,e=>e.tag==='strong').textContent),String(pending.size),'pending ACKs still count the stand-by mail');
  count++;
 }
 console.log('waits stand-by contract passed: '+count+' combinations');
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(prefix+checks);

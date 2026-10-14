'use strict';
// Check that a task closed without its own read is shown as closed and unread, with its own count.
// Usage: node review_gate_contract.js <dashboard.html> <state.json from the real server>
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
(async()=>{
 for(const language of ['it','en']){
  const app=await boot({language});
  const label=language==='it'?'Chiuso, da leggere al gate':'Closed, read at the gate';
  const count=language==='it'?'1 chiusi senza lettura, da leggere al gate':'1 closed without a read, to be read at the gate';
  const proofSub=language==='it'?'Prova e review sulla stessa versione':'Proof and review on the same version';
  assert.equal(app.run('situationBadge(state.progress.L1).textContent'),label,'progress card names the unread close');
  assert.equal(app.run('stepText(state.attention.next_steps.find(s=>s.task==="L1"))'),label,'next step names the gate read');
  const metrics=text(app.doc.getElementById('metrics'));
  assert.ok(metrics.includes(count),'closed metric carries the unread count');
  assert.ok(!metrics.includes(proofSub),'no claim of a review for an unread close');
  await app.tab('Incarichi');
  assert.ok(text(app.doc.getElementById('content')).includes(label),'tasks table shows the unread close');
  app.run('state.progress.L1.state={id:"closed",utc:state.progress.L1.state.utc};render()');
  assert.equal(app.run('situationBadge(state.progress.L1).textContent'),language==='it'?'Chiuso':'Closed','read task is plainly closed');
  assert.ok(!text(app.doc.getElementById('metrics')).includes(count),'no unread count once read');
  assert.ok(text(app.doc.getElementById('metrics')).includes(proofSub),'usual sub-line returns');
 }
 console.log('read at the gate page passed: it en');
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(harness+cases);

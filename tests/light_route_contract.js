'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
(async()=>{
 for(const language of ['it','en']){
  const app=await boot({language});
  const expected=language==='it'?'Passaggio al percorso completo: servono una nuova prova e una lettura indipendente':'Moved to the full route: a fresh proof and independent read are required';
  const story=()=>JSON.parse(app.run('JSON.stringify(panelStory(state.tasks.L))'));
  assert.equal(story().filter(item=>item.text===expected).length,1,'one translated promotion in task history');
  app.run('panelOpenStory("L")');
  assert.ok(text(app.doc.getElementById('story')).includes(expected),'visible task history explains the fresh proof/read');
  const writes=app.writes.length;
  await app.refresh();
  assert.equal(story().filter(item=>item.text===expected).length,1,'refresh retains the promotion');
  assert.equal(app.writes.length,writes,'the promotion display does not write settings');
  app.run('state.tasks.L.route_history=[]');
  assert.equal(story().filter(item=>item.text===expected).length,0,'no promotion invented for an old task');
 }
 console.log('light route page passed: it en');
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(harness+cases);

'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
(async()=>{
 for(const language of ['it-IT','en-US']){
  const app=await boot({language});
  for(const task of ['ONE','TWO']){
   app.run("connectedGo('Prove',{task:"+JSON.stringify(task)+"})");await settle();
   const content=app.doc.getElementById('content');
   const rows=all(content,e=>(e.getAttribute('data-read-scope')||'').startsWith('runs:'));
   assert.equal(rows.length,1,'one physical proof row for each served task context');
   assert.match(text(rows[0]),/Synthetic ONE/);assert.match(text(rows[0]),/Synthetic TWO/);
   assert.equal(app.run("Object.keys(state.runs).length"),1);
   assert.equal(app.run("state.tasks.TWO.proofs.length"),1);
  }
 }
 for(const language of ['it-IT','en-US']){
  const cases=[
   ['v2 members',{v:2,tasks:[{task:'TWO',covers:['B1']}]}],
   ['v2 string',{v:2,tasks:'TWO'}],
   ['v2 null member',{v:2,tasks:[null]}],
   ['v1 string',{v:1,tasks:'TWO'}],
   ['v1 null member',{v:1,tasks:[null]}],
   ['v1 non-string task',{v:1,tasks:[{task:2}]}]
  ];
  for(const [label,metadata] of cases){
   const fixture=clone(base),run=Object.values(fixture.runs)[0];
   run.shared_proof=metadata;fixture.tasks.TWO.proofs=[];
   fixture.tasks.ONE.title='ONE';fixture.tasks.TWO.title='TWO';
   const app=await boot({language,fixture});
   assert.doesNotThrow(()=>app.run("tab='Prove';render()"),label+' renders the Prove tab');
   assert.equal(app.run('runTitles(Object.values(state.runs)[0])'),'ONE',label+' names only the primary');
   for(const [task,count] of [['ONE',1],['TWO',0]]){
    app.run("connectedGo('Prove',{task:"+JSON.stringify(task)+"})");await settle();
    const rows=all(app.doc.getElementById('content'),e=>(e.getAttribute('data-read-scope')||'').startsWith('runs:'));
    assert.equal(rows.length,count,label+' filters on actual membership');
   }
  }
 }
 console.log('shared proof UI passed: IT and EN, valid v1 and unsupported/malformed metadata');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\n'+cases);

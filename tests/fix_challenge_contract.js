'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
(async()=>{
 for(const language of ['it','en']){
  const app=await boot({language});
  app.run('state.tasks.L.fix={v:1,agreement:{files:{}}};state.tasks.L.fix_result={base:"123456789012abcdef",candidate:{head:"abcdef",sha256:"fedcba9876543210"},runs:[]};state.tasks.L.fix_status="matched"');
  const line=()=>app.run('fixChallengeLine(state.tasks.L)');
  assert.ok(text(line()).includes(language==='it'?'Fallisce senza la modifica: sì':'Fails without the change: yes'));
  assert.ok(text(line()).includes('123456789012'));
  assert.ok(text(line()).includes('fedcba9876543210'));
  for(const status of ['pending','failed','outdated','unsupported','exception']){
   app.run('state.tasks.L.fix_status='+JSON.stringify(status)+';state.tasks.L.fix_result.reason="<script>unsafe</script>"');
   const view=line();
   assert.ok(!text(view).includes(language==='it'?'modifica: sì':'change: yes'),status+' never says matched');
   assert.equal(all(view,e=>e.tag==='script').length,0,'reason is text only');
   if(status==='exception')assert.ok(text(view).includes('<script>unsafe</script>'));
  }
  app.run('delete state.tasks.L.fix');
  assert.equal(line(),null,'old tasks do not gain an obligation');
  assert.equal(app.writes.length,0,'display never writes');
 }
 console.log('fix challenge page passed: it en');
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(harness+cases);

// Old runs of a window in stand-by are counted in its row, alone or beside its mail, in both languages.
// Usage: node waits_standby_runs_contract.js <dashboard.html> <state.json> <recorded reason> <runs|mixed>
'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const prefix=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const checks=String.raw`
const reason=process.argv[4],mode=process.argv[5];
(async()=>{
 for(const language of ['it-IT','en-US']){
  const it=language.startsWith('it');
  const app=await boot({language});
  const seat=app.run("seatName('A')");
  const runs=it?'1 esecuzione di '+seat:'1 run of '+seat;
  const expected=mode==='mixed'?(it?'1 messaggio a '+seat+' in stand-by ('+reason+') · '+runs+', stato incerto':'1 message to '+seat+' on stand-by ('+reason+') · '+runs+', state uncertain')
   :(it?runs+' in stand-by ('+reason+'), stato incerto':runs+' on stand-by ('+reason+'), state uncertain');
  const check=label=>{
   const overview=text(app.run('ovWaiting()')),recorded=text(app.run('recordedWaiting()'));
   for(const [name,words] of [['overview',overview],['recorded',recorded]]){
    assert.equal(words.split(expected).length-1,1,label+': one counted row in '+name+': '+words);
    assert.ok(!words.includes(it?'0 messaggi a':'0 messages to'),label+': a run-only window is not zero messages');
    assert.doesNotMatch(words,/⟦/,'both languages have labels');
   }
   assert.ok(recorded.includes(app.run("tr('wait.running')")),label+': the run stays listed with its uncertain result');
   assert.equal(recorded.includes(app.run("tr('page.wait.standby')")),mode==='mixed',label+': the mail sub-line belongs to mail');
  };
  check('server rows');
  // Recorded rows without the runs, as an older server sends them: the page takes them from the timed rows.
  const bare=clone(app.fixture);
  bare.attention.waiting=bare.attention.waiting.filter(w=>w.kind!=='stand-by'||(w.messages||[]).length).map(w=>w.kind==='stand-by'?{...w,runs:[]}:w);
  await app.refresh(bare);
  check('recorded rows without runs');
 }
 console.log('runs contract passed: '+mode);
})().catch(error=>{console.error(error);process.exitCode=1});
`;
eval(prefix+checks);

'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
const ID='TASK-KQ1',ORIGIN=Date.parse('2026-01-01T00:00:00Z');
const at=minutes=>new Date(ORIGIN+minutes*60000).toISOString();
function fixture(){
 const f=clone(base),t=f.tasks[ID];t.status='done';t.status_utc=at(180);t.superseded_by=null;
 const p=f.progress[ID];p.created=at(0);p.state={id:'closed'};
 for(const s of p.steps){s.reached=true;s.utc=at(s.id==='closed'?180:s.id==='build'?35:0)}
 f.messages['assign-'+ID].ack={agent:t.owner,utc:at(10)};
 let seq=0;const add=(kind,min,verdict)=>({id:'time:'+ ++seq,seq,kind,utc:at(min),task:ID,tasks:[ID],verdict,artifacts:[]});
 f.graph.nodes=f.graph.nodes.filter(n=>n.task!==ID).concat([
  add('task',0),add('plan',15),add('plan-verdict',20,'changes-required'),add('plan',30),add('plan-verdict',35,'accepted'),
  add('delivery',70),add('code-verdict',90,'changes-required'),add('delivery',120),add('code-verdict',150,'accepted'),add('close',180)]);
 f.project_started=at(-4320);f.activity.as_of=at(0);f.activity.since=at(-21600);
 f.events=f.events.slice(-2);return f;
}
const result=app=>JSON.parse(app.run('JSON.stringify(taskTimePhases(state.tasks["'+ID+'"]))'));
const task=app=>app.run('state.tasks["'+ID+'"]');
const pane=app=>app.run('taskTimeDetails(state.tasks["'+ID+'"])');
const tests={
 async rounding(){for(const language of ['it','en']){
  const app=await boot({fixture:fixture(),language});
  for(const [seconds,amount,unit] of [[0,0,'min'],[29,0,'min'],[30,1,'min'],[90,2,'min'],[3599,60,'min'],[3600,1,'h'],[5400,2,'h'],[86399,24,'h'],[86400,1,language==='it'?'giorno':'day'],[129600,2,language==='it'?'giorni':'days'],[216000,3,language==='it'?'giorni':'days']])
   assert.equal(app.run('timeWords('+seconds+')'),(language==='it'?'circa':'about')+' '+amount+' '+unit);
  assert.equal(app.run('elapsedSeconds("2026-01-01T01:00:00+01:00","2026-01-01T01:00:00Z")'),3600);
  assert.equal(app.run('elapsedSeconds("2026-01-02T00:00:00Z","2026-01-01T00:00:00Z")'),0);
  for(const value of ['null','undefined','""','"broken"'])assert.equal(app.run('elapsedSeconds('+value+',"2026-01-01T00:00:00Z")'),null);
 }},
 async views(){for(const language of ['it','en'])for(const phone of [false,true]){
  const app=await boot({fixture:fixture(),language,phone}),expected=language==='it'?'Tempo totale: circa 3 h':'Total time: about 3 h';
  const closed=one(app.doc.getElementById('content'),e=>hasClass(e,'sq-closed'));
  const card=one(closed,e=>e.getAttribute('data-task')===ID);
  assert.ok(text(card).includes(expected),'closed card has assignment-to-close total');
  assert.equal(all(card,e=>hasClass(e,'sq-task-timing')).length,0,'phase details do not crowd cards');
  assert.ok(text(app.run('ovProgress()')).includes(expected),'full overview also shows total');
  assert.ok(text(app.run('team()')).includes(expected),'window assignments show total');
  await app.tab('Incarichi');
  const label=language==='it'?'Tempo totale':'Total time';
  assert.ok(all(app.doc.getElementById('content'),e=>e.getAttribute('data-label')===label).some(e=>text(e).includes(language==='it'?'circa 3 h':'about 3 h')),'Tasks time column');
  assert.ok(text(app.doc.getElementById('content')).includes(language==='it'?'Dove è trascorso il tempo':'Where the time went'));
  app.find('table:tasks:sort:time').onclick();await settle();app.find('table:tasks:sort:time').onclick();await settle();
  assert.equal(one(app.doc.getElementById('content'),e=>e.getAttribute('data-read-scope')?.startsWith('tasks:')).getAttribute('data-read-scope'),'tasks:'+ID,'elapsed column sorts numerically');
  app.run('panelOpenStory("'+ID+'")');
  const story=app.doc.getElementById('story');assert.ok(text(story).includes(expected));
  assert.ok(text(story).includes(language==='it'?'Suggerimento per la prossima volta':'Next time faster'));
  assert.ok(!text(story).includes('⟦'),'all labels are translated');
  for(const status of ['assigned','started','delivered','changes-required']){
   app.run('state.tasks["'+ID+'"].status='+JSON.stringify(status));assert.equal(app.run('taskTime(state.tasks["'+ID+'"])'),null);
  }
  app.run('state.tasks["'+ID+'"].status="done";state.tasks["'+ID+'"].superseded_by="TASK-KQ3"');
  assert.equal(app.run('taskTime(state.tasks["'+ID+'"])'),null);assert.equal(pane(app),null);
  assert.equal(all(app.run('panelTaskCard(state.tasks["'+ID+'"])'),e=>hasClass(e,'sq-task-time')).length,0);
 }},
 async refresh(){for(const language of ['it','en']){
  const f=fixture(),app=await boot({fixture:f,language,clock:ORIGIN+99*86400000});
  const hero=()=>text(app.doc.getElementById('connected-hero'));
  assert.ok(hero().includes(language==='it'?'Progetto avviato da circa 3 giorni':'Project running for about 3 days'));
  const timers=app.timers.length,writes=app.writes.length,calls=app.fetched.length;
  f.activity.as_of=at(1440);f.tasks[ID].status_utc=at(1440);await app.refresh(f);
  assert.ok(hero().includes(language==='it'?'circa 4 giorni':'about 4 days'));
  assert.equal(app.run('taskTime(state.tasks["'+ID+'"])'),10800,'close event stops the total even if status/output changes');
  assert.equal(app.fetched.length,calls+1);assert.ok(app.fetched.every(p=>p==='/api/state'));
  assert.equal(app.timers.length,timers);assert.equal(app.writes.length,writes);
  delete f.project_started;await app.refresh(f);assert.equal(all(app.doc.getElementById('connected-hero'),e=>hasClass(e,'sq-project-time')).length,0,'never infer project start from recent events or activity window');
 }},
 async phases(){for(const language of ['it','en']){
  const app=await boot({fixture:fixture(),language}),r=result(app);
  assert.deepEqual(r,{total:10800,values:{ack:600,plan:600,build:2400,read:3000,fix:2400,other:1800},rounds:2,complete:true,hint:'read'});
  assert.equal(Object.values(r.values).reduce((a,b)=>a+b),r.total,'no overlaps counted twice');
  const words=text(pane(app));assert.ok(words.includes(language==='it'?'non ore di lavoro attivo':'not active working hours'));
  assert.ok(words.includes(language==='it'?'correzioni: 2':'changes: 2'));
  const f=fixture();f.graph.nodes.push({...f.graph.nodes.find(n=>n.kind==='delivery'&&n.task===ID),id:'extra',seq:6.5,utc:at(75)});await app.refresh(f);
  assert.equal(result(app).values.read,3000,'overlapping delivery-to-review intervals count once');
 }},
 async plan_wait(){for(const language of ['it','en']){
  const f=fixture();let seq=0;const add=(kind,min,verdict)=>({id:'plan-wait:'+ ++seq,seq,kind,utc:at(min),task:ID,tasks:[ID],verdict,artifacts:[]});
  f.messages['assign-'+ID].ack.utc=at(1);
  f.progress[ID].steps.find(s=>s.id==='build').utc=at(63);
  f.progress[ID].steps.find(s=>s.id==='closed').utc=at(68);
  f.graph.nodes=[add('task',0),add('plan',2),add('plan-verdict',62,'accepted'),add('delivery',65),add('code-verdict',67,'accepted'),add('close',68)];
  const app=await boot({fixture:f,language}),r=result(app);
  assert.deepEqual(r,{total:4080,values:{ack:60,plan:3600,build:240,read:120,fix:0,other:60},rounds:0,complete:true,hint:'plan'});
  assert.equal(Object.values(r.values).reduce((a,b)=>a+b),r.total,'plan wait is separate; planning and accepted-to-delivery stay construction');
  const words=text(pane(app));
  assert.ok(words.includes(language==='it'?'Attesa delle letture del piano':'Waiting for plan reads'));
  assert.ok(words.includes(language==='it'?'circa 1 h':'about 1 h'));
  assert.ok(words.includes(app.run('tr("time.hint.plan")')));
  assert.equal(all(pane(app),e=>e.tag==='h4').length,1);
  assert.ok(!words.includes('⟦'),'new plan labels are translated');
  app.run('panelOpenStory("'+ID+'")');
  assert.ok(text(app.doc.getElementById('story')).includes(app.run('tr("time.hint.plan")')));
  f.progress[ID].steps.find(s=>s.id==='build').reached=false;await app.refresh(f);
  assert.equal(result(app).values.build,240,'a recorded start does not remove planning or accepted-to-start time');
  assert.equal(result(app).values.other,60);
  f.graph.nodes=f.graph.nodes.filter(n=>n.kind!=='plan-verdict');await app.refresh(f);
  assert.equal(result(app).values.plan,null,'missing plan verdict is unavailable');
  assert.equal(result(app).complete,false);assert.equal(result(app).hint,null);
 }},
 async missing(){for(const language of ['it','en']){
  const f=fixture(),app=await boot({fixture:f,language});delete f.messages['assign-'+ID].ack;await app.refresh(f);
  let r=result(app);assert.equal(r.values.ack,null);assert.equal(r.values.build,2100,'build can use recorded start when ack is missing');assert.equal(r.complete,false);assert.equal(r.hint,null);
  assert.ok(text(pane(app)).includes(language==='it'?'Non disponibile':'Not available'));
  f.graph.nodes=f.graph.nodes.filter(n=>!(n.task===ID&&n.kind==='code-verdict'));await app.refresh(f);r=result(app);assert.equal(r.values.read,null);assert.equal(r.hint,null);
  f.progress[ID].steps=f.progress[ID].steps.filter(s=>s.id!=='closed');await app.refresh(f);assert.equal(result(app),null);assert.ok(text(pane(app)).includes(language==='it'?'La cronologia non basta':'The history is insufficient'));
 }},
 async hints(){for(const language of ['it','en'])for(const phase of ['ack','build','read','fix']){
  const f=fixture();let min=0,seq=0;const add=(kind,when,verdict)=>({id:'hint:'+ ++seq,seq,kind,utc:at(when),task:ID,tasks:[ID],verdict,artifacts:[]});
  const ack=phase==='ack'?60:10,delivery=ack+(phase==='build'?60:10),read=delivery+(phase==='read'?60:10),nodes=[add('task',0),add('delivery',delivery),add('code-verdict',read,phase==='fix'?'changes-required':'accepted')];
  min=read;if(phase==='fix'){min+=60;nodes.push(add('delivery',min),add('code-verdict',min+10,'accepted'));min+=10}
  nodes.push(add('close',min));f.graph.nodes=nodes;f.messages['assign-'+ID].ack.utc=at(ack);f.progress[ID].steps.find(s=>s.id==='closed').utc=at(min);
  const app=await boot({fixture:f,language});assert.equal(result(app).hint,phase);
  assert.ok(text(pane(app)).includes(app.run('tr("time.hint.'+phase+'")')));
  assert.equal(all(pane(app),e=>e.tag==='h4').length,1,'one suggestion');
 }
 }
};
tests[scenario]().then(()=>console.log('time contract passed: '+scenario)).catch(e=>{console.error(e.stack);process.exitCode=1});
`;
new Function('require','process',harness+cases)(require,process);

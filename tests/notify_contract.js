// Check notifications with a simplified DOM, a controllable clock and silent audio.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const audio=String.raw`
 app.audio={contexts:[],notes:[],resumes:0,mode:'ok'};
 class Param{setValueAtTime(value){this.value=value}linearRampToValueAtTime(value){this.value=value}exponentialRampToValueAtTime(value){this.value=value}}
 class Audio{
  constructor(){if(app.audio.mode==='throw')throw Error('Audio unavailable');this.state='suspended';this.currentTime=0;this.destination={};app.audio.contexts.push(this)}
  createGain(){return {gain:new Param(),connect(){},disconnect(){}}}
  createOscillator(){const osc={frequency:new Param(),connect(){},disconnect(){},start(at){this.startAt=at;app.audio.notes.push(this)},stop(at){this.stopAt=at}};return osc}
  resume(){app.audio.resumes++;if(app.audio.mode==='reject')return Promise.reject(Error('Blocked'));this.state='running';return Promise.resolve()}
 }
 let timerID=0;
`;
for(const [before,after] of [
 ['phone=false,clock}', 'phone=false,clock=0}'],
 ['focus(){this.doc.activeElement=this}', 'contains(e){return [...walk(this)].includes(e)}\n focus(){this.doc.activeElement=this}'],
 ['const context={console,',audio+'const context={AudioContext:Audio,console,'],
 ['setTimeout:fn=>{timers.push(fn)},','setTimeout:(fn,ms=0)=>{const id=++timerID;timers.push({id,fn,at:app.clock+ms});return id},clearTimeout:id=>{const i=timers.findIndex(t=>t.id===id);if(i>=0)timers.splice(i,1)},'],
 ["app.refresh=async next=>{if(next)app.fixture=next;const i=timers.findIndex(fn=>fn.name==='refresh'),fn=i<0?undefined:timers.splice(i,1)[0];assert.ok(fn,'a refresh is scheduled');await fn();await settle()};",String.raw`
 app.advance=async ms=>{const end=app.clock+ms;let calls=0;while(timers.some(t=>t.at<=end)){
  assert.ok(++calls<100,'bounded timer loop');timers.sort((a,b)=>a.at-b.at);const timer=timers.shift();app.clock=timer.at;await timer.fn();await settle();
 }app.clock=end};
 app.refresh=async next=>{if(next)app.fixture=next;await app.advance(2000)};
 app.click=async()=>{for(const fn of doc.listeners.click||[])fn({isTrusted:true});await settle()};
 `]
]){if(!prefix.includes(before))throw Error('base harness seam changed');prefix=prefix.replace(before,after)}
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const region=app=>app.doc.getElementById('notify-toasts');
const control=app=>one(app.doc.getElementById('notify-controls'),e=>e.tag==='button');
const notices=app=>region(app).children;
const fixture=(statuses=['working','working','working'])=>{
 const f=clone(base);f.tasks={};statuses.forEach((status,i)=>{const id='task'+i;f.tasks[id]={id,title:'Task '+i,status,owner:'builder',reviewer:'reader',scope:{name:'Scope.txt',sha256:'a'.repeat(64),bytes:1},reviews:[],proofs:[],depends:[],rounds:{plan:0,code:0}}});return f;
};
const close=(app,ids)=>{const f=clone(app.fixture);for(const id of ids)f.tasks['task'+id].status='done';return f};
const healthy=app=>assert.equal(app.doc.getElementById('live').className,'status','page refresh succeeded: '+text(app.doc.getElementById('notice')));
const toggle=async app=>{control(app).onclick({isTrusted:true});await settle()};
const texts=app=>notices(app).map(t=>t.children[0].textContent);
const tests={
 async baseline(){
  for(const statuses of [[],['done'],['done','working']]){
   const app=await boot({fixture:fixture(statuses)});healthy(app);
   assert.equal(app.audio.contexts.length,0,'no context at load');assert.equal(notices(app).length,0,'first state is a baseline');
   await app.click();assert.equal(app.audio.contexts.length,1);assert.equal(app.audio.notes.length,0,'unlock never replays history');
   await app.refresh();healthy(app);assert.equal(notices(app).length,0);assert.equal(app.audio.notes.length,0);
  }
 },
 async step_burst_complete(){
  const app=await boot({fixture:fixture(['working','working','working','working'])});await app.click();
  await app.refresh(close(app,[0]));healthy(app);assert.deepEqual(texts(app),['Passo fatto: Task 0']);assert.equal(app.audio.notes.length,1,'one short chime');
  const chime=app.audio.notes[0];assert.ok(chime.stopAt-chime.startAt<0.3);assert.equal(chime.type,'sine');
  await app.refresh(close(app,[1,2]));healthy(app);assert.equal(app.audio.notes.length,2,'a burst is one chime');assert.equal(texts(app).at(-1),'Passi fatti: 2');
  await app.refresh(close(app,[3]));healthy(app);assert.equal(app.audio.notes.length,5,'last close replaces the chime with three rising notes');
  const melody=app.audio.notes.slice(-3);assert.ok(melody[0].frequency.value<melody[1].frequency.value&&melody[1].frequency.value<melody[2].frequency.value);
  assert.ok(melody.at(-1).stopAt-melody[0].startAt>0.6);assert.equal(texts(app).at(-1),'Lavoro completato: nessun incarico aperto');
  await app.refresh();assert.equal(app.audio.notes.length,5,'no repeated completion');assert.equal(texts(app).filter(s=>s.startsWith('Lavoro')).length,1);
  assert.ok(app.fetched.every(p=>p==='/api/state'),'notifications add no requests');
 },
 async mute_storage(){
  const app=await boot({fixture:fixture()});assert.equal(control(app).getAttribute('aria-pressed'),'true');
  await toggle(app);assert.deepEqual(app.writes.at(-1),['squadra-sounds','off']);await app.click();assert.equal(app.audio.contexts.length,0);
  await app.refresh(close(app,[0]));assert.equal(notices(app).length,1);assert.equal(app.audio.notes.length,0);
  const again=await boot({fixture:fixture(),stored:Object.fromEntries(app.writes)});assert.equal(control(again).getAttribute('aria-pressed'),'false');
  await toggle(again);assert.deepEqual(again.writes.at(-1),['squadra-sounds','on']);assert.equal(again.audio.contexts.length,1);
  await again.refresh(close(again,[0]));assert.equal(again.audio.notes.length,1);await toggle(again);await again.refresh(close(again,[1,2]));assert.equal(again.audio.notes.length,1);assert.equal(texts(again).at(-1),'Lavoro completato: nessun incarico aperto');
  for(const storage of ['throws','missing']){
   const b=await boot({fixture:fixture(),storage});assert.equal(control(b).getAttribute('aria-pressed'),'true');await toggle(b);await b.refresh(close(b,[0]));healthy(b);assert.equal(texts(b).length,1);await toggle(b);assert.equal(control(b).getAttribute('aria-pressed'),'true');
  }
 },
 async gesture_failures_hidden(){
  const app=await boot({fixture:fixture()});await app.refresh(close(app,[0]));assert.equal(app.audio.contexts.length,0);assert.equal(texts(app).length,1);
  for(const fn of app.doc.listeners.click)fn({isTrusted:false});assert.equal(app.audio.contexts.length,0,'programmatic click cannot unlock');
  await app.click();assert.equal(app.audio.notes.length,0,'no backlog');const ctx=app.audio.contexts[0];ctx.state='suspended';
  await app.refresh(close(app,[1]));assert.equal(app.audio.resumes,1,'polling never resumes');await app.click();assert.equal(app.audio.resumes,2);
  app.doc.hidden=true;await app.refresh(close(app,[2]));assert.equal(app.audio.notes.length,0,'hidden page skips audio');assert.equal(texts(app).at(-1),'Lavoro completato: nessun incarico aperto');
  for(const mode of ['throw','reject','missing']){
   const a=await boot({fixture:fixture()});a.audio.mode=mode;if(mode==='missing')a.run('delete window.AudioContext');await a.click();await a.refresh(close(a,[0]));healthy(a);assert.equal(texts(a).length,1);assert.equal(a.audio.notes.length,0);
  }
 },
 async toast_lifetime(){
  const app=await boot({fixture:fixture(Array(7).fill('working'))});await app.refresh(close(app,[0]));
  await app.advance(5999);assert.equal(notices(app).length,1);await app.advance(1);assert.equal(notices(app).length,0,'dismiss at six seconds');
  await app.refresh(close(app,[1]));const first=notices(app)[0];first.children[1].focus();await app.refresh(close(app,[2]));assert.equal(app.doc.activeElement,first.children[1],'adding a toast preserves focus');
  first.children[1].onclick();assert.equal(app.doc.activeElement,control(app),'dismiss returns focus to the sounds control');assert.equal(notices(app).length,1);
  // Several successful polls can resolve together after a stalled connection.
  for(const id of [3,4,5]){app.fixture=close(app,[id]);await app.run('refresh()');await settle()}
  healthy(app);assert.equal(notices(app).length,3);assert.deepEqual(texts(app),[3,4,5].map(i=>'Passo fatto: Task '+i));
  for(const n of [...notices(app)])n.children[1].onclick();assert.equal(notices(app).length,0);await app.advance(6000);assert.equal(notices(app).length,0,'cleared timers cannot revive toasts');
 },
 async literal_i18n(){
  const f=fixture(),hostile='<img src=x onerror=alert(1)> & </script>';f.tasks.task0.title=hostile;
  const app=await boot({fixture:f,language:'en-US'});assert.equal(control(app).textContent,'Sounds');await app.refresh(close(app,[0]));
  assert.deepEqual(texts(app),['Step done: '+hostile]);assert.equal(notices(app)[0].children[0].children.length,0,'title is text, never markup');
  notices(app)[0].children[1].focus();const focused=app.doc.activeElement;app.run("setLanguage('it')");await settle();
  assert.equal(control(app).textContent,'Suoni');assert.deepEqual(texts(app),['Passo fatto: '+hostile]);assert.equal(app.doc.activeElement,focused);assert.equal(focused.textContent,'Chiudi avviso');
  assert.match(page,/<div id="notify-toasts"[^>]*role="status"[^>]*aria-live="polite"/);assert.equal(control(app).getAttribute('aria-describedby'),'notify-hint');
  assert.doesNotMatch(text(app.doc.getElementById('notify-controls'))+text(region(app)),/⟦/);
  app.run("setLanguage('en')");await app.refresh(close(app,[1,2]));assert.equal(texts(app).at(-1),'Work complete: no open task');
 },
 async transitions(){
  const app=await boot({fixture:fixture(['working','working'])});await app.click();
  let f=clone(app.fixture);f.tasks.historic={...f.tasks.task0,id:'historic',status:'done'};await app.refresh(f);assert.equal(notices(app).length,0,'arrival already done is not a transition');
  f=clone(app.fixture);f.tasks.task0.status='reviewed';await app.refresh(f);assert.equal(notices(app).length,0,'accepted review is not task completion');
  f=close(app,[0]);f.tasks.task1.superseded_by='task0';await app.refresh(f);assert.equal(texts(app).at(-1),'Lavoro completato: nessun incarico aperto','superseded work is not open');
  f=clone(app.fixture);f.tasks.new={...f.tasks.task0,id:'new',status:'working'};await app.refresh(f);assert.equal(app.audio.notes.length,3);f=clone(app.fixture);f.tasks.new.status='done';await app.refresh(f);assert.equal(app.audio.notes.length,6,'later new work has its own completion');
 }
};
(async()=>{assert.ok(tests[scenario],'known scenario');await tests[scenario]();console.log('notifications contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

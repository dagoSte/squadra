'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'dashboard_loading_contract.js'),'utf8').split('const cases=String.raw`')[0];
const cases=String.raw`
const reply=value=>({ok:true,json:async()=>clone(value),text:async()=>JSON.stringify(value)});
function fixture(){
 const value=clone(base);value.transfer=1;value.change_token='a'.repeat(64);value.server_time='2026-10-15T12:00:00+00:00';value.registry_head={hash:'b'.repeat(64),seq:200};
 value.events=value.events.map(e=>({...e,data:{},detail_pending:true}));
 for(const m of Object.values(value.messages)){m.body='Preview';m.detail_pending=true}
 value.outputs=value.outputs.map(o=>({...o,text:'Preview',artifacts:[],detail_pending:true}));
 return value;
}
function patchFor(section,record){
 if(section==='mail')return {messages:base.messages};
 if(section==='outputs')return {outputs:base.outputs};
 if(section==='history')return {events:base.events};
 if(section==='tasks')return {tasks:base.tasks};
 if(section==='runs')return {runs:base.runs};
 if(section==='graph')return {graph:base.graph};
 if(section==='story')return {tasks:{[record]:base.tasks[record]},messages:base.messages,outputs:base.outputs,graph:base.graph};
 throw Error(section);
}
const tests={
 async unchanged(){
  const value=fixture();let calls=[];const app=await boot({fixture:value,http:(path,options)=>{calls.push({path,headers:options.headers});return reply(value)}});
  assert.equal(calls[0].headers['X-Squadra-State-Token'],'');
  const held=app.run('state');
  app.run("state.comms={...state.comms,as_of:state.server_time,edges:[{utc:state.server_time,age_seconds:0,wait_seconds:900}],pairs:[{oldest_pending_seconds:900},{oldest_pending_seconds:null}]}");
  app.http=(path,options)=>{calls.push({path,headers:options.headers});return reply({not_modified:true,change_token:value.change_token,server_time:'2026-10-15T12:02:00+00:00'})};
  await app.refresh();assert.equal(app.run('state'),held,'unchanged responses retain the held object');assert.equal(calls.at(-1).headers['X-Squadra-State-Token'],value.change_token);
  assert.match(app.run("ageText('2026-10-15T12:00:00+00:00')"),/2/,'relative time advances without a state download');
  assert.equal(app.run('state.comms.edges[0].age_seconds'),120,'communication ages advance');
  assert.equal(app.run('state.comms.edges[0].wait_seconds'),1020,'overdue communication time advances');
  assert.equal(app.run('state.comms.pairs[0].oldest_pending_seconds'),1020,'pair wait time advances');
  assert.equal(app.run('state.comms.pairs[1].oldest_pending_seconds'),null,'missing wait stays unknown');
  app.run("globalThis.balanceFetch=async()=>{globalThis.balancePolls=(globalThis.balancePolls||0)+1};tab='Bilanciamento'");
  await app.run('transferIndependentPoll()');await app.run('transferIndependentPoll()');assert.equal(app.run('globalThis.balancePolls'),2,'integration seam polls Balance independently');
 },
 async details(){
  const value=fixture(),fetched=[];const app=await boot({fixture:value,http:(path,options)=>{fetched.push(path);return reply(value)}});
  assert.equal(fetched.filter(x=>x==='/api/detail/view').length,0,'landing sends no detail request');
  app.http=(path,options)=>{fetched.push(path);const section=options.headers['X-Squadra-Section'],record=decodeURIComponent(options.headers['X-Squadra-Record']);assert.equal(options.headers['X-Squadra-State-Head'],value.registry_head.hash);return reply({head:value.registry_head.hash,section,record,patch:patchFor(section,record)})};
  for(const [tab,section] of [['Posta','mail'],['Output','outputs'],['Cronologia','history'],['Decisioni','graph'],['Incarichi','tasks'],['Prove','runs']]){
   await app.tab(tab);await settle();assert.equal(app.run('transferHas('+JSON.stringify(section)+')'),true,tab+' fully hydrated');
  }
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(state.messages)')),base.messages);
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(state.outputs)')),base.outputs);
  await app.tab('Quadro');app.run("panelOpenStory('TASK-KQ1')");await settle();await settle();assert.equal(app.run("transferHas('story','TASK-KQ1')"),true);assert.equal(app.run('panelUI.story'),'TASK-KQ1');
 },
 async stale(){
  const value=fixture();const app=await boot({fixture:value});let release;
  app.http=()=>new Promise(resolve=>{release=resolve});const pending=app.run("transferLoad('mail')");await settle();
  app.run("state={...state,registry_head:{hash:'c'.repeat(64),seq:201}};transferSync()");
  release(reply({head:value.registry_head.hash,section:'mail',record:'',patch:{messages:{OLD:{id:'OLD'}}}}));await pending;
  assert.equal(app.run("Object.hasOwn(state.messages,'OLD')"),false,'stale details never join a newer state');
  assert.equal(app.run("transferHas('mail')"),false);
 },
 async story_retry(){
  const app=await boot({fixture:fixture()});app.http=()=>Promise.reject(Error('offline'));
  app.run("transferUI.errors.set('story:OTHER','unrelated');panelOpenStory('TASK-KQ1')");
  const dialog=app.doc.getElementById('story');assert.equal(dialog.open,true);assert.match(text(dialog),/Caricamento/);
  await settle();await settle();assert.match(text(dialog),/Dettagli non disponibili/);
  assert.equal(app.run("transferHas('story','TASK-KQ1')"),false);
  app.http=(path,options)=>reply({head:app.run('state.registry_head.hash'),section:'story',record:'TASK-KQ1',patch:patchFor('story','TASK-KQ1')});
  one(dialog,e=>e.getAttribute('data-focus-key')==='story:retry').onclick();await settle();await settle();
  assert.equal(app.run("transferHas('story','TASK-KQ1')"),true);assert.equal(app.run("transferUI.errors.has('story:OTHER')"),true);
  assert.equal(app.run('panelUI.story'),'TASK-KQ1');assert.doesNotMatch(text(dialog),/Dettagli non disponibili/);
 },
 async story_navigation(){
  const app=await boot({fixture:fixture()});let resolve;
  app.http=()=>new Promise(r=>resolve=r);app.run("panelOpenStory('TASK-KQ1')");await settle();await app.tab('Finestre');
  resolve(reply({head:app.run('state.registry_head.hash'),section:'story',record:'TASK-KQ1',patch:patchFor('story','TASK-KQ1')}));await settle();await settle();
  assert.equal(app.run('panelUI.story'),null);assert.equal(app.doc.getElementById('story').open,false);
  assert.equal(app.run("transferHas('story','TASK-KQ1')"),true,'completed immutable details may remain cached');
 },
 async story_latest(){
  const app=await boot({fixture:fixture()});app.run("state.tasks.SECOND={...state.tasks['TASK-KQ1'],id:'SECOND',title:'Second task'}");const pending=[];
  app.http=(path,options)=>new Promise(resolve=>pending.push({resolve,options}));
  app.run("panelOpenStory('TASK-KQ1')");await settle();app.run("panelOpenStory('SECOND')");await settle();
  const done=i=>{const {resolve,options}=pending[i],record=decodeURIComponent(options.headers['X-Squadra-Record']);resolve(reply({head:app.run('state.registry_head.hash'),section:'story',record,patch:{tasks:{[record]:JSON.parse(app.run('JSON.stringify(state.tasks['+JSON.stringify(record)+'])'))}}}))};
  done(1);await settle();await settle();done(0);await settle();await settle();
  assert.equal(app.run('panelUI.story'),'SECOND');assert.equal(app.doc.getElementById('story').getAttribute('data-story-task'),'SECOND');
 },
 async story_close(){
  const app=await boot({fixture:fixture()});let resolve;app.http=()=>new Promise(r=>resolve=r);
  const origin=app.find('work:story:TASK-KQ1');origin.focus();origin.onclick();await settle();
  const dialog=app.doc.getElementById('story');assert.equal(dialog.open,true);dialog.close();dialog.onclose();
  assert.equal(app.doc.activeElement.getAttribute('data-focus-key'),'work:story:TASK-KQ1','closing a pending story returns focus');
  resolve(reply({head:app.run('state.registry_head.hash'),section:'story',record:'TASK-KQ1',patch:patchFor('story','TASK-KQ1')}));await settle();await settle();
  assert.equal(app.run('panelUI.story'),null);assert.equal(dialog.open,false);
 },
 async retry(){
  const app=await boot({fixture:fixture()});app.http=()=>Promise.reject(Error('offline'));await app.tab('Posta');await settle();
  assert.match(text(app.doc.getElementById('content')),/Dettagli non disponibili/);assert.equal(app.run("transferHas('mail')"),false);
  app.http=(path,options)=>reply({head:app.run('state.registry_head.hash'),section:'mail',record:'',patch:{messages:base.messages}});
  one(app.doc.getElementById('content'),e=>e.tag==='button').onclick();await settle();await settle();assert.equal(app.run("transferHas('mail')"),true);
 }
};
(async()=>{await tests[scenario]();console.log('transfer contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
// The loading harness already defines and adapts the common panel harness.
eval(harness+'\neval(harness+\'\\n\'+cases);');

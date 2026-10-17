'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'dashboard_loading_contract.js'),'utf8').split('const cases=String.raw`')[0];
// Reuse the same small DOM/HTTP instrument as the loading contract.
harness=harness.replace("const sourceFS=require('node:fs'),sourcePath=require('node:path');",'');
const cases=String.raw`
const reply=value=>({ok:true,json:async()=>clone(value),text:async()=>JSON.stringify(value)});
const query=(app,value)=>{const el=app.doc.getElementById('search');el.value=value;el.oninput({target:el});return el};
const fixtureWith=(total=125)=>{const f=clone(base);f.registry_head={seq:100,hash:'head'};f.search={query:'canary',tasks:[],runs:[],messages:[],outputs:[],decisions:[],asks:[],reviews:[],total,results:Array.from({length:Math.min(total,100)},(_,i)=>({kind:'tasks',id:'TASK-KQ1',seq:100-i,utc:'2026-10-16T12:00:00Z',actor:'reader',text:'Canary '+i,outcome:'done'}))};return f};
const tests={
 async parity(){
  const fixtures=JSON.parse(fs.readFileSync(casesPath,'utf8'));
  for(const language of ['it-IT','en-US','tr-TR']){
   const app=await boot({language});
   assert.equal(app.run("typeof searchMatch"),'function','One explicit word-prefix rule is required');
   for(const c of fixtures.parity){assert.equal(app.run('searchMatch('+JSON.stringify(c.fields)+','+JSON.stringify(c.query)+')'),c.match,JSON.stringify(c))}
   for(const [group,expected] of [['positive',true],['negative',false]])for(const c of fixtures[group]){
    assert.equal(app.run('searchMatch(searchFields('+JSON.stringify(c.kind)+','+JSON.stringify(c.record)+'),"canary")'),expected,group+' '+c.kind+' '+c.path);
   }
   app.run("filter='tag'");assert.equal(app.run("matches({future:'tag',tags:['tag']})"),false,'no unknown JSON fallback');
   app.run("state.messages.mail={id:'mail',body:'Caffè umano'};state.search=null;filter='caffe'");
   assert.equal(app.run("matches({kind:'message',message:'mail',from_lane:'window',status:'structural'})"),true);
   app.run("filter='structural'");assert.equal(app.run("matches({kind:'ack',from_lane:'window',from:'reader',task:'task',status:'structural'})"),false);
   app.run("filter='';searchFields=()=>{throw Error('empty query projected')}" );assert.equal(app.run("matches({})"),true);
  }
  assert.doesNotMatch(page,/new RegExp\([^\n]*\(\?<\!/,'client has no lookbehind');
 },
 async menu(){
  assert.match(page,/id="search-popup"/,'the top search must expose its results');
  for(const language of ['it-IT','en-US']){
   const fixture=fixtureWith(),app=await boot({fixture,language});
   app.run("navigator.clipboard={writeText:async text=>{window.copied=text}};");
   const input=query(app,'Canary');input.focus();
   const popup=app.doc.getElementById('search-popup'),list=app.doc.getElementById('search-results'),footer=app.doc.getElementById('search-agent');
   assert.equal(popup.hidden,false);assert.equal(input.getAttribute('role'),'combobox');assert.equal(input.getAttribute('aria-expanded'),'true');
   assert.equal(list.children.filter(e=>e.getAttribute('role')==='option').length,10);
   assert.equal(app.doc.activeElement,input);
   assert.match(text(footer),language==='it-IT'?/Oppure chiedi all’agente/:/Or ask your agent/);
   const more=one(app.doc.getElementById('search-actions'),e=>e.tag==='button');more.onclick();
   assert.equal(list.children.length,100);assert.match(text(app.doc.getElementById('search-actions')),/100.*125/);
   assert.match(page,/id="search-results"[\s\S]*id="search-actions"[\s\S]*id="search-agent"/,'agent footer is last');
   const before=list.children.slice();app.http=()=>reply({not_modified:true});await app.refresh();
   assert.deepEqual(list.children,before,'unchanged poll keeps DOM and focus');assert.equal(app.doc.activeElement,input);
   const key=k=>{let prevented=false;input.onkeydown({key:k,preventDefault(){prevented=true}});assert.equal(prevented,true)};
   key('ArrowDown');assert.equal(input.getAttribute('aria-activedescendant'),'search-result-0');
   key('ArrowDown');assert.equal(input.getAttribute('aria-activedescendant'),'search-result-1');key('ArrowUp');assert.equal(input.getAttribute('aria-activedescendant'),'search-result-0');
   key('Escape');assert.equal(popup.hidden,true);assert.equal(input.getAttribute('aria-expanded'),'false');
   input.onfocus();assert.equal(popup.hidden,false);
   const copy=one(footer,e=>e.tag==='button');await copy.onclick();
   const expected=language==='it-IT'?'Cerca nel registro di Squadra: "Canary". Dimmi cosa è stato fatto, quando, da quale finestra e con quale esito.':'Search the Squadra registry for "Canary". Tell me what was done, when, by which window and with what outcome.';
   assert.equal(app.run('window.copied'),expected);assert.ok(text(footer).includes(expected));
   assert.match(text(footer),language==='it-IT'?/Prompt copiato/:/Prompt copied/);
   app.run("navigator.clipboard.writeText=async()=>{throw Error('denied')}" );await copy.onclick();assert.match(text(footer),language==='it-IT'?/Copia non riuscita/:/Copy failed/);
   app.run("state.search={query:'missing',results:[],total:0}");query(app,'missing');assert.equal(list.children.filter(e=>e.getAttribute('role')==='option').length,0);
   assert.match(text(list),language==='it-IT'?/Nessun risultato/:/No results/);assert.match(text(footer),language==='it-IT'?/Oppure/:/Or ask/);
   query(app,'pending');assert.equal(list.getAttribute('aria-busy'),'true');assert.match(text(footer),/pending/);
   query(app,'');assert.equal(popup.hidden,true);
   query(app,'canary');for(const listener of app.doc.listeners.pointerdown||[])listener({target:{}});assert.equal(popup.hidden,true);
  }
 },
 async retry(){
  const review={id:'review',task:'TASK-KQ1',reader:'R',utc:'2026-10-16T12:00:00Z',stage:'code',verdict:'accepted',covers:[],findings:[],findings_status:'recorded'};
  const refused=()=>({ok:false,status:409,text:async()=>'{"error":"state_changed"}'});
  for(const language of ['it-IT','en-US'])for(const mismatch of [false,true])for(const kind of ['messages','outputs','decisions','asks','reviews']){
   const fixture=fixtureWith(1);fixture.search.query='';const app=await boot({fixture,language});
   const fresh=clone(fixture);fresh.registry_head.hash='fresh';let reads=0,polls=0,heads=[];
   const id={messages:'mail',outputs:'output',decisions:'decision',asks:'ask',reviews:'review'}[kind];
   const data=head=>kind==='reviews'?{head:{hash:head,seq:101},reads:[review],tasks:[],revisions:[]}:
    {head,patch:kind==='messages'?{messages:{mail:{id:'mail',body:'Recovered message'}}}:kind==='outputs'?{outputs:[{event:'output',text:'Recovered output',artifacts:[]}]}:{graph:{nodes:[{id:(kind==='asks'?'ask:':'decision:')+id}]}}};
   app.run('decFollow=id=>window.followed=id');
   app.http=(path,options)=>{
    if(path==='/api/state'){polls++;return reply(fresh)}
    reads++;heads.push(options.headers['X-Squadra-State-Head']);
    if(reads===1)return mismatch?reply(data('fresh')):refused();
    return reply(data('fresh'));
   };
   await app.run('searchOpen('+JSON.stringify({kind,id})+')');
   assert.equal(reads,2,kind+' gets one retry');assert.equal(polls,1,'one fresh state request');
   if(kind!=='reviews')assert.deepEqual(heads,['head','fresh']);
   if(kind==='messages'||kind==='outputs'){assert.equal(app.doc.getElementById('artifact').open,true);assert.match(text(app.doc.getElementById('artifact-body')),/Recovered/)}
   else if(kind==='reviews'){assert.equal(app.run('tab'),'Letture');assert.equal(app.run("readsView.open.has('review')"),true)}
   else assert.equal(app.run('window.followed'),(kind==='asks'?'ask:':'decision:')+id);
   assert.doesNotMatch(text(app.doc.getElementById('notice')),/409|state_changed/);
  }
  for(const language of ['it-IT','en-US'])for(const failState of [false,true]){
   const fixture=fixtureWith(1),app=await boot({fixture,language});let details=0,polls=0;
   const fresh=clone(fixture);fresh.registry_head.hash='fresh';
   app.http=path=>{if(path==='/api/state'){polls++;return failState?{ok:false,status:500,text:async()=>'RAW-PRIVATE-DETAIL'}:reply(fresh)}details++;return refused()};
   await app.run("searchOpen({kind:'messages',id:'mail'})");
   assert.equal(details,failState?1:2);assert.equal(polls,1);assert.equal(text(app.doc.getElementById('notice')),language==='it-IT'?'Il record non è più disponibile. Aggiorna la ricerca.':'This record is no longer available. Refresh the search.');
   assert.doesNotMatch(text(app.doc.getElementById('notice')),/409|500|RAW|state_changed/);
  }
  const fixture=fixtureWith(1),app=await boot({fixture});let release,polls=0,details=0;
  const fresh=clone(fixture);fresh.registry_head.hash='fresh';
  app.http=path=>{if(path==='/api/state'){polls++;return polls===1?new Promise(r=>{release=r}):reply(fresh)}details++;return details===1?refused():reply({head:'fresh',patch:{messages:{mail:{body:'After active poll'}}}})};
  const polling=app.refresh();await settle();const opening=app.run("searchOpen({kind:'messages',id:'mail'})");await settle();
  assert.equal(polls,1,'recovery waits for the current poll');release(reply(fresh));await polling;await opening;
  assert.equal(polls,2);assert.equal(details,2);assert.match(text(app.doc.getElementById('artifact-body')),/After active poll/);
 },
 async comms(){
  for(const language of ['it-IT','en-US']){
   const app=await boot({language});
   const edge={id:'edge',message:'human-message',kind:'ping',from:'C',to:'A',from_lane:'C',to_lane:'A',task:null,task_title:null,state:'acked',flags:[],utc:'2026-10-16T11:00:00Z',ack_utc:'2026-10-16T12:34:00Z'};
   app.run("window.edge="+JSON.stringify(edge)+";state.messages['human-message']={id:'human-message',from:'C',to:'A',body:'ordinary prose',repo:'/hidden/repopathcanary'};state.comms={...state.comms,edges:[window.edge]};commsUI.filters={period:0,count:200,task:'',lane:'',pair:''};state.search={query:'',tasks:[],runs:[],messages:[],outputs:[],decisions:[],asks:[],reviews:[],results:[],total:0}");
   const visible=(q,lang)=>app.run('UI_LANG='+JSON.stringify(lang)+';filter=state.search.query='+JSON.stringify(q)+';commsVisible({edges:[window.edge]}).length');
   assert.equal(visible('chiamata di presenza','it'),1);assert.equal(visible('CHIAMÁTA DI PRES','it'),1);assert.equal(visible('hiamata','it'),0);
   assert.equal(visible('chiamata di presenza','en'),0);assert.equal(visible('presence call','en'),1);assert.equal(visible('presence call','it'),0);
   const time=app.run('commsTime(window.edge.ack_utc)'),shown=app.run('commsSentence(window.edge)');
   assert.equal(visible(time,'it'),0,'timestamp-only text is excluded');assert.equal(app.run('commsSentence(window.edge)'),shown,'display sentence stays unchanged');
   app.run("window.edge.kind='ack';window.edge.reply_seconds=601");assert.equal(visible('10','it'),0,'duration-only text is excluded');
   app.run("window.edge.kind='availability';window.edge.state='stand-by';window.edge.ping_utc=window.edge.ack_utc");assert.equal(visible(time,'it'),0,'ping clock is excluded');
   app.run("window.edge.kind='ping';window.edge.state='acked';state.agents.C={id:'C',identity:'C',role:'coordinator',client:'/metadata/clientpathcanary'}");
   assert.equal(visible('clientpathcanary','it'),0,'unknown raw client metadata is excluded');assert.equal(visible('repopathcanary','it'),0);
   app.run("state.search.messages=['human-message']");assert.equal(visible('ordinary','it'),1,'server human-text hit survives the preview');app.run('state.search.messages=[]');
   app.run("UI_LANG='it';filter=loadUI.query=state.search.query='chiamata di presenza';document.getElementById('search').value=filter;searchUI.open=true;renderSearch()");
   assert.equal(app.doc.getElementById('search-results').children.filter(e=>e.getAttribute('role')==='option').length,0,'sentence-only text adds no dropdown row');
   assert.equal(app.run("Object.values(state.search).filter(Array.isArray).flat().length"),0,'view filtering never injects global IDs or rows');
  }
 },
 async records(){
  const fixture=fixtureWith(1),app=await boot({fixture});
  assert.equal(app.run('typeof searchOpen'),'function','results must open the exact record');
  app.run("window.opened=[];panelOpenStory=id=>window.opened.push(id)");
  query(app,'canary');const input=app.doc.getElementById('search');
  input.onkeydown({key:'ArrowDown',preventDefault(){}});input.onkeydown({key:'Enter',preventDefault(){}});await settle();
  assert.deepEqual(JSON.parse(app.run('JSON.stringify(window.opened)')),['TASK-KQ1']);
  const head=fixture.registry_head.hash;
  const patches={messages:{messages:{mail:{id:'mail',body:'Exact full message'}}},outputs:{outputs:[{event:'output',text:'Exact output',artifacts:[]}]},decisions:{graph:{nodes:[{id:'decision:decision'}]}},asks:{graph:{nodes:[{id:'ask:ask'}]}}};
  for(const [kind,id] of [['messages','mail'],['outputs','output'],['decisions','decision'],['asks','ask']]){
   let seen;app.http=(path,options)=>{seen={path,options};return reply({head,patch:patches[kind]})};
   app.run("decFollow=id=>window.followed=id");await app.run('searchOpen('+JSON.stringify({kind,id})+')');
   assert.equal(seen.path,'/api/detail/view');assert.equal(seen.options.headers['X-Squadra-State-Head'],head);
   if(kind==='messages'||kind==='outputs'){assert.equal(app.doc.getElementById('artifact').open,true);assert.match(text(app.doc.getElementById('artifact-body')),kind==='messages'?/Exact full message/:/Exact output/)}
   else assert.equal(app.run('window.followed'),(kind==='asks'?'ask:':'decision:')+id);
  }
  let runSeen;const runId=Object.keys(fixture.runs)[0];app.http=(path,options)=>{runSeen={path,options};return reply(fixture.runs[runId])};
  await app.run('searchOpen('+JSON.stringify({kind:'runs',id:runId})+')');assert.equal(runSeen.path,'/api/detail/run');assert.equal(runSeen.options.headers['X-Squadra-Record'],encodeURIComponent(runId));
  const review={id:'review',task:'TASK-KQ1',reader:'R',utc:'2026-10-16T12:00:00Z',stage:'code',verdict:'accepted',covers:[],findings:[],findings_status:'recorded'};
  app.http=()=>reply({head:{hash:head,seq:100},reads:[review],tasks:[],revisions:[]});await app.run("searchOpen({kind:'reviews',id:'review'})");
  assert.equal(app.run('tab'),'Letture');assert.equal(app.run("readsView.open.has('review')"),true);
  app.http=()=>reply({head,patch:{messages:{}}});await app.run("searchOpen({kind:'messages',id:'gone'})");assert.match(text(app.doc.getElementById('notice')),/non è più disponibile/);
  let release;app.http=()=>new Promise(r=>{release=r});const late=app.run("searchOpen({kind:'messages',id:'mail'})");await settle();
  app.run("state.registry_head.hash='new';document.getElementById('artifact-body').replaceChildren()" );
  const fresh=clone(fixture);fresh.registry_head.hash='new';fresh.search.query=app.run('loadUI.query');
  app.http=path=>reply(path==='/api/state'?fresh:{head:'new',patch:{messages:{mail:{id:'mail',body:'Fresh full message'}}}});
  release(reply({head,patch:patches.messages}));await late;
  assert.equal(text(app.doc.getElementById('artifact-body')),'Fresh full message','a stale detail is replaced only by a fresh bound retry');
  app.doc.getElementById('artifact-body').replaceChildren();
  app.run("state.registry_head.hash='head';tab='Quadro'");app.http=()=>new Promise(r=>{release=r});
  const navigated=app.run("searchOpen({kind:'messages',id:'mail'})");await settle();app.run("tab='Incarichi'");
  release(reply({head,patch:patches.messages}));await navigated;assert.equal(text(app.doc.getElementById('artifact-body')),'','later navigation wins over detail');
  app.http=()=>new Promise(r=>{release=r});const queried=app.run("searchOpen({kind:'messages',id:'mail'})");await settle();query(app,'new-query');
  release(reply({head,patch:patches.messages}));await queried;assert.equal(text(app.doc.getElementById('artifact-body')),'','later query wins over detail');
  app.doc.getElementById('artifact-body').replaceChildren();app.http=()=>new Promise(r=>{release=r});
  const lateRun=app.run('searchOpen('+JSON.stringify({kind:'runs',id:runId})+')');await settle();query(app,'newer-query');
  release(reply(fixture.runs[runId]));await lateRun;assert.equal(text(app.doc.getElementById('artifact-body')),'','later query wins over run detail');
  query(app,'canary');app.run('registryIntegrityFailure()');assert.equal(app.doc.getElementById('search-popup').hidden,true);assert.equal(text(app.doc.getElementById('search-results')),'','failed integrity removes search data');
 }
};
(async()=>{assert.ok(tests[scenario],scenario);await tests[scenario]();console.log('search contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(harness+'\neval(harness+"\\n"+'+JSON.stringify(cases)+');');

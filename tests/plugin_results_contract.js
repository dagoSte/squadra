// Exercise real page scripts against bounded synthetic registry and plugin data.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
prefix=prefix.replace('json:async()=>clone(app.fixture)',"json:async()=>clone(path.startsWith('/api/plugin/')?await app.reply(path):app.fixture)");
const cases=String.raw`
const content=app=>app.doc.getElementById('content');
const local=(it,en)=>({it,en});
const taskId='browser-check',runId='a'.repeat(32),otherRun='b'.repeat(32);
const results=()=>({label:local('Collaudi <b>test</b>','Checks <b>test</b>'),empty:local('Nessun controllo','No checks'),status_labels:{draft:local('Da verificare','To check'),approved:local('Superato','Passed')}});
const about=()=>({publisher:'Example',version:'1',tagline:local('Verifiche','Checks'),sections:[{id:'purpose',title:local('Scopo','Purpose'),text:local('Un testo','Some text')}]});
const panel=()=>({schema:1,updated:'2026-10-12T12:00:00Z',summary:local('Risultati del servizio','Service results'),items:[{id:'one',title:'Literal <script>title</script>',text:'<img src=x onerror=alert(1)> https://example.invalid/',status:'approved',meta:[],refs:[{task:taskId},{task:taskId,run:runId}]}]});
const tabButton=(app,name)=>one(content(app),e=>key(e)==='plugin:tab:'+name,'tab '+name);
const links=app=>all(content(app),e=>e.getAttribute('data-plugin-ref'));
const tasks=()=>({[taskId]:{id:taskId,title:'Check checkout',owner:'',reviewer:'',coordinator:'',status:'ready',depends:[],proofs:[],reviews:[],authors:[],scope:{name:'scope',sha256:'c'.repeat(64)},base:'d'.repeat(40),utc:'2026-10-12T10:00:00Z'}});
async function setup({legacy=false,noAbout=false,...opts}={}){
 const fixture=clone(base);fixture.tasks=tasks();fixture.runs={};
 for(const id of [runId,otherRun])fixture.runs[id]={id,task:taskId,utc:'2026-10-12T12:00:00Z',agent:'',mode:'proof',status:'passed',native_rc:0,seconds:1,argv:['python3','checks.py'],artifacts:[]};
 fixture.plugins={key:'c'.repeat(64),refused:[],plugins:[{schema:1,name:'example',title:local('Esempio','Example'),description:local('Dati','Data'),...(noAbout?{}:{about:about()}),...(legacy?{}:{results:results()}),actions:[{id:'check',label:local('Richiedi verifica','Request check'),pending:null}]}]};
 const app=await boot({...opts,fixture});app.panel=panel();app.reply=async()=>({name:'example',key:fixture.plugins.key,panel:app.panel});
 app.select=async()=>{one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:example').onclick();await settle()};
 await app.select();app.results=async()=>{if(!noAbout){tabButton(app,legacy?'memories':'results').onclick();await settle()}};
 await app.results();return app;
}
const tests={
 async state_badges(){
  for(const language of ['it-IT','en-US'])for(const theme of ['light','dark']){
   const app=await setup({language,stored:{'squadra-theme':theme}}),en=language==='en-US';
   app.panel.items=['approved','draft','sent','error','rejected','withheld','held'].map(status=>({id:status,title:'Item '+status,text:'Result '+status,status:status==='held'?'draft':status,selectable:status==='draft'||status==='held',meta:status==='sent'?[{label:local('Proposta','Proposal'),value:'prp_synthetic'}]:[]}));
   app.fixture.plugins.plugins[0].actions[0].pending={ids:['held','approved','sent','rejected'],requested:'2026-10-12T12:00:00Z'};
   await app.refresh();
   const cards=all(content(app),e=>e.getAttribute('data-plugin-item'));
   assert.deepEqual(cards.map(e=>JSON.parse(e.getAttribute('data-plugin-item'))[0]),['draft','error','withheld','held','approved','sent','rejected']);
   const expected={draft:en?'To check':'Da verificare',error:en?'Error':'Errore',withheld:en?'Withheld':'Trattenuto',held:en?'Waiting to be sent':'In attesa di invio',approved:en?'Passed':'Superato',sent:en?'Sent':'Inviato',rejected:en?'Rejected':'Rifiutato'};
   for(const card of cards){
    const id=JSON.parse(card.getAttribute('data-plugin-item'))[0],badge=one(card,e=>hasClass(e,'sq-plugin-status'));
    assert.equal(badge.getAttribute('data-plugin-status'),id==='held'?'waiting':id);
    assert.equal(badge.textContent,expected[id],'producer state wins over a pending overlay except on drafts');
   }
   const heading=one(content(app),e=>hasClass(e,'sq-plugin-sent-heading'));assert.equal(heading.textContent,en?'Already sent (3)':'Già inviate (3)');
   assert.match(text(content(app)),/prp_synthetic/);assert.match(text(content(app)),en?/Service results/:/Risultati del servizio/);
   assert.equal(all(content(app),e=>e.getAttribute('type')==='checkbox').length,2);
   assert.ok(all(content(app),e=>e.getAttribute('type')==='checkbox').every(e=>e.disabled),'pending freezes every selection control');
   assert.doesNotMatch(text(content(app)),/⟦/);
  }
 },
 async state_focus(){
  const app=await setup();app.panel.items=[{id:'a',title:'First',text:'A',status:'draft',meta:[]},{id:'b',title:'Second',text:'B',status:'error',meta:[]}];await app.refresh();
  const first=one(content(app),e=>e.getAttribute('data-plugin-item')==='["a",0]');first.focus();
  const search=app.doc.getElementById('search');search.value='kept filter';search.oninput({target:search});app.panel.items[0].status='sent';await app.refresh();
  assert.equal(key(app.doc.activeElement),'plugin:item:["a",0]','moving below the heading preserves keyboard focus');
  assert.equal(app.run('filter'),'kept filter');unique(app,'grouped states');
  app.panel.items[1].status='approved';await app.refresh();assert.match(text(content(app)),/Già inviate \(2\)/);
  app.panel.items[0].status='draft';app.panel.items[1].status='withheld';await app.refresh();assert.equal(all(content(app),e=>hasClass(e,'sq-plugin-sent-heading')).length,0);
  assert.equal(key(app.doc.activeElement),'plugin:item:["a",0]');
 },
 async labels(){
  for(const language of ['it-IT','en-US']){
   const app=await setup({language,stored:{'squadra-theme':'dark'}}),en=language==='en-US';
   assert.equal(tabButton(app,'results').textContent,en?'Checks <b>test</b>':'Collaudi <b>test</b>');
   assert.match(text(content(app)),en?/Passed/:/Superato/);assert.match(text(content(app)),en?/reported by the service/:/dichiarati dal servizio/);
   assert.match(text(content(app)),en?/Choose the items/:/Scegli gli elementi/);
   const actions=one(content(app),e=>e.getAttribute('aria-label')===(en?'Choose the items to submit':'Scegli gli elementi da inviare'),'generic action region');
   assert.ok(actions);
   assert.equal(all(content(app),e=>['script','img','a'].includes(e.tag)).length,0,'content remains text');
   assert.doesNotMatch(text(content(app)),/⟦/);assert.ok(text(content(app)).includes('<script>title</script>'));
   app.run('setLanguage('+JSON.stringify(en?'it':'en')+')');await settle();
   assert.equal(tabButton(app,'results').textContent,en?'Collaudi <b>test</b>':'Checks <b>test</b>');
  }
  const legacy=await setup({legacy:true});assert.equal(tabButton(legacy,'memories').textContent,'Memorie');
  assert.match(text(content(legacy)),/Approvato/);assert.match(text(content(legacy)),/Scegli le bozze/);
 },
 async empty(){
  const app=await setup({noAbout:true});assert.equal(tabButton(app,'results').textContent,'Collaudi <b>test</b>');
  assert.match(text(content(app)),/Literal/);app.panel=null;await app.refresh();assert.match(text(content(app)),/Nessun controllo/);
  app.panel=panel();app.panel.items=[];await app.refresh();assert.match(text(content(app)),/Nessun controllo/);
  app.run("setLanguage('en')");await settle();assert.match(text(content(app)),/No checks/);
  const legacy=await setup({legacy:true,noAbout:true});assert.equal(all(content(legacy),e=>e.getAttribute('role')==='tab').length,0);
  assert.match(text(content(legacy)),/Literal/);
 },
 async links(){
  const app=await setup();assert.equal(links(app).length,2);
  const taskLink=links(app)[0],focus=key(taskLink);taskLink.focus();taskLink.onclick();await settle();
  const dialog=app.doc.getElementById('story');assert.equal(dialog.open,true);assert.equal(app.run('panelUI.story'),taskId);
  assert.equal(app.run('panelUI.storyReturn'),focus);assert.match(text(dialog),/Check checkout/);
  app.run('panelUI.story=null;panelUI.storySig=null;document.getElementById("story").close();render()');await settle();
  app.run("panelUI.tables.runs={sort:'',dir:1,filter:'failed',shown:100,open:new Set()}");
  links(app)[1].onclick();await settle();assert.equal(app.run('tab'),'Prove');
  assert.equal(app.run('connectedUI.context.run'),runId);assert.equal(app.run('panelUI.tables.runs.filter'),'');
  assert.equal(all(content(app),e=>e.getAttribute('data-read-scope')==='runs:'+runId).length,1);
  assert.equal(all(content(app),e=>String(key(e)||'').startsWith('table:runs:row:'+otherRun)).length,0,'only requested run');
  assert.ok(app.fetched.every(p=>p==='/api/state'||p==='/api/plugin/example'),'navigation adds no requests');
 },
 async refused(){
  const app=await setup();app.panel.items[0].refs=[{task:'foreign'},{task:taskId,run:'e'.repeat(32)},{task:'toString'},{task:'__proto__'},{task:taskId,run:otherRun}];
  app.fixture.runs[otherRun].task='foreign';await app.refresh();
  assert.equal(links(app).length,0);assert.match(text(content(app)),/non disponibile/);
  assert.equal(all(content(app),e=>e.tag==='a').length,0);
 },
 async refresh(){
  const app=await setup();const stale=links(app)[1];delete app.fixture.runs[runId];await app.refresh();
  assert.equal(links(app).length,1);assert.match(text(content(app)),/non disponibile/);stale.onclick();await settle();assert.equal(app.run('tab'),'Plugin');
  app.fixture.tasks[taskId].title='Updated task';await app.refresh();assert.match(text(links(app)[0]),/Updated task/);
  delete app.fixture.tasks[taskId];await app.refresh();assert.equal(links(app).length,0);
  app.fixture.tasks=tasks();await app.refresh();assert.equal(links(app).length,1);
 },
 async polling(){
  const app=await setup({phone:true});const reference=links(app)[0];reference.focus();const focus=key(reference);
  await app.refresh();assert.equal(key(app.doc.activeElement),focus);unique(app,'references');
  const calls=app.fetched.length;tabButton(app,'overview').onclick();await settle();await app.refresh();
  assert.equal(app.fetched.length,calls+1,'only normal state poll on overview');
  assert.equal(app.fetched.at(-1),'/api/state');await app.results();
  tabButton(app,'results').onkeydown({key:'Home',preventDefault(){}});await settle();
  assert.equal(key(app.doc.activeElement),'plugin:tab:overview');assert.ok(app.fetched.every(p=>p==='/api/state'||p==='/api/plugin/example'));
 }
};
(async()=>{assert.ok(tests[scenario],scenario);await tests[scenario]();console.log('plugin results contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

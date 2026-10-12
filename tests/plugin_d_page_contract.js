// Check draft selections with the page scripts, shared DOM model and synthetic replies.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
for(const [before,after] of [
 ['const context={console,','const context={URL,TextEncoder,console,'],
 ['focus(){this.doc.activeElement=this}', 'focus(){if(!this.disabled)this.doc.activeElement=this}'],
 ["fetch:async path=>{app.fetched.push(path);return", "fetch:async (path,options={})=>{app.fetched.push(path);if(app.http)return app.http(path,options);return"]
]){if(!prefix.includes(before))throw Error('base harness seam changed');prefix=prefix.replace(before,after)}
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const KEY='a'.repeat(64),STAMP='2026-10-11T20:00:00Z';
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>clone(data)});
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
const content=app=>app.doc.getElementById('content');
const buttons=app=>all(content(app),e=>e.tag==='button');
const checkboxes=app=>all(content(app),e=>e.getAttribute('type')==='checkbox');
const selected=app=>checkboxes(app).filter(e=>e.checked).map(e=>JSON.parse(key(e).slice('plugin:action:item:'.length))[0]).sort();
const control=(app,name)=>one(content(app),e=>key(e)==='plugin:action:'+name,'action control '+name);
const checkbox=(app,id)=>one(content(app),e=>key(e)==='plugin:action:item:'+JSON.stringify([id,0]),'checkbox '+id);
const click=async(app,name)=>{const b=control(app,name);assert.equal(b.disabled,false,'enabled '+name);await b.onclick();await settle()};
const choose=async(app,id,checked)=>{const input=checkbox(app,id);assert.equal(input.disabled,false);input.checked=checked;input.onchange();await settle()};
const draft=id=>({id,title:'Draft '+id,text:'Literal <script>alert(1)</script>',status:'draft',selectable:true,meta:[]});
async function setup({about=true,pending=null,actions=true,items=[draft('a'),draft('b')],...options}={}){
 const fixture=clone(base),manifest={schema:1,name:'example',title:{it:'Esempio',en:'Example'},description:{it:'Esempio',en:'Example'},
  ...(actions?{actions:[{id:'send',label:{it:'Invia per revisione',en:'Send for review'},pending}]}:{}),
  ...(about?{about:{publisher:'Example',version:'1',tagline:{it:'Introduzione',en:'Introduction'},sections:[]}}:{})};
 fixture.plugins={key:KEY,refused:[],plugins:[manifest]};const app=await boot({...options,fixture});app.posts=[];app.counter=0;
 app.panel={schema:1,updated:STAMP,summary:{it:'Bozze',en:'Drafts'},items};
 app.http=async(path,options)=>{
  if(path==='/api/state')return app.stateReply?app.stateReply():response(app.fixture);
  if(options.method==='POST'){
   app.posts.push({path,options:clone(options)});
   if(app.post)return app.post(path,options);
   const body=JSON.parse(options.body),request=body.ids.length?{...body,requested:'2026-10-11T20:01:'+String(++app.counter).padStart(2,'0')+'Z'}:null;
   app.fixture.plugins.plugins[0].actions[0].pending=request?{ids:request.ids,requested:request.requested}:null;
   return response({saved:true,request});
  }
  if(app.panelReply)return app.panelReply(path);
  return response({name:path.split('/').pop(),key:app.fixture.plugins.key,panel:app.panel});
 };
 one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:example').onclick();await settle();
 if(about){one(content(app),e=>key(e)==='plugin:tab:memories').onclick();await settle()}
 return app;
}
const tests={
 async eligibility(){
  const items=[draft('a'),draft('b'),{...draft('hidden'),selectable:false},{...draft('string'),selectable:'true'},...['sent','approved','rejected','withheld','error'].map(status=>({...draft(status),status}))];
  for(const about of [true,false]){
   const app=await setup({about,items});assert.equal(checkboxes(app).length,2,'only explicitly selectable drafts have checkboxes');assert.deepEqual(selected(app),[]);
   assert.equal(control(app,'send').disabled,true);assert.ok(!buttons(app).some(b=>key(b)==='plugin:action:cancel'));
   await click(app,'all');assert.deepEqual(selected(app),['a','b']);await click(app,'none');assert.deepEqual(selected(app),[]);assert.equal(app.posts.length,0);
  }
  const legacy=await setup({actions:false});assert.equal(checkboxes(legacy).length,0);assert.ok(!buttons(legacy).some(b=>key(b)==='plugin:action:send'));
  const duplicate=await setup({items:[draft('a'),draft('a')]});assert.equal(control(duplicate,'send').disabled,true);assert.ok(checkboxes(duplicate).every(c=>c.disabled));assert.match(text(content(duplicate)),/non sono distinguibili/);
 },
 async explicit_wire(){
  const app=await setup();await choose(app,'b',true);assert.equal(app.posts.length,0);
  app.post=async(path,o)=>{const b=JSON.parse(o.body);return response({saved:true,request:{ids:[...b.ids].reverse(),requested:STAMP}})};
  await click(app,'send');assert.equal(app.posts.length,1);const {path,options}=app.posts[0];
  assert.equal(path,'/api/plugin/example/action/send');assert.equal(options.method,'POST');assert.equal(options.cache,'no-store');
  assert.deepEqual(options.headers,{'X-Squadra-Token':'token','Content-Type':'application/json'});assert.deepEqual(JSON.parse(options.body),{ids:['b']});
  assert.match(text(content(app)),/richiesta salvata: la eseguirà la finestra coordinatrice, se il plugin ne fornisce le istruzioni/);
  assert.equal(all(content(app),e=>e.className==='badge'&&e.textContent==='Bozza').length,2,'save does not mark drafts sent');
  await click(app,'all');await click(app,'send');assert.deepEqual(selected(app),['a','b'],'reply ordering is irrelevant');
 },
 async pending_replace_cancel(){
  const app=await setup({pending:{ids:['a','b'],requested:STAMP}});assert.deepEqual(selected(app),['a','b']);assert.match(text(content(app)),/Richiesta in attesa · elementi: 2/);
  await choose(app,'a',false);await app.refresh();assert.deepEqual(selected(app),['b'],'unchanged pending polls preserve local edits');
  await click(app,'send');assert.deepEqual(JSON.parse(app.posts[0].options.body).ids,['b']);assert.match(text(content(app)),/nuova scelta sostituisce/);await app.refresh();assert.deepEqual(selected(app),['b']);
  await click(app,'none');assert.equal(control(app,'send').disabled,true);assert.equal(app.posts.length,1,'clearing checkboxes is not cancellation');
  control(app,'cancel').focus();await click(app,'cancel');assert.equal(key(app.doc.activeElement),'plugin:action:heading','cancellation keeps keyboard focus in the action section');assert.deepEqual(JSON.parse(app.posts[1].options.body),{ids:[]});assert.match(text(content(app)),/Richiesta in attesa annullata/);
  assert.ok(!buttons(app).some(b=>key(b)==='plugin:action:cancel'));assert.deepEqual(selected(app),[]);await app.refresh();assert.ok(!text(content(app)).includes('Richiesta in attesa · elementi:'));
  const again=await setup({pending:{ids:['b'],requested:STAMP}});assert.deepEqual(selected(again),['b'],'reload restores only saved IDs');
 },
 async stale_refresh(){
  const app=await setup();await click(app,'all');app.panel.items=[{...draft('a'),status:'sent'},draft('b'),draft('new')];await app.refresh();assert.deepEqual(selected(app),['b'],'new drafts stay unchecked; no-longer-drafts removed');
  app.post=async()=>response({error:'stale_panel'},409);app.panel.items=[draft('new')];const count=app.fetched.length;await click(app,'send');
  assert.deepEqual(selected(app),[]);assert.match(text(content(app)),/bozze sono cambiate/);assert.equal(app.posts.length,1);assert.equal(app.fetched.length-count,2,'one POST and one active-panel refresh');
  const b=await setup({pending:{ids:['a','b'],requested:STAMP},items:[{...draft('a'),selectable:false},draft('b')]});assert.deepEqual(selected(b),['b']);assert.match(text(content(b)),/Richiesta in attesa · elementi: 2/);assert.equal(control(b,'cancel').disabled,false);
 },
 async single_flight_context(){
  const app=await setup(),wait=deferred();app.post=()=>wait.promise;await choose(app,'a',true);
  const send=control(app,'send'),first=send.onclick();send.onclick();await settle();assert.equal(app.posts.length,1,'single flight includes stale DOM callbacks');assert.equal(control(app,'send').disabled,true);
  const next=clone(app.fixture);next.plugins.key='b'.repeat(64);await app.refresh(next);assert.deepEqual(selected(app),[],'a different registry never inherits selection');
  wait.resolve(response({saved:true,request:{ids:['a'],requested:STAMP}}));await first;await settle();assert.ok(!text(content(app)).includes('richiesta salvata'));assert.deepEqual(selected(app),[]);
  const other=await setup(),slow=deferred();other.post=()=>slow.promise;await choose(other,'b',true);const promise=control(other,'send').onclick();await other.tab('Quadro');
  one(other.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:example').onclick();await settle();one(content(other),e=>key(e)==='plugin:tab:memories').onclick();await settle();
  slow.resolve(response({saved:true,request:{ids:['b'],requested:STAMP}}));await promise;await settle();assert.ok(!text(content(other)).includes('richiesta salvata'),'leaving and returning does not revive a late reply');
 },
 async uncertain_replies(){
  for(const answer of [
   response({saved:false,request:{ids:['a'],requested:STAMP}}),
   response({saved:true,request:{ids:['a'],requested:'2026-02-30T20:00:00Z'}}),
   response({saved:true,request:{ids:['a','b'],requested:STAMP}}),
   response({saved:true,request:{ids:['a','a'],requested:STAMP}}),
   response({saved:true,request:{ids:['a'],requested:'nonsense'}}),response({saved:true,request:null}),
   response({saved:true,request:{ids:['a'],requested:STAMP}},201),
   {ok:true,status:200,json:async()=>{throw Error('broken json')}}
  ]){
   const app=await setup();app.post=async()=>answer;await choose(app,'a',true);await click(app,'send');assert.match(text(content(app)),/Esito non confermato/);assert.ok(!text(content(app)).includes('richiesta salvata'));await app.refresh();assert.equal(app.posts.length,1);
  }
  const lost=await setup();lost.post=async()=>{throw Error('secret transport detail')};await choose(lost,'a',true);await click(lost,'send');assert.match(text(content(lost)),/Esito non confermato/);assert.ok(!text(content(lost)).includes('secret transport detail'));
  const cancel=await setup({pending:{ids:['a'],requested:STAMP}});cancel.post=async()=>response({saved:true,request:{ids:[],requested:STAMP}});await click(cancel,'cancel');assert.match(text(content(cancel)),/Esito non confermato/);assert.ok(buttons(cancel).some(b=>key(b)==='plugin:action:cancel'));
 },
 async fixed_errors(){
  for(const language of ['it-IT','en-US'])for(const code of ['stale_panel','not_selectable','undeclared_action','registry_mismatch','request_invalid','request_pending','busy','save_failed','__proto__','constructor','toString','<img src=x onerror=alert(1)>']){
   const app=await setup({language});app.post=async()=>response({error:code},code==='save_failed'?500:409);await choose(app,'a',true);await click(app,'send');
   const words=text(content(app));assert.ok(!words.includes(code),'refusal codes/bodies remain private');assert.doesNotMatch(words,/⟦/);assert.ok(!words.includes('richiesta salvata'));assert.equal(app.posts.length,1);
  }
 },
 async focus_languages_polling(){
  const app=await setup({phone:true,stored:{'squadra-theme':'dark'},items:[{...draft('a'),title:'<img src=x onerror=alert(1)>'},draft('b')]});
  const input=checkbox(app,'a');assert.equal(input.getAttribute('type'),'checkbox');input.focus();await choose(app,'a',true);assert.equal(key(app.doc.activeElement),key(input),'checkbox keeps keyboard focus');
  const count=app.fetched.length;await app.refresh();assert.equal(app.fetched.length-count,2,'only existing state and active-panel polling');assert.equal(key(app.doc.activeElement),key(input));assert.equal(app.posts.length,0);
  app.run("render()");await settle();assert.equal(key(app.doc.activeElement),key(input),'cached controls remain registered');assert.equal(app.fetched.length-count,2);
  app.run("setLanguage('en')");await settle();assert.equal(control(app,'send').textContent,'Send for review');assert.equal(key(app.doc.activeElement),key(input));assert.doesNotMatch(text(content(app)),/⟦/);
  assert.ok(text(content(app)).includes('<img src=x onerror=alert(1)>'));assert.equal(all(content(app),e=>e.tag==='img'||e.tag==='script').length,0);assert.ok(all(content(app),e=>e.getAttribute('role')==='status').length);
  await app.tab('Quadro');const hidden=app.fetched.length;await app.refresh();assert.equal(app.fetched.length-hidden,1,'closed panel adds no requests');assert.equal(app.posts.length,0);
 },
 async stale_state_after_write(){
  const app=await setup({pending:{ids:['a'],requested:STAMP}});await choose(app,'a',false);await choose(app,'b',true);
  const old=response(clone(app.fixture)),wait=deferred();app.stateReply=()=>wait.promise;const poll=app.refresh();await settle();
  await click(app,'send');assert.deepEqual(selected(app),['b']);wait.resolve(old);await poll;assert.deepEqual(selected(app),['b'],'state read started before saving must not resurrect the old selection');assert.match(text(content(app)),/Richiesta in attesa · elementi: 1/);
  app.stateReply=null;await app.refresh();assert.deepEqual(selected(app),['b']);
  const oldAgain=response(clone(app.fixture)),waitAgain=deferred();app.stateReply=()=>waitAgain.promise;const polling=app.refresh();await settle();await click(app,'cancel');waitAgain.resolve(oldAgain);await polling;
  assert.ok(!buttons(app).some(b=>key(b)==='plugin:action:cancel'),'stale state cannot revive a cancelled request');
 }
};
(async()=>{assert.ok(tests[scenario],'known scenario');await tests[scenario]();console.log('plugin draft actions contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

// Check navigation, charts, themes, focus, task cards and task history.
// Run the page scripts in a simplified DOM that drops focus on detached elements.
// The page must restore focus using its own saved keys.
// Usage: node panel_contract.js <dashboard.html> <state.json from the real server> <scenario> [cases.json]
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [htmlPath,fixturePath,scenario,casesPath]=process.argv.slice(2);
const page=fs.readFileSync(htmlPath,'utf8');
const scripts=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const base=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
const clone=value=>JSON.parse(JSON.stringify(value));
const TABS=['Quadro','Finestre','Incarichi','Posta','Comunicazioni','Decisioni','Letture','Prove','Output','Cronologia','Storico'];
const ALLOWED=new Set(['div','span','section','h2','h3','h4','p','button','details','summary','dl','dt','dd','ul','ol','li','table','tr','th','td',
 'a','small','time','nav','strong','select','option','label','article','aside','br','svg','defs','marker','path','g','rect','text','title']);

class Element{
 constructor(doc,tag,ns){this.doc=doc;this.tag=tag;this.ns=ns;this.children=[];this.attributes={};this.textContent='';this.className='';this.style={};doc.created.push(this)}
 append(...children){for(const c of children){if(!(c instanceof Element))throw Error('Non-element child appended: '+String(c));this.children.push(c)}}
 replaceChildren(...children){if(this.doc.activeElement&&[...walk(this)].includes(this.doc.activeElement))this.doc.activeElement=null;this.children=[];this.append(...children)}
 setAttribute(name,value){this.attributes[name]=String(value)}
 getAttribute(name){return name in this.attributes?this.attributes[name]:null}
 set innerHTML(value){throw Error('Untrusted HTML insertion: '+value)}
 set outerHTML(value){throw Error('Untrusted HTML insertion: '+value)}
 insertAdjacentHTML(){throw Error('Untrusted HTML insertion')}
 focus(){this.doc.activeElement=this}
 blur(){if(this.doc.activeElement===this)this.doc.activeElement=null}
 showModal(){this.open=true}
 close(){this.open=false}
}
function* walk(e){if(!e)return;yield e;for(const c of e.children||[])yield* walk(c)}
const all=(root,test)=>[...walk(root)].filter(test);
function one(root,test,message){const found=all(root,test);assert.ok(found.length,message||'element not found');return found[0]}
const text=e=>[...walk(e)].map(x=>x.textContent||'').filter(Boolean).join(' ');
// Closed details show only their summary.
const visibleText=(e,skip=()=>false)=>skip(e)?'':e.tag==='details'?(e.children.find(c=>c.tag==='summary')?visibleText(e.children.find(c=>c.tag==='summary'),skip):''):[e.textContent||'',...e.children.map(c=>visibleText(c,skip))].filter(Boolean).join(' ');
const hasClass=(e,name)=>(' '+(e.className||'')+' '+(e.getAttribute('class')||'')+' ').includes(' '+name+' ');
const key=e=>e&&e.getAttribute('data-focus-key');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};

async function boot({fixture=clone(base),language='it-IT',storage='memory',stored={},phone=false,clock}={}){
 const doc={created:[],ids:new Map(),activeElement:null,title:'',listeners:{}};
 doc.getElementById=id=>{if(!doc.ids.has(id)){const e=new Element(doc,'#'+id);e.id=id;doc.ids.set(id,e)}return doc.ids.get(id)};
 doc.createElement=tag=>new Element(doc,tag);doc.createElementNS=(ns,tag)=>new Element(doc,tag,ns);
 doc.documentElement={lang:'',attributes:{},setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return k in this.attributes?this.attributes[k]:null}};
 doc.addEventListener=(type,fn)=>{(doc.listeners[type]||(doc.listeners[type]=[])).push(fn)};
 const timers=[],writes=[],saved=new Map(Object.entries(stored));
 const app={doc,timers,writes,fixture,fetched:[],clock};
 // Use a controllable clock to check changing ages.
 const PageDate=clock===undefined?Date:class extends Date{constructor(...a){if(a.length)super(...a);else super(app.clock)}static now(){return app.clock}};
 const context={console,Date:PageDate,Intl,document:doc,location:{port:'4242',hash:'#token',pathname:'/'},navigator:{language},
  sessionStorage:{setItem(){},getItem(){return 'token'}},history:{replaceState(){}},setTimeout:fn=>{timers.push(fn)},
  addEventListener(){},
  matchMedia:query=>({matches:phone&&/max-width/.test(query),addEventListener(){}}),
  fetch:async path=>{app.fetched.push(path);return path.startsWith('/api/artifact/')?{ok:true,text:async()=>'artifact'}:{ok:true,json:async()=>clone(app.fixture)}}};
 if(storage==='memory')context.localStorage={getItem:k=>saved.has(k)?saved.get(k):null,setItem:(k,v)=>{writes.push([k,v]);saved.set(k,String(v))}};
 if(storage==='throws')Object.defineProperty(context,'localStorage',{get(){throw Error('storage blocked')}});
 context.window=context;vm.createContext(context);
 for(const script of scripts)vm.runInContext(script,context,{timeout:2000});
 await settle();
 app.run=code=>vm.runInContext(code,context);
 app.refresh=async next=>{if(next)app.fixture=next;const i=timers.findIndex(fn=>fn.name==='refresh'),fn=i<0?undefined:timers.splice(i,1)[0];assert.ok(fn,'a refresh is scheduled');await fn();await settle()};
 app.tab=async name=>{const label=app.run('tr('+JSON.stringify('tab.'+name)+')');const b=doc.getElementById('tabs').children.find(e=>e.textContent===label);assert.ok(b,'tab '+name);b.onclick();await settle()};
 app.find=k=>{for(const id of ['content','charts','tabs','overview'])for(const e of walk(doc.getElementById(id)))if(key(e)===k)return e;return null};
 app.keydown=async k=>{for(const fn of doc.listeners.keydown||[])fn({key:k});await settle()};
 app.keys=()=>['content','charts','tabs','overview','metrics'].flatMap(id=>all(doc.getElementById(id),e=>key(e)!==null).map(key));
 return app;
}
const unique=(app,where)=>{const keys=app.keys();assert.equal(new Set(keys).size,keys.length,'no repeated data-focus-key '+where+': '+keys.filter((k,i)=>keys.indexOf(k)!==i).join(', '))};
const pageText=(app,ids)=>ids.map(id=>app.doc.getElementById(id)).flatMap(r=>all(r,()=>true)).flatMap(e=>[e.textContent,...['aria-label','title','data-label'].map(a=>e.getAttribute(a))]).filter(Boolean).join('\n');

const scenarios={
 async shell(){
  const app=await boot();
  const tabs=()=>app.doc.getElementById('tabs').children;
  assert.deepEqual(tabs().map(b=>b.textContent),TABS,'the button text is the bare label');
  for(const b of tabs()){const svg=b.children.find(c=>c.tag==='svg');assert.ok(svg&&svg.children.length&&svg.children.every(p=>p.tag==='path'),'icon of '+b.textContent);
   assert.equal(svg.getAttribute('aria-hidden'),'true')}
  assert.deepEqual(tabs().map(key),TABS.map(t=>'nav:'+t));
  assert.equal(tabs().filter(b=>b.getAttribute('aria-current')==='page').map(b=>b.className).join(),'active','one current view, the active one');
  const metrics=app.doc.getElementById('metrics').children;assert.equal(metrics.length,4);
  for(const m of metrics)assert.ok(m.children.some(c=>c.tag==='svg'&&hasClass(c,'metric-icon')),'KPI icon');
  assert.match(text(app.doc.getElementById('metrics')),/Identità\s+\d/);
  const toggle=app.doc.getElementById('sidebar-toggle');assert.equal(toggle.getAttribute('aria-pressed'),'false');
  toggle.onclick();await settle();
  assert.match(app.doc.getElementById('app').className,/\bcollapsed\b/);assert.deepEqual(app.writes.at(-1),['squadra-sidebar','collapsed']);
  assert.equal(toggle.getAttribute('aria-pressed'),'true');
  assert.deepEqual(tabs().map(b=>b.getAttribute('title')),TABS,'collapsed buttons keep their names as titles');
  const again=await boot({stored:{'squadra-sidebar':'collapsed'}});assert.match(again.doc.getElementById('app').className,/\bcollapsed\b/,'choice restored');
  const blocked=await boot({storage:'throws'});blocked.doc.getElementById('sidebar-toggle').onclick();await settle();
  assert.match(blocked.doc.getElementById('app').className,/\bcollapsed\b/,'works without storage');
  const desktop=await boot();desktop.doc.getElementById('menu').onclick();await settle();
  assert.doesNotMatch(desktop.doc.getElementById('app').className,/drawer-open/,'no drawer at desktop width');
  const phone=await boot({phone:true}),menu=phone.doc.getElementById('menu'),appEl=phone.doc.getElementById('app');
  assert.equal(menu.getAttribute('aria-expanded'),'false');assert.equal(phone.doc.getElementById('scrim').hidden,true);
  menu.focus();menu.onclick();await settle();
  assert.match(appEl.className,/\bdrawer-open\b/);assert.equal(menu.getAttribute('aria-expanded'),'true');
  assert.equal(phone.doc.getElementById('page').inert,true,'the page behind the drawer is inert');assert.equal(phone.doc.getElementById('scrim').hidden,false);
  assert.equal(phone.doc.activeElement,phone.doc.getElementById('tabs').children.find(b=>b.className==='active'),'focus moves into the drawer');
  await phone.keydown('Escape');
  assert.doesNotMatch(appEl.className,/drawer-open/);assert.equal(phone.doc.getElementById('page').inert,false);assert.equal(phone.doc.activeElement,menu,'Escape returns focus to the menu button');
  menu.onclick();await settle();phone.doc.getElementById('scrim').onclick();await settle();
  assert.doesNotMatch(appEl.className,/drawer-open/);assert.equal(phone.doc.activeElement,menu,'the scrim closes it too');
  menu.onclick();await settle();await phone.tab('Posta');
  assert.doesNotMatch(appEl.className,/drawer-open/);assert.equal(phone.doc.activeElement,menu,'choosing a view closes the drawer');
  assert.equal(phone.doc.getElementById('tabs').children.find(b=>b.className==='active').textContent,'Posta');
  const dark=await boot({stored:{'squadra-theme':'dark'}}),root=dark.doc.documentElement;
  assert.equal(root.getAttribute('data-theme'),'dark','the head script applies the stored theme');
  const buttons=dark.doc.getElementById('theme').children;assert.deepEqual(buttons.map(b=>b.getAttribute('data-theme-choice')),['auto','light','dark']);
  assert.deepEqual(buttons.map(b=>b.getAttribute('aria-pressed')),['false','false','true']);
  buttons[1].onclick();await settle();assert.equal(root.getAttribute('data-theme'),'light');assert.deepEqual(dark.writes.at(-1),['squadra-theme','light']);
  assert.deepEqual(buttons.map(b=>b.getAttribute('aria-pressed')),['false','true','false']);
  const plain=await boot({storage:'throws'});assert.equal(plain.doc.documentElement.getAttribute('data-theme'),'auto');
  plain.doc.getElementById('theme').children[2].onclick();await settle();assert.equal(plain.doc.documentElement.getAttribute('data-theme'),'dark','theme works without storage');
  const en=await boot({language:'en-US'});
  assert.equal(en.doc.getElementById('skip').textContent,'Skip to content');assert.equal(en.doc.getElementById('tabs').getAttribute('aria-label'),'Dashboard views');
  assert.deepEqual(en.doc.getElementById('theme').children.map(b=>b.labelNode.textContent),['Automatic','Light','Dark']);
  assert.equal(en.doc.getElementById('menu').getAttribute('aria-label'),'Open the menu');
  const it=await boot();one(it.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.equal(it.doc.getElementById('sidebar-toggle').getAttribute('title'),'Collapse the menu','static controls follow the language switch');
 },
 async charts(){
  const app=await boot(),F=app.fixture;
  const cards=all(app.doc.getElementById('charts'),e=>e.getAttribute('data-chart')!==null);
  assert.equal(app.doc.getElementById('charts').children[0].textContent,'Andamento','the charts sit under «Andamento», below the work cards');
  assert.deepEqual(cards.map(c=>c.getAttribute('data-chart')),['states','steps','activity','proofs','reads']);
  const card=id=>cards.find(c=>c.getAttribute('data-chart')===id);
  const rows=id=>all(card(id),e=>e.tag==='tr').slice(1).map(r=>r.children.map(c=>c.textContent));
  const state=t=>t.superseded_by?'superseded':t.status,counts={};for(const t of Object.values(F.tasks))counts[state(t)]=(counts[state(t)]||0)+1;
  const label=s=>app.run(`panelStateLabel(${JSON.stringify(s)})`);
  for(const [s,n] of Object.entries(counts))assert.ok(rows('states').some(r=>r[0]===label(s)&&r[1]===String(n)),'state '+s);
  assert.equal(rows('states').reduce((a,r)=>a+Number(r[1]),0),Object.keys(F.tasks).length);
  assert.equal(rows('steps').length,Object.keys(F.progress).length,'one row per task with progress');
  for(const p of Object.values(F.progress)){const reached=p.steps.filter(s=>s.reached).length;
   const marks=all(card('steps'),e=>(e.getAttribute('data-tip-key')||'').startsWith('chart:steps:'+p.task+':'));assert.equal(marks.length,p.steps.length);
   assert.ok(text(card('steps')).includes(reached+' passi su 6'),'reached steps of '+p.task)}
  const proofs={};for(const r of Object.values(F.runs).filter(r=>r.mode==='proof')){const k=r.task;proofs[k]=proofs[k]||{passed:0,failed:0};
   if(r.status==='passed')proofs[k].passed++;else if(['failed','timeout'].includes(r.status))proofs[k].failed++}
  for(const [task,c] of Object.entries(proofs)){const title=app.run(`taskTitle(${JSON.stringify(task)})`);
   assert.ok(rows('proofs').some(r=>r[0]===title&&r[1]===String(c.passed)&&r[2]===String(c.failed)),'proof counts of '+task)}
  for(const t of Object.values(F.tasks).filter(t=>!t.superseded_by)){const title=app.run(`taskTitle(${JSON.stringify(t.id)})`),plan=t.reviews.filter(r=>r.stage==='plan').length;
   assert.ok(rows('reads').some(r=>r[0]===title&&r[1].startsWith(plan+' su ')),'plan reads of '+t.id)}
  const activity=rows('activity'),total=activity.reduce((a,r)=>a+Number(r.at(-1)),0);
  const sentence=one(card('activity'),e=>e.getAttribute('role')==='img').getAttribute('aria-label');
  assert.match(sentence,new RegExp('^'+total+' eventi registrati|^Un evento registrato'),'sentence and table agree');
  assert.ok(total>0&&total<=F.activity.buckets.reduce((a,b)=>a+b[1]+b[2]+b[3]+b[4],0));
  for(const c of cards){const img=one(c,e=>e.getAttribute('role')==='img');assert.equal(img.getAttribute('aria-label'),one(c,e=>hasClass(e,'sq-summary')).textContent,'summary sentence is the accessible name')}
  for(const tag of new Set(app.doc.created.map(e=>e.tag).filter(t=>!t.startsWith('#'))))assert.ok(ALLOWED.has(tag),'unexpected element '+tag);
  const italian=pageText(app,['charts']);assert.match(italian,/Incarichi per stato/);
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  const english=pageText(app,['charts']);
  assert.doesNotMatch(english,/⟦/);assert.doesNotMatch(english,/\{[A-Za-z]+\}/);
  for(const words of ['Tasks by state','How far each task has gone','Activity over time','Proofs passed and failed','Reads and verdicts'])assert.ok(english.includes(words),words);
  for(const words of ['Incarichi per stato','Attività nel tempo','Letture e verdetti','Mostra i dati in tabella'])assert.ok(!english.includes(words),words);
  assert.doesNotMatch(italian,/⟦|\{[A-Za-z]+\}/);
  await app.tab('Posta');assert.equal(app.doc.getElementById('charts').children.length,0,'charts only on the summary');
 },
 async timezones(){
  const zone=process.env.TZ,cases=JSON.parse(fs.readFileSync(casesPath,'utf8')).filter(c=>c.zones.includes(zone));
  assert.ok(cases.length,'cases for '+zone);
  for(const c of cases){
   const fixture=clone(base);fixture.activity=c.activity;const app=await boot({fixture});
   const model=range=>app.run(`panelData.activity(state.activity,${JSON.stringify(range)})`);
   const times=c.events.map(e=>Date.parse(e)),asOf=Date.parse(c.activity.as_of),inside=times.filter(t=>t<=asOf);
   const two=n=>String(n).padStart(2,'0'),localDay=t=>{const d=new Date(t);return c.utc?d.toISOString().slice(0,10):d.getFullYear()+'-'+two(d.getMonth()+1)+'-'+two(d.getDate())};
   // Derive the local hour independently of the page.
   // Use elapsed time for half-hour daylight-saving shifts.
   const localHour=t=>{if(c.utc)return Math.floor(t/3600000)*3600000;const d=new Date(t);return t-(d.getMinutes()*60+d.getSeconds())*1000-d.getMilliseconds()};
   for(const range of ['14d','24h']){
    const m=model(range);assert.equal(m.utc,Boolean(c.utc),c.name+': clock');
    for(const b of m.bins){
     const expected=inside.filter(t=>range==='14d'?localDay(t)===localDay(b.start)&&t>=b.start&&t<b.end:c.elapsed?t>=b.start&&t<b.end:localHour(t)===b.start).length;
     assert.equal(b.total,expected,c.name+' '+range+' '+b.label);
    }
    assert.equal(m.total,inside.filter(t=>t>=m.bins[0].start&&t<m.bins.at(-1).end).length,c.name+' '+range+' total');
    if(range==='24h'){
     assert.ok(m.bins.length===24&&m.bins.every((b,i)=>b.end-b.start===3600000&&(i===0||b.start===m.bins[i-1].end)),c.name+': 24 contiguous elapsed hours');
     const last=m.bins.at(-1);assert.ok(last.start<=asOf&&asOf<last.end,c.name+': the last interval contains as_of');
     assert.equal(last.partial,true,c.name+': the current interval is partial');assert.ok(last.label.includes('in corso, fino alle'),c.name+': and says so');
     for(const t of inside.filter(t=>t>asOf-23*3600000))assert.ok(m.bins.some(b=>t>=b.start&&t<b.end),c.name+': counted '+new Date(t).toISOString());
    }
   }
   if(c.total24!==undefined)assert.equal(model('24h').total,c.total24,c.name+': 24-hour total');
   for(const check of c.checks||[]){
    if(check.labels)for(const l of check.labels)assert.ok(model('24h').bins.some(b=>b.short===l&&b.total===1),c.name+': column '+l);
    if(check.hours)for(const [l,n] of check.hours)assert.equal(model('24h').bins.filter(b=>b.short===l).map(b=>b.total).join(),String(n),c.name+': hour '+l);
    if(check.noLabel)assert.ok(!model('24h').bins.some(b=>b.short.startsWith(check.noLabel)),c.name+': no column '+check.noLabel);
    if(check.dayHours){const day=model('14d').bins.find(b=>localDay(b.start)===check.dayHours[0]);assert.equal((day.end-day.start)/3600000,check.dayHours[1],c.name+': day length')}
    if(check.days)for(const [day,n] of check.days)assert.equal(model('14d').bins.find(b=>localDay(b.start)===day).total,n,c.name+': day '+day);
   }
   const card=one(app.doc.getElementById('charts'),e=>e.getAttribute('data-chart')==='activity');
   const rows=all(card,e=>e.tag==='tr').slice(1),tableTotal=rows.reduce((a,r)=>a+Number(r.children.at(-1).textContent),0);
   assert.equal(tableTotal,model('24h').total,c.name+': table and chart agree');
   assert.ok(one(card,e=>e.getAttribute('role')==='img').getAttribute('aria-label').startsWith(tableTotal===1?'Un evento':tableTotal+' eventi'),c.name+': caption agrees');
   const notes=all(card,e=>hasClass(e,'sq-note')).map(e=>e.textContent).join(' ');
   const named=Intl.DateTimeFormat().resolvedOptions().timeZone;
   assert.ok(c.utc?/in UTC/.test(notes):notes.includes(named),c.name+': the clock is named ('+named+')');
   if(c.future)assert.ok(notes.includes('eventi con data futura '+c.future),c.name+': future events are counted apart');
  }
 },
 async focus(){
  const F=clone(base),app=await boot({fixture:F});
  const check=(k,where)=>{assert.equal(key(app.doc.activeElement),k,where);unique(app,where)};
  const focus=k=>{const e=app.find(k);assert.ok(e,'control '+k);e.focus();return e};
  const steps=async(k,{insert,fallback,remove}={})=>{
   await app.refresh();check(k,k+' after a refresh');
   await app.refresh();check(k,k+' after a second refresh');
   if(insert){await app.refresh(insert(clone(app.fixture)));check(k,k+' after an insert before it')}
   app.run("setLanguage('en')");await settle();check(k,k+' after the language switch');
   app.run("setLanguage('it')");await settle();
   app.run("panelTheme('dark')");await settle();await app.refresh();check(k,k+' after the theme switch');
   if(remove){await remove();check(fallback,k+' falls back to '+fallback)}
  };
  const newestMessage=f=>{f.messages['MSG-NEW-'+Object.keys(f.messages).length]={id:'MSG-NEW-'+Object.keys(f.messages).length,from:'coord-zx',to:'build-qv',body:'Nuovo',utc:new Date().toISOString(),ack:null};return f};
  const newestRun=f=>{const r=clone(Object.values(f.runs)[0]);r.id='run-new-'+Object.keys(f.runs).length;r.utc=new Date().toISOString();f.runs[r.id]=r;return f};
  const firstTask=f=>{const t=clone(Object.values(f.tasks)[0]);t.id='TASK-NEW';t.title='Aaa nuovo incarico';t.brief=null;f.tasks={'TASK-NEW':t,...f.tasks};return f};
  focus('chart:activity:range:14d');await steps('chart:activity:range:14d',{insert:firstTask});
  focus('chart:states:table');await steps('chart:states:table',{insert:firstTask});
  focus('work:story:TASK-KQ2');await steps('work:story:TASK-KQ2',{insert:firstTask});
  await app.tab('Prove');
  focus('table:runs:sort:status');await steps('table:runs:sort:status',{insert:newestRun});
  focus('table:runs:sortselect');await steps('table:runs:sortselect',{insert:newestRun});
  await app.tab('Posta');
  const filter=focus('table:mail:filter');filter.value='waiting';filter.onchange({target:filter});await settle();check('table:mail:filter','filter keeps focus after its own change');
  filter.value='';app.find('table:mail:filter').onchange({target:{value:''}});await settle();
  await steps('table:mail:filter',{insert:newestMessage});
  focus('table:mail:more');await steps('table:mail:more',{insert:newestMessage,fallback:'table:mail:title',remove:async()=>{app.find('table:mail:more').onclick();await settle()}});
  await app.tab('Incarichi');
  const id=Object.keys(app.fixture.tasks).find(t=>t==='TASK-KQ1'),summaryKey='table:tasks:row:'+encodeURIComponent(id)+':details';
  const summary=focus(summaryKey);
  const detailsOf=()=>one(app.doc.getElementById('content'),e=>e.tag==='details'&&e.children.some(c=>key(c)===summaryKey));
  detailsOf().open=true;detailsOf().ontoggle();
  await steps(summaryKey,{insert:firstTask});
  assert.equal(detailsOf().open,true,'open details stay open across refresh, insert, language and theme');
  const select=app.find('table:tasks:filter');select.value='done';summary.focus();app.find(summaryKey).focus();
  select.onchange({target:{value:'done'}});await settle();check('table:tasks:title','a filtered-out row falls back to the table title');
  app.find('table:tasks:filter').onchange({target:{value:''}});await settle();
  assert.equal(detailsOf().open,true,'the open state survives the filter');
  app.find(summaryKey).focus();const gone=clone(app.fixture);delete gone.tasks[id];await app.refresh(gone);check('table:tasks:title','a removed row falls back to the table title');
 },
 async links(){
  const app=await boot();
  const run=Object.values(app.fixture.runs).find(r=>r.mode==='proof'&&r.status==='passed'&&(r.artifacts||[]).filter(a=>a.bytes===0).length>=2);
  assert.ok(run,'a proof with two empty logs');
  const empty=run.artifacts.filter(a=>a.bytes===0),label=a=>a.name+' · '+a.sha256.slice(0,10);
  assert.equal(new Set(empty.map(a=>a.sha256)).size,1,'same bytes');assert.ok(new Set(empty.map(a=>a.name)).size>=2,'different names');
  const task=Object.values(app.fixture.tasks).find(t=>t.reviews.length>=2&&t.reviews[0].report.sha256===t.reviews[1].report.sha256&&t.reviews[0].report.name!==t.reviews[1].report.name);
  assert.ok(task,'two read reports with the same bytes and different names');
  const reports=task.reviews.slice(0,2).map(r=>r.report);
  let scope='';
  const buttons=n=>all(app.doc.getElementById('content'),e=>e.tag==='button'&&e.textContent===n&&(key(e)||'').startsWith(scope));
  const verify=async(view,references,mutate)=>{
   for(const ref of references){
    const target=()=>{const b=buttons(label(ref));assert.equal(b.length,1,'one control for '+ref.name);return b[0]};
    target().focus();const k=key(target());
    const same=async where=>{assert.equal(app.doc.activeElement?.textContent,label(ref),where+': '+ref.name);assert.equal(key(app.doc.activeElement),k,where);
     for(const other of references)assert.equal(buttons(label(other)).length,1,where+': '+other.name+' still present');unique(app,where)};
    await app.refresh();await same('refresh');
    assert.match(k,new RegExp(':link:[a-z]+:'+encodeURIComponent(ref.name).replace(/\./g,'\\.')+':'+ref.sha256+':0$'),'the key names the reference');
    await app.refresh();await same('second refresh');
    await app.refresh(mutate(clone(app.fixture)));await same('unrelated row');
    app.run("setLanguage('en')");await settle();await same('language');app.run("setLanguage('it')");await settle();
    app.run(`panelUI.tables[${JSON.stringify(view)}].sort='title';panelUI.tables[${JSON.stringify(view)}].dir=-1;render()`);await settle();await same('sort');
    app.run(`panelUI.tables[${JSON.stringify(view)}].sort='';render()`);await settle();
   }
  };
  await app.tab('Prove');scope='table:runs:row:'+encodeURIComponent(run.id)+':link:';
  await verify('runs',empty,f=>{const r=clone(run);r.id='run-unrelated';r.utc=new Date().toISOString();r.artifacts=[];f.runs[r.id]=r;return f});
  await app.tab('Incarichi');scope='table:tasks:row:'+encodeURIComponent(task.id)+':link:';
  await verify('tasks',reports,f=>{const t=clone(task);t.id='TASK-UNRELATED';t.reviews=[];t.brief=null;t.title='Aaa unrelated';f.tasks={'TASK-UNRELATED':t,...f.tasks};return f});
  await app.tab('Prove');scope='table:runs:row:'+encodeURIComponent(run.id)+':link:';
  const focusLabel=n=>{buttons(n)[0].focus()};
  focusLabel(label(empty[0]));let f=clone(app.fixture);f.runs[run.id].artifacts=f.runs[run.id].artifacts.filter(a=>a.name!==empty[0].name);
  await app.refresh(f);assert.equal(key(app.doc.activeElement),'table:runs:title','reference removed → table title, never the other same-bytes link');
  await app.refresh(clone(base));focusLabel(label(empty[0]));
  app.find('table:runs:filter').onchange({target:{value:'failed'}});await settle();
  assert.equal(key(app.doc.activeElement),'table:runs:title','row filtered out → table title');
  app.find('table:runs:filter').onchange({target:{value:''}});await settle();
  focusLabel(label(empty[0]));f=clone(base);delete f.runs[run.id];await app.refresh(f);
  assert.equal(key(app.doc.activeElement),'table:runs:title','row removed → table title');
 },
 async tooltip(){
  const app=await boot(),tip=app.doc.getElementById('chart-tip');
  const mark=k=>one(app.doc.getElementById('charts'),e=>e.getAttribute('data-tip-key')===k,'mark '+k);
  const states=Object.values(app.fixture.tasks).map(t=>t.superseded_by?'superseded':t.status),only=states.find(s=>states.filter(x=>x===s).length===1);
  assert.ok(only,'a state held by one task');const k='chart:states:'+only;
  const first=mark(k);first.onpointerenter({clientX:10,clientY:20});
  assert.equal(tip.hidden,false);const words=tip.textContent;assert.ok(words.includes(': 1'),'tooltip names the value');
  await app.refresh();assert.equal(tip.hidden,false,'an identical refresh keeps it');assert.notEqual(mark(k),first,'on the new node');
  assert.equal(app.run('panelUI.tip.key'),k);assert.equal(tip.textContent,words);
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.equal(tip.hidden,false);assert.notEqual(tip.textContent,words,'a language switch rewrites it');assert.doesNotMatch(tip.textContent,/⟦/);
  const f=clone(app.fixture),task=Object.values(f.tasks).find(t=>(t.superseded_by?'superseded':t.status)===only);delete f.tasks[task.id];
  await app.refresh(f);assert.equal(tip.hidden,true,'its mark is gone, so it hides');
  await app.refresh(clone(base));mark(k).onpointerenter({clientX:1,clientY:1});assert.equal(tip.hidden,false);
  await app.keydown('Escape');assert.equal(tip.hidden,true,'Escape hides it');
  mark(k).onpointerenter({});assert.equal(tip.hidden,false);await app.tab('Posta');assert.equal(tip.hidden,true,'a view change hides it');
  await app.tab('Quadro');mark(k).onpointerenter({});mark(k).onpointerleave();assert.equal(tip.hidden,true,'leaving the mark hides it');
 },
 async landing(){
  const app=await boot(),F=app.fixture,status=()=>text(app.doc.getElementById('status')),kpis=()=>app.doc.getElementById('kpis').children;
  const open=Object.values(F.tasks).filter(t=>!t.superseded_by&&t.status!=='done');
  assert.match(status(),new RegExp('^0 lavori su '+Object.keys(F.tasks).length+' chiusi'),'one plain sentence first');
  assert.match(status(),/nessuna richiesta registrata per te$/);
  assert.deepEqual(kpis().map(k=>one(k,e=>hasClass(e,'label')).textContent),['Lavori chiusi','In corso','Da correggere o fermi','Finestre attive']);
  const value=i=>one(kpis()[i],e=>e.tag==='strong').textContent;
  assert.equal(value(0),'0 di '+Object.keys(F.tasks).length);assert.equal(Number(value(1))+Number(value(2)),open.length,'every open task is in progress or stuck');
  assert.ok(Number(value(2))>=1,'a task waiting on an open dependency is stuck');
  assert.equal(value(3),String(new Set(Object.values(F.agents).map(a=>a.identity)).size));
  assert.doesNotMatch(text(app.doc.getElementById('kpis')),/Identità|ACK|Esecuzioni/,'no jargon in the tiles');
  const content=app.doc.getElementById('content');
  assert.ok(hasClass(content.children[0],'is-empty'),'the owner panel is not shown when nothing is recorded');
  const work=content.children[1];assert.equal(work.children[0].textContent,'Il lavoro, adesso.');
  const cards=work.children.filter(c=>hasClass(c,'sq-task'));
  assert.deepEqual(cards.map(c=>c.getAttribute('data-task')).sort(),open.map(t=>t.id).sort(),'one card per open task');
  for(const card of cards){const id=card.getAttribute('data-task'),seen=visibleText(card);
   assert.ok(card.children.some(c=>hasClass(c,'sq-stepline')),'steps inline: '+id);
   assert.match(seen,/Costruisce: /);assert.match(seen,/Ultimo evento: .+ · /);
   assert.ok(card.children.some(c=>key(c)==='work:story:'+id),'opens the story');
   assert.doesNotMatch(seen,/—|nessuna attività registrata|nessun passo dichiarato|non dichiarat/,'no empty field: '+id);
   for(const x of [...Object.keys(F.tasks),...Object.keys(F.agents)])assert.ok(!seen.includes(x)||x.length<6,'no ID on the card: '+x)}
  const long=clone(F);long.tasks['TASK-KQ2'].brief={text:'Pagina\n'+'Una riga lunga di spiegazione. '.repeat(12),utc:new Date().toISOString(),actor:'coord-zx'};
  await app.refresh(long);
  const purpose=()=>one(app.doc.getElementById('content').children[1],e=>e.getAttribute('data-task')==='TASK-KQ2').children.find(c=>hasClass(c,'sq-purpose'));
  assert.ok(hasClass(purpose(),'sq-readable'),'the card uses the shared three-line reader');
  assert.equal(purpose().textContent,long.tasks['TASK-KQ2'].brief.text.split('\n').slice(1).join('\n').trim());
  await app.refresh();assert.ok(hasClass(purpose(),'sq-readable'),'refresh retains the reader');
  const closed=clone(F);closed.tasks['TASK-KQ2'].status='done';await app.refresh(closed);
  const group=one(app.doc.getElementById('content').children[1],e=>hasClass(e,'sq-closed'));
  assert.equal(group.open,false,'closed work is grouped and collapsed');assert.equal(group.children[0].textContent,'Chiusi e sostituiti (1)');
  assert.ok(group.children.some(c=>c.getAttribute('data-task')==='TASK-KQ2'));
  assert.match(status(),/^1 lavoro su 3 chiuso/);
  const asked=clone(F);asked.attention.for_owner=[{kind:'ask',id:'Q',question:'Procediamo?',task:'TASK-KQ1',waiting:['build-qv'],utc:new Date().toISOString(),options:[]}];
  await app.refresh(asked);assert.ok(!hasClass(app.doc.getElementById('content').children[0],'is-empty'),'the banner shows when something is recorded');
  assert.match(status(),/1 richiesta per te$/);
  const legacy=app.doc.getElementById('legacy-toggle'),body=app.doc.getElementById('legacy-body');
  assert.equal(body.hidden,true,'the full overview and technical figures start closed');assert.equal(legacy.textContent,'Mostra la panoramica completa e i numeri tecnici');
  legacy.onclick();await settle();assert.equal(body.hidden,false);assert.equal(legacy.getAttribute('aria-expanded'),'true');
  assert.match(text(app.doc.getElementById('metrics')),/Identità/,'the technical tiles are still there, inside');
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.match(status(),/^0 of 3 tasks closed|^1 of 3 tasks closed|of 3 tasks closed/);
  const english=[status(),text(app.doc.getElementById('kpis')),visibleText(app.doc.getElementById('content').children[1])].join('\n');
  assert.doesNotMatch(english,/⟦|\{[A-Za-z]+\}/);for(const w of ['Lavori chiusi','Ultimo evento','Costruisce'])assert.ok(!english.includes(w),w);
  await app.tab('Posta');assert.equal(app.doc.getElementById('status').children.length,0);assert.equal(app.doc.getElementById('kpis').children.length,0);
  assert.equal(app.doc.getElementById('legacy').hidden,true,'the closed section belongs to the summary');
 },
 async story(){
  const app=await boot(),F=app.fixture,dialog=app.doc.getElementById('story'),id='TASK-KQ1';
  const button=app.find('work:story:'+id);button.focus();button.onclick();await settle();
  assert.equal(dialog.open,true,'the story opens as a dialog');assert.equal(key(app.doc.activeElement),'story:title','focus moves to its title');
  const list=one(dialog,e=>hasClass(e,'sq-story-list')),entries=list.children.map(li=>text(li));
  const utc=app.run('panelStory(state.tasks["'+id+'"]).map(i=>i.utc)');assert.deepEqual(utc,utc.slice().sort(),'oldest first');
  assert.equal(list.children.length,utc.length);
  for(const words of [/ha creato l'incarico/,/ha scritto il briefing/,/ha assegnato il lavoro a/,/ha confermato la presa in carico/,/ha scritto il piano, versione 1/,
   /ha scritto il piano, versione 2/,/ha letto il piano: /,/ha avviato la costruzione/,/Prova di .*: /,/<script>alert\(1\)<\/script> hello/])
   assert.ok(entries.some(t=>words.test(t)),'story has '+words);
  assert.ok(entries.some(t=>/Prova di .*: (prova verde|passata)/i.test(t))||entries.filter(t=>/Prova di/.test(t)).length===2,'both proofs are told');
  const seen=visibleText(dialog),inside=text(dialog);
  for(const x of [id,'MSG-ZZ1',...Object.keys(F.runs)])assert.ok(!seen.includes(x),'ID only in the technical details: '+x);
  assert.ok(inside.includes('MSG-ZZ1'),'technical IDs are behind «Dettagli tecnici»');
  // Document actions show filenames; hashes stay in technical details.
  const refs=app.run('panelStory(state.tasks["'+id+'"]).flatMap(i=>i.links||[])'),prefixes=[...new Set(refs.map(r=>r.sha256.slice(0,10)))];
  const docs=()=>all(dialog,e=>e.tag==='button'&&hasClass(e,'sq-doc'));
  assert.ok(refs.length>=2,'plans and read reports are documents');assert.equal(docs().length,refs.length,'one action per document');
  for(const r of refs)assert.ok(docs().some(b=>b.textContent==='Apri il documento '+r.name),'action for '+r.name);
  const noHash=where=>{const v=visibleText(dialog);for(const h of prefixes)assert.ok(!v.includes(h),where+': hash '+h+' only in the technical details')};
  noHash('Italian');for(const r of refs)assert.ok(inside.includes(r.sha256),'the full hash is in the technical details: '+r.name);
  const reports=F.tasks[id].reviews.map(r=>r.report);assert.equal(reports[0].sha256,reports[1].sha256);assert.notEqual(reports[0].name,reports[1].name);
  const twin=n=>one(dialog,e=>e.tag==='button'&&e.textContent==='Apri il documento '+n);
  assert.notEqual(key(twin(reports[0].name)),key(twin(reports[1].name)),'same bytes, two documents, two keys');
  twin(reports[1].name).focus();const twinKey=key(app.doc.activeElement);await app.refresh();
  assert.equal(key(app.doc.activeElement),twinKey,'focus stays on its own document');assert.equal(app.doc.activeElement.textContent,'Apri il documento '+reports[1].name);
  app.fetched.length=0;await twin(reports[0].name).onclick();await settle();
  assert.deepEqual(app.fetched,['/api/artifact/'+reports[0].sha256],'the action opens that artifact');
  assert.equal(app.doc.getElementById('artifact-title').textContent,reports[0].name);assert.equal(app.doc.getElementById('artifact').open,true);
  const tech=all(dialog,e=>e.tag==='summary')[0];tech.focus();const techKey=key(tech);
  await app.refresh();assert.equal(dialog.open,true);assert.equal(key(app.doc.activeElement),techKey,'an identical refresh keeps the place');
  const more=clone(F);more.messages['MSG-LATE']={id:'MSG-LATE',from:'coord-zx',to:'build-qv',related_task:id,body:'Ancora una cosa',utc:new Date().toISOString(),ack:null};
  await app.refresh(more);assert.equal(key(app.doc.activeElement),techKey,'a new event rebuilds the story and focus returns by key');
  assert.ok(text(one(dialog,e=>hasClass(e,'sq-story-list'))).includes('Ancora una cosa'));
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.match(text(dialog),/Task story/);assert.match(text(dialog),/wrote the plan, version 2/);assert.doesNotMatch(text(dialog),/⟦|\{[A-Za-z]+\}/);
  noHash('English');for(const r of refs)assert.ok(docs().some(b=>b.textContent==='Open the document '+r.name),'English action for '+r.name);
  dialog.close();dialog.onclose();await settle();
  assert.equal(app.run('panelUI.story'),null);assert.equal(key(app.doc.activeElement),'work:story:'+id,'closing returns focus to the card');
  for(const tag of new Set(app.doc.created.map(e=>e.tag).filter(t=>!t.startsWith('#'))))assert.ok(ALLOWED.has(tag),'unexpected element '+tag);
 },
 async storyDetails(){
  // Keep expanded technical details attached to the same task event after refresh.
  // Let the page read the open state; the test does not call ontoggle.
  const app=await boot({clock:Date.parse('2026-10-11T09:00:00Z')}),dialog=app.doc.getElementById('story'),id='TASK-KQ1';
  app.run("panelOpenStory('"+id+"')");await settle();assert.equal(dialog.open,true);
  const items=()=>app.run('panelStory(state.tasks["'+id+'"]).map(i=>({key:i.key,text:i.text,utc:i.utc}))');
  const plan=items().find(i=>/versione 2/.test(i.text));assert.ok(plan,'the second plan is told');
  const tech=k=>'story:'+k+':tech',details=k=>one(dialog,e=>e.tag==='details'&&e.children.some(c=>key(c)===tech(k)),'details of '+k);
  const opened=()=>all(dialog,e=>e.tag==='details'&&e.open).map(d=>key(d.children[0])).sort();
  const sig=()=>app.run('panelUI.storySig');
  details(plan.key).open=true;details(plan.key).children[0].focus();
  let before=sig(),node=details(plan.key);await app.refresh();
  assert.equal(details(plan.key),node,'an identical refresh keeps the node');assert.deepEqual(opened(),[tech(plan.key)]);
  const placed=where=>{assert.equal(sig()!==before,true,where+': the story was rebuilt');before=sig();
   assert.deepEqual(opened(),[tech(plan.key)],where+': only the opened event is open');assert.equal(key(app.doc.activeElement),tech(plan.key),where+': focus returns')};
  // Append a new event to the current task.
  let f=clone(app.fixture);f.messages['OUTSIDE-NEW']={id:'OUTSIDE-NEW',from:'coord-zx',to:'build-qv',related_task:id,body:'A new recorded event',utc:'2026-10-10T22:00:00+00:00',ack:null};
  await app.refresh(f);placed('a new event');
  // An event older than the opened one lands before it in time order.
  f=clone(app.fixture);f.messages['MSG-EARLY']={id:'MSG-EARLY',from:'coord-zx',to:'build-qv',related_task:id,body:'Prima di tutto',utc:'2000-01-01T00:00:00+00:00',ack:null};
  await app.refresh(f);placed('an earlier event');assert.equal(items()[0].key,'m:MSG-EARLY');
  app.run("setLanguage('en')");await settle();placed('the language switch');app.run("setLanguage('it')");await settle();placed('back to Italian');
  app.clock+=3*3600000;app.run('render()');await settle();placed('three hours later');
  // An event of another task does not touch this story.
  f=clone(app.fixture);f.messages['MSG-OTHER']={id:'MSG-OTHER',from:'coord-zx',to:'build-ht',related_task:'TASK-KQ2',body:'Altro',utc:new Date(app.clock).toISOString(),ack:null};
  await app.refresh(f);assert.equal(sig(),before,'an unrelated event leaves the story as it is');assert.deepEqual(opened(),[tech(plan.key)]);
  // Removing an event removes its open state; the replacement starts closed.
  details('m:MSG-EARLY').open=true;details('m:MSG-EARLY').children[0].focus();
  f=clone(app.fixture);delete f.messages['MSG-EARLY'];await app.refresh(f);
  assert.deepEqual(opened(),[tech(plan.key)],'the removed event takes its open state with it');assert.equal(key(app.doc.activeElement),'story:title','focus falls back to the title');
  f=clone(app.fixture);f.messages['MSG-EARLY']={id:'MSG-EARLY',from:'coord-zx',to:'build-qv',related_task:id,body:'Prima di tutto',utc:'2000-01-01T00:00:00+00:00',ack:null};
  await app.refresh(f);assert.deepEqual(opened(),[tech(plan.key)],'a removed event does not come back open');
  // Remember explicit closing; another task starts with all details closed.
  details(plan.key).open=false;app.clock+=3600000;app.run('render()');await settle();assert.equal(details(plan.key).open,false,'a closed disclosure stays closed');
  details(plan.key).open=true;dialog.close();dialog.onclose();await settle();
  app.run("panelOpenStory('TASK-KQ2')");await settle();assert.deepEqual(all(dialog,e=>e.tag==='details'&&e.open).length,0,'another story starts closed');
  dialog.close();dialog.onclose();await settle();app.run("panelOpenStory('"+id+"')");await settle();
  assert.equal(details(plan.key).open,true,'reopening the same story keeps what the reader opened');
 },
 async cards(){
  // Use several tasks, a long title, a superseded next step and a later decision.
  const app=await boot(),F=app.fixture,content=()=>app.doc.getElementById('content');
  const T=app.run('Object.fromEntries(Object.keys(state.tasks).map(k=>[k,taskTitle(k)]))'),ids=Object.keys(T);
  const card=id=>one(content(),e=>e.getAttribute('data-task')===id,'card '+id);
  const lines=(id,cls)=>card(id).children.filter(c=>hasClass(c,cls));
  const roles=id=>card(id).children.find(c=>c.tag==='p'&&/^(Costruisce|Builds): /.test(c.textContent)).textContent;
  const output=(id)=>F.outputs.filter(o=>o.task===id&&o.next).at(-1);
  const check=lang=>{
   const it=lang==='it';
   // Declared steps retain their recorded words; other labels come from the page.
   for(const id of ids){const seen=visibleText(card(id));for(const other of ids.filter(x=>x!==id))assert.ok(!seen.includes(T[other]),id+' never names '+other+' ('+lang+')');
    const own=visibleText(card(id),e=>hasClass(e,'sq-declared'));for(const x of [...ids,...Object.keys(F.agents)])assert.ok(!own.includes(x),id+': no identifier on the card: '+x)}
   assert.equal(roles('TASK-LONG'),it?"Costruisce: Claude · Legge: un'altra finestra Claude":'Builds: Claude · Reads: another Claude window');
   assert.equal(roles('TASK-OTHER'),it?'Costruisce: Claude':'Builds: Claude');
   assert.equal(roles('TASK-THIRD'),it?'Costruisce: Claude · Legge: Codex':'Builds: Claude · Reads: Codex');
   // A later decision moves the old intention into history, off the current card.
   const old=output('TASK-LONG');assert.equal(lines('TASK-LONG','sq-declared').length,0,'disposed intention is not on the card');
   assert.ok(!visibleText(card('TASK-LONG')).includes(old.next),'nor anywhere visible on it');
   assert.equal(lines('TASK-THIRD','sq-declared').length,0,'a brief rewritten after it disposes it too');
   // A live declared step: who and when, separate from the current state, the recorded text unchanged.
   const live=output('TASK-OTHER'),when=app.run('when('+JSON.stringify(live.utc)+')');
   const declared=lines('TASK-OTHER','sq-declared');assert.equal(declared.length,1);
   assert.equal(declared[0].textContent,(it?'Prossimo passo dichiarato da Costruttore (Claude), ':'Next step declared by Builder (Claude), ')+when+': '+live.next);
   assert.ok(!hasClass(declared[0],'sq-next'),'not the current state');for(const n of lines('TASK-OTHER','sq-next'))assert.ok(!n.textContent.includes(live.next));
   const tech=one(card('TASK-OTHER'),e=>e.tag==='details'&&hasClass(e,'sq-card-tech'));assert.equal(tech.open,false);
   assert.ok(text(tech).includes(live.agent)&&text(tech).includes(F.agents[live.agent].identity),'the window and its identity are in the details');
   assert.ok(text(tech).includes(live.event),'the source event is in the details');
  };
  check('it');
  const title=card('TASK-LONG').children[0].children[0];assert.ok(hasClass(title,'sq-readable'),'long titles use the shared reader');
  assert.equal(text(title),T['TASK-LONG'],'the full title is kept');
  assert.ok(hasClass(lines('TASK-OTHER','sq-declared')[0],'sq-readable'),'declared steps use the shared reader');
  await app.refresh();assert.equal(text(card('TASK-LONG').children[0].children[0]),T['TASK-LONG']);
  // The task table and the story name the same short roles.
  app.run("panelOpenStory('TASK-LONG')");await settle();const dialog=app.doc.getElementById('story');
  for(const other of ids.filter(x=>x!=='TASK-LONG'))assert.ok(!visibleText(dialog).includes(T[other]),'the story never names '+other);
  assert.ok(text(dialog).includes(output('TASK-LONG').next),'the old intention stays readable in the story');
  assert.ok(visibleText(dialog).includes("Costruisce: Claude · Legge: un'altra finestra Claude"));
  dialog.close();dialog.onclose();await settle();
  await app.tab('Incarichi');
  const row=one(app.doc.getElementById('content'),e=>hasClass(e,'row')&&e.getAttribute('role')==='row'&&text(e).includes(T['TASK-LONG']));
  for(const other of ids.filter(x=>x!=='TASK-LONG'))assert.ok(!visibleText(row).includes(T[other]),'the table row never names '+other);
  assert.ok(visibleText(row).includes('Costruttore (Claude)'));
  await app.tab('Quadro');
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  check('en');assert.ok(hasClass(card('TASK-LONG').children[0].children[0],'sq-readable'));
  assert.doesNotMatch(visibleText(content().children[1]),/⟦|\{[A-Za-z]+\}|Costruisce|dichiarato/);
  // Closed or formally replaced work has no current or declared next step.
  const closed=clone(F);closed.tasks['TASK-OTHER'].status='done';await app.refresh(closed);
  assert.equal(lines('TASK-OTHER','sq-declared').length+lines('TASK-OTHER','sq-next').length,0,'closed work has no next step');
  const replaced=clone(F);replaced.tasks['TASK-OTHER'].superseded_by='TASK-LONG';await app.refresh(replaced);
  assert.equal(lines('TASK-OTHER','sq-declared').length+lines('TASK-OTHER','sq-next').length,0,'replaced work has no next step');
  for(const tag of new Set(app.doc.created.map(e=>e.tag).filter(t=>!t.startsWith('#'))))assert.ok(ALLOWED.has(tag),'unexpected element '+tag);
 },
 async tables(){
  const app=await boot(),F=app.fixture;
  const panel=()=>app.doc.getElementById('content').children[0];
  const rows=()=>panel().children.filter(c=>hasClass(c,'row'));
  const titles=()=>rows().map(r=>r.children[0].children[0].textContent||text(r.children[0].children[0]));
  await app.tab('Incarichi');
  assert.equal(panel().getAttribute('role'),'table');assert.equal(panel().children[0].getAttribute('role'),'caption');
  assert.ok(rows().every(r=>r.getAttribute('role')==='row'&&r.children.every(c=>c.getAttribute('role')==='cell')),'rows of cells');
  const head=panel().children.find(c=>hasClass(c,'sq-head'));assert.equal(head.getAttribute('role'),'row');
  const columns=head.children.filter(c=>c.children.some(b=>b.tag==='button'));
  for(const col of columns){const b=col.children.find(x=>x.tag==='button'),k=key(b),name=k.split(':').at(-1);
   b.onclick();await settle();
   assert.equal(one(panel(),e=>e.getAttribute('role')==='columnheader'&&e.children.some(x=>key(x)===k)).getAttribute('aria-sort'),'ascending',name);
   one(panel(),e=>key(e)===k).onclick();await settle();
   assert.equal(one(panel(),e=>e.getAttribute('role')==='columnheader'&&e.children.some(x=>key(x)===k)).getAttribute('aria-sort'),'descending',name)}
  app.run("panelUI.tables.tasks.sort='title';panelUI.tables.tasks.dir=1;render()");await settle();
  const sorted=titles().slice().sort((a,b)=>a.localeCompare(b,'it-IT',{numeric:true,sensitivity:'base'}));assert.deepEqual(titles(),sorted,'title ascending');
  app.run("panelUI.tables.tasks.dir=-1;render()");await settle();assert.deepEqual(titles(),sorted.slice().reverse(),'title descending');
  app.run("panelUI.tables.tasks.sort='';render()");await settle();
  const filter=k=>{app.find('table:tasks:filter').onchange({target:{value:k}});return settle()};
  const tasks=Object.values(F.tasks);
  for(const [v,test] of [['open',t=>!t.superseded_by&&t.status!=='done'],['to-fix',t=>!t.superseded_by&&t.status==='changes-required'],['done',t=>!t.superseded_by&&t.status==='done'],['superseded',t=>Boolean(t.superseded_by)],['',()=>true]]){
   await filter(v);assert.equal(rows().length,tasks.filter(test).length,'filter '+v);
   assert.equal(one(panel(),e=>hasClass(e,'sq-count')).textContent,app.run(`tr('table.count',{shown:${tasks.filter(test).length},total:${tasks.length}})`))}
  const search=app.doc.getElementById('search');search.oninput({target:{value:'servizio delle chiavi'}});await settle();
  assert.equal(rows().length,1,'the global search filters the table');search.oninput({target:{value:''}});await settle();
  await app.tab('Posta');
  assert.equal(rows().length,100,'one hundred rows at first');app.find('table:mail:more').onclick();await settle();
  assert.equal(rows().length,Object.keys(F.messages).length,'then the rest');
  assert.ok(text(panel()).includes('<script>alert(1)</script>'),'hostile text stays text');
  // Keep identifiers and hashes inside technical details.
  const ids=[...Object.keys(F.tasks),...Object.keys(F.agents),...Object.keys(F.messages),...Object.keys(F.runs)].filter(x=>x.length>=6);
  const hashes=[...JSON.stringify(F).matchAll(/"sha256":"([0-9a-f]{64})"/g)].map(m=>m[1].slice(0,10));
  for(const view of ['Incarichi','Finestre','Prove','Posta']){
   // Messages and declared steps are user content, even when they contain technical text.
   const recorded=new Set([app.run("tr('table.mail.text')"),app.run("tr('table.windows.next')")]);
   await app.tab(view);const seen=visibleText(app.doc.getElementById('content').children[0],e=>recorded.has(e.getAttribute('data-label')));
   for(const id of ids)assert.ok(!seen.includes(id),view+': '+id+' only in details');
   for(const h of hashes)assert.ok(!seen.includes(h),view+': hash '+h+' only in details');
   const inside=text(app.doc.getElementById('content').children[0]);
   assert.ok(ids.some(id=>inside.includes(id)),view+': identifiers are still in the details');
  }
  for(const tag of new Set(app.doc.created.map(e=>e.tag).filter(t=>!t.startsWith('#'))))assert.ok(ALLOWED.has(tag),'unexpected element '+tag);
 },
 async connected(){
  const fixture=clone(base),a='TASK-KQ1',b='TASK-KQ2';
  fixture.tasks[b].brief=clone(fixture.tasks[a].brief);fixture.tasks[b].title=fixture.tasks[a].title;
  fixture.messages['RELATED-ONLY']={id:'RELATED-ONLY',from:'coord-zx',to:'build-qv',related_task:a,body:'Only the first task',utc:'2026-01-01T00:00:00Z'};
  fixture.messages['SAME-TITLE']={id:'SAME-TITLE',from:'coord-zx',to:'build-ht',related_task:b,body:'Only the other task',utc:'2026-01-01T00:00:00Z'};
  const app=await boot({fixture}),original=app.run('JSON.stringify(state)'),content=()=>app.doc.getElementById('content'),context=()=>app.doc.getElementById('connected-context');
  const click=async(root,k)=>{one(root,e=>key(e)===k,'button '+k).onclick();await settle()};
  app.run("filter='saved search';document.getElementById('search').value='saved search';commsUI.filters.task='TASK-KQ2';decView.filter='TASK-KQ2';connectedGo('Posta',{task:'TASK-KQ1'})");await settle();
  assert.ok(text(content()).includes('Only the first task'));assert.ok(!text(content()).includes('Only the other task'),'duplicate titles cannot mix mail');
  assert.equal(app.run('filter'),'');assert.equal(key(app.doc.activeElement),'connected:context');
  const row='connected:row:mail:RELATED-ONLY:'+a;await click(content(),row);assert.equal(app.run('panelUI.story'),a);
  const dialog=app.doc.getElementById('story');dialog.close();dialog.onclose();await settle();assert.equal(key(app.doc.activeElement),row,'return to originating row');
  app.run("connectedGo('Prove',{task:'TASK-KQ1'})");await settle();
  assert.equal(all(content(),e=>(key(e)||'').startsWith('connected:review:')).length,fixture.tasks[a].reviews.length,'recorded reviews remain reachable with checks');
  assert.ok(!all(content(),e=>(key(e)||'').startsWith('table:runs:row:')).some(e=>text(e).includes(b)));
  app.run("connectedGo('Output',{task:'TASK-KQ3'})");await settle();assert.ok(text(content()).includes(app.run("tr('page.empty')")),'empty scoped outputs have an explicit message');
  app.run("connectedGo('Decisioni',{task:'TASK-KQ1'})");await settle();assert.equal(app.run('decView.filter'),a);
  app.run("decView.filter='';render()");await settle();assert.ok(text(context()).includes('vista completa'),'native graph selector remains authoritative');
  await click(context(),'connected:clear');assert.equal(app.run('filter'),'saved search');assert.equal(app.run('decView.filter'),b);assert.equal(app.run('commsUI.filters.task'),b);
  assert.equal(app.run('JSON.stringify(state)'),original,'navigation never mutates registry data');
  app.run("filter='';document.getElementById('search').value='';connectedGo('Posta',{task:'TASK-KQ1'})");await settle();
  let next=clone(fixture);next.messages['NEW-LINKED']={...next.messages['RELATED-ONLY'],id:'NEW-LINKED',body:'A new linked record'};
  await app.refresh(next);assert.ok(text(content()).includes('A new linked record'));assert.ok(!text(content()).includes('Only the other task'));assert.equal(app.run('connectedUI.context.task'),a);
  app.run("setLanguage('en')");await settle();assert.ok(text(context()).includes('You are following'));assert.ok(text(content()).includes('Only the first task'),'recorded content stays original');
  await click(context(),'connected:back');assert.equal(app.run('tab'),'Quadro');assert.equal(app.run('panelUI.story'),a);
  dialog.close();dialog.onclose();await settle();
  app.run("connectedGo('Posta',{task:'TASK-KQ1'})");await settle();next=clone(next);delete next.tasks[a];await app.refresh(next);
  assert.ok(text(context()).includes('no longer present'));await click(context(),'connected:back');assert.equal(app.run('tab'),'Quadro');assert.equal(app.run('panelUI.story'),null);
  unique(app,'connected task journeys');
 },
 async connected_agents(){
  const fixture=clone(base);fixture.agents['builder-alias']={...fixture.agents['build-qv'],id:'builder-alias',role:'executor'};
  const app=await boot({fixture});const content=()=>app.doc.getElementById('content');
  assert.equal(all(content(),e=>(key(e)||'').startsWith('connected:agent:')).length,new Set(Object.values(fixture.agents).map(a=>a.identity)).size,'one overview seat per real identity');
  app.run("connectedGo('Finestre',{agent:'build-qv'})");await settle();
  const rows=all(content(),e=>(key(e)||'').startsWith('table:windows:row:')&&(key(e)||'').endsWith(':details')).map(key);
  assert.equal(rows.length,2);assert.ok(rows.some(k=>k.includes('builder-alias')));assert.ok(rows.some(k=>k.includes('build-qv')));
  await app.tab('Incarichi');assert.equal(app.run('connectedUI.context'),null,'ordinary navigation clears context');
  assert.equal(all(content(),e=>(key(e)||'').startsWith('table:tasks:row:')&&(key(e)||'').endsWith(':details')).length,Object.keys(fixture.tasks).length);
  for(const lang of ['it','en']){app.run("setLanguage('"+lang+"')");await app.tab('Quadro');assert.ok(!pageText(app,['content','connected-hero','connected-project','connected-context']).includes('⟦'));unique(app,'connected identity '+lang)}
 }
,

 async connected_edges(){
  const fixture=clone(base),task='TASK-KQ1';
  fixture.messages['ORIGIN-ROW']={id:'ORIGIN-ROW',from:'coord-zx',to:'build-qv',related_task:task,body:'New pending message',utc:'2026-10-11T00:00:00Z',ack:null};
  fixture.attention.waiting.push({kind:'ack',id:'ORIGIN-ROW',who:['build-qv'],utc:'2026-10-11T00:00:00Z'});
  const app=await boot({fixture}),context=()=>app.doc.getElementById('connected-context');
  // The selector placeholder is a filter choice, not a task ID.
  app.run("connectedUI.context={task:'TASK-KQ1',originTask:'TASK-KQ1'};tab='Comunicazioni';commsUI.filters.task='-';connectedContext()");
  for(const lang of ['it','en']){
   app.run("UI_LANG='"+lang+"';connectedContext()");
   assert.ok(text(context()).includes(app.run("tr('comms.task.none')")));
   assert.ok(text(context()).includes(app.run("tr('connected.noTaskHint')")));
   assert.ok(!text(context()).includes(app.run("tr('connected.missing')")));
  }
  for(const [value,expected] of [['-',"tr('comms.task.none')"],['',"tr('connected.completeHint')"],[task,"taskTitle('TASK-KQ1')"],['MISSING',"tr('connected.missing')"]]){
   app.run('commsUI.filters.task='+JSON.stringify(value)+';connectedContext()');assert.ok(text(context()).includes(app.run(expected)),'native task choice '+value);
  }
  app.run("connectedReset();commsUI.filters.task='';UI_LANG='it';tab='Posta';render();panelUI.tables.mail.filter='waiting';render()");await settle();
  const origin='connected:row:mail:ORIGIN-ROW:'+task,dialog=app.doc.getElementById('story');
  app.find(origin).onclick();await settle();await app.refresh();dialog.close();dialog.onclose();await settle();
  assert.equal(key(app.doc.activeElement),origin,'unchanged origin is the exact focus return');
  app.find(origin).onclick();await settle();const changed=clone(app.fixture);changed.messages['ORIGIN-ROW'].ack={actor:'build-qv',utc:'2026-10-11T00:01:00Z'};
  changed.attention.waiting=changed.attention.waiting.filter(w=>w.id!=='ORIGIN-ROW');
  await app.refresh(changed);assert.equal(app.find(origin),null,'ACK hides the origin in the pending-only table');
  app.run("window.returnHeadingTop=-600;window.scrollBy=(x,y)=>{window.returnHeadingTop-=y};pageFocusables.get('table:mail:title').getBoundingClientRect=()=>({top:window.returnHeadingTop,bottom:window.returnHeadingTop+24})");
  dialog.close();dialog.onclose();await settle();assert.equal(key(app.doc.activeElement),'table:mail:title','missing origin returns to its actual table heading');
  assert.ok(app.run('window.returnHeadingTop')>=0,'a fallback outside the viewport is brought into view');
 }

};
(async()=>{
 const run=scenarios[scenario];if(!run)throw Error('unknown scenario '+scenario);
 await run();console.log('panel contract passed: '+scenario);
})().catch(e=>{console.error(e);process.exitCode=1});

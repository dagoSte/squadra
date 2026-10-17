// Check the overview, decision graph, languages and glossary.
// Run the page scripts in order with a simplified DOM.
// Usage: node decisions_contract.js <dashboard.html> <state.json from the real server> <scenario>
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [htmlPath,fixturePath,scenario]=process.argv.slice(2);
const page=fs.readFileSync(htmlPath,'utf8');
const scripts=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const styles=[...page.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]).join('\n');
const base=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
const clone=value=>JSON.parse(JSON.stringify(value));
const SVG='http://www.w3.org/2000/svg';
const TABS=['Quadro','Finestre','Incarichi','Posta','Prove','Output','Cronologia','Storico','Decisioni'];
const ALLOWED=new Set(['div','span','section','h2','h3','h4','p','button','details','summary','dl','dt','dd','ul','ol','li','table','tr','th','td',
 'a','small','time','nav','strong','select','option','label','article','aside','br','svg','defs','marker','path','g','rect','text','title']);

class Element{
 constructor(doc,tag,ns){this.doc=doc;this.tag=tag;this.ns=ns;this.children=[];this.attributes={};this.textContent='';this.className='';this.scrollLeft=0;this.scrollTop=0;doc.created.push(this)}
 append(...children){for(const c of children){if(!(c instanceof Element))throw Error('Non-element child appended: '+String(c));this.children.push(c)}}
 // As in a browser, removing the focused element's subtree drops focus; restoration is the page's job.
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
function makeDocument(){
 const doc={created:[],ids:new Map(),activeElement:null,title:'',documentElement:{lang:''}};
 doc.getElementById=id=>{if(!doc.ids.has(id)){const e=new Element(doc,'#'+id);e.id=id;doc.ids.set(id,e)}return doc.ids.get(id)};
 doc.createElement=tag=>new Element(doc,tag);
 doc.createElementNS=(ns,tag)=>new Element(doc,tag,ns);
 return doc;
}
const settle=async()=>{for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r))};
async function boot({fixture=clone(base),language,storage='memory',stored=null,prelude=''}={}){
 const doc=makeDocument(),timers=[],writes=[],saved=new Map(stored?[['squadra-lang',stored]]:[]);
 const app={doc,timers,writes,fixture};
 const context={console,Date,document:doc,location:{port:'4242',hash:'#token',pathname:'/'},
  sessionStorage:{setItem(){},getItem(){return 'token'}},history:{replaceState(){}},setTimeout:fn=>{timers.push(fn)},
  fetch:async path=>path.startsWith('/api/artifact/')?{ok:true,text:async()=>'artifact '+path.slice(14)}:{ok:true,json:async()=>clone(app.fixture)}};
 if(language!==undefined)context.navigator={language};
 if(storage==='memory')context.localStorage={getItem:k=>saved.has(k)?saved.get(k):null,setItem:(k,v)=>{writes.push([k,v]);saved.set(k,String(v))}};
 if(storage==='throws')Object.defineProperty(context,'localStorage',{get(){throw Error('storage blocked')}});
 context.window=context;vm.createContext(context);
 if(prelude)vm.runInContext(prelude,context);
 for(const script of scripts)vm.runInContext(script,context,{timeout:2000});
 await settle();
 app.run=code=>vm.runInContext(code,context);
 app.refresh=async next=>{if(next)app.fixture=next;const fn=timers.shift();assert.ok(fn,'a refresh is scheduled');await fn();await settle()};
 app.tab=async name=>{const label=app.run('tr('+JSON.stringify('tab.'+name)+')');const b=doc.getElementById('tabs').children.find(e=>e.textContent===label);assert.ok(b,'tab '+name);b.onclick();await settle()};
 app.content=()=>doc.getElementById('content');
 app.svg=()=>one(app.content(),e=>e.tag==='svg','inline graph');
 app.node=id=>one(app.svg(),e=>e.getAttribute('data-node-id')===id&&e.tag==='g','graph node '+id);
 app.aside=()=>one(app.content(),e=>e.tag==='aside','details panel');
 app.select=async id=>{app.node(id).onclick();await settle()};
 return app;
}
function* walk(e){if(!e)return;yield e;for(const c of e.children||[])yield* walk(c)}
const all=(root,test)=>[...walk(root)].filter(test);
function one(root,test,message){const found=all(root,test);assert.ok(found.length,message||'element not found');return found[0]}
const text=e=>[...walk(e)].map(x=>x.textContent||'').filter(Boolean).join(' ');
const hasClass=(e,name)=>(' '+(e.className||'')+' '+(e.getAttribute('class')||'')+' ').includes(' '+name+' ');
const nodeOf=id=>base.graph.nodes.find(n=>n.id===id);
function pageText(app){
 const roots=['name','project','live','wordmark','lang','notice','overview','metrics','tabs','content','footer'].map(id=>app.doc.getElementById(id));
 const attributes=roots.flatMap(r=>all(r,()=>true)).flatMap(e=>['aria-label','aria-pressed','title','data-lang'].map(a=>e.getAttribute(a)).filter(Boolean));
 return [app.doc.title,app.doc.getElementById('search').placeholder,...roots.map(text),...attributes].join('\n');
}

const scenarios={
 async graph(){
  const app=await boot({language:'it-IT'});await app.tab('Decisioni');
  const svg=app.svg();assert.equal(svg.ns,SVG,'SVG built with createElementNS');
  const drawn=all(svg,e=>e.tag==='g'&&e.getAttribute('data-node-id'));
  assert.deepEqual(drawn.map(e=>e.getAttribute('data-node-id')).sort(),base.graph.nodes.map(n=>n.id).sort());
  for(const g of drawn){assert.equal(g.ns,SVG);assert.equal(g.getAttribute('tabindex'),'0');assert.equal(g.getAttribute('role'),'button')}
  const key=e=>[e.from,e.to,e.kind].join('|');
  const paths=all(svg,e=>e.tag==='path'&&e.getAttribute('data-kind'));
  assert.deepEqual(paths.map(p=>[p.getAttribute('data-from'),p.getAttribute('data-to'),p.getAttribute('data-kind')].join('|')).sort(),base.graph.edges.map(key).sort());
  for(const p of paths){const e=base.graph.edges.find(x=>key(x)===[p.getAttribute('data-from'),p.getAttribute('data-to'),p.getAttribute('data-kind')].join('|'));assert.equal(hasClass(p,'dec-dashed'),!e.recorded)}
  assert.ok(base.graph.edges.some(e=>!e.recorded)&&base.graph.edges.some(e=>e.recorded));
  const position=id=>Number(app.node(id).getAttribute('transform').match(/translate\(([-\d.]+),/)[1]);
  for(const e of base.graph.edges)assert.ok(position(e.from)<position(e.to),'earlier record left of later: '+key(e));
  const pitch=76+16,spans=paths.filter(p=>position(p.getAttribute('data-to'))-position(p.getAttribute('data-from'))>236+64);
  assert.ok(spans.length>0,'the fixture has edges that skip columns');
  for(const p of spans){const lane=Number(p.getAttribute('d').match(/ L[-\d.]+,([-\d.]+)/)[1]);assert.equal((lane-16)%pitch,84,'long edges run between rows: '+p.getAttribute('d'))}
  assert.match(text(app.node('decision:D-KEYS-GO')),/Risposta del proprietario · scritto qui/);
  assert.match(text(app.node('decision:D-SCOPE')),/Istruzione del proprietario · riferito da un'altra finestra/);
  assert.match(text(app.node('decision:D-SCOPE')),/«Tieni fuori SQ-DECISIONS-OLD»/);
  const unknown=base.graph.nodes.find(n=>n.kind==='owner-instruction'&&!n.channel);
  assert.match(text(app.node(unknown.id)),/canale non registrato/);
  assert.ok(hasClass(app.node('decision:D-SCOPE'),'dec-owner'));
  const red=base.graph.nodes.find(n=>n.kind==='code-verdict'&&n.verdict==='changes-required'),green=base.graph.nodes.find(n=>n.kind==='code-verdict'&&n.verdict==='accepted');
  assert.match(text(app.node(red.id)),/Lettura del codice.*da correggere/s);assert.ok(hasClass(app.node(red.id),'dec-bad'));
  assert.match(text(app.node(green.id)),/accettato/);assert.ok(hasClass(app.node(green.id),'dec-good'));
  const failed=base.graph.nodes.find(n=>n.kind==='proof'&&n.status==='failed');
  assert.match(text(app.node(failed.id)),/prova rossa: exit 1, 2 falliti, 56 errori/);
  const plan=base.graph.nodes.find(n=>n.kind==='plan');
  await app.select(plan.id);
  let aside=app.aside();assert.equal(aside.getAttribute('data-node-id'),plan.id);
  for(const label of ['Chi','Quando','Arriva da','Porta a','Documenti collegati','Dettagli tecnici'])assert.match(text(aside),new RegExp(label));
  assert.match(text(aside),/lettura di questo piano/);
  const doc=one(aside,e=>e.tag==='button'&&e.textContent.startsWith(plan.artifacts[0].name),'artifact link');
  await doc.onclick();assert.equal(app.doc.getElementById('artifact').open,true);
  assert.equal(app.doc.getElementById('artifact-body').textContent,'artifact '+plan.artifacts[0].sha256);
  let prevented=false;app.node('ask:Q-KEYS').onkeydown({key:'Enter',preventDefault(){prevented=true}});await settle();
  assert.ok(prevented);assert.equal(app.aside().getAttribute('data-node-id'),'ask:Q-KEYS');
  assert.ok(hasClass(app.node('ask:Q-KEYS'),'dec-selected'));
  assert.ok(all(app.svg(),e=>e.tag==='path'&&hasClass(e,'dec-active')).length>0,'links of the selection are highlighted');
  app.node('decision:D-SCOPE').onkeydown({key:' ',preventDefault(){}});await settle();
  aside=app.aside();assert.equal(aside.getAttribute('data-node-id'),'decision:D-SCOPE');
  assert.match(text(aside),/Canale riferito da un'altra finestra/);
  assert.equal(all(aside,e=>e.tag==='button'&&/· segue$/.test(e.textContent)).length,2);
  assert.match(text(aside),/Servizio delle chiavi/);
  const back=one(aside,e=>e.tag==='button'&&e.textContent.startsWith('Risposta del proprietario'));back.onclick();await settle();
  assert.equal(app.aside().getAttribute('data-node-id'),'decision:D-KEYS-GO','links in the details move the selection');
  const select=one(app.content(),e=>e.tag==='select');
  assert.equal(select.children.length,1+Object.keys(base.tasks).length);
  assert.ok(select.children.some(o=>o.textContent==='Servizio delle chiavi'&&o.value==='K'));
 },
 async refresh(){
  const app=await boot({language:'it-IT'});await app.tab('Decisioni');
  const N='decision:D-SCOPE';await app.select(N);
  const select=()=>one(app.content(),e=>e.tag==='select');
  select().value='K';select().onchange({target:{value:'K'}});await settle();
  const plus=()=>one(app.content(),e=>e.tag==='button'&&e.textContent==='+');
  plus().onclick();await settle();plus().onclick();await settle();
  const zoomed=()=>{const svg=app.svg(),box=svg.getAttribute('viewBox').split(' ').map(Number);return [Number(svg.getAttribute('width')),Math.round(box[2]*1.5625)]};
  assert.equal(...zoomed());
  const scroller=()=>one(app.content(),e=>hasClass(e,'dec-graph'));
  scroller().scrollLeft=137;scroller().scrollTop=21;scroller().onscroll();
  const visibleIds=()=>all(app.svg(),e=>e.tag==='g'&&e.getAttribute('data-node-id')).map(e=>e.getAttribute('data-node-id'));
  assert.ok(visibleIds().includes('task:K')&&!visibleIds().includes('task:W'),'filter shows the chosen task');
  const next=clone(base),top=Math.max(...next.graph.nodes.map(n=>n.seq));
  next.graph.nodes.push({id:'decision:D-LATER',kind:'owner-instruction',seq:top+1,hash:'e'.repeat(64),actor:'coord',utc:new Date().toISOString(),
   artifacts:[],tasks:['K'],decision:'D-LATER',explicit_id:true,words:'Aggiungi la rotazione automatica',channel:'typed-here',reason:'later',follows:[]});
  next.graph.edges.push({from:'task:K',to:'decision:D-LATER',kind:'concerns',recorded:true},{from:N,to:'decision:D-LATER',kind:'follows',recorded:true});
  next.graph.nodes.reverse();next.graph.edges.reverse();
  await app.refresh(next);
  assert.equal(app.aside().getAttribute('data-node-id'),N,'selection kept');
  assert.equal(select().value,'K','filter kept');
  assert.equal(...zoomed());
  assert.equal(scroller().scrollLeft,137,'horizontal scroll kept');assert.equal(scroller().scrollTop,21,'vertical scroll kept');
  assert.ok(hasClass(app.node('decision:D-LATER'),'dec-new'));
  assert.ok(!hasClass(app.node(N),'dec-new')&&hasClass(app.node(N),'dec-selected'));
  const x=id=>Number(app.node(id).getAttribute('transform').match(/translate\(([-\d.]+),/)[1]);
  assert.ok(x(N)<x('decision:D-LATER'));
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.deepEqual([app.aside().getAttribute('data-node-id'),select().value,scroller().scrollLeft,scroller().scrollTop],[N,'K',137,21],'language switch keeps selection, filter and scroll');
  assert.equal(...zoomed());
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='it').onclick();await settle();
  const gone=clone(next);gone.graph.nodes=gone.graph.nodes.filter(n=>n.id!==N);gone.graph.edges=gone.graph.edges.filter(e=>e.from!==N&&e.to!==N);delete gone.tasks.K;
  await app.refresh(gone);
  const notices=()=>all(app.content(),e=>hasClass(e,'dec-notice')).map(e=>e.textContent);
  assert.equal(notices().length,2);
  assert.ok(notices().every(t=>t.startsWith('Elemento non più presente')));
  assert.ok(notices().some(t=>t.includes(N))&&notices().some(t=>t.includes('incarico K')));
  assert.equal(select().value,'');assert.match(text(app.aside()),/Seleziona un riquadro/);
  await app.refresh(gone);assert.equal(notices().length,2,'the explanation stays until the reader acts');
  await app.select('task:W');assert.equal(notices().length,0);
 },
 async language(){
  for(const [options,expected] of [[{language:'it-IT'},'it'],[{language:'en-US'},'en'],[{language:'fr-FR'},'en'],[{},'it'],
   [{language:'en-US',stored:'it'},'it'],[{language:'it-IT',stored:'en'},'en'],[{language:'it-IT',storage:'throws'},'it'],[{language:'en-GB',storage:'throws'},'en']]){
   const app=await boot(options);assert.equal(app.run('UI_LANG'),expected,JSON.stringify(options));assert.equal(app.doc.getElementById('name').textContent,'Atlas');
  }
  const prelude="window.I18N=window.I18N||{'presence.test':{it:'Presenza',en:'Presence'}};window.tr=window.tr||(k=>'fallback');window.UI_LANG=window.UI_LANG||'xx';window.KEEP=window.I18N;";
  const merged=await boot({language:'en-US',prelude});
  assert.equal(merged.run('I18N===KEEP'),true,'existing dictionary object is merged, not replaced');
  assert.equal(merged.run("tr('presence.test')"),'Presence');assert.equal(merged.run("tr('page.owner.title')"),'Needs you');
  assert.equal(merged.run("window.tr===tr&&UI_LANG"),'en');
  const app=await boot({language:'it-IT'});
  assert.equal(app.run("tr('no.such.key')"),'⟦no.such.key⟧');
  const sweep=async()=>{const seen=[];for(const t of TABS){await app.tab(t);if(t==='Decisioni')await app.select('decision:D-SCOPE');seen.push(pageText(app))}return seen.join('\n')};
  const italian=await sweep();
  assert.doesNotMatch(italian,/⟦/);assert.doesNotMatch(italian,/\{[A-Za-z]+\}/);
  for(const label of ['Panoramica','Serve a te','Decisioni','Come leggere questa pagina','Prossimo passo dichiarato'])assert.ok(italian.includes(label),label);
  const english=one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en');
  english.onclick();await settle();
  assert.equal(app.run('UI_LANG'),'en');assert.deepEqual(app.writes.at(-1),['squadra-lang','en']);
  assert.equal(english.getAttribute('aria-pressed'),'true');
  assert.equal(app.aside().getAttribute('data-node-id'),'decision:D-SCOPE','switch keeps the selection');
  assert.match(text(app.aside()),/Comes from/);assert.equal(app.doc.documentElement.lang,'en');
  assert.match(text(app.node('decision:D-SCOPE')),/«Tieni fuori SQ-DECISIONS-OLD»/,'graph keeps the recorded words');
  assert.match(text(app.aside()),/«Tieni fuori SQ-DECISIONS-OLD»/,'details keep the recorded words');
  assert.match(text(app.node('ask:Q-KEYS')),/Procediamo con le chiavi\?/,'recorded question kept');
  await app.tab('Quadro');const englishOverview=text(app.doc.getElementById('overview'));
  for(const recorded of ['Atlas tiene in ordine le chiavi dei clienti','Servizio delle chiavi','Quale vista prima?','Vista web per i clienti'])assert.ok(englishOverview.includes(recorded),'overview keeps '+recorded);
  await app.tab('Decisioni');
  const englishText=await sweep();
  assert.doesNotMatch(englishText,/⟦/);assert.doesNotMatch(englishText,/\{[A-Za-z]+\}/);
  for(const label of ['Overview','Needs you','Decisions','How to read this page','Declared next step','Comes from'])assert.ok(englishText.includes(label),label);
  for(const label of ['Panoramica','Serve a te','Prossimo passo dichiarato','Come leggere questa pagina'])assert.ok(!englishText.includes(label),label);
  for(const recorded of ['Procedi con il servizio delle chiavi JEV','Tieni fuori SQ-DECISIONS-OLD','Servizio delle chiavi','Procediamo con le chiavi?',"Avvio la costruzione dopo l'approvazione del piano"]){
   assert.ok(italian.includes(recorded),recorded);assert.ok(englishText.includes(recorded),'recorded text is never translated: '+recorded);
  }
  const names=s=>[...s.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort().join(',');
  for(const [key,entry] of Object.entries(app.run('I18N'))){
   assert.ok(typeof entry.it==='string'&&entry.it&&typeof entry.en==='string'&&entry.en,'both languages: '+key);
   assert.equal(names(entry.it),names(entry.en),'same placeholders: '+key);
  }
  const blocked=await boot({language:'it-IT',storage:'throws'});
  one(blocked.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.equal(blocked.run('UI_LANG'),'en','switch works without storage');assert.equal(blocked.doc.getElementById('overview').children[0].children[0].textContent,'Overview');
 },
 async overview(){
  const app=await boot({language:'it-IT'});
  assert.equal(app.content().children[0].children[0].textContent,'Serve a te','owner panel stays first in the content');
  const panel=app.doc.getElementById('overview').children[0];
  assert.equal(panel.children[0].textContent,'Panoramica');
  const sections=panel.children.filter(c=>c.tag==='section');
  assert.deepEqual(sections.map(s=>s.children[0].textContent),['1 · Di cosa si tratta','2 · Cosa succede adesso e chi lo fa','3 · A che punto è ogni lavoro','4 · Cosa è fermo o aspetta qualcuno','5 · Dove seguire una decisione fino al risultato']);
  const help=panel.children.at(-1);assert.equal(help.tag,'details');assert.equal(help.children[0].textContent,'Come leggere questa pagina');
  assert.equal(all(help,e=>e.tag==='dt').length,app.run('HELP_TERMS.length')+2);
  for(const term of ['Linea tratteggiata','Canale','Sostituito','Prossimo passo dichiarato','JEV'])assert.ok(all(help,e=>e.tag==='dt').some(e=>e.textContent===term),term);
  const [project,windows,progress,waiting,decisions]=sections;
  assert.match(text(project),/Atlas tiene in ordine le chiavi dei clienti/);
  assert.match(text(project),/✓ +Piano del servizio delle chiavi +· fatto, secondo il briefing/);
  assert.match(text(project),/○ +Servizio delle chiavi in produzione +· da fare, secondo il briefing/);
  assert.match(text(project),/Briefing scritto da coordinatore coord/);
  const card=id=>one(progress,e=>e.getAttribute('data-task')===id,'task '+id);
  const steps=id=>card(id).children.find(c=>c.tag==='ol').children;
  assert.deepEqual(steps('K').map(s=>s.getAttribute('data-step')),['plan','plan-read','build','proof','code-read','closed']);
  assert.deepEqual(steps('K').map(s=>s.children[0].textContent),['✓ Piano scritto','✓ Piano letto','✓ Costruzione','✓ Prova','✓ Codice letto','○ Chiuso']);
  assert.match(text(card('K')),/Servizio delle chiavi.*Da correggere.*Perché: i clienti ruotano le chiavi/s);
  assert.match(text(steps('K')[0]),/2 versioni del piano/);assert.match(text(steps('K')[1]),/piano accettato/);
  assert.match(text(steps('K')[3]),/prova verde.*consegnata/s);assert.match(text(steps('K')[4]),/codice da correggere/);
  assert.match(text(steps('K')[5]),/non ancora/);
  const red=steps('RED')[3];assert.equal(red.children[0].textContent,'✓ Prova','a reached step');
  assert.deepEqual(red.children.filter(c=>hasClass(c,'ov-note')).map(c=>[c.textContent,hasClass(c,'bad')]),
   [['prova rossa: exit 1, 2 falliti, 56 errori',true],['consegna rifiutata: nessuna prova verde su questa versione',true]]);
  assert.match(text(card('DONE')),/Chiuso/);assert.equal(steps('DONE')[5].children[0].textContent,'✓ Chiuso');
  for(const id of ['K','W','RED','NEW','DOC','OLD'])assert.equal(steps(id)[5].children[0].textContent,'○ Chiuso','only a recorded close: '+id);
  assert.match(text(card('OLD')),/Sostituito da «Nuovo import dei clienti»/);
  assert.match(text(card('DOC')),/descrizione non ancora scritta/);
  assert.ok(!/descrizione non ancora scritta/.test(text(card('K'))));
  const ids=progress.children[1].children.map(c=>c.getAttribute('data-task'));
  assert.ok(ids.indexOf('OLD')>ids.indexOf('DONE')&&ids.indexOf('DONE')>ids.indexOf('RED'),'open work first, then closed, then replaced');
  const waits=all(waiting,e=>e.tag==='li').map(text);
  assert.ok(waits.some(t=>/Serve a te: Quale vista prima\?/.test(t)));
  assert.ok(waits.some(t=>/deve avviare «Vista web per i clienti»/.test(t)));
  assert.ok(waits.some(t=>/«Docs» aspetta che si chiudano: Old import/.test(t)),'dependent of the replaced task stays blocked');
  assert.ok(!waits.some(t=>/il messaggio su «Old import»/.test(t)),'the replaced task leaves its assignment wait');
  assert.ok(waits.some(t=>/il messaggio su «Nuovo import dei clienti»/.test(t)));
  const follow=all(decisions,e=>e.tag==='button'&&e.textContent==='Segui nel grafo');
  assert.equal(follow.length,5);assert.match(text(decisions),/Istruzione del proprietario · scritto qui:\s+«<img src=x onerror=alert\(1\)>/);
  const target=all(decisions,e=>e.tag==='li')[1];one(target,e=>e.tag==='button').onclick();await settle();
  assert.equal(app.doc.getElementById('tabs').children.find(b=>b.className==='active').textContent,'Decisioni');
  assert.equal(app.aside().getAttribute('data-node-id'),'ask:Q-WEB');
  const at=app.node('ask:Q-WEB').getAttribute('transform').match(/translate\(([-\d.]+),([-\d.]+)\)/).slice(1).map(Number);
  const view=one(app.content(),e=>hasClass(e,'dec-graph'));
  assert.ok(at[0]>40,'the followed node is beyond the first screen');
  assert.deepEqual([view.scrollLeft,view.scrollTop],[Math.max(0,at[0]-40),Math.max(0,at[1]-40)],'the followed decision is scrolled into view');
  // D-STRIP-OVERVIEW-ONLY: other pages carry no project strip; the goal stays on the overview.
  assert.equal(app.doc.getElementById('overview').children.length,0);
  one(app.doc.getElementById('tabs'),e=>e.tag==='button'&&e.textContent.includes('Quadro')).onclick();await settle();
  const overview=app.doc.getElementById('overview').children[0];
  assert.equal(overview.children[0].textContent,'Panoramica');
  assert.match(text(overview),/Atlas tiene in ordine/);
 },
 async cards(){
  const app=await boot({language:'it-IT'});
  const cards=()=>all(app.doc.getElementById('overview'),e=>e.tag==='article'&&e.getAttribute('data-seat'));
  assert.deepEqual(cards().map(c=>c.getAttribute('data-seat')),Object.keys(base.agents));
  const card=seat=>cards().find(c=>c.getAttribute('data-seat')===seat);
  const lines=seat=>card(seat).children.filter(c=>hasClass(c,'ov-line'));
  for(const seat of Object.keys(base.agents)){
   assert.deepEqual(lines(seat).map(l=>l.children[0].textContent),['Compito assegnato','Ultima attività registrata','Prossimo passo dichiarato','Stato registrato']);
   assert.doesNotMatch(card(seat).children[0].textContent,/[0-9a-f]{12}/,'no hash in the first line');
  }
  const web=lines('web');
  assert.equal(card('web').children[0].textContent,'costruttore di «Vista web per i clienti» (Claude)');
  assert.match(text(web[0]),/Vista web per i clienti/);assert.match(web[0].children[2].textContent,/^assegnato /);
  assert.match(text(web[1]),/Ho letto il perimetro della vista web/);assert.match(web[1].children[2].textContent,/^scritto /);
  assert.match(text(web[2]),/Avvio la costruzione dopo l'approvazione del piano/);
  assert.match(web[2].children[2].textContent,/dichiarato dalla finestra .*è un'intenzione, non un'attività in corso/);
  assert.match(text(web[3]),/presa in carico confermata, lavoro non ancora avviato/);assert.match(web[3].children[2].textContent,/^dal /);
  assert.doesNotMatch(text(web[3])+text(web[0])+text(web[1]),/sta facendo|sta lavorando|costruzione avviata|in lavorazione/i);
  const alias=lines('web-alias');
  assert.match(text(alias[0]),/Docs/);assert.match(text(alias[1]),/nessuna attività registrata/);assert.match(text(alias[2]),/nessun passo dichiarato/);
  assert.doesNotMatch(text(card('web-alias')),/Avvio la costruzione/);
  assert.match(text(card('web')),/Stessa finestra di: web-alias/);assert.match(text(card('web-alias')),/Stessa finestra di: web/);
  assert.match(text(lines('reader')[0]),/lettura di «Servizio delle chiavi»/);
  assert.match(text(lines('coord')[0]),/coordina \d+ incarichi aperti/);
  const older=clone(base);older.outputs.find(o=>o.agent==='web').utc=new Date(Date.now()-3*86400e3).toISOString();
  await app.refresh(older);
  assert.match(lines('web')[2].children[2].textContent,/3 giorni fa/);
  assert.match(text(lines('web')[2]),/Avvio la costruzione dopo l'approvazione del piano/);
  assert.doesNotMatch(text(card('web')).replace("non un'attività in corso",''),/sta facendo|sta lavorando|in corso|costruzione avviata/i);
 },
 async glossary(){
  assert.match(styles,/\.term:hover \.tip,\.term:focus \.tip,\.term:focus-within \.tip\{display:block\}/);
  const app=await boot({language:'it-IT'});
  const overview=()=>app.doc.getElementById('overview');
  const terms=()=>all(overview(),e=>hasClass(e,'term'));
  const jev=terms().find(t=>t.children[0].textContent==='JEV');
  assert.equal(jev.getAttribute('tabindex'),'0');
  const tip=one(overview(),e=>e.id===jev.getAttribute('aria-describedby'),'tooltip target');
  assert.equal(tip.getAttribute('role'),'tooltip');assert.equal(tip.textContent,'verifica del motore dei lavori');
  assert.ok(!terms().some(t=>t.children[0].textContent==='SQ-DECISIONS'),'no partial match inside SQ-DECISIONS-OLD');
  assert.ok(text(overview()).includes('SQ-DECISIONS-OLD')&&text(overview()).includes('ZZQ-9'));
  assert.ok(!terms().some(t=>/ZZQ-9/.test(text(t))),'unknown terms stay as they are');
  jev.focus();const focusKey=jev.getAttribute('data-focus-key');
  await app.refresh();
  const active=app.doc.activeElement;assert.ok(active&&active!==jev,'a new element after refresh');
  assert.equal(active.getAttribute('data-focus-key'),focusKey,'keyboard focus returns to the same term');
  active.onkeydown({key:'Escape'});assert.notEqual(app.doc.activeElement,active,'Escape dismisses the explanation');
  await app.tab('Decisioni');await app.select('decision:D-KEYS-GO');
  assert.match(text(app.aside()),/Sigle in questo elemento.*JEV: verifica del motore dei lavori/s);
  assert.ok(all(app.aside(),e=>hasClass(e,'term')).length>0,'terms in the details are expandable too');
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.match(text(app.aside()),/Terms in this item.*JEV: job engine verification/s);
  await app.tab('Quadro');
  const english=terms().find(t=>t.children[0].textContent==='JEV');
  assert.equal(one(overview(),e=>e.id===english.getAttribute('aria-describedby')).textContent,'job engine verification');
 },
 async hostile(){
  const app=await boot({language:'it-IT'});
  for(const t of TABS)await app.tab(t);
  await app.tab('Decisioni');
  const id='decision:D-HOSTILE',words=nodeOf(id).words;assert.ok(words.length>20000);
  const labels=all(app.node(id),e=>e.tag==='text').map(e=>e.textContent);
  assert.ok(labels.every(l=>Array.from(l).length<=40),'graph labels are clipped');
  assert.doesNotMatch(labels.join(''),/[\u0000-\u001f\u202a-\u202e\u2066-\u2069]/,'control and direction characters never reach a label');
  await app.select(id);
  assert.ok(text(app.aside()).includes(words),'the full recorded words are in the details, as text');
  await app.tab('Quadro');
  assert.ok(text(app.doc.getElementById('overview')).includes('<script>alert(1)</script> Pezzo chiuso'));
  assert.ok(text(app.doc.getElementById('overview')).includes('<img src=x onerror=alert(2)>'));
  const tags=new Set(app.doc.created.map(e=>e.tag).filter(t=>!t.startsWith('#')));
  for(const tag of tags)assert.ok(ALLOWED.has(tag),'unexpected element '+tag);
 },
 async cycle(){
  const looped=clone(base),now=new Date().toISOString();
  for(const [id,seq] of [['decision:LOOP-A',9001],['decision:LOOP-B',9002]])
   looped.graph.nodes.push({id,kind:'coordinator-decision',seq,hash:'0'.repeat(64),actor:'coord',utc:now,artifacts:[],tasks:[],reason:'loop '+id,follows:[]});
  looped.graph.edges.push({from:'decision:LOOP-A',to:'decision:LOOP-B',kind:'follows',recorded:true},{from:'decision:LOOP-B',to:'decision:LOOP-A',kind:'follows',recorded:true});
  looped.graph.cycles=['decision:LOOP-A','decision:LOOP-B'];
  const app=await boot({fixture:looped,language:'it-IT'});await app.tab('Decisioni');
  const box=one(app.content(),e=>hasClass(e,'dec-unordered'),'listing of unordered links');
  assert.match(text(box),/Collegamenti non ordinabili/);
  assert.deepEqual(all(box,e=>e.tag==='li').map(li=>li.getAttribute('data-node-id')),['decision:LOOP-B','decision:LOOP-A']);
  assert.equal(all(app.svg(),e=>e.tag==='g'&&e.getAttribute('data-node-id')).length,base.graph.nodes.length,'every orderable node is still drawn');
  one(box,e=>e.tag==='button').onclick();await settle();
  assert.equal(app.aside().getAttribute('data-node-id'),'decision:LOOP-B');
 },
 async phone(){
  assert.match(styles,/\.dec-list-box\{display:none\}/);
  assert.match(styles,/@media \(max-width:700px\)\{\.dec-graph\{display:none\}\.dec-list-box\{display:block\}/);
  assert.match(styles,/@media \(prefers-reduced-motion: reduce\)\{\.dec-new\{animation:none\}/);
  assert.match(styles,/@media \(prefers-color-scheme: light\)\{:root\{color-scheme:light;/);
  const app=await boot({language:'it-IT'});await app.tab('Decisioni');
  const box=one(app.content(),e=>hasClass(e,'dec-list-box'));assert.equal(box.children[0].textContent,'Elenco · più recenti in alto');
  const list=one(box,e=>e.tag==='ol'&&hasClass(e,'dec-list'));
  assert.deepEqual(list.children.map(li=>li.getAttribute('data-node-id')),base.graph.nodes.slice().sort((a,b)=>b.seq-a.seq).map(n=>n.id));
  const item=list.children.find(li=>li.getAttribute('data-node-id')==='decision:D-SCOPE');
  assert.match(text(item),/Istruzione del proprietario · riferito da un'altra finestra/);
  assert.match(text(item),/Arriva da: .*segue/);
  one(item,e=>e.tag==='button').onclick();await settle();
  assert.equal(app.aside().getAttribute('data-node-id'),'decision:D-SCOPE');
  const large=clone(base),top=Math.max(...large.graph.nodes.map(n=>n.seq));
  for(let i=1;i<=320;i++)large.graph.nodes.push({id:'decision:BULK-'+i,kind:'coordinator-decision',seq:top+i,hash:'1'.repeat(64),actor:'coord',utc:new Date().toISOString(),artifacts:[],tasks:[],reason:'bulk '+i,follows:[]});
  await app.refresh(large);
  assert.equal(all(app.svg(),e=>e.tag==='g'&&e.getAttribute('data-node-id')).length,300);
  assert.match(text(app.content()),new RegExp('Mostrati gli ultimi 300 elementi su '+large.graph.nodes.length));
  one(app.content(),e=>e.tag==='button'&&e.textContent==='Mostra tutto').onclick();await settle();
  assert.equal(all(app.svg(),e=>e.tag==='g'&&e.getAttribute('data-node-id')).length,large.graph.nodes.length);
 },
 async channels(){
  const real=id=>base.graph.nodes.find(n=>n.id===id);
  assert.deepEqual([real('decision:D-RELAY').channel,real('decision:D-RELAY').words],['relayed',null],'recorded by the CLI without words');
  assert.deepEqual([real('decision:D-TYPED').channel,real('decision:D-TYPED').words],['typed-here',null]);
  const variants=clone(base);let top=Math.max(...variants.graph.nodes.map(n=>n.seq));
  const add=(from,id,changes)=>{const n=Object.assign(clone(variants.graph.nodes.find(x=>x.id===from)),{id,seq:++top},changes);variants.graph.nodes.push(n)};
  add('decision:D-KEYS-GO','decision:V-RELAYED-WORDS',{channel:'relayed',words:'Sì, procedi'});
  add('decision:D-RELAY','decision:V-LEGACY-NO-WORDS',{channel:null});
  add('decision:D-KEYS-GO','decision:V-LEGACY-WORDS',{channel:null});
  const cases=[['decision:D-TYPED','typed-here',false],['decision:D-KEYS-GO','typed-here',true],['decision:D-RELAY','relayed',false],
   ['decision:V-RELAYED-WORDS','relayed',true],['decision:V-LEGACY-NO-WORDS',null,false],['decision:V-LEGACY-WORDS',null,true]];
  const words={it:{'typed-here':'scritto qui',relayed:"riferito da un'altra finestra",null:'canale non registrato',kind:'Risposta del proprietario',label:'Canale'},
   en:{'typed-here':'typed here',relayed:'relayed by another window',null:'channel not recorded',kind:"Owner's answer",label:'Channel'}};
  for(const lang of ['it','en']){
   const app=await boot({fixture:variants,language:lang});await app.tab('Decisioni');const w=words[lang];
   for(const [id,channel,hasWords] of cases){
    const n=variants.graph.nodes.find(x=>x.id===id);assert.equal(n.kind,'owner-answer');assert.equal(Boolean(n.words),hasWords,id);
    const line=w.kind+' · '+w[channel];
    assert.ok(text(app.node(id)).includes(line),'graph shows the recorded channel: '+id+' '+lang+' :: '+text(app.node(id)));
    assert.ok(hasClass(app.node(id),'dec-owner'),'an answer is an owner box: '+id);
    for(const other of Object.values({a:w['typed-here'],b:w.relayed,c:w.null}).filter(x=>x!==w[channel]))
     assert.ok(!text(app.node(id)).includes(other),'never another channel: '+id);
    await app.select(id);
    const aside=app.aside();assert.ok(text(aside).includes(line),'details kind line: '+id+' '+lang);
    const dl=one(aside,e=>e.tag==='dl'),at=dl.children.findIndex(e=>e.tag==='dt'&&e.textContent===w.label);
    assert.ok(at>=0,'details have a channel row: '+id+' '+lang);
    assert.equal(text(dl.children[at+1]),w[channel],'details channel value: '+id+' '+lang);
   }
   const withdrawal=base.graph.nodes.find(n=>n.kind==='withdrawal'&&!n.words&&!n.channel);
   assert.doesNotMatch(text(app.node(withdrawal.id)),new RegExp(w.null+'|'+w['typed-here']),'a coordinator withdrawal gets no invented owner channel');
  }
 },
 async titles(){
  for(const lang of ['it','en']){
   const app=await boot({language:lang==='it'?'it-IT':'en-US'});
   const noBrief=lang==='it'?'descrizione non ancora scritta':'description not written yet';
   const card=id=>one(app.doc.getElementById('overview'),e=>e.tag==='article'&&e.getAttribute('data-task')===id,'overview card '+id);
   assert.equal(text(one(card('K'),e=>e.tag==='h4')),'Servizio delle chiavi');
   assert.equal(text(one(card('DOC'),e=>e.tag==='h4')),'Docs');assert.ok(text(card('DOC')).includes(noBrief));
   const work=one(app.content(),e=>e.tag==='section'&&e.children[0]?.textContent===app.run("tr('connected.workNow')"),'work summary');
   const heads=root=>root.children.filter(c=>hasClass(c,'row')).map(r=>r.children[0].children[0].textContent);
   const workTitles=heads(work);
   for(const title of ['Servizio delle chiavi','Docs','Vista web per i clienti','Nuovo import dei clienti'])assert.ok(workTitles.includes(title),'work summary names '+title);
   for(const old of ['Key service','Web view','New import'])assert.ok(!workTitles.includes(old),'work summary drops the recorded title when a brief exists: '+old);
   const waiting=one(work,e=>e.tag==='section'&&e.children[0]?.textContent===app.run("tr('page.wait.title')"),'who is waiting');
   assert.ok(heads(waiting).includes('Nuovo import dei clienti')&&!heads(waiting).includes('NEW'),'next steps are named by title');
   await app.tab('Decisioni');
   assert.match(text(app.node('task:K')),/Servizio delle chiavi/);assert.doesNotMatch(text(app.node('task:K')),/Key service/);
   assert.match(text(app.node('task:DOC')),/Docs/);
   await app.tab('Incarichi');
   const rows=app.content().children[0].children.filter(c=>hasClass(c,'row'));
   const row=title=>{const r=rows.find(x=>x.children[0].children[0].textContent===title);assert.ok(r,'Incarichi row titled '+title);return r};
   const kRow=rows.find(r=>r.getAttribute('data-read-scope')==='tasks:K');assert.ok(kRow,'the K row');
   assert.equal(kRow.children[0].children[0].textContent,'Servizio delle chiavi','its heading is the brief title');
   const k=row('Servizio delle chiavi');assert.match(text(k),/Perché: i clienti ruotano le chiavi senza fermare il servizio/);
   const tech=all(k,e=>hasClass(e,'mono')&&/\bID K\b/.test(e.textContent));
   assert.equal(tech.length,1,'one technical line carries the ID');assert.match(tech[0].textContent,lang==='it'?/titolo registrato «Key service»/:/recorded title “Key service”/);
   const doc=row('Docs');assert.ok(text(doc).includes(noBrief),'honest fallback without a brief');
   assert.match(text(doc),lang==='it'?/Dipende da: Old import/:/Depends on: Old import/);
   assert.match(text(row('Old import')),lang==='it'?/Sostituito da Nuovo import dei clienti/:/Replaced by Nuovo import dei clienti/);
   for(const r of rows){const h=r.children[0].children[0].textContent;assert.doesNotMatch(h,/^[A-Z]+ \/ /,'no ID in a heading: '+h);assert.ok(!Object.keys(base.tasks).includes(h)||!base.tasks[h].brief,'no bare ID heading: '+h)}
   await app.tab('Finestre');assert.match(text(app.content()),/Servizio delle chiavi: /);
  }
 },
 async focus(){
  const app=await boot({language:'it-IT'});
  const ov=()=>app.doc.getElementById('overview');
  const termAt=key=>one(ov(),e=>hasClass(e,'term')&&e.getAttribute('data-focus-key')===key,'term '+key);
  const BODY='ov.q3|task:K|body|JEV#1',GOAL='ov.q1|project|goal|JEV#1';
  assert.notEqual(termAt(BODY),termAt(GOAL),'the same term in two places has two keys');
  const focused=()=>app.doc.activeElement&&app.doc.activeElement.getAttribute('data-focus-key');
  const before=termAt(BODY);before.focus();await app.refresh();
  assert.equal(focused(),BODY,'unchanged refresh keeps the term');assert.notEqual(app.doc.activeElement,before);
  const inserted=clone(base);inserted.tasks.K.brief.text=inserted.tasks.K.brief.text.replace('Perché:','SQ-DECISIONS prima. Perché:');
  inserted.workspace.brief.text=inserted.workspace.brief.text.replace('Atlas tiene','JEV e SQ-DECISIONS: Atlas tiene');
  await app.refresh(inserted);
  assert.equal(focused(),BODY,'a known term inserted before it in the same text');
  assert.equal(app.doc.activeElement.children[0].textContent,'JEV');
  const reordered=clone(inserted);reordered.progress.K.state={id:'closed',utc:reordered.progress.K.state.utc};
  await app.refresh(reordered);
  const cards=all(ov(),e=>e.tag==='article'&&e.getAttribute('data-task')).map(e=>e.getAttribute('data-task'));
  assert.ok(cards.indexOf('K')>cards.indexOf('W'),'the card moved');assert.equal(focused(),BODY,'the card moved, focus followed');
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.equal(focused(),BODY,'language switch keeps the term');
  assert.equal(one(ov(),e=>e.id===app.doc.activeElement.getAttribute('aria-describedby')).textContent,'job engine verification');
  const removed=clone(reordered);removed.tasks.K.brief.text=removed.tasks.K.brief.text.replace(/JEV/g,'motore');
  await app.refresh(removed);
  const active=app.doc.activeElement;assert.ok(active,'focus is placed explicitly');
  assert.ok(!hasClass(active,'term'),'never another term');
  assert.deepEqual([active.tag,active.getAttribute('data-focus-key'),active.getAttribute('tabindex')],['h3','ov.q3','-1'],'falls back to the section heading');
  assert.ok(all(ov(),e=>hasClass(e,'term')&&e.getAttribute('data-focus-key')===GOAL.replace('#1','#2')).length===1,'other JEV terms survive');
  await app.tab('Decisioni');await app.select('decision:D-SCOPE');app.node('decision:D-SCOPE').focus();
  const gone=clone(removed);gone.graph.nodes=gone.graph.nodes.filter(n=>n.id!=='decision:D-SCOPE');gone.graph.edges=gone.graph.edges.filter(e=>e.from!=='decision:D-SCOPE'&&e.to!=='decision:D-SCOPE');
  await app.refresh(gone);
  assert.equal(focused(),'dec:details','a vanished graph box falls back to the details heading');
 },
 async assignment(){
  const handoffId=base.tasks.K.delivery.handoff,handoff=base.messages[handoffId];
  assert.equal(handoff.to,'reader');
  const app=await boot({language:'it-IT'});
  const line=seat=>one(app.doc.getElementById('overview'),e=>e.getAttribute('data-seat')===seat).children.filter(c=>hasClass(c,'ov-line'))[0];
  const shown=seat=>line(seat).children[2].textContent;
  assert.match(text(line('reader')),/lettura di «Servizio delle chiavi»/);
  assert.ok(shown('reader').startsWith('consegnato per la lettura '+app.run('date('+JSON.stringify(handoff.utc)+')')),'reader: recorded handoff time :: '+shown('reader'));
  assert.ok(shown('web').startsWith('assegnato '+app.run('date('+JSON.stringify(base.progress.W.created)+')')),'builder: task creation time');
  const day=86400e3,ago=ms=>new Date(Date.now()-ms).toISOString();
  const revised=clone(base);revised.messages[handoffId].utc=ago(2*day);
  revised.progress.W.created=ago(3*day);revised.graph.nodes.find(n=>n.id==='task:W').utc=ago(3*day);revised.tasks.W.brief.utc=ago(3600e3);
  await app.refresh(revised);
  assert.match(shown('reader'),/^consegnato per la lettura .*\(2 giorni fa\)$/);
  assert.match(shown('web'),/^assegnato .*\(3 giorni fa\)$/,'a later brief revision is not the assignment time');
  assert.doesNotMatch(shown('web'),/1 h fa/);
  const named=clone(revised);delete named.tasks.K.delivery;named.progress.K.created=ago(5*day);named.graph.nodes.find(n=>n.id==='task:K').utc=ago(5*day);
  await app.refresh(named);
  assert.match(shown('reader'),/^nominato lettore .*\(5 giorni fa\)$/,'a named reader without a handoff yet');
  const unknown=clone(named);delete unknown.progress.K.created;unknown.graph.nodes.find(n=>n.id==='task:K').utc=null;
  await app.refresh(unknown);
  assert.equal(shown('reader'),'ora non registrata','a missing time is said, never borrowed from another event');
  one(app.doc.getElementById('lang'),e=>e.getAttribute('data-lang')==='en').onclick();await settle();
  assert.equal(shown('reader'),'time not recorded');assert.match(shown('web'),/^assigned .*\(3 days ago\)$/);
 },
};

(async()=>{
 assert.ok(scenarios[scenario],'unknown scenario '+scenario);
 assert.ok(scripts.length>=3,'all inline blocks are executed');
 await scenarios[scenario]();
 console.log('decisions contract passed: '+scenario);
})().catch(e=>{console.error(e);process.exitCode=1});

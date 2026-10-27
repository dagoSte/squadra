// Render recorded presence fixtures through the page scripts without a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.className='';this.attributes={}}
 setAttribute(name,value){this.attributes[name]=String(value)}
 getAttribute(name){return name in this.attributes?this.attributes[name]:null}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 set innerHTML(value){throw Error('Unexpected HTML insertion: '+value)}
}
const ids=new Map(),get=id=>{if(!ids.has(id))ids.set(id,new Element(id));return ids.get(id)};
const text=e=>e.textContent+' '+e.children.map(text).join(' ');
const visibleText=e=>e.tag==='details'?'':e.textContent+' '+e.children.map(visibleText).join(' ');
const fixtures=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
let index=0;
const timers=[];
const context={console,Date,Set,JSON,Error,
 document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(ns,tag)=>new Element(tag)},
 location:{port:'123',hash:'#fixture',pathname:'/'},sessionStorage:{setItem(){},getItem(){return 'fixture'}},
 history:{replaceState(){}},setTimeout:fn=>timers.push(fn),
 fetch:async()=>({ok:true,json:async()=>fixtures[index]})};
context.I18N={'shared.example':{it:'Testo comune',en:'Shared text'}};
context.UI_LANG='it';
const sharedTranslator=(key,vars={})=>{
 const entry=context.I18N[key];
 return entry?entry[context.UI_LANG].replace(/\{(\w+)\}/g,(match,name)=>Object.hasOwn(vars,name)?String(vars[name]):match):'⟦'+key+'⟧';
};
context.tr=sharedTranslator;
// Every inline block runs in document order in one context, the page-wide language block included.
const blocks=[...fs.readFileSync(process.argv[2],'utf8').matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].map(match=>({attributes:match[1],script:match[2]}));
assert.ok(blocks.some(b=>/id="squadra-i18n"/.test(b.attributes)),'the page-wide language block is present');
context.window=context;vm.createContext(context);
(async()=>{
 let pageTranslator=null;
 for(const block of blocks){vm.runInContext(block.script,context,{timeout:1000});if(/id="squadra-i18n"/.test(block.attributes))pageTranslator=context.tr}
 await new Promise(r=>setTimeout(r,20));
 assert.equal(typeof pageTranslator,'function','the language block defines the page-wide translator');
 assert.notEqual(pageTranslator,sharedTranslator,'the page defines its own translator');
 assert.equal(context.tr,pageTranslator,'the branch must retain the page-wide translator');
 assert.equal(context.I18N['shared.example'].en,'Shared text','merging presence labels must preserve other labels');
 const presence=()=>vm.runInNewContext('presencePanel()',context);
 assert.match(text(get('content')),/Finestre in stand-by/);
 assert.match(text(get('content')),/7s \/ 300s/);
 assert.match(text(get('content')),/conferma ricevuta \(ACK\)/,'fresh ACK does not itself clear recorded stand-by');
 get('tabs').children.find(e=>e.textContent==='Finestre').onclick();
 assert.match(text(get('content')),/Finestre in stand-by/);
 assert.match(text(get('content')),/A-alias/);
 index=1;await timers.shift()();
 assert.doesNotMatch(text(get('content')),/Finestre in stand-by/,'coordinator sweep records the return');
 assert.match(text(get('content')),/8s \/ 300s/);
 let panel=presence();
 assert.match(visibleText(panel),/Costruttore per «Synthetic change» \(Codex\)/,'task title identifies the window before a brief exists');
 assert.match(visibleText(panel),/Ha confermato la chiamata .*Coordinatore della squadra/);
 assert.doesNotMatch(visibleText(panel),/A-alias|ping-|ACK|sweep|\bactive\b/,'technical identifiers and state codes are secondary');
 assert.match(text(panel),/Dettagli tecnici/);
 assert.match(text(panel),/A-alias/,'aliases remain available in technical details');
 index=0;await timers.shift()();
 assert.match(visibleText(presence()),/In stand-by: non aveva risposto alla chiamata/);
 assert.match(visibleText(presence()),/La conferma non cambia lo stand-by/,'reply alone does not restore availability');
 index=2;await timers.shift()();
 assert.match(visibleText(presence()),/Non ha ancora confermato la chiamata/);
 assert.doesNotMatch(visibleText(presence()),/tempo di attesa è scaduto/);
 index=3;await timers.shift()();
 assert.match(visibleText(presence()),/tempo di attesa è scaduto/);
 assert.doesNotMatch(visibleText(presence()),/Finestre in stand-by/,'elapsed time alone does not suspend the window');
 index=4;await timers.shift()();
 const heldReviewer=presence().children.find(e=>visibleText(e).includes('Revisore in attesa di incarico (Claude) — In stand-by'));
 assert.ok(heldReviewer,'manual hold describes the role without requiring an assignment');
 assert.match(visibleText(heldReviewer),/In stand-by per decisione del coordinatore/);
 assert.doesNotMatch(visibleText(heldReviewer),/non aveva risposto/,'manual hold must not invent a missed call');
 const details=heldReviewer.children.find(e=>e.tag==='details');details.open=true;details.ontoggle();
 await timers.shift()();
 assert.ok(presence().children.find(e=>visibleText(e).includes('Revisore in attesa di incarico (Claude) — In stand-by')).children.find(e=>e.tag==='details').open,'technical detail stays open after a refresh');
 fixtures[index].tasks.T.brief='Servizio delle chiavi\nPerché: proteggere le chiavi.';
 await timers.shift()();
 assert.match(visibleText(presence()),/Costruttore per «Servizio delle chiavi»/,'brief first line takes precedence over task title');
 fixtures[index].tasks.T.brief={text:'# Chiavi <script>alert(1)</script>\nDescrizione'};
 await timers.shift()();
 assert.match(visibleText(presence()),/Chiavi <script>alert\(1\)<\/script>/,'brief remains text; innerHTML is forbidden by the DOM fixture');
 vm.runInNewContext("UI_LANG='en';render()",context);
 let english=presence();
 assert.match(visibleText(english),/Availability · replies and stand-by/);
 assert.match(visibleText(english),/Builder for “Chiavi <script>alert\(1\)<\/script>” \(Codex\)/,'recorded brief text is not translated');
 assert.match(visibleText(english),/Reviewer awaiting an assignment \(Claude\) — In stand-by by the coordinator's decision/);
 assert.doesNotMatch(visibleText(english),/Presenza|Finestr|Coordinatore|conferma|⟦|\{(?:time|sender|role)\}/);
 assert.match(text(english),/Technical details/);
 assert.ok(english.children.find(e=>visibleText(e).includes('Reviewer awaiting an assignment (Claude) — In stand-by')).children.find(e=>e.tag==='details').open,'expanded details survive a language change');
 index=0;await timers.shift()();
 assert.match(visibleText(presence()),/had not answered the call recorded at/);
 assert.match(visibleText(presence()),/Confirmation does not end stand-by/);
 index=1;await timers.shift()();
 assert.doesNotMatch(visibleText(presence()),/Windows in stand-by/);
 assert.match(visibleText(presence()),/Confirmed at .* the call recorded at .* Team coordinator/);
 index=2;await timers.shift()();
 assert.match(visibleText(presence()),/Has not yet confirmed/);
 assert.doesNotMatch(visibleText(presence()),/waiting period has expired/);
 index=3;await timers.shift()();
 assert.match(visibleText(presence()),/waiting period has expired/);
 for(const [key,entry] of Object.entries(context.I18N).filter(([key])=>key.startsWith('presence.'))){
  assert.deepEqual(Object.keys(entry).sort(),['en','it']);
  assert.deepEqual([...entry.it.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort(),[...entry.en.matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort(),key);
 }
 assert.equal(context.tr('presence.missing'),'⟦presence.missing⟧');
 vm.runInNewContext("UI_LANG='it';render()",context);
 assert.match(visibleText(presence()),/Presenza · risposte e stand-by/);
 fixtures[index].agents.A.replaces=['old-window'];
 fixtures[index].presence.find(a=>a.agent==='A').service_wait={active:true,until:'2026-10-26T18:30:00+00:00',cause:'429'};
 await timers.shift()();
 assert.match(visibleText(presence()),/A \(sostituisce old-window\)/);
 assert.match(visibleText(presence()),/in attesa del servizio fino alle.*429/);
 vm.runInNewContext("UI_LANG='en';render()",context);
 assert.match(visibleText(presence()),/A \(replaces old-window\)/);
 assert.match(visibleText(presence()),/waiting for service until.*429/);
 fixtures[index].presence.find(a=>a.agent==='A').service_wait.active=false;
 await timers.shift()();
 assert.doesNotMatch(visibleText(presence()),/waiting for service until/);
 console.log('Presence dashboard: real replay histories, shared dictionary, Italian/English rendering, unchanged recorded content, preserved details and server age passed');
})().catch(e=>{console.error(e);process.exitCode=1});

// Run page scripts in order with a simplified DOM and recorded state snapshots.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [page,fixture]=process.argv.slice(2);
const snapshots=JSON.parse(fs.readFileSync(fixture,'utf8'));
const ROW=40;let scrollY=0;const scrolls=[];
const detach=n=>{if(n.parentNode){const s=n.parentNode.children;s.splice(s.indexOf(n),1);n.parentNode=null}};
class Text{constructor(v){this.textContent=String(v);this.parentNode=null;this.children=[]}}
const adopt=(parent,n)=>{if(typeof n==='string')n=new Text(n);detach(n);n.parentNode=parent;return n};
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.parentNode=null;this.own='';this.className='';this.attributes={};this.style={};this.hidden=false;this.root=false}
  get textContent(){return this.own+this.children.map(c=>c.textContent).join('')}
  set textContent(v){this.own=String(v);for(const c of [...this.children])detach(c)}
  set innerHTML(v){throw Error('Untrusted HTML insertion: '+v)}
  append(...c){for(const n of c)this.children.push(adopt(this,n))}
  prepend(...c){this.children.unshift(...c.map(n=>adopt(this,n)))}
  insertBefore(n,ref){if(!ref)return this.append(n);n=adopt(this,n);this.children.splice(this.children.indexOf(ref),0,n);return n}
  replaceChildren(...c){for(const x of [...this.children])detach(x);this.own='';this.append(...c)}
  replaceWith(n){const p=this.parentNode,i=p.children.indexOf(this);detach(this);n=adopt(p,n);p.children.splice(i,0,n)}
  remove(){detach(this)}
  contains(n){for(;n;n=n.parentNode)if(n===this)return true;return false}
  get firstChild(){return this.children[0]??null}
  get nextSibling(){if(!this.parentNode)return null;const s=this.parentNode.children;return s[s.indexOf(this)+1]??null}
  get isConnected(){let n=this;while(n.parentNode)n=n.parentNode;return n.root}
  setAttribute(k,v){this.attributes[k]=String(v)}
  getAttribute(k){return this.attributes[k]??null}
  focus(){context.document.activeElement=this}
  showModal(){this.open=true}
  close(){this.open=false}
  getBoundingClientRect(){return layout(this)}
}
const ids=new Map(),get=id=>{if(!ids.has(id)){const e=new Element(id);e.root=true;ids.set(id,e)}return ids.get(id)};
const descendants=e=>[e,...(e.children||[]).flatMap(descendants)];
let current=snapshots.first,failedFetch=false;const timers=[];
const context={console,
  document:{getElementById:get,createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t),activeElement:null},
  location:{port:'12345',hash:'#test-token',pathname:'/'},sessionStorage:{setItem(){},getItem(){return 'test-token'}},
  history:{replaceState(){}},setTimeout:fn=>{timers.push(fn)},innerHeight:800,scrollBy:(x,y)=>{scrollY+=y;scrolls.push(y)},
  fetch:async(path,opts)=>{assert.equal(opts.headers['X-Squadra-Token'],'test-token');if(failedFetch)throw Error('controlled offline');
    return {ok:true,json:async()=>JSON.parse(JSON.stringify(current)),text:async()=>''}}};
// The page-wide language block reads window.I18N, window.UI_LANG and window.tr.
context.window=context;
vm.createContext(context);
const ui=()=>vm.runInContext('commsUI',context);
// Layout model: the notice adds 40 px above #content, each sibling before the root 100 px, each row 40 px,
// and an opened row whose message text is shown 2000 px (taller than the 800 px screen).
const heightOf=li=>{const r=[...ui().rows.values()].find(x=>x.li===li);return r&&!r.gone&&r.bodyButton?.getAttribute('aria-expanded')==='true'?2000:ROW};
function layout(el){
  const parts=ui().parts;
  if(parts&&el===parts.lanes)return {top:0,bottom:60};
  if(parts&&el.parentNode===parts.list){let top=(get('notice').children.length?40:0)+get('content').children.indexOf(ui().root)*100+300-scrollY;
    for(const li of parts.list.children){if(li===el)break;top+=heightOf(li)}return {top,bottom:top+heightOf(el)}}
  return {top:0,bottom:0};
}
const place=(li,top)=>{scrollY=0;scrollY=layout(li).top-top};
const html=fs.readFileSync(page,'utf8');
const blocks=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
assert.ok(blocks.length>=1,'inline script blocks found');
const refresh=async()=>{await timers.shift()()};
const rowOf=(message,kind='message')=>[...ui().rows.values()].find(r=>r.edge.message===message&&r.edge.kind===kind);
const rowWhere=test=>[...ui().rows.values()].find(r=>test(r.edge));
const firstLines=()=>[...[...ui().rows.values()].filter(r=>!r.gone).map(r=>r.sentence),...ui().parts.pairList.children.map(li=>li.textContent),
  ...ui().parts.lanes.children.map(c=>c.textContent),...Object.values(ui().parts.selects).flatMap(s=>s.children.map(o=>o.textContent))];
const technical=/seat|window-|\bm-|ping-|evt|SQ-|MS-|#\d|\b[0-9a-f]{7,}\b/;
(async()=>{
  for(const block of blocks)vm.runInContext(block,context,{timeout:1000});
  await new Promise(r=>setTimeout(r,20));
  get('tabs').children.find(e=>e.textContent==='Comunicazioni').onclick();
  const root=ui().root,parts=ui().parts,content=get('content');
  assert.equal(content.children[0],root,'the view owns one container inside #content');
  assert.equal(root.id,'comms-root');
  assert.match(root.textContent,/Qui vedi chi parla con chi/);
  assert.match(root.textContent,/Chi parla con chi/);
  const guide=root.children.find(c=>c.className==='comms-guide');assert.equal(guide.open,true,'the reading guide is open the first time');
  assert.match(guide.textContent,/Conferma di lettura.*«ACK»/);
  assert.equal(parts.list.children.length,66,'every communication edge of the fixture is a row');
  assert.equal(parts.lanes.children.length,5,'one lane per window plus the unregistered recipient');
  assert.match(parts.lanes.children[2].textContent,/in stand-by/);
  assert.match(parts.lanes.children[4].textContent,/Finestra non registrata/);
  // Plain words, one sentence per row.
  assert.match(rowOf('m-assign').sentence,/^Il coordinatore \(Claude\) ha assegnato al costruttore \(Codex\).*l'incarico intitolato «Live view of inter-window communication \(parallel\)» — confermato alle \d\d:\d\d/);
  assert.match(rowOf('m-assign','ack').sentence,/ha confermato di aver letto il messaggio del coordinatore \(Claude\)/);
  assert.match(rowOf('m-queued').sentence,/messo nella coda di Codex: lettura non provata; il destinatario è in stand-by/);
  assert.match(rowOf('m-queued','transport').sentence,/ha messo il messaggio nella coda di Codex del costruttore \(Codex\)/);
  assert.match(rowOf('m-late').sentence,/nessuna conferma da 16 minuti \(attesa normale: 15 minuti\)/);
  assert.equal(rowOf('m-late').text.children[0].textContent,'!','overdue carries a mark besides the words');
  assert.match(rowOf('m-xss').sentence,/^Il revisore \(Claude\) ha scritto al coordinatore \(Claude\) \(messaggio senza incarico collegato\) — inviato, non ancora letto\.$/);
  assert.match(rowOf('m-alias').sentence,/ha scritto all'esecutore \(Codex\)/);
  assert.match(rowOf('ping-1','ping').sentence,/ha mandato una chiamata di presenza al costruttore \(Codex\)/);
  assert.match(rowWhere(e=>e.kind==='availability').sentence,/ha messo in stand-by il costruttore \(Codex\).*: non ha risposto alla chiamata delle \d\d:\d\d/);
  assert.match(rowWhere(e=>e.kind==='unknown').sentence,/un evento di un tipo che questa vista non conosce/);
  assert.match(rowOf('m-awaited').sentence,/a una finestra non registrata.*il destinatario non è ancora registrato/);
  for(const line of firstLines())assert.doesNotMatch(line,technical,'no id, event number or hash in a first line: '+line);
  assert.equal(rowOf('m-queued').li.className,'comms-row state-queued');
  assert.match(rowOf('m-late').strip.children.at(-1).attributes['marker-end'],/comms-head-red/);
  // D-SEARCH-COMMS: body matches use IDs; the displayed text still stays on demand.
  assert.doesNotMatch(root.textContent,/zebraword/,'no body before it is asked for');
  get('search').oninput({target:{value:'zebraword'}});
  assert.equal(parts.list.children.length,1,'a body-only word finds exactly its message');assert.equal(parts.list.children[0],rowOf('m-xss').li);
  assert.doesNotMatch(parts.list.textContent,/zebraword/,'matching a body does not display it in the result');
  get('search').oninput({target:{value:'chiamata di presenza'}});
  assert.equal(parts.list.children.length,1,'search reads the sentence');assert.equal(parts.list.children[0],rowOf('ping-1','ping').li);
  get('search').oninput({target:{value:'ping-1'}});
  assert.equal(parts.list.children.length,1,'recorded ping ID remains searchable');assert.equal(parts.list.children[0],rowOf('ping-1','ping').li);
  get('search').oninput({target:{value:''}});
  const xss=rowOf('m-xss');xss.button.onclick();
  assert.equal(xss.button.getAttribute('aria-expanded'),'true');assert.equal(xss.detail.hidden,false);
  assert.match(xss.detail.textContent,/Consegna: posta condivisa del registro/);
  assert.match(xss.detail.textContent,/Dettagli tecnici: evento n\. \d+ · tipo message .*messaggio m-xss/);
  assert.doesNotMatch(root.textContent,/zebraword/);
  const show=xss.detail.children.find(c=>c.textContent==='Mostra il testo del messaggio');show.onclick();
  assert.match(xss.detail.textContent,/<img src=x onerror=alert\(1\)> zebraword/,'the body is shown as text');
  parts.selects.count.value='100';parts.selects.count.onchange();
  guide.open=false;
  // Keep the reading position when refresh inserts rows above the visible messages.
  const before=new Map([...ui().rows].map(([id,r])=>[id,r.li]));
  const header=new Element('section');content.prepend(header);
  const anchorLi=parts.list.children[30];place(anchorLi,200);const anchorTop=layout(anchorLi).top;
  current=snapshots.second;await refresh();
  assert.equal(ui().root,root,'the same container survives a refresh');assert.equal(content.children[0],root);assert.equal(header.parentNode,null);
  for(const [id,li] of before)assert.equal(ui().rows.get(id).li,li,'existing rows are the same objects');
  for(const id of ['m-new-1','m-new-2']){assert.match(rowOf(id).li.textContent,/nuova/);assert.match(rowOf(id).li.className,/fresh/)}
  assert.equal([...ui().rows.values()].filter(r=>/fresh/.test(r.li.className)).length,2,'only the two new rows are marked');
  assert.equal(parts.live.textContent.startsWith('2 nuove comunicazioni'),true);
  assert.equal(xss.button.getAttribute('aria-expanded'),'true','an opened row stays open');assert.match(xss.detail.textContent,/zebraword/);
  assert.equal(parts.selects.count.value,'100','filters survive');assert.equal(guide.open,false,'the guide stays as the reader left it');
  assert.ok(Math.abs(layout(anchorLi).top-anchorTop)<=1,'the reader\'s row keeps its place: '+layout(anchorLi).top+' vs '+anchorTop);
  assert.ok(scrolls.length>0,'the compensation scrolled');
  // Offline notice written outside render(), then cleared inside it: the row keeps its place both ways.
  const rows=parts.list.children.length;
  failedFetch=true;await refresh();
  assert.match(get('notice').textContent,/Ultima vista conservata/);assert.equal(parts.list.children.length,rows,'offline keeps the view');
  assert.ok(Math.abs(layout(anchorLi).top-anchorTop)<=1,'offline notice is compensated');
  failedFetch=false;await refresh();
  assert.equal(get('notice').children.length,0);assert.ok(Math.abs(layout(anchorLi).top-anchorTop)<=1,'cleared notice is compensated');
  // Keep reading position, focus and open state inside a tall message
  // when rows or connection notices appear above it.
  place(xss.li,-500);assert.ok(layout(xss.li).bottom>800,'the opened message is taller than the screen');
  context.document.activeElement=xss.bodyButton;const tallTop=layout(xss.li).top;
  assert.equal(vm.runInContext('commsAnchor()',context)?.li,xss.li,'the anchor is the row being read');
  current=snapshots.third;await refresh();
  assert.ok(rowOf('m-new-3')&&rowOf('m-new-4'),'two rows arrived above');
  assert.ok(Math.abs(layout(xss.li).top-tallTop)<=1,'rows inserted above a tall opened row: '+layout(xss.li).top+' vs '+tallTop);
  failedFetch=true;await refresh();assert.ok(Math.abs(layout(xss.li).top-tallTop)<=1,'notice appears above a tall opened row');
  failedFetch=false;await refresh();assert.ok(Math.abs(layout(xss.li).top-tallTop)<=1,'notice clears above a tall opened row');
  assert.equal(context.document.activeElement,xss.bodyButton,'focus stays');assert.equal(xss.button.getAttribute('aria-expanded'),'true');assert.match(xss.detail.textContent,/zebraword/);
  scrollY=0;
  // Rows that leave the filter: kept while opened or focused, marked, then released.
  const alias=rowOf('m-alias');context.document.activeElement=alias.button;
  parts.selects.period.value='900';parts.selects.period.onchange();
  current=snapshots.aged;await refresh();
  assert.deepEqual(parts.list.children.map(li=>[...ui().rows.values()].find(r=>r.li===li).edge.message).sort(),['m-alias','m-xss']);
  assert.equal(xss.out.hidden,false);assert.equal(alias.out.hidden,false);assert.match(alias.li.textContent,/fuori dal filtro scelto/);
  context.document.activeElement=null;await refresh();
  assert.deepEqual(parts.list.children.map(li=>[...ui().rows.values()].find(r=>r.li===li).edge.message),['m-xss']);
  parts.selects.period.value='0';parts.selects.period.onchange();
  current=snapshots.second;await refresh();
  const filler=rowOf('m-fill-00');filler.button.onclick();
  parts.selects.count.value='50';parts.selects.count.onchange();
  assert.ok(parts.list.children.includes(filler.li),'an opened row pushed out by Quanti stays');assert.equal(filler.out.hidden,false);
  // A row whose event is no longer loaded becomes an explicit notice that takes the focus.
  const hide=xss.detail.children.find(c=>c.tag==='button');context.document.activeElement=hide;
  current=snapshots.bounded;parts.selects.count.value='200';parts.selects.count.onchange();await refresh();
  assert.match(xss.li.textContent,/Elemento non più presente/);assert.equal(context.document.activeElement,xss.li);
  // The retained notice follows the language without new nodes, focus or position.
  const goneText=xss.goneText,goneClose=xss.goneClose;
  vm.runInContext("UI_LANG='en'",context);await refresh();
  assert.match(xss.li.textContent,/Item no longer present/);assert.equal(goneClose.textContent,'Close');
  assert.equal(xss.goneText,goneText);assert.ok(xss.li.children.includes(goneClose));assert.equal(xss.li.parentNode,parts.list);
  assert.equal(context.document.activeElement,xss.li,'focus stays on the retained notice');
  vm.runInContext("UI_LANG='it'",context);await refresh();
  assert.match(xss.li.textContent,/Elemento non più presente/);assert.equal(goneClose.textContent,'Chiudi');assert.equal(context.document.activeElement,xss.li);
  xss.li.children.find(c=>c.textContent==='Chiudi').onclick();assert.equal(xss.li.parentNode,null);
  // Conversations summary: a pair filters the timeline; tab switches keep the same container.
  current=snapshots.second;filler.button.onclick();await refresh();
  const pair=parts.pairList.children[0].children[0].children[0];
  assert.match(pair.getAttribute('aria-label'),/^Il coordinatore \(Claude\) e il revisore \(Claude\): 57 messaggi — 0 confermati, 1 senza conferma oltre l'attesa normale, 56 in attesa/);
  pair.onclick();
  assert.ok(parts.list.children.length>0&&[...ui().rows.values()].filter(r=>r.li.parentNode).every(r=>[r.edge.from_lane,r.edge.to_lane].sort().join()==='window-c,window-r'));
  assert.equal(parts.pairList.children[0].children[0].children[0].getAttribute('aria-pressed'),'true');assert.equal(parts.pairReset.hidden,false);
  parts.pairReset.onclick();
  get('tabs').children.find(e=>e.textContent==='Posta').onclick();assert.equal(root.parentNode,null);
  get('tabs').children.find(e=>e.textContent==='Comunicazioni').onclick();assert.equal(content.children[0],root);
  // Check matching Italian and English placeholders and complete translation keys.
  const dictionary=vm.runInContext('I18N',context),keys=Object.keys(dictionary).filter(k=>k.startsWith('comms.'));
  assert.ok(keys.length>100,'the view reads its labels from the shared dictionary');
  for(const k of keys){const e=dictionary[k],vars=t=>[...String(t).matchAll(/\{(\w+)\}/g)].map(m=>m[1]).sort().join();
    assert.ok(e.it&&e.en,'both languages for '+k);assert.equal(vars(e.it),vars(e.en),'same placeholders in '+k)}
  assert.doesNotMatch(root.textContent,/⟦/);
  vm.runInContext("UI_LANG='en'",context);await refresh();
  rowOf('m-late').button.onclick();
  assert.doesNotMatch(root.textContent,/⟦/,'no missing key in English');
  assert.match(root.textContent,/How to read this view/);assert.doesNotMatch(root.textContent,/Come leggere/);
  assert.match(parts.live.textContent,/new communications? · updated at/,'the announcement follows the language');
  assert.match(rowOf('m-assign').sentence,/^The coordinator \(Claude\) assigned the task titled “Live view of inter-window communication \(parallel\)” to the builder \(Codex\)/);
  assert.match(rowOf('m-late').detail.textContent,/No confirmation for 16 minutes; the normal wait is 15 minutes\./);
  assert.match(parts.pairList.children[0].children[0].children[0].getAttribute('aria-label'),/^The coordinator \(Claude\) and the reviewer \(Claude\): 57 messages/);
  for(const line of firstLines())assert.doesNotMatch(line,technical,'no id in an English first line: '+line);
  vm.runInContext("UI_LANG='it'",context);await refresh();
  assert.match(root.textContent,/Come leggere questa vista/);assert.match(rowOf('m-late').detail.textContent,/Nessuna conferma da 16 minuti/);
  assert.match(parts.live.textContent,/nuov[ae] comunicazion[ei] · aggiornato alle/);
  console.log('comms contract passed: plain sentences, no ids in first lines, states in words, bodies on demand, search without bodies, keyed refresh, place kept through rows and notices, filter retention, gone notice, tall opened row, pairs, tab switch, Italian and English with retained nodes');
})().catch(e=>{console.error(e);process.exitCode=1});

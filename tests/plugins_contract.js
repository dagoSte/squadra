// Reuse the panel DOM model with synthetic plugin replies.
// Measure viewport geometry and reject unsafe HTML insertion.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const fetchBefore='json:async()=>clone(app.fixture)';
if(!prefix.includes(fetchBefore))throw Error('base harness seam changed');
prefix=prefix.replace(fetchBefore,"json:async()=>clone(path.startsWith('/api/plugin/')?await app.reply(path):app.fixture)");
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
prefix=prefix.replace('const context={console,Date:PageDate', 'const context={scrollBy:(x,y)=>{doc.scroll=(doc.scroll||0)+y;app.scrolled=(app.scrolled||[]).concat(y)},innerHeight:600,console,Date:PageDate');
const cases=String.raw`
Element.prototype.getBoundingClientRect=function(){
 const parent=this.doc.getElementById('content').children[0];
 const cards=(parent?.children||[]).filter(e=>e.getAttribute('data-plugin-item'));
 const i=cards.indexOf(this);return i<0?{top:0,bottom:0}:{top:i*60-(this.doc.scroll||0),bottom:(i+1)*60-(this.doc.scroll||0)};
};
const payload=()=>({schema:1,updated:'2026-10-11T12:00:00Z',summary:{it:'Sommario',en:'Summary'},note:{it:'Nota',en:'Note'},items:['draft','sent','approved','rejected','withheld','error'].map((status,i)=>({id:String(i),title:'<img src=x onerror=alert(1)> '+i,text:'https://example.invalid/ <script>literal</script>',status,meta:[{label:{it:'Origine',en:'Source'},value:'<b>literal</b>'}]}))});
async function setup(opts={}){
 const app=await boot(opts);app.doc.scroll=0;app.payload=payload();
 app.reply=async url=>({name:url.split('/').at(-1),key:app.fixture.plugins.key,panel:clone(app.payload)});
 app.select=async(name='example')=>{const b=app.doc.getElementById('plugin-links').children.find(b=>key(b)==='nav:plugin:'+name);assert.ok(b,'plugin button');b.onclick();await settle()};
 app.pluginKeys=()=>all(app.doc.getElementById('content'),e=>key(e)!==null).map(key);
 return app;
}
const tests={
 async render(){
  const app=await setup();assert.deepEqual(app.doc.getElementById('tabs').children.map(b=>b.textContent),TABS);
  assert.equal(app.fetched.length,1,'discovery is in state, not an extra fetch');
  await app.select();const content=app.doc.getElementById('content');
  assert.match(text(content),/Sommario.*Nota.*<img src=x onerror=alert\(1\)>.*https:\/\/example.invalid/);
  assert.deepEqual(all(content,e=>e.className==='badge').map(e=>e.textContent),['Bozza','Inviato','Approvato','Rifiutato','Trattenuto','Errore']);
  assert.equal(all(content,e=>['a','img','iframe','script'].includes(e.tag)).length,0,'plugin text cannot create links or code');
  assert.match(text(content),/<b>literal<\/b>/);assert.equal(app.fetched.at(-1),'/api/plugin/example');
  app.run("setLanguage('en')");await settle();assert.match(text(content),/Summary.*Note.*Source/);
  assert.deepEqual(all(content,e=>e.className==='badge').map(e=>e.textContent),['Draft','Sent','Approved','Rejected','Withheld','Error']);
  const empty=clone(base);empty.plugins={plugins:[],refused:[],key:base.plugins.key};await app.refresh(empty);
  assert.match(text(app.doc.getElementById('plugin-notices')),/No plugins installed/);
  assert.match(text(content),/no longer available/);assert.doesNotMatch(text(content),/<script>literal/);
 },
 async refresh(){
  const app=await setup();for(let i=0;i<3;i++)await app.refresh();assert.equal(app.fetched.length,4,'base polling remains one request per cycle');
  await app.select();const old=app.doc.getElementById('content').children[0];await app.refresh();
  assert.equal(app.doc.getElementById('content').children[0],old,'unchanged content retains the same DOM');
  app.payload.items[0].text='Changed with the same updated timestamp';await app.refresh();
  assert.match(text(app.doc.getElementById('content')),/Changed with the same updated timestamp/);
  await app.tab('Quadro');const before=app.fetched.filter(p=>p.startsWith('/api/plugin/')).length;await app.refresh();
  assert.equal(app.fetched.filter(p=>p.startsWith('/api/plugin/')).length,before,'closed view never fetches panel');
  const next=clone(app.fixture);next.plugins.plugins.push({...clone(next.plugins.plugins[0]),name:'other',title:{it:'Altro',en:'Other'}});await app.refresh(next);
  const pending={};app.reply=url=>new Promise(resolve=>{pending[url]=resolve});
  await app.select();await app.select('other');
  pending['/api/plugin/example']({name:'example',key:next.plugins.key,panel:payload()});await settle();
  assert.doesNotMatch(text(app.doc.getElementById('content')),/<script>literal/,'late previous selection is discarded');
  const other=payload();other.summary.it='Only the selected plugin';pending['/api/plugin/other']({name:'other',key:next.plugins.key,panel:other});await settle();
  assert.match(text(app.doc.getElementById('content')),/Only the selected plugin/);
  const changed=clone(next);changed.plugins.key='f'.repeat(64);await app.refresh(changed);
  assert.doesNotMatch(text(app.doc.getElementById('content')),/Only the selected plugin/,'registry change clears previous content immediately');
  pending['/api/plugin/other']({name:'other',key:next.plugins.key,panel:other});await settle();
  assert.match(text(app.doc.getElementById('content')),/non disponibili o rifiutati/);
 },
 async shell(){
  const app=await setup({stored:{'squadra-sidebar':'collapsed','squadra-theme':'dark'}});
  await app.select();let b=app.doc.getElementById('plugin-links').children[0];
  assert.equal(b.getAttribute('title'),'Esempio');assert.equal(b.getAttribute('aria-current'),'page');assert.match(app.doc.getElementById('app').className,/collapsed/);
  assert.equal(app.doc.documentElement.getAttribute('data-theme'),'dark');b.focus();await app.refresh();
  assert.equal(key(app.doc.activeElement),'nav:plugin:example','nav focus survives poll');
  app.run("setLanguage('en')");await settle();b=app.doc.getElementById('plugin-links').children[0];assert.equal(b.getAttribute('title'),'Example');
  assert.equal(key(app.doc.activeElement),'nav:plugin:example');app.doc.getElementById('theme').children[1].onclick();await settle();
  assert.equal(app.doc.documentElement.getAttribute('data-theme'),'light');
  const phone=await setup({phone:true});const menu=phone.doc.getElementById('menu');menu.onclick();await settle();await phone.select();
  assert.doesNotMatch(phone.doc.getElementById('app').className,/drawer-open/);assert.equal(phone.doc.activeElement,menu);
  menu.onclick();await settle();assert.equal(key(phone.doc.activeElement),'nav:plugin:example');await phone.keydown('Escape');
  assert.equal(phone.doc.activeElement,menu);assert.equal(menu.getAttribute('aria-expanded'),'false');
 },
 async place(){
  const app=await setup();app.payload.items=Array.from({length:500},(_,i)=>({...payload().items[0],id:String(i)}));await app.select();
  app.doc.scroll=12005;const oldScroll=app.doc.scroll;const before=app.doc.getElementById('content').children[0];
  app.payload.items.unshift({...payload().items[0],id:'inserted'});app.payload.items.pop();await app.refresh();
  assert.notEqual(app.doc.getElementById('content').children[0],before);assert.equal(app.doc.scroll,oldScroll+60,'reading anchor stays at its old viewport position');
  const card=all(app.doc.getElementById('content'),e=>e.getAttribute('data-plugin-item')===JSON.stringify(['300',0]))[0];card.focus();
  const top=card.getBoundingClientRect().top;app.payload.items.unshift({...payload().items[0],id:'second'});app.payload.items.pop();await app.refresh();
  assert.equal(app.doc.activeElement.getAttribute('data-plugin-item'),JSON.stringify(['300',0]));assert.equal(app.doc.activeElement.getBoundingClientRect().top,top,'focus/place survive a changed large panel');
  app.payload.items=app.payload.items.filter(i=>i.id!=='300');await app.refresh();assert.equal(key(app.doc.activeElement),'plugin:heading','deleted focused card falls back to its own heading');
  assert.equal(new Set(app.pluginKeys()).size,app.pluginKeys().length,'focus keys are unique');
 },
 async refusals(){
  const app=await setup();app.payload=null;await app.select();assert.match(text(app.doc.getElementById('content')),/Nessun dato per questo registro/);
  app.run("setLanguage('en')");await settle();assert.match(text(app.doc.getElementById('content')),/No data for this registry/);
  const next=clone(app.fixture);next.plugins.refused=[{name:'<img src=x>',code:'bad_name'}];await app.refresh(next);
  assert.match(text(app.doc.getElementById('plugin-notices')),/<img src=x>: refused \(bad_name\)/);
  app.reply=async()=>{throw Error('synthetic denied')};await app.refresh();assert.match(text(app.doc.getElementById('content')),/unavailable or refused/);
  assert.doesNotMatch(text(app.doc.getElementById('content')),/synthetic denied/);
 }
};
(async()=>{assert.ok(tests[scenario],'known scenario');await tests[scenario]();console.log('plugins contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

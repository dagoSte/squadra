// Exercise the actual scripts with the shared DOM; no catalogue fetch on a poll.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/plugin-store'?await app.reply():app.fixture)");
prefix=prefix.replace('const context={console,Date:PageDate','const context={URL,scrollBy:(x,y)=>{doc.scroll=(doc.scroll||0)+y},console,Date:PageDate');
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
Element.prototype.getBoundingClientRect=function(){
 const root=this.doc.getElementById('content'),cards=all(root,e=>e.getAttribute('data-store-item'));
 const i=cards.indexOf(this);return i<0?{top:0,bottom:0}:{top:i*300-(this.doc.scroll||0),bottom:(i+1)*300-(this.doc.scroll||0)};
};
async function setup(opts={}){
 const app=await boot(opts);app.data=clone(payload);app.reply=async()=>clone(app.data);
 app.content=app.doc.getElementById('content');app.nav=()=>app.doc.getElementById('plugin-links').children.find(b=>key(b)==='nav:store');
 app.select=async()=>{assert.ok(app.nav(),'Store always in Plugin group');app.nav().onclick();await settle()};
 app.reload=async()=>{one(app.content,e=>key(e)==='store:refresh').onclick();await settle()};
 app.cards=()=>all(app.content,e=>e.getAttribute('data-store-item'));return app;
}
const tests={
 async playwright(){
  const app=await setup();await app.select();
  const matching=app.cards().filter(c=>c.getAttribute('data-store-item')==='playwright');
  assert.equal(matching.length,1);
  assert.ok(app.cards().some(c=>c.getAttribute('data-store-item')==='memoranda'));
  assert.deepEqual(app.cards().slice(0,2).map(c=>c.getAttribute('data-store-item')),['memoranda','playwright']);
  const rows=payload.plugins.filter(p=>p.name==='playwright');assert.equal(rows.length,1);
  const row=rows[0];
  assert.match(text(app.content),/Playwright.*Controlli del browser/);
  assert.ok(text(app.content).includes(row.install_command));
  assert.equal(row.receipt.state,'complete');
  app.run("setLanguage('en')");await settle();
  assert.match(text(app.content),/Playwright.*Browser checks/);
  assert.match(text(app.content),/Installed.*Installation complete.*Receipt release:/);
  for(let i=0;i<2;i++)await app.refresh();
  assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,1);
 },
 async labels(){
  const app=await setup();assert.equal(app.fetched.length,1);await app.select();
  assert.match(text(app.content),/Plugin inclusi.*Release in esecuzione.*Autore.*Versione inclusa.*Licenza.*Prezzo.*Gratuito.*Basato su MCP.*Sì.*Data della revisione.*Comando di installazione/);
  const link=one(app.content,e=>e.tag==='a');assert.equal(link.getAttribute('href'),payload.plugins[0].about.homepage);
  assert.equal(link.getAttribute('target'),'_blank');assert.equal(link.getAttribute('rel'),'noopener noreferrer');assert.match(link.getAttribute('aria-label'),/example.invalid/);
  assert.deepEqual(all(app.content,e=>e.tag==='button').map(e=>e.textContent),['Aggiorna catalogo'],'no install button');
  assert.equal(all(app.content,e=>['img','iframe','script','form'].includes(e.tag)).length,0);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/Plugins bundled.*Running release.*Publisher.*Bundled version.*Licence.*Price.*Free.*MCP-based.*Yes.*Review date.*Install command/);
  assert.deepEqual(all(app.content,e=>e.tag==='button').map(e=>e.textContent),['Refresh catalogue']);
  assert.ok(text(app.content).includes(payload.plugins[0].install_command));assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,1);
 },
 async xss(){
  const app=await setup(),evil='<img src=x onerror=alert(1)>',p=app.data.plugins[0];
  p.title={it:evil,en:evil};p.description={it:evil,en:evil};p.about.publisher=evil;p.about.version=evil;p.store.licence=evil;p.install_command=evil;p.about.homepage='javascript:alert(1)';
  app.data.refused=[{name:evil,code:evil}];await app.select();assert.ok(text(app.content).includes(evil));
  assert.equal(all(app.content,e=>['a','img','iframe','script'].includes(e.tag)).length,0,'unsafe text remains literal');
  for(const link of ['http://example.invalid','https://user:pass@example.invalid','https://example.invalid/#fragment','https://example.invalid/\nattack']){
   p.about.homepage=link;await app.reload();assert.equal(all(app.content,e=>e.tag==='a').length,0,'unsafe link refused');
  }
  p.about.homepage='https://example.invalid/safe';await app.reload();assert.equal(all(app.content,e=>e.tag==='a').length,1,'HTTPS positive control');
 },
 async order(){
  const app=await setup(),p=app.data.plugins[0];app.data.plugins=[
   {...clone(p),name:'plain',title:{it:'AAA',en:'AAA'},store:{...p.store,mcp:false}},
   {...clone(p),name:'alpha',title:{it:'Zeta',en:'Apple'}}, {...clone(p),name:'beta',title:{it:'Alfa',en:'Zebra'}}];
  await app.select();assert.deepEqual(app.cards().map(e=>e.getAttribute('data-store-item')),['beta','alpha','plain']);
  app.run("setLanguage('en')");await settle();assert.deepEqual(app.cards().map(e=>e.getAttribute('data-store-item')),['alpha','beta','plain']);
 },
 async fetches(){
  const app=await setup();for(let i=0;i<3;i++)await app.refresh();assert.equal(app.fetched.length,4,'original polling count unchanged');
  await app.select();const element=app.content.children[0],loading=app.run('JSON.stringify(loadUI)');
  for(let i=0;i<3;i++)await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,1);
  assert.equal(app.content.children[0],element,'poll keeps cached Store DOM');
  app.run("setLanguage('en')");await settle();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,1);
  await app.reload();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,2,'explicit refresh only');
  await app.tab('Quadro');await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,2);
  await app.select();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,3,'entry reloads installed state');
  assert.equal(app.run('loadUI.error'),null);assert.equal(app.doc.getElementById('loading').hidden,true);
 },
 async states(){
  const app=await setup();let resolve;app.reply=()=>new Promise(r=>{resolve=r});await app.select();
  assert.match(text(app.content),/Caricamento del catalogo/);assert.equal(one(app.content,e=>key(e)==='store:refresh').disabled,true);
  assert.equal(app.doc.getElementById('loading').hidden,true,'Store wait is not initial registry loading');
  await app.refresh();assert.equal(app.fetched.filter(p=>p==='/api/plugin-store').length,1);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/Loading catalogue/);
  resolve({...clone(payload),plugins:[]});await settle();assert.match(text(app.content),/This release has no Store plugins/);
  app.run("setLanguage('it')");await settle();assert.match(text(app.content),/Questa release non include plugin/);
  app.reply=async()=>{throw Error('PRIVATE raw error')};await app.reload();assert.match(text(app.content),/Catalogo non disponibile/);assert.doesNotMatch(text(app.content),/PRIVATE/);
  app.run("setLanguage('en')");await settle();assert.match(text(app.content),/Catalogue unavailable/);assert.equal(app.run('loadUI.error'),null);
  app.reply=async()=>({wrong:1});await app.reload();assert.match(text(app.content),/Catalogue unavailable/);
  app.reply=async()=>({...clone(payload),refused:[{name:'bad',code:'invalid_bundle'}]});await app.reload();assert.match(text(app.content),/Entry excluded: bad \(invalid_bundle\)/);
  app.reply=()=>new Promise(r=>{resolve=r});await app.reload();await app.tab('Quadro');resolve(clone(payload));await settle();
  assert.equal(app.run('tab'),'Quadro');assert.equal(all(app.content,e=>e.getAttribute('data-store-item')).length,0,'late Store reply does not change view');
 },
 async receipts(){
  const app=await setup({language:'en-US'}),p=app.data.plugins[0];p.installed=true;p.managed=false;p.installation_error='unmanaged_installation';await app.select();
  assert.match(text(app.content),/Installed.*Folder not managed/);
  assert.doesNotMatch(text(app.content),/Local status could not be verified|unmanaged_installation/);
  app.run("setLanguage('it')");await settle();assert.match(text(app.content),/Cartella non gestita/);
  assert.doesNotMatch(text(app.content),/Stato locale non verificabile|unmanaged_installation/);
  app.run("setLanguage('en')");await settle();delete p.installation_error;
  p.managed=true;p.receipt={state:'upgrading',version:'0.5.0',release:{version:'3.0.0',id:'a'.repeat(64)}};await app.reload();
  assert.match(text(app.content),/Installation incomplete.*Installed version: 0.5.0.*Receipt release: 3.0.0.*different release/);
  p.receipt={state:'complete',version:p.about.version,release:clone(app.data.release)};await app.reload();
  assert.match(text(app.content),/Installation complete/);assert.doesNotMatch(text(app.content),/different release/);
  p.installation_error='unsafe_permissions';await app.reload();assert.match(text(app.content),/Local status could not be verified \(unsafe_permissions\)/);
  app.run("setLanguage('it')");await settle();assert.match(text(app.content),/Installato.*Installazione completa.*Versione installata.*Release della ricevuta.*Stato locale non verificabile/);
 },
 async shell(){
  const app=await setup({stored:{'squadra-sidebar':'collapsed'}});await app.select();
  assert.equal(app.nav().getAttribute('title'),'Store');assert.equal(app.nav().getAttribute('aria-current'),'page');assert.match(app.doc.getElementById('app').className,/collapsed/);
  app.nav().focus();await app.refresh();assert.equal(key(app.doc.activeElement),'nav:store');
  const link=one(app.content,e=>e.tag==='a');link.focus();await app.refresh();assert.equal(app.doc.activeElement,link);
  app.run("setLanguage('en')");await settle();assert.equal(key(app.doc.activeElement),'store:home:example');
  const phone=await setup({phone:true}),menu=phone.doc.getElementById('menu');menu.onclick();await settle();await phone.select();
  assert.doesNotMatch(phone.doc.getElementById('app').className,/drawer-open/);assert.equal(phone.doc.activeElement,menu);
  menu.onclick();await settle();assert.equal(key(phone.doc.activeElement),'nav:store');await phone.keydown('Escape');assert.equal(phone.doc.activeElement,menu);
 },
 async place(){
  const app=await setup(),p=app.data.plugins[0];app.data.plugins=Array.from({length:100},(_,i)=>({...clone(p),name:'p'+i,title:{it:String(i).padStart(3,'0'),en:String(i).padStart(3,'0')}}));
  await app.select();app.doc.scroll=9050;const root=app.content.children[0];await app.refresh();assert.equal(app.content.children[0],root);assert.equal(app.doc.scroll,9050);
  const card=app.cards()[30];card.focus();const top=card.getBoundingClientRect().top;
  app.data.plugins.unshift({...clone(p),name:'first',title:{it:'000-first',en:'000-first'}});await app.reload();
  assert.equal(key(app.doc.activeElement),key(card));assert.equal(app.doc.activeElement.getBoundingClientRect().top,top);
  app.doc.activeElement=null;const before=app.doc.scroll;app.data.plugins.shift();await app.reload();assert.equal(app.doc.scroll,before-300,'unfocused reading anchor retained');
  const keys=all(app.content,e=>key(e)!==null).map(key);assert.equal(new Set(keys).size,keys.length);
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('store contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

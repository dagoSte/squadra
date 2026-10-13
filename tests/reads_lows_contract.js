// Exercise the page with attachment-aware scroll semantics; Chrome measures real layout separately.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/reads'?await app.reply():app.fixture)");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
Object.defineProperty(Element.prototype,'scrollLeft',{
 get(){return this.savedScroll||0},
 set(value){this.savedScroll=[...walk(this.doc.getElementById('content'))].includes(this)?value:0}
});
async function setup(phone=false){
 const app=await boot({phone});app.data=clone(payload.reads);app.reply=async()=>app.data;
 await app.tab('Letture');app.content=app.doc.getElementById('content');
 app.bump=async()=>{const next=clone(app.fixture);next.event_count++;next.registry_head={seq:next.event_count,hash:'next-'+next.event_count};app.data.head=next.registry_head;await app.refresh(next)};
 return app;
}
const tests={
 async scroll(){
  const app=await setup(),first=app.find('reads:table');first.scrollLeft=180;first.onscroll();
  assert.equal(first.scrollLeft,180,'attached positive control scrolls');
  first.focus();await app.bump();const second=app.find('reads:table');
  assert.notEqual(first,second,'changed head replaces the wrapper');
  assert.equal(second.scrollLeft,180,'restore scroll only after attachment');
  assert.equal(key(app.doc.activeElement),'reads:table','scroll-region focus retained');
  await app.refresh();assert.equal(app.find('reads:table'),second,'unchanged head keeps the DOM');
  second.scrollLeft=75;second.onscroll();await app.bump();assert.equal(app.find('reads:table').scrollLeft,75);
 },
 async cards(){
  const app=await setup(true),keys=['reads','accepted','changes-required','blocked','codeRevisions','planRevisions','findings'];
  for(const lang of ['it','en']){
   app.run('setLanguage('+JSON.stringify(lang)+')');await settle();
   const table=one(app.content,e=>hasClass(e,'sq-reads-table')),rows=all(table,e=>hasClass(e,'sq-reads-task'));
   assert.equal(rows.length,payload.reads.tasks.length,'one card per task');
   assert.equal(all(table,e=>e.getAttribute('scope')==='col').length,8,'accessible column headings retained');
   for(const row of rows){
    const cells=row.children.filter(e=>e.tag==='td');assert.equal(cells.length,7,'all values retained');
    assert.deepEqual(cells.map(e=>e.getAttribute('data-label')),keys.map(k=>app.run('tr('+JSON.stringify('rv.'+k)+')')),'localized card labels');
    assert.deepEqual(cells.map(e=>e.textContent),['3','2','1','0','0','1','1'],'recorded counts retained');
    const button=one(row,e=>e.tag==='button');assert.equal(key(button),'reads:task:T');
   }
  }
  const css=page.match(/<style id="squadra-reads">([\s\S]*?)<\/style>/)[1];
  assert.match(css,/@media\(max-width:600px\)\{\s*\.sq-reads \.sq-table-wrap\{overflow:visible\}/,'phone wrapper does not clip cards');
  assert.match(css,/\.sq-reads-table,\.sq-reads-table \.sq-reads-task\{display:block;min-width:0\}/,'phone rows stack');
  assert.match(css,/\.sq-reads-table td::before\{content:attr\(data-label\)/,'phone values show their labels');
  assert.match(css,/grid-template-columns:minmax\(0,1fr\) auto/,'long labels can shrink and wrap');
  assert.match(css,/overflow-wrap:anywhere/);assert.doesNotMatch(css,/overflow-x:(hidden|clip)/);
  app.find('reads:task:T').onclick();await settle();assert.equal(app.find('reads:task').value,'T');
 },
 async gap(){
  const app=await setup();
  const children=app.content.children[0].children,at=children.findIndex(e=>hasClass(e,'sq-lede'));
  assert.match(children[at+1].textContent,/Dati verificati/,'data line follows intro without an empty grid row');
  assert.equal(all(app.content,e=>e.getAttribute('role')==='status'&&!e.textContent).length,0);
  assert.match(page,/\.sq-reads>p\{margin:0\}/,'grid gap is the only inter-paragraph spacing');
  app.reply=async()=>{throw Error('synthetic offline')};await app.bump();
  assert.match(one(app.content,e=>e.getAttribute('role')==='status').textContent,/Letture non aggiornate/,'real status remains announced');
 },
 async markers(){
  const fixture=clone(base),manifest=clone(payload.plugin);delete manifest.icon;
  fixture.plugins={key:'a'.repeat(64),refused:[],plugins:[manifest]};
  const app=await boot({fixture});one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:memoranda').onclick();await settle();
  for(const lang of ['it','en']){
   app.run('setLanguage('+JSON.stringify(lang)+')');await settle();const root=app.doc.getElementById('content');
   const numbered=all(root,e=>hasClass(e,'sq-plugin-numbered'));assert.equal(numbered.length,1,'only the already-numbered steps lose bullets');
   assert.equal(numbered[0].getAttribute('role'),'list','unmarked list retains accessible semantics');
   assert.deepEqual(numbered[0].children.map(e=>e.textContent),manifest.about.sections.find(s=>s.id==='how').items.map(i=>i[lang]),'step text and order unchanged');
   assert.ok(all(root,e=>e.tag==='ul'&&!hasClass(e,'sq-plugin-numbered')).length>=2,'ordinary lists keep bullets');
  }
  assert.match(page,/\.sq-plugin-section ul\.sq-plugin-numbered\{list-style:none;padding-left:0\}/,'one marker, already in the text');
  const changed=clone(app.fixture);changed.plugins.plugins[0].about.sections.find(s=>s.id==='how').items[1]={it:'Una voce ordinaria',en:'An ordinary item'};
  await app.refresh(changed);assert.equal(all(app.doc.getElementById('content'),e=>hasClass(e,'sq-plugin-numbered')).length,0,'mixed lists are not silently renumbered');
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('reads lows passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

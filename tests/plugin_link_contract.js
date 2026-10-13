// Check plugin links with the page scripts, shared DOM model and synthetic data.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
for(const [before,after] of [
 ['const context={console,','const context={URL,console,'],
 ['json:async()=>clone(app.fixture)',"json:async()=>clone(path.startsWith('/api/plugin/')?await app.reply(path):app.fixture)"]
]){if(!prefix.includes(before))throw Error('base harness seam changed');prefix=prefix.replace(before,after)}
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=';
const HOME='https://example.invalid/start?from=squadra';
const hostile='<img src=x onerror=alert(1)> https://other.invalid/';
const content=app=>app.doc.getElementById('content');
const nav=app=>app.doc.getElementById('plugin-links');
const images=root=>all(root,e=>e.tag==='img');
const links=root=>all(root,e=>e.tag==='a');
async function setup({icon=PNG,homepage=HOME,about=true,...opts}={}){
 const fixture=clone(base);fixture.plugins={key:'a'.repeat(64),refused:[],plugins:[{schema:1,name:'example',title:{it:'Esempio',en:'Example'},description:{it:hostile,en:hostile},icon,
  ...(about?{about:{publisher:hostile,version:'1.0',tagline:{it:hostile,en:hostile},homepage,sections:[{id:'purpose',title:{it:'Titolo',en:'Title'},text:{it:hostile,en:hostile}}]}}:{})}]};
 const app=await boot({...opts,fixture});app.reply=async()=>({name:'example',key:fixture.plugins.key,panel:{schema:1,updated:'2026-10-11T12:00:00Z',summary:{it:hostile,en:hostile},items:[{id:'one',title:hostile,text:hostile,status:'draft',meta:[]}]}});
 one(nav(app),e=>key(e)==='nav:plugin:example').onclick();await settle();return app;
}
function logo(app,title='Esempio'){
 for(const [root,size] of [[content(app),64],[nav(app),18]]){
  const found=images(root);assert.equal(found.length,1,'one logo at each location');
  assert.equal(found[0].getAttribute('src'),PNG);assert.equal(found[0].getAttribute('alt'),title+' logo');
  assert.equal(found[0].getAttribute('width'),String(size));assert.equal(found[0].getAttribute('height'),String(size));
  assert.notEqual(found[0].getAttribute('aria-hidden'),'true','logo alt remains accessible');
 }
}
function homepage(app,url=HOME){
 const found=links(content(app));assert.equal(found.length,1,'only the declared homepage is linked');const a=found[0];
 assert.equal(a.textContent,new URL(url).host+' ↗');assert.equal(a.getAttribute('href'),url);assert.equal(a.getAttribute('target'),'_blank');assert.equal(a.getAttribute('rel'),'noopener noreferrer');
 assert.equal(key(a),'plugin:homepage');return a;
}
const tests={
 async header_sidebar(){
  const app=await setup();logo(app);const a=homepage(app);assert.equal(a.getAttribute('aria-label'),'Visita example.invalid (nuova scheda)');
  assert.ok(text(content(app)).includes(hostile),'producer content remains literal');assert.equal(app.fetched.filter(p=>p.startsWith('/api/plugin/')).length,0);
  const custom='https://docs.example.invalid:8443/guide?q=one';homepage(await setup({homepage:custom}),custom);
 },
 async icon_sources(){
  for(const icon of [undefined,null,123,'','https://example.invalid/logo.png','/api/icon','//example.invalid/x','data:image/svg+xml;base64,PHN2Zy8+','data:image/jpeg;base64,AAAA','data:image/png;foo=bar;base64,AAAA','data:image/png;base64,AA AA','data:image/png;base64,AAAA\n']){
   const app=await setup({icon:icon===undefined?null:icon});assert.equal(images(content(app)).length+images(nav(app)).length,0,'refuse non-PNG source '+icon);
   const mark=one(content(app),e=>e.className==='sq-plugin-mark');assert.ok(all(mark,e=>e.tag==='svg').length,'header generic fallback');assert.ok(all(nav(app),e=>e.tag==='svg').length,'sidebar generic fallback');
  }
 },
 async homepage_rejections(){
  for(const homepage of [null,123,{},'','http://example.invalid','javascript:alert(1)','data:text/html,hello','ftp://example.invalid','//example.invalid','https://','https:example.invalid','https:///example.invalid','https://@example.invalid','https://example.invalid\\path','https://user@example.invalid','https://user:pass@example.invalid','https://example.invalid/#','https://example.invalid/#section','https://example.invalid/ space','https://example.invalid/\n','https://example.invalid/\u0080','https://example.invalid/\u202e','https://example.invalid/'+ 'a'.repeat(200)]){
   const app=await setup({homepage});assert.equal(links(content(app)).length,0,'refuse homepage '+JSON.stringify(homepage));
  }
  const valid='HTTPS://example.invalid/';homepage(await setup({homepage:valid}),valid);
 },
 async languages_themes_phone(){
  const app=await setup({stored:{'squadra-theme':'dark'},phone:true});logo(app);homepage(app);
  app.run("setLanguage('en')");await settle();logo(app,'Example');assert.equal(homepage(app).getAttribute('aria-label'),'Visit example.invalid (new tab)');assert.doesNotMatch(text(content(app)),/⟦/);
  assert.equal(app.doc.documentElement.getAttribute('data-theme'),'dark');app.doc.getElementById('theme').children[1].onclick();await settle();logo(app,'Example');homepage(app);assert.equal(app.doc.documentElement.getAttribute('data-theme'),'light');
  assert.doesNotMatch(app.doc.getElementById('app').className,/drawer-open/);
 },
 async refresh_focus(){
  const app=await setup();homepage(app).focus();await app.refresh();assert.equal(key(app.doc.activeElement),'plugin:homepage','homepage focus survives polling');logo(app);
  app.run("setLanguage('en')");await settle();assert.equal(key(app.doc.activeElement),'plugin:homepage');logo(app,'Example');
  const next=clone(app.fixture);next.plugins.plugins[0].about.homepage='https://changed.invalid/';await app.refresh(next);homepage(app,'https://changed.invalid/');
  const gone=clone(next);delete gone.plugins.plugins[0].about.homepage;delete gone.plugins.plugins[0].icon;await app.refresh(gone);
  assert.equal(links(content(app)).length,0);assert.equal(images(content(app)).length+images(nav(app)).length,0);assert.equal(key(app.doc.activeElement),'plugin:heading','removed link uses local heading fallback');
  await app.refresh(next);logo(app,'Example');homepage(app,'https://changed.invalid/');
 },
 async legacy_and_literal(){
  const old=await setup({about:false});logo(old);assert.equal(links(content(old)).length,0,'no homepage without about');assert.ok(text(content(old)).includes(hostile));
  const noIcon=await setup({about:false,icon:null});assert.equal(images(content(noIcon)).length+images(nav(noIcon)).length,0);assert.ok(text(content(noIcon)).includes(hostile));
  const app=await setup();one(content(app),e=>key(e)==='plugin:tab:memories').onclick();await settle();logo(app);homepage(app);
  assert.ok(text(content(app)).includes(hostile));assert.equal(all(content(app),e=>['script','iframe'].includes(e.tag)).length,0);
  await app.refresh();assert.ok(app.fetched.every(p=>p==='/api/state'||p==='/api/plugin/example'),'no remote requests for logo or homepage');
 }
};
(async()=>{assert.ok(tests[scenario],'known scenario');await tests[scenario]();console.log('plugin link contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

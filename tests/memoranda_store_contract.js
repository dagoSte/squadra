// Real page scripts and real synthetic Store/settings/panel projections, no browser claim.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
prefix=prefix.replace('json:async()=>clone(app.fixture)',"json:async()=>clone(path==='/api/plugin-store'?payload.catalogue:path.startsWith('/api/plugin/')?payload.panel:app.fixture)");
prefix=prefix.replace('const context={console,Date:PageDate','const context={URL,console,Date:PageDate');
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
(async()=>{
 for(const language of ['it-IT','en-US'])for(const phone of [false,true])for(const theme of ['light','dark']){
  const app=await boot({language,phone,stored:{'squadra-theme':theme}}),en=language==='en-US';
  const content=app.doc.getElementById('content');
  one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:store').onclick();await settle();
  const card=one(content,e=>e.getAttribute('data-store-item')==='memoranda');
  assert.match(text(card),en?/Free trial, then paid/:/Prova gratuita, poi a pagamento/);
  assert.match(text(card),en?/Free plugin; memoranda.dev service/:/Plugin gratuito; servizio memoranda.dev/);
  assert.match(text(card),/MIT/);assert.doesNotMatch(text(card),/⟦/);
  one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:memoranda').onclick();await settle();
  assert.match(text(content),en?/Free plugin, service trial/:/Plugin gratuito, servizio in prova/);
  one(content,e=>key(e)==='plugin:tab:memories').onclick();await settle();
  assert.ok(text(content).includes('<img src=x onerror=alert(1)>'),'hostile declaration stays text');
  assert.equal(all(content,e=>['iframe','script'].includes(e.tag)).length,0);
  const images=all(content,e=>e.tag==='img');assert.equal(images.length,1,'only the bundled logo');
  assert.ok(images[0].getAttribute('src')===app.fixture.plugins.plugins.find(p=>p.name==='memoranda').icon,'exact embedded PNG from validated projection');
  assert.ok(text(content).includes(en?'Automatic sends':'Invii automatici'));
  one(content,e=>key(e)==='plugin:tab:settings').onclick();await settle();
  const password=app.find('settings:key:input');assert.equal(password.getAttribute('type'),'password');
  assert.equal(password.value,'');assert.ok(!text(content).includes('SENTINEL-SECRET'));
  password.focus();await app.refresh();assert.equal(app.doc.activeElement,password);
  assert.ok(app.fetched.every(p=>['/api/state','/api/plugin-store','/api/plugin/memoranda'].includes(p)));
 }
 console.log('memoranda Store DOM: 8 variants passed');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

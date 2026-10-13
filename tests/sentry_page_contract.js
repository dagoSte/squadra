// Real dashboard scripts with the installed bundle's manifest and generated panel.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
prefix=prefix.replace('const context={console,','const context={URL,TextEncoder,console,');
prefix=prefix.replace('fetch:async path=>{app.fetched.push(path);return','fetch:async (path,options={})=>{app.fetched.push(path);if(app.http)return app.http(path,options);return');
prefix=prefix.replaceAll("throw Error('Untrusted HTML insertion", "assert.fail('Untrusted HTML insertion");
const cases=String.raw`
const payload=JSON.parse(fs.readFileSync(casesPath,'utf8'));
const content=app=>app.doc.getElementById('content');
const reply=data=>({ok:true,status:200,json:async()=>clone(data)});
(async()=>{
 for(const language of ['it-IT','en-US']){
  const app=await boot({language,fixture:clone(base)}),english=language==='en-US';app.posts=[];
  app.http=async(path,options)=>{
   if(options.method==='POST'){
    assert.equal(path,'/api/plugin/sentry/action/analyse');app.posts.push(JSON.parse(options.body));
    return reply({saved:true,request:{ids:app.posts.at(-1).ids,requested:'2026-10-13T07:00:00Z'}});
   }
   return reply(path==='/api/state'?app.fixture:{name:'sentry',key:app.fixture.plugins.key,panel:payload});
  };
  one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:sentry').onclick();await settle();
  assert.match(text(content(app)),english?/Development version/:/Versione di sviluppo/);
  assert.equal(all(content(app),e=>key(e)==='plugin:tab:settings').length,0);
  const tab=one(content(app),e=>key(e)==='plugin:tab:results');
  assert.equal(tab.textContent,english?'App errors':'Errori dell’app');tab.onclick();await settle();
  assert.match(text(content(app)),english?/Linked to a task/:/Collegato a un incarico/);
  assert.match(text(content(app)),english?/Ready to request analysis/:/Analisi da richiedere/);
  assert.match(text(content(app)),/unresolved/);assert.doesNotMatch(text(content(app)),/⟦/);
  const boxes=all(content(app),e=>e.getAttribute('type')==='checkbox');assert.equal(boxes.length,1);
  boxes[0].checked=true;boxes[0].onchange();await settle();
  const send=one(content(app),e=>key(e)==='plugin:action:send');
  assert.equal(send.textContent,english?'Request analysis':'Richiedi analisi');assert.equal(send.disabled,false);
  await send.onclick();await settle();assert.deepEqual(app.posts,[{ids:[payload.items.find(x=>x.selectable).id]}]);
  const links=all(content(app),e=>e.getAttribute('data-plugin-ref'));assert.equal(links.length,1);
  links[0].onclick();await settle();assert.equal(app.doc.getElementById('story').open,true);
  assert.match(text(app.doc.getElementById('story')),/Analyse observed checkout failure/);
  assert.ok(app.fetched.every(p=>p==='/api/state'||p==='/api/plugin/sentry'||p==='/api/plugin/sentry/action/analyse'));
  assert.equal(all(content(app),e=>['img','script','iframe'].includes(e.tag)).length,0);
 }
 console.log('Sentry dashboard JS contract passed: IT EN requests and task links');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

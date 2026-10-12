'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
const prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const cases=String.raw`
(async()=>{
 const fixture=clone(base);fixture.plugins={plugins:[],refused:[],key:'a'.repeat(64)};
 const app=await boot({fixture,stored:{'squadra-sidebar':'collapsed'}});
 if(scenario==='footer'){
  const check=language=>{
   const footer=app.doc.getElementById('footer'),link=one(footer,e=>e.tag==='a');
   assert.match(text(footer).replace(/\s+/g,' '),language==='it'?/Sviluppato da Stefano D'Agostino · GitHub/:/Developed by Stefano D'Agostino · GitHub/);
   assert.equal(link.textContent,'GitHub');assert.equal(link.getAttribute('href'),'https://github.com/dagoSte/squadra');assert.equal(link.getAttribute('target'),'_blank');assert.equal(link.getAttribute('rel'),'noopener noreferrer');
  };
  const originalLink=one(app.doc.getElementById('footer'),e=>e.tag==='a');originalLink.focus();check('it');app.run("setLanguage('en')");await settle();check('en');await app.refresh();check('en');assert.equal(one(app.doc.getElementById('footer'),e=>e.tag==='a'),originalLink);assert.equal(app.doc.activeElement,originalLink,'footer focus survives polling');
 }else if(scenario==='empty'){
  const getButton=()=>one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:empty');
  let b=getButton();assert.equal(b.getAttribute('title'),'Plugin');assert.equal(b.getAttribute('aria-label'),'Plugin');
  b.onclick();await settle();assert.equal(getButton().getAttribute('aria-current'),'page');
  const content=app.doc.getElementById('content');assert.match(text(content),/Nessun plugin installato.*Un plugin aggiunge.*\$HOME\/\.squadra\/plugins\/<name>\/.*HOME dell’utente che avvia squadra dash.*README/);
  assert.equal(all(content,e=>['input','form','a','script','iframe'].includes(e.tag)).length,0,'empty page neither installs nor embeds');
  getButton().focus();await app.refresh();assert.equal(key(app.doc.activeElement),'nav:plugin:empty');
  app.run("setLanguage('en')");await settle();assert.equal(getButton().getAttribute('title'),'Plugins');assert.match(text(content),/No plugins installed.*A plugin adds.*HOME of the user running squadra dash/);
  assert.ok(app.fetched.every(p=>p==='/api/state'),'empty page makes no plugin request');
  const installed=clone(fixture);installed.plugins.plugins=[{name:'example',title:{it:'Esempio',en:'Example'},description:{it:'Dati',en:'Data'}}];
  await app.refresh(installed);assert.match(text(content),/Select a plugin from the menu/);assert.doesNotMatch(text(content),/No plugins installed/);
  assert.ok(app.fetched.every(p=>p==='/api/state'),'discovery does not open the installed plugin');await app.refresh(fixture);assert.match(text(content),/No plugins installed/);
  const phone=await boot({fixture,phone:true}),menu=phone.doc.getElementById('menu');menu.onclick();await settle();
  one(phone.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:empty').onclick();await settle();
  assert.doesNotMatch(phone.doc.getElementById('app').className,/drawer-open/);assert.equal(phone.doc.activeElement,menu);
  menu.onclick();await settle();assert.equal(key(phone.doc.activeElement),'nav:plugin:empty');await phone.keydown('Escape');assert.equal(phone.doc.activeElement,menu);
 }else throw Error('unknown scenario');
 console.log('comms shell passed: '+scenario);
})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',prefix+cases)(require,process);

'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
let harness=sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const a=harness.indexOf('class Element{'),b=harness.indexOf('function* walk',a);
const dom=String.raw`
const layout={width:360,glyph:8.5,line:24};
const detached=e=>{if(e.parentNode){const p=e.parentNode;p.children.splice(p.children.indexOf(e),1);e.parentNode=null}};
class TextNode{constructor(value){this.textContent=String(value);this.nodeType=3;this.children=[];this.parentNode=null}}
class Element{
 constructor(doc,tag,ns){this.doc=doc;this.tag=tag;this.ns=ns;this.nodeType=1;this.children=[];this.attributes={};this.own='';this.className='';this.style={};this.parentNode=null;doc.created.push(this)}
 get childNodes(){return [...(this.own?[new TextNode(this.own)]:[]),...this.children]}
 get textContent(){return this.own}
 set textContent(value){this.own=String(value??'');for(const c of [...this.children])detached(c)}
 append(...children){for(let c of children){if(typeof c==='string')c=new TextNode(c);detached(c);c.parentNode=this;this.children.push(c)}}
 prepend(...children){for(const c of children.reverse())this.insertBefore(c,this.firstChild)}
 insertBefore(c,ref){if(!ref)return this.append(c);detached(c);c.parentNode=this;this.children.splice(this.children.indexOf(ref),0,c)}
 replaceChildren(...children){if(this.contains(this.doc.activeElement))this.doc.activeElement=null;for(const c of [...this.children])detached(c);this.own='';this.append(...children)}
 replaceWith(c){const p=this.parentNode;p.insertBefore(c,this);this.remove()}
 remove(){detached(this)}
 contains(e){for(;e;e=e.parentNode)if(e===this)return true;return false}
 get firstChild(){return this.children[0]||null}
 get nextSibling(){return this.parentNode?.children[this.parentNode.children.indexOf(this)+1]||null}
 get isConnected(){let e=this;while(e.parentNode)e=e.parentNode;return [...this.doc.ids.values()].includes(e)}
 setAttribute(k,v){this.attributes[k]=String(v)}
 getAttribute(k){return this.attributes[k]??null}
 set innerHTML(v){assert.fail('HTML insertion: '+v)}
 set outerHTML(v){assert.fail('HTML insertion: '+v)}
 insertAdjacentHTML(){assert.fail('HTML insertion')}
 focus(){this.doc.activeElement=this}
 blur(){if(this.doc.activeElement===this)this.doc.activeElement=null}
 showModal(){this.open=true}
 close(){this.open=false;this.onclose?.()}
 get invisible(){for(let c=this;c;c=c.parentNode){if(c.hidden)return true;const p=c.parentNode;if(p?.tag==='details'&&!p.open&&c.tag!=='summary')return true}return false}
 get lines(){return text(this).split('\n').reduce((n,s)=>n+Math.max(1,Math.ceil([...s].length*layout.glyph/layout.width)),0)}
 get scrollHeight(){return this.invisible?0:this.lines*layout.line}
 get clientHeight(){return this.invisible?0:(hasClass(this,'sq-readable')?Math.min(3,this.lines):this.lines)*layout.line}
 getBoundingClientRect(){return {top:0,bottom:this.clientHeight,width:layout.width,height:this.clientHeight}}
}
`;
harness=harness.slice(0,a)+dom+harness.slice(b);
harness=harness.replace('const context={console,','const context={URL,TextEncoder,innerHeight:900,scrollBy(){},getComputedStyle:e=>({lineHeight:e.measuredLineHeight??layout.line+"px"}),console,');
harness=harness.replace('const timers=[],writes=[]',"doc.fonts={addEventListener(t,f){doc.fontLoaded=f}};const timers=[],writes=[]");
harness=harness.replace('addEventListener(){},',"addEventListener(t,f){(app.listeners||(app.listeners={}))[t]=f},requestAnimationFrame(f){(app.frames||(app.frames=[])).push(f);return app.frames.length},");
const cases=String.raw`
const literal='<img src=x onerror=alert(1)> & <script>bad()</script>\n'+('Long recorded text 🪁 '.repeat(90)).trim();
const live=app=>app.run('[...readUI.items.values()]');
const findText=(app,value)=>live(app).filter(i=>app.run('readableValue')(i.element)===value).sort((a,b)=>Number(a.button.hidden)-Number(b.button.hidden))[0];
const full=(app,e)=>app.run('readableValue')(e);
const frame=async app=>{while(app.frames?.length)app.frames.shift()();await settle()};
const dialog=app=>app.doc.getElementById('read-dialog');
const close=app=>app.doc.getElementById('read-close');
const press=(app,key,shiftKey=false)=>{const event={key,shiftKey,prevented:false,preventDefault(){this.prevented=true},stopPropagation(){}};dialog(app).onkeydown(event);return event};
async function setup(){
 const fixture=clone(base);fixture.tasks['TASK-OTHER'].brief={text:'Short title\n'+literal};
 fixture.outputs.find(o=>o.task==='TASK-OTHER').next=literal;
 const app=await boot({fixture});await settle();return app;
}
const tests={
 async ink(){
  const app=await setup();
  for(const [tag,css,line,box] of [['h1','sq-hero-title',39.1,39],['h1','sq-hero-title',33.35,33],['h2','plugin-title',32.2,32],['p','',24,24],['p','',3,3]]){
   const element=app.doc.createElement(tag);element.className=css;element.textContent='A short title';element.measuredLineHeight=line+'px';
   app.doc.getElementById('content').append(element);
   let height=box,scroll=box+2;
   Object.defineProperties(element,{clientHeight:{get:()=>height},scrollHeight:{get:()=>scroll}});
   app.run('readableAfter()');const item=live(app).find(i=>i.element===element);assert.ok(item,'text participates in the generic reader');
   assert.equal(item.button.hidden,true,'one-line glyph ink overflow must not show Read: '+line);
   height=Math.round(line*3);scroll=height+2;app.run('readableAfter()');
   assert.equal(item.button.hidden,true,'three complete lines with ink overflow do not show Read');
   for(const language of ['it','en']){
    app.run("UI_LANG='"+language+"'");element.textContent='One\nTwo\nThree\nFour';scroll=Math.round(line*4);app.run('readableAfter()');
    assert.equal(item.button.hidden,false,'a real fourth line always shows Read: '+line);
    assert.equal(item.button.textContent,language==='it'?'Leggi':'Read');item.button.onclick();
    assert.equal(app.doc.getElementById('read-body').textContent,element.textContent,'the dialog still contains all four lines');close(app).onclick();
   }
   height=box;scroll=box+1;element.textContent='Short again';app.run('readableAfter()');
   assert.equal(item.button.hidden,true,'returning to one line removes the visible control');element.remove();
  }
 },
 async charts(){
  const app=await setup();
  const shape=e=>({tag:e.tag,classes:e.className,attrs:e.attributes,text:e.textContent,children:e.children.map(shape)});
  for(const language of ['it','en'])for(const range of ['24h','14d']){
   app.run("setLanguage('"+language+"');panelUI.range='"+range+"';render()");await settle();
   for(const id of ['states','steps','activity','proofs','reads']){
    const card=one(app.doc.getElementById('charts'),e=>e.getAttribute('data-chart')===id);
    assert.equal(all(card,e=>hasClass(e,'sq-readable')||hasClass(e,'sq-read-button')).length,0,id+' chart must not receive reading controls or clamps');
    assert.deepEqual(shape(card),shape(app.run("panelCharts['"+id+"']()")),id+' chart keeps its original structure and labels');
    const details=one(card,e=>e.tag==='details');details.open=true;details.ontoggle();app.run('readableAfter()');
    assert.equal(all(card,e=>hasClass(e,'sq-readable')||hasClass(e,'sq-read-button')).length,0,id+' data table stays untouched when opened');
   }
   const axis=one(app.doc.getElementById('charts'),e=>hasClass(e,'sq-xaxis'));
   assert.equal(axis.children.length,range==='24h'?4:7,'all hour or day columns remain separate');
   const y=one(app.doc.getElementById('charts'),e=>hasClass(e,'sq-yaxis'));
   assert.deepEqual(y.children.map(e=>e.className),['top','mid','zero'],'all three vertical labels stay on the axis');
   assert.ok(findText(app,literal)&&!findText(app,literal).button.hidden,'ordinary long text keeps Read');
   await app.refresh();
   assert.equal(all(app.doc.getElementById('charts'),e=>hasClass(e,'sq-read-button')).length,0,'polling keeps charts free of Read buttons');
  }
 },
 async measured(){
  const app=await setup();
  for(const width of [220,360,390,1024,1440]){
   layout.width=width;app.run('readableAfter()');unique(app,'read controls at '+width);
   for(const item of live(app)){assert.ok(hasClass(item.element,'sq-readable'));assert.ok(item.element.clientHeight<=3*layout.line);assert.equal(item.button.hidden,!(item.element.clientHeight>0&&item.element.scrollHeight>item.element.clientHeight+1))}
  }
  const long=findText(app,literal);assert.ok(long&&!long.button.hidden,'long text has Read');
  assert.equal(findText(app,'Short title').button.hidden,true,'short text has no visible action');
  layout.width=220;app.run('readableAfter()');const before=live(app).filter(i=>!i.button.hidden).length;
  layout.width=5000;app.listeners.resize();await frame(app);assert.ok(live(app).filter(i=>!i.button.hidden).length<before,'resize measures without a poll');
  layout.width=1000;app.run('readableAfter()');const narrow=live(app).filter(i=>!i.button.hidden).length;layout.glyph=40;app.doc.fontLoaded();await frame(app);assert.ok(live(app).filter(i=>!i.button.hidden).length>narrow,'late fonts trigger a new measurement');
  assert.match(page,/\.sq-readable\{[^}]*-webkit-line-clamp:3/);
  assert.match(page,/\.sq-readable\{[^}]*overflow-wrap:anywhere/);
 },
 async modal(){
  const app=await setup(),item=findText(app,literal);item.button.focus();item.button.onclick();
  assert.equal(dialog(app).open,true);assert.equal(app.doc.activeElement,close(app));assert.equal(app.doc.getElementById('read-body').textContent,literal);
  assert.match(page,/<dialog id="read-dialog"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="read-title"/);
  assert.equal(item.button.getAttribute('aria-haspopup'),'dialog');assert.equal(item.button.getAttribute('aria-controls'),'read-dialog');
  press(app,'Tab',true);assert.equal(app.doc.activeElement,app.doc.getElementById('read-body'),'Shift-Tab stays inside');
  press(app,'Tab');assert.equal(app.doc.activeElement,close(app),'Tab stays inside');
  await app.refresh();assert.equal(dialog(app).open,true);assert.equal(app.doc.activeElement,close(app),'poll never steals dialog focus');
  assert.equal(app.doc.getElementById('read-body').textContent,literal,'poll preserves the text being read');
  press(app,'Escape');assert.equal(dialog(app).open,false);assert.equal(app.doc.activeElement,findText(app,literal).button,'return to rebuilt trigger');
  findText(app,literal).button.onclick();close(app).onclick();assert.equal(dialog(app).open,false);
  findText(app,literal).button.onclick();const changed=clone(app.fixture);changed.tasks['TASK-OTHER'].brief={text:'Short title\nChanged'};await app.refresh(changed);press(app,'Escape');
  assert.ok(!String(key(app.doc.activeElement)).startsWith('read:'),'removed origin returns to navigation, never another text');
  assert.ok(!app.doc.created.some(e=>['img','script'].includes(e.tag)),'recorded HTML never becomes elements');
 },
 async languages(){
  const app=await setup();let item=findText(app,literal);assert.equal(item.button.textContent,'Leggi');item.button.onclick();
  app.run("setLanguage('en')");await settle();assert.equal(close(app).textContent,'Close');assert.equal(app.doc.getElementById('read-title').textContent,'Full text');
  assert.equal(app.doc.getElementById('read-body').textContent,literal,'recorded text is not translated');
  assert.equal(app.doc.activeElement,close(app));close(app).onclick();
  assert.equal(findText(app,literal).button.textContent,'Read');
  assert.equal(app.fetched.filter(x=>x!=='/api/state').length,0,'reading makes no requests');
  assert.equal(app.timers.length,1,'one existing poll loop');
 },
 async views(){
  const app=await setup();
  for(const name of TABS){await app.tab(name);const content=app.doc.getElementById('content');
   const leaf=app.doc.createElement('p');leaf.textContent=literal;content.append(leaf);app.run('readableAfter()');
   const item=findText(app,literal);assert.ok(item&&!item.button.hidden,name+' applies the same helper');item.button.onclick();assert.equal(app.doc.getElementById('read-body').textContent,literal);close(app).onclick();
  }
  await app.tab('Finestre');assert.ok(live(app).some(i=>full(app,i.element).includes('Short title')&&full(app,i.element).includes('\n')),'window assignments keep every task');
  await app.tab('Posta');
  const m=Object.values(app.fixture.messages)[0];m.body=literal;await app.refresh();assert.ok(findText(app,literal),'mail preview retains the complete body, not only its first line');
  await app.tab('Cronologia');assert.ok(live(app).some(i=>full(app,i.element).includes('"')),'event payloads have previews');
  for(const semantic of ['td','dd','dt','li']){
   const cell=app.doc.createElement(semantic);cell.textContent=literal;const parent=app.doc.createElement(semantic==='td'?'tr':semantic==='li'?'ul':'dl');parent.append(cell);app.doc.getElementById('content').append(parent);app.run('readableAfter()');
   assert.equal(parent.children.length,1,'Read never becomes an invalid sibling of '+semantic);assert.ok(cell.children.some(c=>hasClass(c,'sq-read-button')));
   cell.textContent='Updated '+literal;app.run('readableAfter()');const updated=live(app).find(i=>i.element.parentNode===cell&&full(app,i.element)==='Updated '+literal);assert.ok(updated&&!updated.button.hidden,'a reused '+semantic+' measures its new text');updated.button.onclick();assert.equal(app.doc.getElementById('read-body').textContent,'Updated '+literal);close(app).onclick();
  }
 },
 async closed(){
  const app=await setup(),f=clone(app.fixture);f.tasks['TASK-OTHER'].status='done';await app.refresh(f);
  const group=one(app.doc.getElementById('content'),e=>hasClass(e,'sq-closed'));assert.equal(group.open,false);
  const cardText=()=>live(app).find(i=>i.key.startsWith('Quadro:content|item:TASK-OTHER/')&&full(app,i.element)===literal);
  assert.equal(cardText().button.hidden,true,'a closed details element cannot be measured');
  const before=app.fetched.length;group.open=true;group.ontoggle();await frame(app);
  assert.equal(cardText().button.hidden,false,'opening measures without waiting for the poll');assert.equal(app.fetched.length,before);
  cardText().button.onclick();await app.refresh();assert.equal(dialog(app).open,true);press(app,'Escape');assert.equal(app.doc.activeElement,cardText().button);
 },
 async comms(){
  const app=await setup();await app.tab('Comunicazioni');
  const row=app.run("[...commsUI.rows.values()].find(r=>r.edge.message&&r.edge.kind==='message')");assert.ok(row,'real communication projection');
  const body=app.fixture.messages[row.edge.message];body.body=literal;await app.refresh();
  row.button.onclick();row.bodyButton.onclick();await settle();await frame(app);
  const item=findText(app,literal);assert.ok(item&&!item.button.hidden,'message body uses Read');
  assert.ok(!item.button.parentNode.contains(row.button),'Read is never nested in the timeline toggle');item.button.onclick();await app.refresh();assert.equal(app.doc.activeElement,close(app));press(app,'Escape');
  assert.equal(app.doc.activeElement,findText(app,literal).button,'timeline trigger survives a poll');
 },
 async plugin(){
  const app=await setup();const fixture=clone(app.fixture),local=x=>({it:x,en:x});
  fixture.plugins={key:'a'.repeat(64),refused:[],plugins:[{name:'example',title:local('Example'),description:local(literal),about:{publisher:'Example',version:'1',tagline:local(literal),sections:[{id:'purpose',title:local('Purpose'),text:local(literal)}]}}]};
  await app.refresh(fixture);one(app.doc.getElementById('plugin-links'),e=>key(e)==='nav:plugin:example').onclick();await settle();
  assert.ok(findText(app,literal),'plugin overview uses the helper');
  const panel={schema:1,updated:'2026-10-12T12:00:00Z',summary:local('Items'),items:[{id:'one',title:'Item',text:literal,status:'draft',meta:[{label:local('Context'),value:literal}]}]};
  app.run("pluginView.section='memories';pluginView.panel="+JSON.stringify(panel)+";pluginView.state=state;pluginView.signature=null;render()");await settle();
  assert.ok(live(app).filter(i=>full(app,i.element)===literal).length>=2,'plugin item and metadata use the helper');
 }
};
(async()=>{await tests[scenario]();console.log('read contract passed: '+scenario)})().catch(e=>{console.error(e);process.exit(1)});
`;
new Function('require','process',harness+'\n'+cases)(require,process);

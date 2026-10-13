'use strict';
const fs = require('node:fs'), path = require('node:path');
const prelude = fs.readFileSync(path.join(__dirname, 'read_contract.js'), 'utf8').split('const cases=String.raw`')[0];
const extension = String.raw`
harness=harness.replace('const layout={', 'const trace={enabled:false,events:[],styles:[],write(e){if(this.enabled)this.events.push(["write",e])},read(e){if(this.enabled)this.events.push(["read",e])}};const layout={');
harness=harness.replace('get childNodes(){', 'get className(){return this._className||""} set className(v){trace.write(this);this._className=v} get hidden(){return this._hidden} set hidden(v){trace.write(this);this._hidden=v} get childNodes(){');
harness=harness.replace('set textContent(value){', 'set textContent(value){trace.write(this);');
harness=harness.replace('append(...children){', 'append(...children){trace.write(this);');
harness=harness.replace('setAttribute(k,v){', 'setAttribute(k,v){trace.write(this);');
harness=harness.replace('get clientHeight(){', 'get clientHeight(){trace.read(this);');
harness=harness.replace('get scrollHeight(){', 'get scrollHeight(){trace.read(this);');
harness=harness.replace('getComputedStyle:e=>({lineHeight:e.measuredLineHeight??layout.line+"px"})', 'getComputedStyle:e=>{if(trace.enabled)trace.styles.push(e);return {lineHeight:e.measuredLineHeight??layout.line+"px",display:e.cssDisplay||e.style.display||"block"}}');
`;
const cases = String.raw`
const literal='One long recorded line '.repeat(80);
const live=app=>app.run('[...readUI.items.values()]');
const content=app=>app.doc.getElementById('content');
const frame=async app=>{while(app.frames?.length)app.frames.shift()();await settle()};
const paragraph=(app,parent,value=literal)=>{const p=app.doc.createElement('p');p.textContent=value;parent.append(p);return p};
const instrument=app=>{trace.events=[];trace.styles=[];trace.enabled=true;try{app.run('readableAfter()')}finally{trace.enabled=false}};
const checkBatch=()=>{
 const start=trace.events.findIndex(([kind])=>kind==='read');
 let end=trace.events.length-1;while(end>=0&&trace.events[end][0]!=='read')end--;
 assert.ok(start>=0&&end>start,'actual geometry was measured');
 assert.ok(trace.events.slice(start,end+1).every(([kind])=>kind==='read'),'all geometry reads precede every control write');
 assert.ok(trace.events.slice(end+1).some(([kind])=>kind==='write'),'controls are updated after the measurements');
};
const tests={
 async batch(){
  const app=await boot();content(app).replaceChildren();
  for(const count of [25,1000]){
   content(app).replaceChildren();const paragraphs=[];
   for(let i=0;i<count;i++)paragraphs.push(paragraph(app,content(app),'Text '+i+' '+literal));
   instrument(app);checkBatch();
   for(const p of paragraphs){const item=live(app).find(i=>i.element===p);assert.ok(item&&!item.button.hidden,'long text keeps its reading control')}
   instrument(app);checkBatch();unique(app,'repeated grouped measurements');
  }
 },
 async hidden(){
  const app=await boot();content(app).replaceChildren();
  const shell=app.doc.createElement('section');shell.cssDisplay='none';shell.append(content(app));
  const p=paragraph(app,content(app));let shown=false,reads=0;
  Object.defineProperties(p,{clientHeight:{get(){assert.ok(shown,'hidden root descendants are never measured');reads++;return 72}},scrollHeight:{get(){assert.ok(shown);reads++;return 120}}});
  instrument(app);assert.equal(reads,0);assert.equal(live(app).find(i=>i.element===p).button.hidden,true);
  assert.equal(trace.styles.filter(e=>e===shell).length,1,'a CSS-hidden ancestor is inspected once per pass');
  shown=true;shell.cssDisplay='block';app.run('readableSoon()');await frame(app);
  assert.ok(reads>0);assert.equal(live(app).find(i=>i.element===p).button.hidden,false,'showing the root restores Read');
  p.cssDisplay='none';shown=false;const before=reads;
  instrument(app);assert.equal(reads,before,'a leaf hidden by its own CSS is not measured');
  assert.equal(live(app).find(i=>i.element===p).button.hidden,true);
  p.cssDisplay='block';shown=true;instrument(app);assert.ok(reads>before);
  const group=app.doc.createElement('details'),summary=app.doc.createElement('summary');summary.textContent='Open details';group.append(summary);content(app).append(group);
  const inside=paragraph(app,group);let openReads=0;
  Object.defineProperties(inside,{clientHeight:{get(){assert.equal(group.open,true,'closed details are not measured');openReads++;return 72}},scrollHeight:{get(){assert.equal(group.open,true);return 120}}});
  instrument(app);assert.equal(openReads,0);assert.equal(live(app).find(i=>i.element===inside).button.hidden,true);
  const fetches=app.fetched.length;group.open=true;
  for(const listener of app.doc.listeners.toggle||[])listener({target:group});await frame(app);
  const item=live(app).find(i=>i.element===inside);assert.ok(openReads>0&&!item.button.hidden);assert.equal(app.fetched.length,fetches,'opening only measures, without another request');
  item.button.focus();item.button.onclick();assert.equal(app.doc.getElementById('read-body').textContent,literal);
  app.doc.getElementById('read-close').onclick();assert.equal(app.doc.activeElement,item.button);
 },
 async cells(){
  const app=await boot();content(app).replaceChildren();
  const label={tag:'dt',textContent:'Canale',children:[],parentNode:content(app)};
  assert.equal(app.run('readableText')(label,'without-layout'),null,'semantic labels without geometry APIs are left intact');
  assert.equal(label.textContent,'Canale');assert.equal(label.children.length,0);
  for(const tag of ['td','dd','dt','li']){
   const parent=app.doc.createElement(tag==='td'?'tr':tag==='li'?'ul':'dl'),cell=app.doc.createElement(tag);cell.textContent=literal;parent.append(cell);content(app).append(parent);
   instrument(app);checkBatch();assert.equal(parent.children.length,1,'reading control stays inside '+tag);
   const item=live(app).find(i=>i.element.parentNode===cell);assert.ok(item&&!item.button.hidden);
   cell.cssDisplay='none';instrument(app);
   assert.ok(!trace.events.some(([kind,e])=>kind==='read'&&e===item.element),'an existing wrapper under its own hidden cell is not measured');
   cell.cssDisplay='block';
   cell.textContent='Updated '+literal;instrument(app);checkBatch();
   const updated=live(app).find(i=>i.element.parentNode===cell);updated.button.onclick();
   assert.equal(app.doc.getElementById('read-body').textContent,'Updated '+literal);app.doc.getElementById('read-close').onclick();
  }
 },
 async dialog(){
  const app=await boot(),story=app.doc.getElementById('story');story.tag='dialog';story.open=false;story.replaceChildren();
  const p=paragraph(app,story);let measured=0;
  Object.defineProperties(p,{clientHeight:{get(){assert.equal(story.open,true,'closed dialog is not measured');measured++;return 72}},scrollHeight:{get(){return 120}}});
  instrument(app);assert.equal(measured,0);
  story.showModal();instrument(app);assert.ok(measured>0);assert.equal(live(app).find(i=>i.element===p).button.hidden,false);
 }
};
(async()=>{await tests[scenario]();console.log('read performance contract passed: '+scenario)})().catch(error=>{console.error(error);process.exitCode=1});
`;
new Function('require', 'process', '__dirname', prelude + extension + '\nnew Function("require","process",harness+' + JSON.stringify(cases) + ')(require,process);')(require, process, __dirname);

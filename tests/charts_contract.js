// Exercise the shipped Usage charts: same tables, motion only on change, reduced motion, exact values on focus.
'use strict';
const fsSource=require('node:fs'),pathSource=require('node:path');
let prefix=fsSource.readFileSync(pathSource.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const seam='json:async()=>clone(app.fixture)';
if(!prefix.includes(seam))throw Error('base harness seam changed');
prefix=prefix.replace(seam,"json:async()=>clone(path==='/api/usage'?await app.reply():app.fixture)");
const motionSeam=' focus(){this.doc.activeElement=this}';
if(!prefix.includes(motionSeam))throw Error('base harness element seam changed');
prefix=prefix.replace(motionSeam,' animate(frames,options){(this.doc.animations||(this.doc.animations=[])).push({el:this,frames,options});return {cancel(){},finished:Promise.resolve()}}\n'+motionSeam);
prefix=prefix.replace('const context={console,Date:PageDate','const context={URL,console,Date:PageDate');
const cases=String.raw`
const totals=(input,cache_read,cache_creation,output,reasoning=0)=>({input,cache_read,cache_creation,output,reasoning,total:input+cache_read+cache_creation+output});
function payload(){
 const a=totals(100,800,0,100,20),b=totals(500,0,0,600,200),at='2026-10-15T11:30:00Z';
 const q={identity:'first',model:'model-a',limit:'primary',used_percent:85,window_minutes:300,resets_at:'2026-10-15T13:00:00Z',observed_at:at,status:'measured'};
 return {schema:1,observed_at:at,status:'measured',coverage:{status:'ready',sources:2,measured:2,reasons:[]},
  windows:[{identity:'first',actors:[],client:'claude',status:'measured',reasons:[],totals:a,models:[],quota:[q]},{identity:'second',actors:[],client:'codex',status:'measured',reasons:[],totals:b,models:[],quota:[]}],
  models:[{model:'model-a',status:'measured',totals:a,windows:['first']},{model:'model-b',status:'measured',totals:b,windows:['second']},{model:'model-c',status:'not_measured',totals:null,windows:['second']}],
  tasks:[{task:null,status:'measured',totals:totals(600,800,0,700,220),estimated:true,reasons:['overlapping_tasks']}],
  days:[{day:'2026-10-15',status:'measured',totals:totals(600,800,0,700,220)},{day:'2026-10-14',status:'measured',totals:totals(50,10,0,40)}],
  quota_history:[{...q,observed_at:'2026-10-15T09:00:00Z',used_percent:40},{...q,observed_at:'2026-10-15T09:30:00Z',used_percent:null,status:'not_measured'},{...q,observed_at:'2026-10-15T10:00:00Z',used_percent:75},{...q,observed_at:'2026-10-15T10:30:00Z',used_percent:60},q],
  suggestions:[],cost:null};
}
async function setup(opts={}){
 const app=await boot({...opts,clock:Date.parse('2026-10-15T11:30:00Z')});app.data=payload();app.reply=async()=>clone(app.data);app.content=app.doc.getElementById('content');
 if(opts.reduced)app.run("window.matchMedia=q=>({matches:/prefers-reduced-motion: reduce/.test(q),addEventListener(){}})");
 app.select=async()=>{const links=app.doc.getElementById('usage-links');links.children[0].onclick();await settle()};return app;
}
const charts=app=>all(app.content,e=>e.getAttribute('data-usage-chart')!==null);
const tables=app=>Object.fromEntries(charts(app).map(c=>[c.getAttribute('data-usage-chart'),all(c,e=>e.tag==='tr').map(r=>r.children.map(x=>x.textContent))]));
const anims=app=>(app.doc.animations||[]).length;
const geometry=app=>all(app.content,e=>e.getAttribute('data-viz-value')!==null).map(e=>[e.tag,e.getAttribute('width')||e.style?.width||'',e.getAttribute('stroke-dasharray'),e.getAttribute('d')].join('|'));
const tests={
 async tables(){const app=await setup();await app.select();console.log('TABLES '+JSON.stringify(tables(app)))},
 async kinds(){
  const app=await setup();await app.select();
  const daily=one(app.content,e=>e.getAttribute('data-usage-chart')==='daily'),window=one(app.content,e=>e.getAttribute('data-usage-chart')==='byWindow');
  assert.ok(all(daily,e=>hasClass(e,'sq-viz-seg')&&/usage-k[0-3]/.test(e.className)).length>=4,'stacked segments by kind');
  assert.equal(all(window,e=>hasClass(e,'sq-viz-seg')&&hasClass(e,'sq-viz-grad')).length,2,'one gradient bar per window');
  assert.ok(all(window,e=>hasClass(e,'sq-viz-name')).every(e=>e.textContent),'labels are text at their real size');
  const model=one(app.content,e=>e.getAttribute('data-usage-chart')==='byModel');
  assert.equal(all(model,e=>e.tag==='circle'&&/sq-viz-ring /.test(e.getAttribute('class')||'')).length,2,'one ring arc per measured model, none for the unmeasured one');
  const legend=all(model,e=>e.getAttribute('data-viz-legend')!==null).map(e=>text(e));
  assert.equal(legend.length,3);assert.match(legend[0],/model-a · 48% · 1000/);assert.match(legend[1],/model-b · 52% · 1100/);assert.match(legend[2],/model-c · —/);
  const quota=one(app.content,e=>e.getAttribute('data-usage-chart')==='quota');
  assert.equal(all(quota,e=>e.tag==='path'&&/sq-viz-line/.test(e.getAttribute('class')||'')).length,2,'a missing value breaks the line');
  for(const p of all(quota,e=>e.tag==='path'&&/sq-viz-line/.test(e.getAttribute('class')||''))){
   const nums=(p.getAttribute('d').match(/-?[\d.]+/g)||[]).map(Number);let y0=nums[1];
   for(let i=2;i+5<nums.length;i+=6){const c1=nums[i+1],c2=nums[i+3],y1=nums[i+5],lo=Math.min(y0,y1)-0.01,hi=Math.max(y0,y1)+0.01;
    assert.ok(c1>=lo&&c1<=hi&&c2>=lo&&c2<=hi,'each interval stays between its two observed points: '+[y0,c1,c2,y1].join(','));y0=y1}}
 },
 async motion(){
  const app=await setup();await app.select();const first=anims(app);assert.ok(first>0,'bars, arcs and lines animate on first draw');
  await app.refresh();await settle();assert.equal(anims(app),first,'an unchanged value does not re-animate on refresh');
  app.run("setLanguage('en')");await settle();app.run("setLanguage('it')");await settle();assert.equal(anims(app),first,'a language change does not replay motion');
  app.data.windows[0].totals=totals(300,800,0,100,20);app.data.models[0].totals=app.data.windows[0].totals;await app.refresh();await settle();
  const moved=(app.doc.animations||[]).slice(first);assert.ok(moved.length>=1,'a changed value moves');
  assert.ok(moved.every(a=>a.frames.length===2),'from the old value to the new one');
  const scale=moved.find(a=>/scaleX/.test(String(a.frames[0].transform||'')));assert.ok(scale&&!/scaleX\(0\)/.test(scale.frames[0].transform),'a changed bar starts from its old size, not from zero');
 },
 async reduced(){
  const moving=await setup();await moving.select();const shape=geometry(moving);
  const still=await setup({reduced:true});await still.select();assert.equal(anims(still),0,'reduced motion: no animation call');
  assert.deepEqual(geometry(still),shape,'reduced motion: the same final picture');assert.ok(shape.length>=6);
 },
 async focus(){
  const app=await setup();await app.select();const window=one(app.content,e=>e.getAttribute('data-usage-chart')==='byWindow');
  const marks=all(window,e=>e.getAttribute('data-viz-key')!==null&&e.getAttribute('tabindex')==='0'&&e.getAttribute('aria-label'));assert.equal(marks.length,2,'every bar is reachable by keyboard');
  const readout=one(window,e=>hasClass(e,'sq-viz-readout'));marks[0].onfocus();assert.equal(readout.textContent,marks[0].getAttribute('aria-label'),'focus shows the exact value');
  assert.match(readout.textContent,/1100|1000/);marks[0].onblur();assert.notEqual(readout.textContent,marks[0].getAttribute('aria-label'));
  marks[1].onpointerenter();assert.equal(readout.textContent,marks[1].getAttribute('aria-label'),'hover shows the exact value');
  const quota=one(app.content,e=>e.getAttribute('data-usage-chart')==='quota'),series=one(quota,e=>e.getAttribute('data-viz-series')!==null),out=one(quota,e=>hasClass(e,'sq-viz-readout'));
  series.onkeydown({key:'ArrowLeft'});assert.match(out.textContent,/60% ·/,'arrows walk the observed points');series.onkeydown({key:'ArrowLeft'});assert.match(out.textContent,/75% ·/);
 },
 async inline(){
  const css=page.match(/<style id="squadra-charts-style">([\s\S]*?)<\/style>/);assert.ok(css,'chart style block');assert.doesNotMatch(css[1],/#[0-9a-f]{3,8}\b/i,'theme tokens only');
  assert.match(css[1],/prefers-reduced-motion: reduce/);assert.match(css[1],/\.sq-viz-track\{[^}]*border-radius:8px/,'rounded track');assert.match(css[1],/\.sq-viz-seg\{[^}]*border-radius:8px/,'rounded bars');assert.match(css[1],/\.sq-viz-grad\{background:linear-gradient\(90deg,color-mix\(in srgb,var\(--sq-accent\)/,'gradient from the theme token');assert.match(css[1],/@media\(max-width:699\.98px\)/,'phone layout');
  const js=page.match(/<script id="squadra-chart-motion">([\s\S]*?)<\/script>/);assert.ok(js,'chart motion block');
  for(const bad of [/https?:\/\//,/\bsrc=/,/import\(/,/fetch\(/])assert.doesNotMatch(js[1],bad,'no library or network: '+bad);
  assert.doesNotMatch(page,/<script[^>]+src=/,'no external script');
 }
};
(async()=>{assert.ok(tests[scenario]);await tests[scenario]();console.log('charts contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
eval(prefix+cases);

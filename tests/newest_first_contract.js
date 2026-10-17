// Check that every dashboard list and picker over time shows the newest first.
// An explicit column sort, a chart's time axis and non-temporal orders stay as they are.
// Usage: node newest_first_contract.js <dashboard.html> <state.json from the real server> <scenario>
// The Communications view needs the fuller DOM of comms_dashboard.js; the other views use panel_contract.js.
'use strict';
const sourceFS=require('node:fs'),sourcePath=require('node:path');
const comms=process.argv[4]==='comms';
const harness=comms?sourceFS.readFileSync(sourcePath.join(__dirname,'comms_dashboard.js'),'utf8').split('(async()=>{')[0]
 :sourceFS.readFileSync(sourcePath.join(__dirname,'panel_contract.js'),'utf8').split('const scenarios={')[0];
const shared=String.raw`
const T=hour=>'2025-01-01T'+hour+':00+00:00';
// Four tasks registered A, B, C, D. Their latest activity comes from four different fields,
// so the newest first order differs from registration order, its reverse and the status time alone.
const SYN=['NF-A','NF-B','NF-C','NF-D'],NEWEST=['NF-B','NF-A','NF-D','NF-C'];
const TITLE={'NF-A':'Alphaxq','NF-B':'Bravoxq','NF-C':'Charliexq','NF-D':'Deltaxq'};
const report=(name,c)=>({name,sha256:c.repeat(64),bytes:1});
function synthetic({done=false}={}){
 const f=clone(base),template=f.tasks['TASK-KQ3'],progress=f.progress['TASK-KQ3'],run=Object.values(base.runs)[0];
 const fields={
  'NF-A':{status_utc:T('10:00'),reviews:[{stage:'plan',verdict:'changes-required',reviewer:'read-wy',utc:T('12:00'),report:report('a1.md','a'),covers:[]},
   {stage:'plan',verdict:'accepted',reviewer:'read-wy',utc:T('13:00'),report:report('a2.md','b'),covers:[]}]},
  'NF-B':{status_utc:T('14:00')},
  'NF-C':{status_utc:T('09:00'),brief:{text:'Charliexq\nWhy it matters.',artifact:report('brief.md','c'),actor:'coord-zx',utc:T('11:00')}},
  'NF-D':{status_utc:T('08:00')}};
 for(const [i,id] of SYN.entries()){
  const t=f.tasks[id]={...clone(template),id,title:TITLE[id],owner:'build-ht',reviewer:'read-wy',depends:[],status:done?'done':'working',reviews:[],proofs:[],...fields[id]};
  const steps=clone(progress.steps);if(id==='NF-D'){steps[0].reached=true;steps[0].utc=T('12:00')}
  f.progress[id]={...clone(progress),task:id,created:T('0'+i+':00'),status:t.status,status_utc:t.status_utc,state:{id:done?'closed':'open',utc:t.status_utc},steps};
  f.runs['nf-run-'+id]={...clone(run),id:'nf-run-'+id,task:id,mode:'proof',status:'passed',utc:T('07:0'+i)};
  f.comms.edges.push({...clone(base.comms.edges[0]),id:'nf-edge-'+id,seq:5000+i,task:id,task_title:TITLE[id]});
  f.attention.next_steps.push({task:id,status:'working',phase:'working',role:'builder',text:'Build',who:['build-ht']});
 }
 f.runs['nf-run-NF-A-2']={...clone(run),id:'nf-run-NF-A-2',task:'NF-A',mode:'proof',status:'failed',utc:T('07:05')};
 f.attention.for_owner=[['1','10:00'],['2','12:00'],['3','11:00']].map(([n,h])=>({kind:'ask',id:'NF-ASK-'+n,question:'Question '+n+'?',utc:T(h),options:[],waiting:[],evidence:[]}));
 f.attention.decided=['1','2','3'].map(n=>({reason:'Decided '+n,utc:T('1'+n+':00'),choice:'a',channel:'typed-here',actor:'coord-zx'}));
 f.attention.waiting.push(...[['NF-A','10:00'],['NF-B','14:00'],['NF-C','11:00'],['NF-D','12:00']].map(([task,h])=>({kind:'ready',task,who:['build-ht'],utc:T(h),text:'Ready'})));
 f.waits=[['NF-B','delivered',500],['NF-C','delivered',100],['NF-D','delivered',300],['NF-A','delivered',900],['NF-A','reviewed',200]]
  .map(([task,kind,seconds])=>({kind,task,seconds,threshold:60,utc:T('10:00'),who:[],text:'Overdue'}));
 f.resources={gate:{name:'gate',owner:'coord-zx',reason:'release',utc:T('10:00')},lab:{name:'lab',owner:'coord-zx',reason:'proof',utc:T('12:00')},
  box:{name:'box',owner:'coord-zx',reason:'build',utc:T('11:00')}};
 f.decisions=[1,2,3,4,5,6,7].map(n=>({actor:'coord-zx',reason:'Reserve '+n,utc:T('1'+n+':00')}));
 f.legacy={'/legacy/a':{path:'/legacy/a',notice:'Imported',utc:T('10:00'),files:[]},'/legacy/b':{path:'/legacy/b',notice:'Imported',utc:T('12:00'),files:[]},
  '/legacy/c':{path:'/legacy/c',notice:'Imported',utc:T('11:00'),files:[]}};
 for(const [id,seq] of [['decision:LOOP-A',9001],['decision:LOOP-B',9002]])
  f.graph.nodes.push({id,kind:'coordinator-decision',seq,hash:'0'.repeat(64),actor:'coord-zx',utc:T('15:00'),artifacts:[],tasks:[],reason:'loop '+id,follows:[]});
 f.graph.edges.push({from:'decision:LOOP-A',to:'decision:LOOP-B',kind:'follows',recorded:true},{from:'decision:LOOP-B',to:'decision:LOOP-A',kind:'follows',recorded:true});
 f.graph.cycles=['decision:LOOP-A','decision:LOOP-B'];
 return f;
}
`;
const commsCases=String.raw`
const base=snapshots,clone=value=>JSON.parse(JSON.stringify(value));
current=synthetic();
(async()=>{
 for(const block of blocks)vm.runInContext(block,context,{timeout:1000});
 await new Promise(r=>setTimeout(r,20));
 get('tabs').children.find(e=>e.textContent==='Comunicazioni').onclick();
 const picked=ui().parts.selects.task.children.map(o=>o.value).filter(id=>SYN.includes(id));
 try{assert.deepEqual(picked,NEWEST)}catch(e){console.error('out of order (1):\nComunicazioni task picker: '+JSON.stringify(picked)+' != '+JSON.stringify(NEWEST));process.exitCode=1;return}
 console.log('newest first contract passed: comms');
})().catch(e=>{console.error(e);process.exitCode=1});
`;
const cases=String.raw`
const content=app=>app.doc.getElementById('content');
const json=(app,code)=>JSON.parse(app.run('JSON.stringify('+code+')'));
const syn=ids=>ids.filter(id=>SYN.includes(id));
// The synthetic tasks named in a text, in the order the text names them.
const named=s=>SYN.filter(id=>s.includes(TITLE[id])).sort((a,b)=>s.indexOf(TITLE[a])-s.indexOf(TITLE[b]));
const rows=root=>root.children.filter(c=>hasClass(c,'row')).map(r=>({head:text(r.children[0].children[0]),body:r.children[1]?text(r.children[1]):''}));
const byKey=(root,k)=>one(root,e=>key(e)===k,'control '+k);
const scope=(root,prefix)=>all(root,e=>(e.getAttribute('data-read-scope')||'').startsWith(prefix)).map(e=>e.getAttribute('data-read-scope').slice(prefix.length));
const failures=[];
// Run every row of a table so one report names each list that is out of order.
async function table(entries){
 for(const [name,get,expected] of entries){
  try{assert.deepEqual(await get(),expected)}catch(e){failures.push(name+': '+String(e.message).split('\n').filter(Boolean).slice(0,6).join(' | '))}
 }
}
const tests={
 async helper(){
  const app=await boot();
  app.run("state.progress['NF-PLAN']={steps:[{id:'plan',reached:true,utc:'"+T('16:00')+"'}]};state.progress['NF-BARE']={created:'"+T('05:00')+"',steps:[]};"+
   "state.outputs.push({task:'NF-OUT',agent:'build-qv',utc:'"+T('18:00')+"',text:'later output'})");
  const recency=record=>()=>app.run('taskRecency('+JSON.stringify(record)+')');
  await table([
   ['status time only',recency({id:'X1',status_utc:T('10:00')}),T('10:00')],
   ['a later read',recency({id:'X2',status_utc:T('10:00'),reviews:[{utc:T('09:00')},{utc:T('11:00')}]}),T('11:00')],
   ['a later brief',recency({id:'X3',status_utc:T('10:00'),brief:{text:'b',utc:T('12:00')}}),T('12:00')],
   ['a brief kept as plain text',recency({id:'X4',status_utc:T('10:00'),brief:'b'}),T('10:00')],
   ['a delivery',recency({id:'X5',status_utc:T('09:00'),delivered_utc:T('10:30')}),T('10:30')],
   ['a replacement',recency({id:'X6',status_utc:T('10:00'),superseded_utc:T('13:00')}),T('13:00')],
   ['a fix before the ACK',recency({id:'X7',status_utc:T('10:00'),fix_history:[{utc:T('14:00')}]}),T('14:00')],
   ['a route change',recency({id:'X8',status_utc:T('10:00'),route_history:[{utc:T('15:00')}]}),T('15:00')],
   ['the latest plan',recency({id:'NF-PLAN',status_utc:T('10:00')}),T('16:00')],
   ['no time on the record',recency({id:'NF-BARE'}),T('05:00')],
   ['older fields never win',recency({id:'X9',status_utc:T('10:00'),reviews:[{utc:T('08:00')}],brief:{utc:T('07:00')}}),T('10:00')],
   ['an output does not move a task',recency({id:'NF-OUT',status_utc:T('10:00')}),T('10:00')],
   ['equal times keep the later task first',()=>json(app,"newestTasks([{id:'T1',status_utc:'"+T('10:00')+"'},{id:'T2',status_utc:'"+T('10:00')+"'},{id:'T3',status_utc:'"+T('11:00')+"'}]).map(t=>t.id)"),['T3','T2','T1']],
  ]);
 },
 async tasks(){
  const app=await boot({fixture:synthetic()}),closed=await boot({fixture:synthetic({done:true})});
  const readsData={head:{seq:1,hash:'h'},reads:[],revisions:[],tasks:SYN.map(id=>({id,title:TITLE[id],totals:{}}))};
  const reads=async()=>{app.run("tab='Letture';readsView.data="+JSON.stringify(readsData)+";readsView.attempt=readsHead();readsView.signature=null;render()");await settle()};
  const finestre=async()=>{await app.tab('Finestre');return one(content(app),e=>e.getAttribute('data-read-scope')==='windows:build-ht','the build-ht row')};
  const seat=id=>text(one(app.doc.getElementById('overview'),e=>e.tag==='article'&&e.getAttribute('data-seat')===id,'overview card '+id));
  const waiting=()=>app.run('recordedWaiting()');
  await table([
   ['Incarichi default order',async()=>{await app.tab('Incarichi');return syn(scope(content(app),'tasks:'))},NEWEST],
   ['Decisioni task picker',async()=>{await app.tab('Decisioni');return syn(byKey(content(app),'dec:filter').children.map(o=>o.value))},NEWEST],
   ['Letture task picker',async()=>{await reads();return syn(byKey(content(app),'reads:task').children.map(o=>o.value))},NEWEST],
   ['Letture per-task table',async()=>{await reads();return syn(all(content(app),e=>/^reads:task:/.test(key(e)||'')).map(e=>key(e).slice(11)))},NEWEST],
   ['Bilanciamento task picker',async()=>syn(one(app.run('balanceTask({})'),e=>e.tag==='select').children.map(o=>o.value)),NEWEST],
   ['work cards, open',async()=>{await app.tab('Quadro');return syn(one(content(app),e=>hasClass(e,'sq-work')).children.filter(c=>hasClass(c,'sq-task')).map(c=>c.getAttribute('data-task')))},NEWEST],
   ['work cards, closed',async()=>syn(one(content(closed),e=>hasClass(e,'sq-closed')).children.map(c=>c.getAttribute('data-task')).filter(Boolean)),NEWEST],
   ['overview task cards',async()=>{await app.tab('Quadro');return syn(all(app.doc.getElementById('overview'),e=>hasClass(e,'ov-task')).map(e=>e.getAttribute('data-task')))},NEWEST],
   ['overview window card, builder',async()=>{await app.tab('Quadro');return named(seat('build-ht'))},NEWEST],
   ['overview window card, reader',async()=>{await app.tab('Quadro');return named(seat('read-wy'))},NEWEST],
   ['team card work',async()=>{await app.tab('Quadro');return named(text(one(byKey(content(app),'connected:agent:build-ht'),e=>hasClass(e,'sq-agent-work'))))},NEWEST],
   ['presence window sentence',async()=>named(app.run("presenceWindowLabel('build-ht')")),NEWEST],
   ['Finestre tasks column',async()=>{const r=await finestre(),label=app.run("tr('table.windows.tasks')");return named(text(one(r,e=>e.getAttribute('data-label')===label)))},NEWEST],
   ['Finestre related task links',async()=>syn(all(await finestre(),e=>e.getAttribute('data-connected-task')!==null&&e.tag==='button').map(e=>e.getAttribute('data-connected-task'))),NEWEST],
   ['next steps while waiting',async()=>syn(rows(waiting()).map(r=>SYN.find(id=>r.head===TITLE[id])).filter(Boolean)),NEWEST],
   ['chart rows, steps',async()=>syn(json(app,'panelData.steps().map(p=>p.task)')),NEWEST],
   ['chart rows, reads',async()=>syn(json(app,'panelData.reads().map(t=>t.task)')),NEWEST],
   ['chart rows, proofs',async()=>syn(json(app,'panelData.proofs().map(p=>p.task)')),NEWEST],
  ]);
 },
 async events(){
  const app=await boot({fixture:synthetic()});
  const panelNamed=(root,title)=>one(root,e=>e.tag==='section'&&e.children[0]?.textContent===app.run('tr('+JSON.stringify(title)+')'),title);
  const overdue=r=>{const m=/^(\S+) · (\d+)s/.exec(r.body);return m?m[1]+':'+m[2]:null};
  await table([
   ['task story',async()=>{app.run("panelOpenStory('TASK-KQ1')");await settle();
    const list=one(app.doc.getElementById('story'),e=>hasClass(e,'sq-story-list'));const keys=list.children.map(li=>li.getAttribute('data-story-key'));
    assert.ok(keys.length>=5,'a story with several events');return keys},json(app,"panelStory(state.tasks['TASK-KQ1']).map(i=>i.key).reverse()")],
   ['task reads beside the proofs',async()=>all(app.run("connectedReviews(state.tasks['NF-A'])"),e=>/^connected:review:/.test(key(e)||'')).map(e=>key(e).split(':')[3]),['1','0']],
   ['task reads in the Incarichi details',async()=>{await app.tab('Incarichi');const r=one(content(app),e=>e.getAttribute('data-read-scope')==='tasks:NF-A');
    return all(r,e=>/:link:review:/.test(key(e)||'')).map(e=>/:link:review:([^:]+):/.exec(key(e))[1])},['a2.md','a1.md']],
   ['decision list',async()=>{await app.tab('Decisioni');const seq=new Map(json(app,'state.graph.nodes').map(n=>[n.id,n.seq]));
    const seqs=one(content(app),e=>e.tag==='ol'&&hasClass(e,'dec-list')).children.map(li=>seq.get(li.getAttribute('data-node-id')));
    assert.ok(seqs.length>=5,'several listed decisions');return seqs},json(app,"(state.graph.nodes.filter(n=>!String(n.id).startsWith('decision:LOOP')).map(n=>n.seq).sort((a,b)=>b-a))")],
   ['unordered links',async()=>{await app.tab('Decisioni');return all(one(content(app),e=>hasClass(e,'dec-unordered')),e=>e.tag==='li').map(li=>li.getAttribute('data-node-id'))},['decision:LOOP-B','decision:LOOP-A']],
   ['reserved resources',async()=>rows(panelNamed(app.run('transferOverviewDetail()'),'page.reserve.title')).map(r=>r.head).filter(h=>['gate','lab','box'].includes(h)),['lab','box','gate']],
   ['reserve decisions',async()=>rows(panelNamed(app.run('transferOverviewDetail()'),'page.reserve.title')).map(r=>r.body).filter(b=>/^Reserve/.test(b)),['Reserve 7','Reserve 6','Reserve 5','Reserve 4','Reserve 3']],
   ['questions for the owner',async()=>rows(app.run('ownerAttention()')).map(r=>r.head).filter(h=>/^Question/.test(h)),['Question 2?','Question 3?','Question 1?']],
   ['decided answers',async()=>rows(one(app.run('ownerAttention()'),e=>e.tag==='details')).map(r=>r.head),['Decided 3','Decided 2','Decided 1']],
   ['recorded waits',async()=>named(rows(app.run('recordedWaiting()')).filter(r=>r.head===app.run("waitText({kind:'ready'})")).map(r=>r.body.split(' · ')[0]).join('|')),['NF-B','NF-D','NF-C','NF-A']],
   ['overdue waits',async()=>rows(app.run('recordedWaiting()')).map(overdue).filter(Boolean),['Charliexq:100','Alphaxq:200','Deltaxq:300','Bravoxq:500','Alphaxq:900']],
   ['overview questions',async()=>all(app.run('ovWaiting()'),e=>e.tag==='li').map(li=>text(li)).filter(t=>/Question/.test(t)).map(t=>/Question \d\?/.exec(t)[0]),['Question 2?','Question 3?','Question 1?']],
   ['overview waits',async()=>named(all(app.run('ovWaiting()'),e=>e.tag==='li').map(li=>text(li)).filter(t=>!/Question/.test(t)).join('|')),['NF-B','NF-D','NF-C','NF-A']],
   ['a task card\'s overdue waits',async()=>all(app.run("panelTaskCard(state.tasks['NF-A'])"),e=>hasClass(e,'sq-line')&&hasClass(e,'bad')).map(e=>text(e).split(' · ')[0]),
    [app.run("waitText({kind:'reviewed'})"),app.run("waitText({kind:'delivered'})")]],
   ['Storico imports',async()=>rows(app.run('legacyView()')).map(r=>r.head),['/legacy/b','/legacy/c','/legacy/a']],
   ['order labels',async()=>['it','en'].flatMap(lang=>{app.run('UI_LANG='+JSON.stringify(lang));return [app.run("tr('dec.list')"),app.run("tr('story.intro')")]}),
    ['Elenco · più recenti in alto','Tutto quello che il registro sa di questo lavoro, dal più recente al più vecchio.','List · newest first','Everything the registry knows about this task, newest first.']],
  ]);
 },
 async kept(){
  const app=await boot({fixture:synthetic()});
  const incarichi=async(dir)=>{app.run("tab='Incarichi';panelUI.tables.tasks={sort:'updated',dir:"+dir+",filter:'',shown:100,open:new Set()};render()");await settle();return syn(scope(content(app),'tasks:'))};
  await table([
   ['an explicit ascending column sort wins',()=>incarichi(1),['NF-D','NF-C','NF-A','NF-B']],
   ['an explicit descending column sort wins',()=>incarichi(-1),['NF-B','NF-A','NF-C','NF-D']],
   ['a proof strip keeps its time axis',async()=>json(app,"panelData.proofs().find(p=>p.task==='NF-A').runs.map(r=>r.id)"),['nf-run-NF-A','nf-run-NF-A-2']],
   ['windows keep their registry order',async()=>{await app.tab('Finestre');return scope(content(app),'windows:')},Object.keys(base.agents)],
  ]);
 }
};
(async()=>{assert.ok(tests[scenario],scenario);await tests[scenario]();
 if(failures.length){console.error('out of order ('+failures.length+'):\n'+failures.join('\n'));process.exitCode=1}
 else console.log('newest first contract passed: '+scenario)})().catch(e=>{console.error(e);process.exitCode=1});
`;
new Function('require','process',harness+shared+(comms?commsCases:cases))(require,process);

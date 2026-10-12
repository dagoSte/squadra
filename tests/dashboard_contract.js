// Run every page script in order in one simplified DOM.
// This checks script behavior, not browser rendering.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
class Element {
  constructor(tag,ns){this.tag=tag;this.ns=ns;this.children=[];this.textContent='';this.className='';this.attributes={}}
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=children}
  setAttribute(name,value){this.attributes[name]=String(value)}
  getAttribute(name){return name in this.attributes?this.attributes[name]:null}
  set innerHTML(value){throw Error('Untrusted HTML insertion: '+value)}
  showModal(){this.open=true}
  close(){this.open=false}
}
const ids=new Map(), get=id=>{if(!ids.has(id))ids.set(id,new Element(id));return ids.get(id)};
const text=e=>e.textContent+' '+e.children.map(text).join(' ');
const fixture={workspace:{name:'Synthetic dashboard',project:'/private/synthetic'},event_count:12,paused:false,
  agents:{A:{id:'A',identity:'one-window',client:'codex',role:'builder',skills:[]},
          C:{id:'C',identity:'one-window',client:'codex',role:'coordinator',skills:[]},
          R:{id:'R',identity:'outside',client:'claude',role:'reviewer',skills:[{name:'review',sha256:'1'.repeat(64)}]}},
  tasks:{T:{id:'T',title:'Synthetic task',owner:'A',base:'a'.repeat(40),status:'working',depends:[],proofs:[],reviews:[],scope:{name:'scope',sha256:'b'.repeat(64)}}},
  messages:{M:{id:'M',from:'C',to:'A',utc:new Date().toISOString(),body:'<img src=x onerror=alert(1)>',ack:null}},
  resources:{},decisions:[],runs:{},outputs:[],legacy:{},events:[],
  attention:{for_owner:[],decided:[],waiting:[],next_steps:[]}};
let historyReplaced=false, failedFetch=false, fetches=0;
const timers=[];
const context={console,Date,Set,JSON,Error,
  document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(ns,tag)=>new Element(tag,ns)},
  location:{port:'12345',hash:'#test-token',pathname:'/'},
  sessionStorage:{setItem(){},getItem(){return 'test-token'}},
  history:{replaceState(){historyReplaced=true}},
  setTimeout:fn=>{timers.push(fn)},
  fetch:async(path,opts)=>{fetches++;assert.equal(opts.headers['X-Squadra-Token'],'test-token');
    if(failedFetch)throw Error('controlled offline');
    return {ok:true,json:async()=>fixture,text:async()=>'<script>not executable</script>'}}
};
const scripts=[...fs.readFileSync(process.argv[2],'utf8').matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
assert.ok(scripts.length>=3,'every inline block is executed, not only the first');
context.window=context;
(async()=>{
  vm.createContext(context);
  for(const script of scripts)vm.runInContext(script,context,{timeout:1000});
  await new Promise(r=>setTimeout(r,20));
  assert.equal(historyReplaced,true,'URL token handling must not be shadowed by a tab function');
  assert.equal(get('name').textContent,'Synthetic dashboard');
  assert.deepEqual(get('tabs').children.map(e=>e.textContent),['Quadro','Finestre','Incarichi','Posta','Comunicazioni','Decisioni','Prove','Output','Cronologia','Storico']);
  assert.equal(get('content').children[0].children[0].textContent,'Serve a te','owner panel must be first');
  assert.match(text(get('content').children[0]),/Nessuna richiesta registrata.*solo in chat/);
  const artifact={name:'evidence.md',sha256:'c'.repeat(64)};
  fixture.attention.for_owner=[{kind:'ask',id:'Q',question:'<img src=x onerror=alert(1)>',task:'T',waiting:['A'],
    utc:'2026-10-09T12:00:00Z',recommended:'yes',evidence:[artifact],
    options:[{id:'yes',label:'Continue',say:'Continue with evidence',buys:'finish',costs:'one review'},
             {id:'no',label:'Stop',say:'Stop and save'}]}];
  fixture.attention.decided=[{reason:'Choice',actor:'C',channel:'relayed',choice:'yes',ask:{question:'Previous question'},owner_words:'Exact words'}];
  fixture.attention.waiting=[{kind:'changes-required',task:'T',who:['A'],text:'Correction pending',
    rounds:{plan:1,code:2},limits:{plan:3,code:99}},
    {kind:'dependencies',task:'T',who:[],text:'Dependencies pending',depends:['DEP-PENDING']}];
  fixture.runs.r={id:'r',status:'cleanup-unknown'};
  fixture.waits=[{kind:'ack',id:'M',seconds:901,threshold:900,text:'ACK A non registrato'}];
  fixture.requirements={T:[{id:'R1',text:'Value preserved',proofs:[{id:'native-proof',native_rc:0}],reviewed:true}]};
  await timers.shift()();
  assert.match(text(get('content').children[0]),/<img src=x onerror=alert\(1\)>/);
  assert.match(text(get('content').children[0]),/Continue with evidence/);
  assert.match(text(get('content').children[0]),/Riferita da C/);
  assert.match(text(get('content')),/2\/99/,'used review limits are visible');
  assert.match(text(get('content')),/DEP-PENDING/,'dependency IDs are visible');
  assert.match(text(get('metrics')),/stop da verificare/);
  assert.match(text(get('content').children[1]),/Il lavoro, adesso\./);
  assert.match(text(get('content').children[1]),/901s \/ 900s/,'wait age is server supplied');
  assert.match(text(get('content')),/native-proof \/ exit 0/,'requirement has native evidence');
  assert.match(text(get('content')),/Lettura coperta/,'independent coverage is distinct');
  assert.doesNotMatch(text(get('metrics')),/Esito terminale non ancora registrato/);
  const descendants=e=>[e,...e.children.flatMap(descendants)];
  const proof=descendants(get('content').children[0]).find(e=>e.tag==='button'&&e.textContent.startsWith('evidence.md'));
  assert.ok(proof,'registered evidence has a working drill-down');await proof.onclick();
  assert.equal(get('artifact').open,true);assert.match(get('artifact-body').textContent,/<script>not executable/);
  assert.match(text(get('metrics')),/Identità\s+2/,'aliases must not count as independent windows');
  get('tabs').children.find(e=>e.textContent==='Finestre').onclick();
  assert.match(text(get('content')),/outside/);assert.match(text(get('content')),/claude/);
  assert.match(text(get('content')),/review @ 111111111111/);
  get('tabs').children.find(e=>e.textContent==='Posta').onclick();
  assert.match(text(get('content')),/<img src=x onerror=alert\(1\)>/);
  assert.match(text(get('content')),/ACK pendente/);
  failedFetch=true;await timers.shift()();
  assert.match(text(get('notice')),/Ultima vista conservata/);
  assert.match(text(get('content')),/ACK pendente/,'offline state must preserve last visible content');
  assert.equal(fetches,4);console.log('dashboard contract passed: owner-first, empty caveat, structured choices, relayed receipt, uncertain stop, artifact, token, roles, XSS, ACK, offline');
})().catch(e=>{console.error(e);process.exitCode=1});

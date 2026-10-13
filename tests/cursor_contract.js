// Check Cursor labels in cards and tables in both languages.
// Run all page scripts in Node with a simplified DOM.
// Usage: node cursor_contract.js <dashboard.html> <state.json from the real server>
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [htmlPath,fixturePath]=process.argv.slice(2);
const page=fs.readFileSync(htmlPath,'utf8');
const scripts=[...page.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const base=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
const clone=value=>JSON.parse(JSON.stringify(value));

function* walk(e){if(!e)return;yield e;for(const c of e.children||[])yield* walk(c)}
const text=e=>[...walk(e)].map(x=>x.textContent||'').filter(Boolean).join(' ');
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
class Element{
 constructor(doc,tag,ns){this.doc=doc;this.tag=tag;this.ns=ns;this.children=[];this.attributes={};this.textContent='';this.className='';this.style={}}
 append(...children){for(const c of children){if(!(c instanceof Element))throw Error('Non-element child appended: '+String(c));this.children.push(c)}}
 replaceChildren(...children){if(this.doc.activeElement&&[...walk(this)].includes(this.doc.activeElement))this.doc.activeElement=null;this.children=[];this.append(...children)}
 setAttribute(name,value){this.attributes[name]=String(value)}
 getAttribute(name){return name in this.attributes?this.attributes[name]:null}
 set innerHTML(value){throw Error('Untrusted HTML insertion: '+value)}
 insertAdjacentHTML(){throw Error('Untrusted HTML insertion')}
 focus(){this.doc.activeElement=this}
 blur(){if(this.doc.activeElement===this)this.doc.activeElement=null}
 showModal(){this.open=true}
 close(){this.open=false}
}

(async()=>{
 const doc={ids:new Map(),activeElement:null,title:'',listeners:{}};
 doc.getElementById=id=>{if(!doc.ids.has(id)){const e=new Element(doc,'#'+id);e.id=id;doc.ids.set(id,e)}return doc.ids.get(id)};
 doc.createElement=tag=>new Element(doc,tag);doc.createElementNS=(ns,tag)=>new Element(doc,tag,ns);
 doc.documentElement={lang:'',attributes:{},setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return k in this.attributes?this.attributes[k]:null}};
 doc.addEventListener=(type,fn)=>{(doc.listeners[type]||(doc.listeners[type]=[])).push(fn)};
 const timers=[];
 const context={console,Date,Intl,document:doc,location:{port:'4242',hash:'#token',pathname:'/'},navigator:{language:'it-IT'},
  sessionStorage:{setItem(){},getItem(){return 'token'}},history:{replaceState(){}},setTimeout:fn=>{timers.push(fn)},addEventListener(){},
  matchMedia:()=>({matches:false,addEventListener(){}}),localStorage:{getItem:()=>null,setItem(){}},
  fetch:async()=>({ok:true,json:async()=>clone(base)})};
 context.window=context;vm.createContext(context);
 for(const script of scripts)vm.runInContext(script,context,{timeout:2000});
 await settle();
 const run=code=>vm.runInContext(code,context);
 const content=()=>doc.getElementById('content');
 const tab=async name=>{const label=run('tr('+JSON.stringify('tab.'+name)+')');const b=doc.getElementById('tabs').children.find(e=>e.textContent===label);
  assert.ok(b,'tab '+name);b.onclick();await settle()};
 const check=async(lang,builds,windowWord)=>{
  await tab('Quadro');
  const card=[...walk(content())].find(e=>e.getAttribute('data-task')==='T1');assert.ok(card,'the T1 card');
  assert.ok(text(card).includes(builds+'Cursor'),lang+': the card names the builder «Cursor»: '+text(card).slice(0,300));
  await tab('Finestre');assert.ok(text(content()).includes('Cursor'),lang+': the Windows view names the Cursor seats');
  const unknown=run("tr('presence.client.unknown')");
  assert.ok(!text(content()).includes(unknown),lang+': no Cursor seat is described as «'+unknown+'»');
  await tab('Incarichi');assert.ok(text(content()).includes(windowWord+' (Cursor)'),lang+': the task table names the builder (Cursor)');
  for(const id of ['content','tabs','overview'])assert.doesNotMatch(text(doc.getElementById(id)),/⟦/,lang+': no missing label in '+id);
 };
 await check('it','Costruisce: ','Costruttore');
 const en=[...walk(doc.getElementById('lang'))].find(e=>e.getAttribute('data-lang')==='en');assert.ok(en,'the language switch');en.onclick();await settle();
 await check('en','Builds: ','Builder');
 console.log('cursor contract passed');
})().catch(error=>{console.error(error);process.exit(1)});

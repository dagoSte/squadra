// Check the composition advice on the overview in both languages.
// Run all page scripts in Node with a simplified DOM.
// Usage: node composition_contract.js <dashboard.html> <state.json from the real server>
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
 const tab=async name=>{const label=run('tr('+JSON.stringify('tab.'+name)+')');const b=doc.getElementById('tabs').children.find(e=>e.textContent===label);
  assert.ok(b,'tab '+name);b.onclick();await settle()};
 const check=async(lang,count,advice,source)=>{
  await tab('Quadro');
  const box=[...walk(doc.getElementById('overview'))].find(e=>e.getAttribute('data-composition'));
  assert.ok(box,lang+': the composition block');
  assert.equal(box.getAttribute('data-composition'),'imbalance');
  for(const words of [count,advice,source])assert.ok(text(box).includes(words),lang+': «'+words+'» in '+text(box));
  assert.doesNotMatch(text(doc.getElementById('overview')),/⟦/,lang+': no missing label');
 };
 await check('it','Composizione: 2 Claude, 1 Codex','L\'unica finestra Codex rileggerà tutto','È solo un consiglio');
 const en=[...walk(doc.getElementById('lang'))].find(e=>e.getAttribute('data-lang')==='en');assert.ok(en,'the language switch');en.onclick();await settle();
 await check('en','Composition: 2 Claude, 1 Codex','The lone Codex window will read everything','Advice only');
 console.log('composition contract passed');
})().catch(error=>{console.error(error);process.exit(1)});

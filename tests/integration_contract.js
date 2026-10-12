// Run page scripts in a shared simplified DOM.
// Call page functions with state built by the dashboard server.
// Usage: node integration_contract.js <dashboard.html> <state.json>
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const [page,fixture]=process.argv.slice(2);
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';this.className='';this.style={}}
 append(...c){this.children.push(...c)}
 prepend(...c){this.children.unshift(...c)}
 replaceChildren(...c){this.children=c}
 setAttribute(k,v){this.attributes[k]=String(v)}
 getAttribute(k){return k in this.attributes?this.attributes[k]:null}
 set innerHTML(v){throw Error('Untrusted HTML insertion: '+v)}
}
const ids=new Map(),get=id=>{if(!ids.has(id))ids.set(id,new Element('#'+id));return ids.get(id)};
const context={console,Date,
 document:{getElementById:get,createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t),activeElement:null,title:'',documentElement:{lang:''}},
 location:{port:'1',hash:'',pathname:'/'},sessionStorage:{setItem(){},getItem(){return 't'}},history:{replaceState(){}},
 navigator:{language:'it-IT'},setTimeout(){},fetch:()=>new Promise(()=>{})};
context.window=context;vm.createContext(context);
const blocks=[...fs.readFileSync(page,'utf8').matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
assert.ok(blocks.length>=4,'language, presence, main and overview blocks');
for(const block of blocks)vm.runInContext(block,context,{timeout:2000});
const state=JSON.parse(fs.readFileSync(fixture,'utf8'));
context.fixture=state;vm.runInContext('state=fixture',context);
const run=code=>vm.runInContext(code,context);
const assignment=state.comms.edges.find(e=>e.kind==='message'&&e.message==='assign-T');
assert.ok(assignment,'the assignment of T is a communication edge');
context.edge=assignment;
const name=run('taskTitle("T")');
assert.equal(assignment.task_title,name,'communications and overview give the task the same name');
assert.equal(assignment.task_described,true);
run("UI_LANG='it'");
let facts=run('commsFacts(edge)');
assert.match(facts,/Incarico «Servizio delle chiavi»; la sua descrizione completa è nella Panoramica\./);
assert.doesNotMatch(facts,/non ancora scritta/,'a described task is never called undescribed');
assert.match(run('commsSentence(edge)'),/«Servizio delle chiavi»/);
run("UI_LANG='en'");
facts=run('commsFacts(edge)');
assert.match(facts,/Task “Servizio delle chiavi”; its full description is in the Overview\./,'recorded brief text is not translated');
assert.doesNotMatch(facts,/not written yet|⟦/);
const plain=state.comms.edges.find(e=>e.kind==='message'&&e.message==='assign-U');
assert.ok(plain&&plain.task_described===false&&plain.task_title==='Synthetic change','a task without a brief keeps its recorded title');
context.edge=plain;
assert.match(run('commsFacts(edge)'),/Task titled “Synthetic change”\. Task description: not written yet\./);
console.log('integration contract passed: one task name across views, true description facts in Italian and English');

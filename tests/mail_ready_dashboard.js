const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8'),task=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const source=html.slice(html.indexOf('function readerReadyLine('),html.indexOf('function panelOpenStory('));
class Element{constructor(){this.children=[];this.textContent='';this.className=''}append(...xs){this.children.push(...xs)}setAttribute(){}}
function node(tag,text){const el=new Element();el.textContent=text||'';return el}
const context={state:task,UI_LANG:'en',panelSeat:n=>n,node,
 row:title=>{const r=new Element(),line=new Element();line.append(node('span',title));r.append(line);return r},
 titled:t=>t,taskTitle:id=>id,panelSituation:()=>new Element(),taskBody:()=>'',panelSteps:()=>null,
 appendTaskTime:()=>{},panelRoles:()=>'',panelStory:()=>[],panelDeclared:()=>null,panelOpen:()=>true,
 newestFirst:x=>x,panelRolesTech:()=>'',tr:k=>k,focusKey:()=>{},connectedTaskLinks:()=>new Element()};
vm.createContext(context);vm.runInContext(source,context);
const text=e=>e.textContent+' '+e.children.map(text).join(' ');
assert.match(text(context.panelTaskCard(task.tasks.U)),/reader ready, waiting for delivery/);
context.UI_LANG='it';assert.match(text(context.panelTaskCard(task.tasks.U)),/lettore pronto, attende consegna/);
task.agents.R.availability={status:'stand-by'};assert.equal(context.readerReadyLine(task.tasks.U),'');
delete task.agents.R.availability;task.tasks.U.status='delivered';assert.equal(context.readerReadyLine(task.tasks.U),'');
task.tasks.U.status='working';task.tasks.U.superseded_by='V';assert.equal(context.readerReadyLine(task.tasks.U),'');
console.log('Reader readiness dashboard: passed');

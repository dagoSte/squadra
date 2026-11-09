"""Pending handovers change dashboard responses on clock and worktree changes, without events."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import dashboard
import test_dashboard_performance as fixtures


class DashboardHandover(unittest.TestCase):
    setUp = fixtures.DashboardPerformance.setUp
    stop_server = fixtures.DashboardPerformance.stop_server
    get = fixtures.DashboardPerformance.get
    stamp = fixtures.DashboardPerformance.stamp
    task_id = fixtures.DashboardPerformance.task_id
    run_id = fixtures.DashboardPerformance.run_id
    source_path = fixtures.DashboardPerformance.source_path
    source_hash = fixtures.DashboardPerformance.source_hash

    def inject(self, conditions):
        # The engine lane owns journal format 5. This lane tests the frozen synthetic state contract.
        original = dashboard.VerifiedView.read
        self.checkout = self.base / 'handover-checkout'
        self.checkout.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.checkout)], check=True, capture_output=True)
        handover = {'id': 'handover-test', 'from': 'A', 'to': 'next-A', 'status': 'pending',
                    'utc': self.stamp, 'conditions': conditions}

        def read(view):
            events, recorded, compact = original(view)
            state = {**recorded, 'handovers': {'handover-test': deepcopy(handover)},
                     'tasks': {**recorded['tasks']}}
            state['tasks'][self.task_id] = {**state['tasks'][self.task_id], 'repo': str(self.checkout)}
            return events, state, compact

        mock = patch.object(dashboard.VerifiedView, 'read', read)
        mock.start()
        self.addCleanup(mock.stop)

    def compact(self, stamp, token=''):
        with patch.object(dashboard, 'now', return_value=stamp):
            code, _, raw = self.get(headers={'X-Squadra-View': 'compact', 'X-Squadra-State-Token': token})
        self.assertEqual(code, 200, raw)
        return json.loads(raw)

    def test_after_condition_invalidates_the_conditional_response_without_a_new_event(self):
        self.inject([{'n': 1, 'kind': 'after', 'value': '2026-10-12T12:00:02+00:00'}])
        before = self.compact('2026-10-12T12:00:01+00:00')
        after = self.compact('2026-10-12T12:00:02+00:00', before['change_token'])
        self.assertFalse(after.get('not_modified', False))
        self.assertEqual(before['registry_head'], after['registry_head'])
        self.assertFalse(before['handover_status'][0]['ready'])
        self.assertTrue(after['handover_status'][0]['ready'])
        self.assertTrue(any(w['kind'] == 'handover' for w in before['attention']['waiting']))
        self.assertTrue(any(w['kind'] == 'handover' for w in after['attention']['for_owner']))
        self.assertFalse(any(w['kind'] == 'handover' for w in after['attention']['waiting']))
        unchanged = self.compact('2026-10-12T12:00:03+00:00', after['change_token'])
        if not unchanged.get('not_modified'):
            # The independent initial integrity audit can finish between requests.
            self.assertTrue(unchanged['handover_status'][0]['ready'])
            self.assertTrue(self.compact('2026-10-12T12:00:03+00:00', unchanged['change_token'])['not_modified'])

    def test_local_wip_and_commit_invalidate_the_gate_without_a_registry_event(self):
        self.inject([])
        first = self.compact(self.stamp)
        self.assertTrue(first['handover_status'][0]['ready'])
        (self.checkout / 'code.py').write_text('VALUE = 1\n')
        dirty = self.compact(self.stamp, first['change_token'])
        self.assertFalse(dirty.get('not_modified', False))
        self.assertFalse(dirty['handover_status'][0]['ready'])
        self.assertEqual(dirty['registry_head'], first['registry_head'])
        subprocess.run(['git', '-C', str(self.checkout), 'add', 'code.py'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.checkout), '-c', 'user.name=Synthetic',
                        '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '-qm', 'Saved WIP'], check=True, capture_output=True)
        clean = self.compact(self.stamp, dirty['change_token'])
        self.assertFalse(clean.get('not_modified', False))
        self.assertTrue(clean['handover_status'][0]['ready'])
        self.assertEqual(clean['registry_head'], first['registry_head'])


class HandoverCard(unittest.TestCase):
    def test_ready_and_pending_cards_render_in_the_correct_panel_in_both_languages(self):
        html = Path(__file__).resolve().parents[1] / 'assets/dashboard.html'
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[1],'utf8');
const names=['handoverCard','ownerAttention','recordedWaiting'];
let code=names.map(name=>{const a=html.indexOf('function '+name+'('),b=html.indexOf('\nfunction ',a+10);return html.slice(a,b)}).join('\n');
class Element{
 constructor(tag,text){this.tag=tag;this.children=[];this.attrs={};if(text!==undefined)this.children.push(String(text))}
 append(...v){this.children.push(...v)}setAttribute(k,v){this.attrs[k]=v}
 get textContent(){return this.children.map(c=>typeof c==='string'?c:c.textContent).join(' ')}
 set innerHTML(value){throw Error('Handover data must stay text')}
}
const node=(tag,text)=>new Element(tag,text),row=(...v)=>{const r=node('div');r.append(...v.filter(x=>x!==undefined).map(x=>node('span',x)));return r};
const state={attention:{for_owner:[],waiting:[],next_steps:[],decided:[]},waits:[]};
const context={node,row,state,UI_LANG:'it',I18N:{},seatName:x=>x,taskTitle:x=>'Task '+x,date:x=>x,
 panel:x=>node('section',x),tr:x=>x,newestFirst:x=>x,newestTasks:x=>x,standbyWithRuns:x=>x,compactWaits:x=>x,
 addEmpty:()=>{},proofLink:()=>{},descriptions:{},stepText:()=>'',waitText:w=>w.text};
vm.createContext(context);vm.runInContext(code,context);
const pending={kind:'handover',id:'handover-test',from:'A',to:'next-A',ready:false,
 conditions:[{n:1,kind:'confirm',value:'<img src=x onerror=bad()>',met:false}],
 gate:{items:[{id:'runs',status:'fail',detail:'Wait for run R'}]}};
for(const lang of ['it','en']){
 context.UI_LANG=lang;state.attention.waiting=[pending];state.attention.for_owner=[];
 const wait=context.recordedWaiting();assert(wait.textContent.includes('<img src=x onerror=bad()>'));
 assert(wait.textContent.includes(lang==='it'?'Da attendere':'Waiting'));
 assert(!context.ownerAttention().textContent.includes('next-A'));
 const ready={...pending,ready:true,conditions:[{...pending.conditions[0],met:true}],gate:{items:[]}};
 state.attention.waiting=[];state.attention.for_owner=[ready];
 assert(context.ownerAttention().textContent.includes(lang==='it'?'riprendi la sede next-A':'resume seat next-A'));
 assert(!context.recordedWaiting().textContent.includes('next-A'));
}
'''
        result = subprocess.run(['node', '-e', script, str(html)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

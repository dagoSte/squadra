"""Check handoffs, requirement coverage and wait times using SQLite and Git."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import unittest

import test_squadra as support
from registry import Store, Error, waits, snapshot, requirement_matrix
from squadra import deliver, bindings
from runtime import execute


class Features(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered

    def configured(self, requirements=None):
        data = {"id": "N", "title": "Bound handoff", "owner": "A", "reviewer": "R", "repo": str(self.repo),
                "base": snapshot(self.repo)["head"], "scope": self.store.artifact(self.scope),
                "proof": None, "depends": [], "requirements": requirements or []}
        self.store.batch([("task", "C", data, None), ("message", "C", {"id": "assign-N", "to": "A", "task": "N", "body": "ACK"}, None)])
        self.ack("N")

    def test_calculated_age_crosses_threshold_without_events(self):
        state = self.store.state(); before = len(self.store.events())
        stamp = dt.datetime.fromisoformat(state["messages"]["assign-T"]["utc"])
        self.assertFalse(waits(state, stamp + dt.timedelta(seconds=899)))
        self.assertEqual(waits(state, stamp + dt.timedelta(seconds=900))[0]["seconds"], 900)
        self.assertEqual(len(self.store.events()), before)
        self.assertFalse(waits(state, stamp - dt.timedelta(seconds=30)))

    def test_zero_threshold_invalid_settings_and_uncertain_stop(self):
        state = self.store.state(); stamp = state["messages"]["assign-T"]["utc"]
        self.assertEqual(waits(state, stamp, {"ack": 0})[0]["seconds"], 0)
        for value in (-1, True, 1.5):
            with self.assertRaises(Error): self.store.append("settings", "C", {"wait_seconds": {"ack": value}})
        self.store.append("settings", "C", {"wait_seconds": {"ack": 0}})
        self.ack(); state=self.store.state()
        state["runs"]["uncertain"]={"id":"uncertain","task":"T","agent":"A","mode":"proof","status":"cleanup-unknown","utc":stamp,"ended":stamp}
        self.assertEqual(waits(state,stamp)[0]["kind"],"cleanup-unknown")

    def test_awaited_seat_never_invents_identity_or_ack(self):
        self.store.append("message", "C", {"id":"awaited","to":"New","awaited":True,"body":"Read plan"})
        self.assertNotIn("New",self.store.state()["agents"])
        with self.assertRaises(Error):self.store.append("ack","R",{"message":"awaited"})
        self.store.append("agent","New",{"id":"New","role":"reviewer","identity":"actual-new-session","client":"manual","repo":str(self.repo)})
        self.assertIsNone(self.store.state()["messages"]["awaited"]["ack"])
        self.store.append("ack","New",{"message":"awaited"})
        with self.assertRaises(Error):self.store.append("message","C",{"id":"duplicate-name","to":"New","awaited":True,"body":"Read"})

    def test_delivery_packet_message_and_ack_are_bound_and_idempotent(self):
        self.configured();self.proof(task="N")
        result=deliver(self.store,"A","N"); message=result["handoff"]
        state=self.store.state();packet=state["tasks"]["N"]["delivery"]["packet_data"]
        self.assertEqual(packet["candidate"],snapshot(self.repo,self.root));self.assertEqual(packet["proofs"][0]["native_rc"],0)
        self.assertIsNone(state["messages"][message]["ack"])
        count=len(self.store.events());deliver(self.store,"C","N",reconcile=True);self.assertEqual(len(self.store.events()),count)
        with self.assertRaises(Error):deliver(self.store,"R","N")
        self.assertEqual(self.cli("review","--actor","R","--task","N","--verdict","accepted","--report",str(self.report)),1)
        self.store.append("ack","R",{"message":message})
        self.assertEqual(self.cli("review","--actor","R","--task","N","--verdict","accepted","--report",str(self.report)),0)
        (self.repo/"code.py").write_text("VALUE = 2\n")
        self.proof(task="N");next_delivery=deliver(self.store,"A","N")
        self.assertNotEqual(next_delivery["handoff"],message)
        self.assertIsNone(self.store.state()["messages"][next_delivery["handoff"]]["ack"])

    def test_candidate_cycle_and_new_proof_need_new_packet_and_ack(self):
        self.configured();messages=[]
        for value in (1,2,1):
            (self.repo/'code.py').write_text('VALUE = '+str(value)+'\n')
            self.proof(task='N');d=deliver(self.store,'A','N');messages.append(d['handoff'])
            self.assertIsNone(self.store.state()['messages'][d['handoff']]['ack'])
        self.assertEqual(len(set(messages)),3)
        count=len(self.store.events());self.assertTrue(deliver(self.store,'C','N',reconcile=True)['already_delivered'])
        self.assertEqual(len(self.store.events()),count)
        self.proof(task='N');new=deliver(self.store,'A','N');self.assertNotIn(new['handoff'],messages)
        self.assertIsNone(self.store.state()['messages'][new['handoff']]['ack'])

    def test_manual_review_refused_while_paused_but_output_and_save_allowed(self):
        self.configured();self.proof(task='N');d=deliver(self.store,'A','N');self.store.append('ack','R',{'message':d['handoff']})
        self.store.append('pause','C',{'reason':'Owner stop'})
        count=len(self.store.events())
        self.assertEqual(self.cli('review','--actor','R','--task','N','--verdict','accepted','--report',str(self.report)),1)
        self.assertEqual(self.store.state()['tasks']['N']['status'],'delivered')
        self.assertFalse(self.store.state()['tasks']['N']['reviews'])
        from recovery import save
        self.assertEqual(save(self.store,self.base/'pause-save',temporary=True)['status'],'saved')
        self.store.append('output','C',{'text':'Checkpoint','next':'Owner resume','artifacts':[]})
        self.store.append('resume','C',{'reason':'Owner explicitly resumed'})
        self.assertEqual(self.cli('review','--actor','R','--task','N','--verdict','accepted','--report',str(self.report)),0)

    def test_delivery_transaction_rolls_back_without_exact_proof(self):
        self.configured(); before=len(self.store.events())
        with self.assertRaises(Error):deliver(self.store,"A","N")
        self.assertEqual(len(self.store.events()),before)
        self.assertNotIn("candidate",self.store.state()["tasks"]["N"])

    def test_alias_cannot_be_configured_reader(self):
        with self.assertRaisesRegex(Error,"independent"):
            self.store.append("task","C",{"id":"bad","owner":"A","reviewer":"A-alias","repo":str(self.repo),"requirements":[]})

    def test_native_and_review_coverage_both_required_on_exact_candidate(self):
        self.configured([{"id":"R1","text":"Keep value"},{"id":"R2","text":"Second invariant"}])
        run=execute(self.store,"A","N",argv=[sys.executable,"-c","print('meaningful native fixture')"],covers=["R1"])
        with self.assertRaisesRegex(Error,"native"):
            deliver(self.store,"A","N")
        execute(self.store,"A","N",argv=[sys.executable,"-c","print('second check')"],covers=["R2"])
        delivery=deliver(self.store,"A","N");self.store.append("ack","R",{"message":delivery["handoff"]})
        args=["review","--actor","R","--task","N","--verdict","accepted","--report",str(self.report)]
        self.assertEqual(self.cli(*args,"--covers","R1"),1)
        self.assertEqual(self.cli(*args,"--covers","R1","--covers","R2"),0)
        self.assertEqual(self.cli("close","--actor","C","--task","N"),0)
        matrix=requirement_matrix(self.store.state()["tasks"]["N"])
        self.assertTrue(all(x["proofs"] and x["reviewed"] for x in matrix))
        (self.repo/"code.py").write_text("VALUE=99\n")
        with self.assertRaisesRegex(Error,"drift"):bindings(self.store,"N")

    def test_unknown_coverage_is_rejected_before_process(self):
        self.configured([{"id":"R1","text":"Keep value"}]);before=len(self.store.events())
        with self.assertRaisesRegex(Error,"Unknown covered"):
            execute(self.store,"A","N",argv=[sys.executable,"-c","raise SystemExit(0)"],covers=["Unknown"])
        self.assertEqual(len(self.store.events()),before)

    def test_workspace_output_and_common_daily_projection(self):
        self.store.append("output","C",{"task":None,"text":"Workspace note","next":"No task reassigned","artifacts":[]})
        self.store.project()
        daily=list((self.root/"posta").glob("exchange-*.md"));self.assertEqual(len(daily),1)
        self.assertIn("Workspace note",daily[0].read_text());self.assertNotIn('"seconds"', (self.root/"state.json").read_text())

    def test_paused_delivery_refuses_even_with_passing_proof(self):
        self.configured();self.proof(task="N");self.store.append("pause","C",{"reason":"Owner stop"})
        with self.assertRaisesRegex(Error,"paused"):deliver(self.store,"A","N")

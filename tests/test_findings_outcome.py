"""Review observations keep an explicit, traceable disposition across rereads."""
import json
import unittest

from test_errata import Fixture, run
from findings import summary
from registry import Error, Store, validate_findings
from source_manifests import transform_packet
from squadra import deliver


class Findings(Fixture, unittest.TestCase):
    def reviewed(self, findings=None, verdict='accepted'):
        self.delivered()
        self.findings = findings or [{'id': 'N1', 'severity': 'minor', 'title': 'Check the boundary'},
                                     {'id': 'N2', 'severity': 'info', 'title': 'Explain the caller'}]
        source = self.base / 'findings.json'; source.write_text(json.dumps(self.findings))
        rc, _, err = run(self.root, 'review', '--actor', 'R', '--task', 'T', '--verdict', verdict,
                         '--report', str(self.report), '--findings-file', str(source))
        self.assertEqual(rc, 0, err)
        self.review = next(e['id'] for e in reversed(self.store.events()) if e['kind'] == 'review')
        self.store.append('decision', 'C', {'id': 'D', 'reason': 'Tracked disposition', 'tasks': ['T']})

    def outcome(self, finding='N1', outcome='rejected', reason='The documented caller already checks it', ref='decision:D'):
        return run(self.root, 'finding', '--actor', 'A', '--task', 'T', '--review', self.review,
                   '--id', finding, '--outcome', outcome, '--ref', ref, '--reason', reason)

    def test_optional_ids_are_validated_without_changing_old_findings(self):
        old = [{'severity': 'minor', 'title': 'Existing observation'}]
        self.assertEqual(validate_findings(old), old)
        for bad in ('', 'n1', 'NN1', 'N', 'N1000', 1, None, 'N1\n'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_findings([{'id': bad, **old[0]}])
        with self.assertRaises(ValueError):
            validate_findings([{'id': 'N1', **old[0]}] * 2)
        self.assertEqual(validate_findings([{'id': 'N999', **old[0]}])[0]['id'], 'N999')

    def test_cli_records_all_outcomes_and_retains_rejected_reasons_after_reopening(self):
        self.reviewed()
        self.assertEqual(self.outcome()[0], 0)
        self.assertEqual(self.outcome('N2', 'fixed', 'The new caller was checked', self.review)[0], 0)
        reopened = Store(self.root); self.addCleanup(reopened.close)
        state = reopened.state(); value = summary(state, state['tasks']['T'])
        self.assertEqual(value['open'], 0)
        self.assertEqual(value['rounds'], [{'review': self.review, 'open': 0, 'total': 2}])
        self.assertEqual(value['rejected'][0]['id'], 'N1')
        self.assertIn('already checks', value['rejected'][0]['reason'])
        self.assertEqual(self.outcome('N2', 'deferred', 'Follow-up belongs to the next task')[0], 0)
        self.assertEqual(len(self.store.state()['tasks']['T']['finding_outcomes']), 3)
        self.assertTrue(reopened.audit())

    def test_invalid_outcomes_and_unknown_references_do_not_settle_a_finding(self):
        self.reviewed()
        valid = {'task': 'T', 'review': self.review, 'finding': 'N1', 'outcome': 'rejected',
                 'ref': 'decision:D', 'reason': 'Caller is already bounded'}
        variants = [({'reason': ''}, 'finding_reason_required'),
                    ({'reason': '  '}, 'finding_reason_required'),
                    ({'reason': 'hidden\x00reason'}, 'finding_reason_required'),
                    ({'outcome': 'ignored'}, 'finding_outcome_invalid'),
                    ({'finding': 'N3'}, 'finding_id_unknown'),
                    ({'review': 'missing'}, 'finding_review_unknown'),
                    ({'ref': 'decision:missing'}, 'finding_ref_unknown'),
                    ({'ref': 'missing-event'}, 'finding_ref_unknown')]
        for change, error in variants:
            with self.subTest(change=change), self.assertRaisesRegex(Error, error):
                self.store.append('finding.outcome', 'A', {**valid, **change})
        with self.assertRaisesRegex(Error, 'finding_actor_refused'):
            self.store.append('finding.outcome', 'A-alias', valid)
        self.assertEqual(summary(self.store.state(), self.store.state()['tasks']['T'])['open'], 2)

    def test_a_bound_proof_event_can_reference_a_fix_but_an_unrelated_event_cannot(self):
        self.reviewed()
        proof = next(e['id'] for e in self.store.events() if e['kind'] == 'run.end')
        rc, _, err = self.outcome(outcome='fixed', reason='The native proof covers the change', ref=proof)
        self.assertEqual(rc, 0, err)
        unrelated = next(e['id'] for e in self.store.events() if e['kind'] == 'agent')
        rc, _, err = self.outcome('N2', 'fixed', 'An unrelated receipt is not evidence', unrelated)
        self.assertEqual(rc, 1)
        self.assertIn('finding_ref_wrong_task', err)

    def test_check_and_checklist_warn_without_blocking_the_normal_close(self):
        self.reviewed()
        rc, result, err = run(self.root, 'check', '--actor', 'C', '--task', 'T')
        self.assertEqual(rc, 0, err)
        self.assertTrue(result['verified'])
        self.assertEqual(result['findings']['status'], 'unverifiable')
        self.assertIn('unverifiable', err)
        rc, result, err = run(self.root, 'checklist', '--moment', 'deliver', '--actor', 'A', '--task', 'T')
        item = next(i for i in result['items'] if i['id'] == 'finding_outcomes')
        self.assertEqual(item['status'], 'unverifiable')
        self.assertNotEqual(item['status'], 'fail')
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)

    def test_context_and_reread_packet_explain_rejections_and_open_round_counts(self):
        self.reviewed(verdict='changes-required')
        self.assertEqual(self.outcome()[0], 0)
        rc, result, err = run(self.root, 'context', '--task', 'T', '--json', '--max-chars', '32000')
        self.assertEqual(rc, 0, err)
        self.assertEqual(result['findings']['open'], 1)
        self.assertEqual(result['findings']['rejected'][0]['ref'], 'decision:D')
        # Legacy tasks have no configured handoff; a real frozen task exercises the packet path.
        task = self.store.state()['tasks']['T']
        declaration = {k: task[k] for k in ('title', 'owner', 'repo', 'base', 'scope', 'proof', 'expected', 'full_gate', 'depends')}
        declaration.update(id='F', reviewer='R', delivery_policy='frozen-v1')
        self.store.batch([('task', 'C', declaration, None), ('message', 'C',
                         {'id': 'assign-F', 'task': 'F', 'to': 'A', 'body': 'Read findings'}, None)])
        self.ack('F'); self.assertEqual(self.proof(task='F')['status'], 'passed')
        handoff = deliver(self.store, 'A', 'F')['handoff']
        self.store.append('ack', 'R', {'message': handoff})
        source = self.base / 'F.json'; source.write_text(json.dumps(self.findings))
        rc, _, err = run(self.root, 'review', '--actor', 'R', '--task', 'F', '--verdict', 'changes-required',
                         '--report', str(self.report), '--findings-file', str(source))
        self.assertEqual(rc, 0, err)
        review = self.store.state()['tasks']['F']['reviews'][-1]['receipt']
        self.store.append('finding.outcome', 'A', {'task': 'F', 'review': review, 'finding': 'N1',
                          'outcome': 'rejected', 'ref': 'decision:D', 'reason': 'The caller was verified'})
        deliver(self.store, 'A', 'F')
        packet = transform_packet(self.store.state()['tasks']['F']['delivery']['packet_data'], self.store.resolve_snapshot)
        self.assertEqual(packet['findings']['open'], 1)
        self.assertEqual(packet['findings']['rejected'][0]['reason'], 'The caller was verified')
        self.store.append('ack', 'R', {'message': self.store.state()['tasks']['F']['delivery']['handoff']})
        rc, _, err = run(self.root, 'review', '--actor', 'R', '--task', 'F', '--verdict', 'accepted',
                         '--report', str(self.report), '--findings-file', str(source))
        self.assertEqual(rc, 0, err)
        state = self.store.state(); value = summary(state, state['tasks']['F'])
        self.assertEqual([r['open'] for r in value['rounds']], [1, 2])
        self.assertEqual(value['rejected'][0]['review'], review)

    def test_unidentified_historical_review_gains_no_outcome_state_or_warning(self):
        self.reviewed([{'severity': 'info', 'title': 'Historical note'}])
        task = self.store.state()['tasks']['T']
        self.assertNotIn('receipt', task['reviews'][-1])
        self.assertNotIn('finding_outcomes', task)
        self.assertEqual(summary(self.store.state(), task)['rounds'], [])
        rc, result, err = run(self.root, 'check', '--actor', 'C', '--task', 'T')
        self.assertEqual(rc, 0, err)
        self.assertNotIn('findings', result)


if __name__ == '__main__':
    unittest.main()

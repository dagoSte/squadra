"""Short receipts preserve the verbose interface and explicit workflow options."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
from squadra import main
from checklist import _round_scope
import test_squadra as support


class QuietOptions(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def test_five_writes_have_one_bounded_receipt_with_real_coordinates(self):
        commands = [('decision', '--actor', 'C', '--reason', 'Round', '--id', 'D'),
                    ('send', '--actor', 'C', '--to', 'A', '--id', 'M', '--body-file', str(self.scope)),
                    ('ack', '--actor', 'A', '--message', 'M'),
                    ('brief', '--actor', 'C', '--task', 'T', '--file', str(self.scope)),
                    ('ack', '--actor', 'A', '--message', 'assign-T')]
        for args in commands:
            with self.subTest(command=args[0]):
                rc, out, err = self.invoke(*args)
                self.assertEqual((rc, err), (0, ''))
                self.assertEqual(len(out.splitlines()), 1)
                self.assertLessEqual(len(out.encode()), 200)
                receipt = json.loads(out)
                self.assertTrue({'committed', 'event', 'seq', 'next'} <= set(receipt))
                self.assertIs(receipt['committed'], True)
                self.assertEqual(receipt['event'], self.store.events()[-1]['id'])
                self.assertEqual(receipt['seq'], self.store.events()[-1]['seq'])

    def test_verbose_before_or_after_command_restores_the_table(self):
        for args in [('--verbose', 'decision', '--actor', 'C', '--reason', 'First'),
                     ('decision', '--actor', 'C', '--reason', 'Second', '--verbose')]:
            rc, out, err = self.invoke(*args)
            self.assertEqual(rc, 0, err)
            self.assertIn('In corso', out)

    def test_short_receipts_keep_transport_outcome_and_generated_ping_id(self):
        self.invoke('send', '--actor', 'C', '--to', 'A', '--id', 'M', '--body-file', str(self.scope))
        rc, out, err = self.invoke('transport', '--actor', 'C', '--message', 'M', '--via', 'manual')
        self.assertEqual(rc, 0, err)
        self.assertIn('delivered', json.loads(out))
        self.assertIsNone(json.loads(out)['delivered'])
        rc, out, err = self.invoke('ping', '--actor', 'C', '--to', 'A')
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)['id'].startswith('ping-'))
        self.assertLessEqual(len(out.encode()), 200)

    def test_round_scope_flag_and_legacy_prefix_are_both_read(self):
        self.assertEqual(self.invoke('decision', '--actor', 'C', '--id', 'flag', '--reason', 'Explicit', '--round-scope')[0], 0)
        self.assertIs(self.store.events()[-1]['data']['round_scope'], True)
        self.assertIn('flag', _round_scope(self.store, self.store.view()['tasks']['T']))
        self.invoke('decision', '--actor', 'C', '--id', 'legacy', '--reason', 'Round scope: T')
        self.assertIn('legacy', _round_scope(self.store, self.store.view()['tasks']['T']))

    def test_ask_resolves_decision_and_ask_to_immutable_event_evidence(self):
        options = self.base / 'options.json'
        options.write_text(json.dumps([{'id': 'A', 'label': 'A', 'say': 'A'}, {'id': 'B', 'label': 'B', 'say': 'B'}]))
        self.invoke('decision', '--actor', 'C', '--id', 'D', '--reason', 'Evidence')
        args = ('ask', '--actor', 'C', '--question', 'Which?', '--options-file', str(options))
        self.assertEqual(self.invoke(*args, '--id', 'Q')[0], 0)
        rc, _, err = self.invoke(*args, '--id', 'Q2', '--evidence', 'decision:D', '--evidence', 'ask:Q')
        self.assertEqual(rc, 0, err)
        evidence = self.store.view()['asks']['Q2']['evidence']
        self.assertEqual([a['original'] for a in evidence], ['decision:D', 'ask:Q'])
        self.assertEqual([json.loads((self.root / 'artifacts' / a['sha256']).read_text())['kind'] for a in evidence], ['decision', 'ask'])
        self.assertNotEqual(self.invoke(*args, '--id', 'Q3', '--evidence', 'decision:missing')[0], 0)


if __name__ == '__main__':
    unittest.main()

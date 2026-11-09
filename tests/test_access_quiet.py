"""Access writes use the common receipt; diagnostics remain complete without a commit."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_access_probe as support
import access
from registry import Store
from squadra import main


class AccessQuiet(unittest.TestCase):
    def setUp(self):
        self.fixture = support.AccessProbe()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    def invoke(self, *, before=(), after=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([*before, 'access', 'probe', '--actor', 'A', '--session', 'recipient-session',
                       '--context', 'sandbox', '--repo', str(self.fixture.repo),
                       '--root', str(self.fixture.root), *after])
        return rc, out.getvalue(), err.getvalue()

    def test_run_returns_its_actual_commit_after_the_store_closes(self):
        result = self.fixture.probe()
        self.assertIn('last_commit', result)
        event = self.store.events()[-1]
        self.assertEqual(result['last_commit'], {'committed': True, 'event': event['id'], 'seq': event['seq']})
        json.dumps(result)  # No lazy data tied to the now-closed writer.

    def test_default_output_is_one_bounded_receipt_for_the_recorded_event(self):
        rc, out, err = self.invoke()
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertLessEqual(len(out.encode('utf-8')), 200)
        event = self.store.events()[-1]
        self.assertEqual(event['kind'], 'access.probe')
        self.assertEqual(json.loads(out), {'committed': True, 'event': event['id'],
                                          'seq': event['seq'], 'next': 'status'})

    def test_verbose_before_or_after_command_keeps_the_complete_report(self):
        for options in ({'before': ['--verbose']}, {'after': ['--verbose']}):
            with self.subTest(options=options):
                rc, out, err = self.invoke(**options)
                self.assertEqual(rc, 0, err)
                report = json.loads(out)
                self.assertTrue(report['recorded'])
                self.assertEqual(report['receipt']['registry']['outcome'], 'writable')
                self.assertEqual(report['access']['clone']['status'], 'verified')
                self.assertNotIn('last_commit', report)

    def test_denied_registry_keeps_the_complete_diagnostic_and_no_commit(self):
        original = access.write_probe
        def probe(path):
            if path == self.fixture.root:
                return {'path': str(path), 'outcome': 'denied', 'error': 'EPERM'}
            return original(path)
        events = self.store.events()
        with patch('access.write_probe', side_effect=probe):
            rc, out, err = self.invoke()
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertFalse(report['recorded'])
        self.assertEqual(report['receipt']['registry']['outcome'], 'denied')
        self.assertNotIn('committed', report)
        self.assertEqual(self.store.events(), events)

    def test_permission_change_before_append_keeps_the_diagnostic(self):
        events = self.store.events()
        with patch.object(Store, 'append', side_effect=PermissionError('Synthetic write refusal')):
            rc, out, err = self.invoke()
        self.assertEqual(rc, 0, err)
        report = json.loads(out)
        self.assertFalse(report['recorded'])
        self.assertEqual(report['record_error'], 'PermissionError')
        self.assertNotIn('committed', report)
        self.assertEqual(self.store.events(), events)

    def test_projection_failure_keeps_the_commit_receipt_and_a_visible_warning(self):
        before = len(self.store.events())
        with patch.object(Store, 'publish', side_effect=OSError('Synthetic projection refusal')):
            rc, out, err = self.invoke()
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(json.loads(out)['committed'])
        self.assertIn('command committed; projections unavailable; do not retry', err)
        self.assertEqual(len(self.store.events()), before + 1)
        self.assertEqual(self.store.events()[-1]['kind'], 'access.probe')

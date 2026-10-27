"""Large failed-run diagnostics and permission writes retain usable projections."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_access_probe
import test_context_reentry
from context_brief import reentry, render
from registry import Store
from squadra import main


class LiveContext(unittest.TestCase):
    def setUp(self):
        self.fixture = test_context_reentry.Reentry()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    def test_large_prove_diagnostic_keeps_event_and_run_summary_within_cap(self):
        diagnostic = {'id': 'failed-proof', 'status': 'failed', 'native_rc': 1,
                      'after': {'files': [{'path': 'source-' + str(n), 'sha256': 'a' * 64}
                                          for n in range(1200)]},
                      'error': 'Native command exit differs from expected result'}
        raw = json.dumps(diagnostic)
        self.store.append('diagnostic', 'system', {'task': 'T', 'command': 'prove', 'error': raw})
        source = self.store.events()[-1]
        before = self.store.events()
        for since in (None, self.fixture.cursor()):
            with self.subTest(since=since is not None):
                rc, out, err = self.fixture.cli('--for', 'A', '--json', '--max-chars', '16000',
                                               *(['--since', since] if since else []))
                self.assertEqual(rc, 0, err)
                self.assertLessEqual(len(out), 16000)
                packet = json.loads(out)
                error = next(r for r in packet['errors'] if r['seq'] == source['seq'])
                self.assertEqual(error['id'], 'failed-proof')
                self.assertEqual(error['status'], 'failed')
                self.assertEqual(error['native_rc'], 1)
                self.assertEqual(error['error'], diagnostic['error'])
                self.assertEqual(error['error_summary']['original_characters'], len(raw))
                self.assertTrue(error['error_summary']['abridged'])
                self.assertNotIn('source-1199', out)
                self.assertIn('unresolved', error['resolution'])
        self.assertEqual(self.store.events(), before)

    def test_large_plain_error_retains_each_event_with_explicit_excerpt(self):
        for text in ('Plain failure ' + 'x' * 90000, 'Unicode failure ' + '\u2603' * 90000):
            self.store.append('diagnostic', 'system', {'task': 'T', 'command': 'prove', 'error': text})
        rc, out, err = self.fixture.cli('--for', 'A', '--since', self.fixture.cursor(),
                                       '--json', '--max-chars', '16000')
        self.assertEqual(rc, 0, err)
        packet = json.loads(out)
        self.assertEqual(len(packet['errors']), 2)
        for entry in packet['errors']:
            self.assertLessEqual(len(entry['error']), 400)
            self.assertTrue(entry['error_summary']['abridged'])
            self.assertGreater(entry['error_summary']['original_characters'], 90000)
        self.assertLessEqual(len(out), 16000)


class LiveAccess(unittest.TestCase):
    def setUp(self):
        self.fixture = test_access_probe.AccessProbe()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.store.publish()

    def test_probe_cli_publishes_its_own_event_immediately(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(['access', 'probe', '--actor', 'A', '--session', 'recipient-session',
                       '--context', 'sandbox', '--repo', str(self.fixture.repo), '--root', str(self.fixture.root)])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out.getvalue())['recorded'])
        event = self.store.events()[-1]
        projected = json.loads((self.fixture.root / 'events.jsonl').read_text().splitlines()[-1])
        self.assertEqual(projected['seq'], event['seq'])
        self.assertEqual(projected['kind'], 'access.probe')
        marker = json.loads((self.fixture.root / 'projection-freshness.json').read_text())
        self.assertTrue(marker['events_current'])
        self.assertEqual(marker['events']['seq'], event['seq'])
        self.assertEqual(marker['events']['head'], event['hash'])

    def test_projection_failure_does_not_disguise_committed_probe(self):
        with patch.object(Store, 'publish', side_effect=OSError('projection unavailable')):
            result = self.fixture.probe()
        self.assertTrue(result['recorded'])
        self.assertIn('projection_error', result)
        self.assertNotIn('record_error', result)
        self.assertEqual(self.store.events()[-1]['kind'], 'access.probe')


if __name__ == '__main__':
    unittest.main()

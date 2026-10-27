"""Access and reader readiness remain visible in per-window re-entry packets."""
import os
import unittest
from unittest.mock import patch

import test_mail_ready
from access import run
from context_brief import reentry
from replacement import execute
from registry import Error, access_view, ready_readers, require_handoff


class ContextIntegrationSeams(unittest.TestCase):
    def setUp(self):
        self.fixture = test_mail_ready.MailReady()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.seat('R', 'outside')

    def seat(self, name, identity, role='reviewer'):
        self.store.append('agent', name, {'id': name, 'role': role, 'identity': identity, 'client': 'claude',
                                          'repo': str(self.fixture.repo), 'skills': [], 'session': 'probe-' + name})

    def cursor(self):
        event = self.store.events()[-1]
        return str(event['seq']) + ':' + event['hash']

    def packet(self, since, recipient='R'):
        return reentry(self.store, 'U', recipient=recipient, since=since, max_chars=32000)

    def probe(self, name):
        with patch.dict(os.environ, {'CLAUDE_SESSION_ID': 'probe-' + name, 'CODEX_THREAD_ID': 'probe-' + name}):
            self.assertTrue(run(self.fixture.root, name, 'probe', str(self.fixture.repo),
                                'probe-' + name, 'sandbox')['recorded'])
        return self.store.events()[-1]['seq']

    def test_access_probe_delta_follows_selected_identity_only(self):
        self.seat('R-alias', 'outside')
        self.seat('OTHER', 'unrelated')
        since = self.cursor()
        own_seq = self.probe('R-alias')
        self.probe('OTHER')
        before = self.store.events()
        packet = self.packet(since)
        rows = [row for row in packet['changes'] if row['kind'] == 'access.probe']
        self.assertEqual([(row['seq'], row['author']) for row in rows], [(own_seq, 'R-alias')])
        self.assertFalse(packet['delta_empty'])
        self.assertEqual(self.store.events(), before)
        self.assertEqual(packet['window']['authority'], 'selection only')
        self.assertEqual(self.packet(since, recipient='A')['changes'], [])

    def test_ready_and_revocation_deltas_preserve_boolean(self):
        for value in (True, False):
            with self.subTest(ready=value):
                since = self.cursor()
                self.fixture.ready(value)
                rows = [row for row in self.packet(since)['changes'] if row['kind'] == 'reader.ready']
                self.assertEqual(len(rows), 1)
                self.assertIs(rows[0]['data'].get('ready'), value)

    def test_empty_delta_retains_readiness_and_replacement_does_not_transfer_it(self):
        self.fixture.ready()
        packet = self.packet(self.cursor())
        self.assertTrue(packet['delta_empty'])
        self.assertEqual(packet['references']['task'].get('reader_ready'),
                         self.store.state()['tasks']['U']['reader_ready'])
        self.assertEqual(packet['references']['task']['seq'], self.store.events()[-1]['seq'])
        execute(self.store, 'C', {'map': {'R': 'NEW'}, 'sessions': {'NEW': 'new-reader'}, 'client': 'manual',
                                 'carry': 'none', 'force': False, 'reason': 'Reader turnover'})
        state = self.store.state()
        packet = self.packet(self.cursor(), recipient='NEW')
        self.assertFalse(packet['references']['task'].get('reader_ready'))
        self.assertEqual(packet['references']['task']['reviewer'], 'NEW')
        self.assertEqual(ready_readers(state, state['tasks']['U']), [])
        self.assertEqual(access_view(state, 'NEW')['clone']['status'], 'not-verified')
        with self.assertRaises(Error):
            require_handoff(state, state['tasks']['U'], 'NEW', 'reading')


if __name__ == '__main__':
    unittest.main()

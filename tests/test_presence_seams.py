"""Pin the entrant-to-coordinator ping seam: reuse, transport and the coordinator's own sweep."""
import datetime as dt
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Error, latest_ping, presence, standby


class CoordinatorPingSeams(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    cli = support.Workflow.cli

    def at(self, ping, seconds):
        return (dt.datetime.fromisoformat(ping['utc']) + dt.timedelta(seconds=seconds)).isoformat()

    def sweep(self, at):
        with patch('squadra.now', return_value=at), patch('registry.now', return_value=at):
            return self.cli('ping', '--actor', 'C', '--sweep')

    def test_second_entrant_can_hand_over_the_reused_coordinator_ping(self):
        self.assertEqual(self.cli('ping', '--actor', 'A', '--to', 'C'), 0)
        first = latest_ping(self.store.state(), 'C')
        self.assertEqual(self.cli('ping', '--actor', 'R', '--to', 'C'), 0)
        self.assertEqual(latest_ping(self.store.state(), 'C')['id'], first['id'])
        self.assertEqual(self.cli('transport', '--actor', 'R', '--message', first['id'], '--via', 'manual'), 0)
        self.assertEqual(self.cli('ack', '--actor', 'C', '--message', first['id']), 0)
        self.assertIsNotNone(latest_ping(self.store.state(), 'C')['ack'])

    def test_worker_cannot_transport_another_workers_message(self):
        self.assertEqual(self.cli('ping', '--actor', 'C', '--to', 'A'), 0)
        ping = latest_ping(self.store.state(), 'A')
        self.assertEqual(self.cli('transport', '--actor', 'R', '--message', ping['id'], '--via', 'manual'), 1)

    def test_plain_message_to_the_coordinator_stays_sender_only(self):
        self.store.append('message', 'A', {'id': 'PLAIN', 'to': 'C', 'body': 'Ordinary note'})
        self.assertEqual(self.cli('transport', '--actor', 'R', '--message', 'PLAIN', '--via', 'manual'), 1)
        self.assertEqual(self.cli('transport', '--actor', 'A', '--message', 'PLAIN', '--via', 'manual'), 0)

    def test_validator_refuses_a_sweep_standby_of_its_own_identity(self):
        self.assertEqual(self.cli('ping', '--actor', 'A', '--to', 'C'), 0)
        ping = latest_ping(self.store.state(), 'C')
        self.store.append('transport', 'A', {'message': ping['id'], 'via': 'codex-queue', 'delivered': True})
        later = self.at(ping, 10 ** 6)
        record = {'agent': 'C', 'status': 'stand-by', 'ping': ping['id'], 'reason': 'Overdue'}
        with patch('registry.now', return_value=later):
            self.assertTrue(next(r for r in presence(self.store.state(), later) if r['agent'] == 'C')['due'])
            with self.assertRaisesRegex(Error, 'never stands its own coordinator by'):
                self.store.append('availability', 'C', record)
        self.assertFalse(standby(self.store.state(), 'C'))

    def test_coordinator_sweep_never_stands_its_own_identity_by(self):
        self.assertEqual(self.cli('ping', '--actor', 'A', '--to', 'C'), 0)
        to_coordinator = latest_ping(self.store.state(), 'C')
        self.assertEqual(self.cli('ping', '--actor', 'C', '--to', 'R'), 0)
        to_worker = latest_ping(self.store.state(), 'R')
        for ping, sender in ((to_coordinator, 'A'), (to_worker, 'C')):
            self.store.append('transport', sender, {'message': ping['id'], 'via': 'codex-queue', 'delivered': True})
        self.assertEqual(self.sweep(self.at(max(to_coordinator, to_worker, key=lambda p: p['utc']), 301)), 0)
        self.assertFalse(standby(self.store.state(), 'C'))
        self.assertTrue(standby(self.store.state(), 'R'))


if __name__ == '__main__':
    unittest.main()

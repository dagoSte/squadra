"""Regressions for truncated age, cold doctor collection and cross-process status."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import registry
import ripresa
import usage
import test_usage as support

NOW = support.NOW


class TirednessFix(unittest.TestCase):
    setUp = support.UsageCollector.setUp
    seat = support.UsageCollector.seat
    write = support.UsageCollector.write
    collect = support.UsageCollector.collect
    one = support.UsageCollector.one

    def test_truncated_samples_do_not_invent_a_session_start(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 9 * 3600, key='old'),
                              support.claude(NOW - 3600, key='middle'),
                              support.claude(NOW - 1, key='latest')])
        with patch.object(usage, 'MAX_RECORDS', 2):
            _, row = self.one()
        self.assertIn('sample_limit', row['reasons'])
        self.assertIsNone(row['tiredness']['session_started'])
        self.assertIsNone(row['tiredness']['age_hours'])
        complete = usage.Collector(self.roots).collect(self.events, self.state, NOW, 2)
        self.assertEqual(complete['windows'][0]['tiredness']['age_hours'], 9)

    def test_oversized_record_does_not_make_the_remaining_sample_a_start(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 3600)])
        self.one()
        source = next(iter(next(iter(usage._COLLECTORS.values())).sources.values()))
        source.reasons.add('record_too_large')
        source._normal = None
        self.assertIsNone(usage.tiredness([source], NOW, {})['age_hours'])

    def test_cold_doctor_finds_age_after_discovery_uses_a_quarter_second(self):
        self.seat()
        self.state = {**registry.initial(), **self.state}
        self.state['agents']['builder'].update(id='builder', role='executor')
        self.write('claude', [support.claude(NOW - 9 * 3600)])
        original = deepcopy(self.state)
        start_walk = usage.Collector.start_walk

        def discovery(collector, *args):
            time.sleep(.3)
            return start_walk(collector, *args)

        with patch.object(usage, 'source_roots', return_value=self.roots), \
                patch.object(usage.Collector, 'start_walk', discovery):
            result = ripresa.readiness(self.state, datetime.fromtimestamp(NOW, timezone.utc))
        self.assertTrue(any('sessione aperta da 9.0 ore' in n for n in result['notices']))
        self.assertEqual(result['tired_windows'][0]['reasons'], ['age'])
        self.assertTrue(result['ready'])
        self.assertEqual(self.state, original)


class TiredStatus(unittest.TestCase):
    def setUp(self):
        support.UsageCollector.setUp(self)
        self.home = self.folder / 'home'
        self.home.mkdir(mode=0o700)
        self.roots = {client: self.home / client / leaf
                      for client, leaf in [('claude', 'projects'), ('codex', 'sessions')]}
        for root in self.roots.values():
            root.mkdir(parents=True, mode=0o700)
        self.env = dict(HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.home / 'claude'),
                        CODEX_HOME=str(self.home / 'codex'), PYTHONDONTWRITEBYTECODE='1')
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)
        self.project = self.home / 'project'
        self.project.mkdir()
        self.store = registry.Store(self.home / 'registry', create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'test', 'project': str(self.project), 'lang': 'it'})
        self.store.append('agent', 'builder', {'id': 'builder', 'identity': 'window-one',
                          'session': support.CLAUDE, 'client': 'claude', 'role': 'coordinator',
                          'repo': str(self.project), 'skills': []})
        self.state = registry.plain(self.store.state())
        self.events = self.store.events()
        support.UsageCollector.write(self, 'claude', [support.claude(NOW - 9 * 3600)])

    def seed(self):
        data = usage.collect(self.events, self.state, now=NOW, budget_seconds=2)
        self.assertEqual(data['windows'][0]['tiredness']['reasons'], ['age'])

    def status(self, *, at=NOW, mutation=None):
        program = '''import sys
sys.path.insert(0, sys.argv[1])
import squadra, usage
def forbidden(*a, **kw):
    raise AssertionError('status must not discover or read transcripts')
usage.collect = forbidden
usage.Source.read = forbidden
usage.Collector.discover = forbidden
squadra.now = lambda: sys.argv[3]
if sys.argv[4]:
    from registry import Store
    store = Store(sys.argv[2])
    kind, actor, data = __import__('json').loads(sys.argv[4])
    store.append(kind, actor, data)
    store.close()
raise SystemExit(squadra.main(['--root', sys.argv[2], 'status']))
'''
        run = subprocess.run([sys.executable, '-B', '-c', program, str(ROOT / 'scripts'),
                              str(self.store.root), support.stamp(at),
                              json.dumps(mutation) if mutation else ''],
                             env=dict(os.environ), capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_fresh_process_status_shows_persisted_notice_without_transcripts(self):
        self.seed()
        before = self.store.events()
        for path in self.roots['claude'].rglob('*.jsonl'):
            path.unlink()
        output = self.status(at=NOW + 59)
        self.assertIn('builder: conviene ripartire: sessione aperta da 9.0 ore', output)
        self.assertIn('squadra handover prepare', output)
        self.assertNotIn('%', output)
        self.assertEqual(self.store.events(), before)

    def test_status_rejects_stale_future_session_and_settings(self):
        self.seed()
        for at in (NOW - 1, NOW + 61):
            with self.subTest(at=at):
                self.assertNotIn('conviene ripartire', self.status(at=at))
        agent = deepcopy(self.state['agents']['builder'])
        self.assertNotIn('conviene ripartire', self.status(
            mutation=['agent', 'builder', {**agent, 'session': support.CHILD}]))
        # Restore the original seat before changing the settings alone.
        self.store.append('agent', 'builder', agent)
        self.assertNotIn('conviene ripartire', self.status(
            mutation=['settings', 'builder', registry.wait_settings({'tired_hours': 10})]))

    def test_missing_cache_status_is_quiet_and_does_not_create_it(self):
        self.assertNotIn('conviene ripartire', self.status())
        self.assertFalse((self.home / '.cache').exists())


if __name__ == '__main__':
    unittest.main()

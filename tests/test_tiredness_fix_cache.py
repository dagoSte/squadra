"""Persisted notices keep usage-cache projection and read-only status contracts."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import usage
import usage_cache
import registry
import test_tiredness_fix as support
import test_usage as fixture

NOW = support.NOW


class PersistedTiredness(unittest.TestCase):
    setUp = support.TiredStatus.setUp
    seed = support.TiredStatus.seed
    status = support.TiredStatus.status

    def test_exact_freshness_and_all_binding_fields_without_source_io(self):
        self.seed()
        with patch.object(usage.Source, 'read', side_effect=AssertionError('no transcript reads')):
            self.assertIn('builder', usage.status_tiredness(self.state, NOW + 60))
            self.assertEqual(usage.status_tiredness(self.state, NOW + 60.001), {})
            for changed in ({'identity': 'other'}, {'client': 'codex'}, {'session': fixture.CHILD}):
                state = deepcopy(self.state)
                state['agents']['builder'].update(changed)
                with self.subTest(agent=changed):
                    self.assertEqual(usage.status_tiredness(state, NOW), {})
            for changed in ({'tired_hours': 10}, {'tired_context_fraction': .8},
                            {'context_window': {'claude-sonnet-4-5': 100000}}):
                state = deepcopy(self.state)
                state['workspace'].update(changed)
                with self.subTest(settings=changed):
                    self.assertEqual(usage.status_tiredness(state, NOW), {})

    def test_status_is_read_only_and_uses_the_current_language(self):
        self.seed()
        cache = usage._tired_cache(self.state)
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.folder.iterdir()}
        self.assertIn('conviene ripartire', self.status())
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.folder.iterdir()}, files)
        self.state['workspace']['lang'] = 'en'
        text = usage.status_notices(self.state, NOW)[0]
        self.assertIn('consider restarting: session age 9.0 hours', text)
        self.assertNotIn('%', text)
        for raw, _ in files.values():
            for forbidden in (fixture.CANARY, str(self.home), 'window-one', 'builder'):
                self.assertNotIn(forbidden.encode(), raw)

    def test_malformed_notice_payloads_with_valid_digests_are_ignored(self):
        self.seed()
        cache = usage._tired_cache(self.state)
        path = cache.folder / (cache.key + '.notice')
        good = path.read_bytes()
        value = cache.load(notices=True)
        binding = next(iter(value))
        mutations = [lambda v: v.update(content=fixture.CANARY),
                     lambda v: v.update(age_hours=True),
                     lambda v: v.update(context_tokens=-1),
                     lambda v: v.update(reasons=['context']),
                     lambda v: v['source'].update(observed_at=fixture.CANARY)]
        for mutate in mutations:
            damaged = deepcopy(value)
            mutate(damaged[binding]['value'])
            with self.subTest(value=damaged):
                self.assertFalse(cache.save(damaged, notices=True))
                self.assertEqual(path.read_bytes(), good)
                payload = usage_cache.encoded(damaged)
                path.write_bytes(usage_cache.encoded(dict(version=1, notices=damaged,
                                 digest=hashlib.sha256(payload).hexdigest())))
                self.assertEqual(usage.status_tiredness(self.state, NOW), {})
                path.write_bytes(good)
        self.assertIn('builder', usage.status_tiredness(self.state, NOW))

    def test_unsafe_cache_is_quiet_and_does_not_follow_links(self):
        self.seed()
        cache = usage._tired_cache(self.state)
        path = cache.folder / (cache.key + '.notice')
        outside = self.home / 'outside'
        outside.write_bytes(path.read_bytes())
        outside.chmod(0o600)
        before = outside.read_bytes()
        path.unlink()
        path.symlink_to(outside)
        self.assertNotIn('conviene ripartire', self.status())
        self.assertEqual(outside.read_bytes(), before)

    def test_cold_doctor_cli_reads_real_transcript_and_preserves_registry(self):
        before = self.store.events()
        program = '''import sys,time
sys.path.insert(0,sys.argv[1])
import squadra, usage
original = usage.Collector.start_walk
def delayed(self,*args):
    time.sleep(.3)
    return original(self,*args)
usage.Collector.start_walk = delayed
squadra.now = lambda: sys.argv[3]
raise SystemExit(squadra.main(['--root',sys.argv[2],'doctor','--resume']))
'''
        run = subprocess.run([sys.executable, '-B', '-c', program, str(ROOT / 'scripts'),
                              str(self.store.root), fixture.stamp(NOW)],
                             env=dict(os.environ), capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('sessione aperta da 9.0 ore', run.stdout)
        self.assertEqual(self.store.events(), before)

    def test_known_context_percentage_and_a_new_measurement_replace_the_notice(self):
        self.store.append('settings', 'builder', registry.wait_settings(
            {'context_window': {'claude-sonnet-4-5': 100000}}))
        self.state = registry.plain(self.store.state())
        fixture.UsageCollector.write(self, 'claude', [fixture.claude(NOW - 1, inp=60000)])
        usage.collect(self.events, self.state, now=NOW, budget_seconds=2)
        self.assertIn('contesto 60%', self.status())
        fixture.UsageCollector.write(self, 'claude', [fixture.claude(NOW, inp=100)])
        usage.collect(self.events, self.state, now=NOW, budget_seconds=2)
        self.assertNotIn('conviene ripartire', self.status())


if __name__ == '__main__':
    unittest.main()

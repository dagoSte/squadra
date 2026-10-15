"""Real process restarts reuse the disposable numeric collector cache."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import usage
from test_usage_live import CLAUDE, CANARY, NOW, claude, line


CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import usage
config = json.load(sys.stdin)
original, parsed = usage.project, []
def project(raw, client):
    if config['mode'] == 'no_parse':
        raise AssertionError('a restored complete source was reparsed')
    if config['mode'] == 'new_only' and b'"old"' in raw:
        raise AssertionError('restart plus append reparsed an old record')
    parsed.append(len(raw))
    return original(raw, client)
usage.project = project
data = usage.collect(config['events'], config['state'], roots=config['roots'],
                     now=config['now'], budget_seconds=2)
print(json.dumps({'data': data, 'parsed_records': len(parsed), 'parsed_bytes': sum(parsed)}))
'''


class UsageLiveCacheIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-usage-cache-process-')
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.home = self.folder / 'home'; self.home.mkdir(mode=0o700)
        self.roots = {client: self.folder / client for client in ('claude', 'codex')}
        for root in self.roots.values(): root.mkdir(mode=0o700)
        self.env = {**os.environ, 'HOME': str(self.home), 'CODEX_HOME': str(self.home / '.codex'),
                    'CLAUDE_CONFIG_DIR': str(self.home / '.claude')}
        env = patch.dict(os.environ, self.env); env.start(); self.addCleanup(env.stop)
        usage._COLLECTORS.clear(); self.addCleanup(usage._COLLECTORS.clear)
        self.events = []
        self.state = {'agents': {'builder': {'identity': 'claude-window-' + CLAUDE,
                      'client': 'claude', 'session': CLAUDE}}, 'tasks': {}}
        self.path = self.roots['claude'] / (CLAUDE + '.jsonl')
        self.path.write_bytes(line(claude('old', 37))); self.path.chmod(0o600)

    def collect(self):
        return usage.collect(self.events, self.state, roots=self.roots, now=NOW, budget_seconds=2)

    def populated(self):
        result = self.collect()
        self.assertEqual(result['windows'][0]['totals']['total'], 37)
        entries = list((self.home / '.cache/squadra/usage-v1').glob('*.json'))
        self.assertEqual(len(entries), 1, 'real collector must save a usable cache entry')
        return entries[0]

    def restart(self, mode='ordinary'):
        config = {'events': self.events, 'state': self.state,
                  'roots': {k: str(v) for k, v in self.roots.items()}, 'now': NOW, 'mode': mode}
        run = subprocess.run([sys.executable, '-I', '-B', '-c', CHILD, str(ROOT / 'scripts')],
                             input=json.dumps(config), capture_output=True, text=True,
                             env=self.env, cwd=self.folder, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(CANARY, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def test_complete_cache_reused_by_a_new_isolated_process_without_reparse(self):
        self.populated()
        result = self.restart('no_parse')
        self.assertEqual(result['parsed_records'], 0)
        self.assertEqual(result['data']['windows'][0]['totals']['total'], 37)
        self.assertEqual(result['data']['windows'][0]['status'], 'measured')

    def test_restart_then_append_parses_only_the_new_complete_record(self):
        self.populated()
        with self.path.open('ab') as stream: stream.write(line(claude('new', 19)))
        result = self.restart('new_only')
        self.assertEqual(result['parsed_records'], 1)
        self.assertEqual(result['data']['windows'][0]['totals']['total'], 56)
        self.assertEqual(result['data']['windows'][0]['status'], 'measured')

    def test_missing_corrupt_unknown_version_or_unsafe_cache_rebuilds(self):
        entry = self.populated()
        original = entry.read_bytes()
        for case in ('missing', 'corrupt', 'version', 'mode'):
            with self.subTest(case=case):
                entry.parent.chmod(0o700)
                entry.write_bytes(original); entry.chmod(0o600)
                if case == 'missing': entry.unlink()
                elif case == 'corrupt': entry.write_bytes(b'{broken')
                elif case == 'version':
                    data = json.loads(original); data['version'] = 999
                    entry.write_text(json.dumps(data))
                else: entry.parent.chmod(0o777)
                result = self.restart()
                self.assertEqual(result['parsed_records'], 1)
                self.assertEqual(result['data']['windows'][0]['totals']['total'], 37)
                self.assertEqual(result['data']['windows'][0]['status'], 'measured')
        entry.parent.chmod(0o700)

    def test_restored_sample_mismatch_rebuilds_instead_of_retaining_old_counts(self):
        self.populated()
        self.path.write_bytes(line(claude('old', 11)))
        result = self.restart()
        self.assertEqual(result['parsed_records'], 1)
        self.assertEqual(result['data']['windows'][0]['totals']['total'], 11)

    def test_expired_slice_never_publishes_unverified_restored_counts(self):
        self.populated()
        self.path.write_bytes(line(claude('old', 11)))
        usage._COLLECTORS.clear()
        collector = usage.get_collector(self.events, self.state, self.roots)
        collector.discover(usage.seats(self.state), time.monotonic() + 2)
        waiting = usage.collect(self.events, self.state, roots=self.roots,
                                now=NOW, budget_seconds=0)
        self.assertIsNone(waiting['windows'][0]['totals'])
        self.assertEqual(waiting['coverage']['status'], 'collecting')
        verified = self.collect()
        self.assertEqual(verified['windows'][0]['totals']['total'], 11)
        self.assertEqual(verified['windows'][0]['status'], 'measured')

    def test_cache_contains_only_projection_and_never_changes_sources_or_registry(self):
        registry = self.folder / 'registry'; registry.mkdir(mode=0o700)
        project = self.folder / 'project'; project.mkdir(mode=0o700)
        for name in ('events.jsonl', 'state.json', 'projection.md'):
            (registry / name).write_text(CANARY + '\n')
        self.state['workspace'] = {'root': str(registry), 'project': str(project)}
        self.state['tasks']['T'] = {'title': CANARY}
        state_before = json.dumps(self.state, sort_keys=True)
        paths = [self.path, *registry.iterdir()]
        before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in paths}
        entry = self.populated()
        raw = entry.read_bytes()
        self.assertNotIn(CANARY.encode(), raw)
        self.assertNotIn(str(self.path).encode(), raw)
        self.assertNotIn(b'"content"', raw)
        self.assertNotIn(b'"message"', raw)
        self.assertEqual(len(entry.stem), 64)
        self.assertEqual(entry.stat().st_mode & 0o777, 0o600)
        self.assertEqual(entry.parent.stat().st_mode & 0o777, 0o700)
        result = self.restart('no_parse')
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(json.dumps(self.state, sort_keys=True), state_before)
        self.assertEqual({p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                          for p in paths}, before)


class UsageCacheRecoveryExclusion(unittest.TestCase):
    def setUp(self):
        import test_squadra as support
        self.git = support.Workflow.git.__get__(self)
        self.task = support.Workflow.task.__get__(self)
        support.Workflow.setUp(self)
        env = patch.dict(os.environ, self.env); env.start(); self.addCleanup(env.stop)
        usage._COLLECTORS.clear(); self.addCleanup(usage._COLLECTORS.clear)

    def test_checkpoint_and_save_packages_exclude_the_external_calculation_cache(self):
        from recovery import save, verify_package
        roots = {c: self.base / ('transcripts-' + c) for c in ('claude', 'codex')}
        for root in roots.values(): root.mkdir(mode=0o700)
        source = roots['claude'] / (CLAUDE + '.jsonl')
        source.write_bytes(line(claude('old', 37))); source.chmod(0o600)
        state = {'workspace': {'project': str(self.repo), 'root': str(self.root)},
                 'agents': {'A': {'identity': 'claude-window-' + CLAUDE,
                                 'client': 'claude', 'session': CLAUDE}}, 'tasks': {}}
        usage.collect([], state, roots=roots, now=NOW, budget_seconds=2)
        entries = list((self.home / '.cache/squadra/usage-v1').glob('*.json'))
        self.assertEqual(len(entries), 1)
        entry = entries[0]; cache = entry.read_bytes()
        checkpoint, saved = self.base / 'checkpoint', self.base / 'saved'
        self.store.checkpoint(checkpoint)
        result = save(self.store, saved, temporary=True)
        self.assertEqual(result['status'], 'saved')
        verify_package(saved)
        for package in (checkpoint, saved):
            files = [p for p in package.rglob('*') if p.is_file() and not p.is_symlink()]
            self.assertTrue(files)
            self.assertFalse(any(p.name == entry.name for p in files))
            self.assertFalse(any(cache in p.read_bytes() for p in files))
        self.assertEqual(entry.read_bytes(), cache)


if __name__ == '__main__':
    unittest.main()

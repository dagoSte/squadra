"""Disposable usage cache: strict numeric projection and private bounded persistence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import usage
import usage_cache as cache

KEY = 'a' * 64
SOURCE = 'b' * 64
SESSION = '12345678-1234-4567-8123-123456789abc'
CANARY = 'PRIVATE_CONVERSATION_NEVER_PERSIST_bearer@example.invalid'


def fixture(client='claude'):
    if client == 'claude':
        rows = [{'type': 'assistant', 'timestamp': '2026-10-15T14:00:00Z', 'sessionId': SESSION,
                 'message': {'id': CANARY, 'model': 'claude-sonnet-4-5', 'content': CANARY,
                             'usage': {'input_tokens': 10, 'output_tokens': 3}}}]
    else:
        rows = [{'type': 'session_meta', 'payload': {'id': SESSION, 'cwd': CANARY}},
                {'type': 'turn_context', 'timestamp': '2026-10-15T14:00:00Z',
                 'payload': {'model': 'gpt-5.4', 'instructions': CANARY}},
                {'type': 'event_msg', 'timestamp': '2026-10-15T14:00:01Z', 'payload': {
                    'type': 'token_count', 'message': CANARY, 'info': {'total_token_usage': {
                        'input_tokens': 12, 'cached_input_tokens': 2, 'output_tokens': 5,
                        'reasoning_output_tokens': 3, 'total_tokens': 17}}, 'rate_limits': {
                            'primary': {'used_percent': 12.5, 'window_minutes': 300, 'resets_at': 1792076400},
                            'plan_type': 'pro'}}}]
    blob = dict(client=client, session=SESSION, stamp=[1, 2, 10000, 1792072800000000000, 1792072800000000000],
                cursor=0, end=10000, append_cursor=10000, backfill_end=10000,
                fingerprint=['c' * 64, 'd' * 64],
                records=[[i * 100, usage.project(json.dumps(row).encode(), client)] for i, row in enumerate(rows)],
                reasons=[], observed=1792072800.25, pending=False, checked=True)
    return {SOURCE: blob}


class UsageCache(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sq-usage-cache-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.home.chmod(0o700)
        self.cache = cache.Cache(KEY, home=self.home)
        self.path = self.cache.folder / (KEY + '.json')

    def rewrite(self, data):
        self.path.write_bytes(cache.encoded(data))
        self.path.chmod(0o600)

    def envelope(self, value):
        return dict(version=cache.VERSION, sources=value,
                    digest=hashlib.sha256(cache.encoded(value)).hexdigest())

    def test_round_trip_both_clients_has_only_projected_metadata_and_private_modes(self):
        value = fixture()
        value['e' * 64] = fixture('codex')[SOURCE]
        original = deepcopy(value)
        self.assertTrue(self.cache.save(value))
        self.assertEqual(self.cache.load(), value)
        self.assertEqual(value, original)
        for path in self.home.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
            if path.is_file():
                raw = path.read_text()
                self.assertNotIn(CANARY, raw)
                self.assertNotIn(str(self.home), raw)
                self.assertNotIn('content', raw)
                self.assertNotIn('instructions', raw)
        self.assertEqual(self.cache.load()['e' * 64]['stamp'][3], 1792072800000000000)

    def test_fresh_process_restores_same_projected_values(self):
        value = fixture('codex')
        self.assertTrue(self.cache.save(value))
        program = ('import json,sys;sys.path.insert(0,sys.argv[1]);from usage_cache import Cache;'
                   'print(json.dumps(Cache(sys.argv[2]).load(),sort_keys=True))')
        env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / 'codex'),
                   CLAUDE_CONFIG_DIR=str(self.home / 'claude'), PYTHONDONTWRITEBYTECODE='1')
        run = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(ROOT / 'scripts'), KEY],
                             env=env, cwd='/private/tmp', capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), value)
        self.assertNotIn(CANARY, run.stdout + run.stderr)

    def test_missing_cache_and_missing_home_create_nothing_on_load(self):
        self.assertEqual(self.cache.load(), {})
        self.assertEqual(list(self.home.iterdir()), [])
        absent = cache.Cache(KEY, home=self.home / 'absent')
        self.assertEqual(absent.load(), {})
        self.assertFalse(absent.save(fixture()))
        self.assertFalse((self.home / 'absent').exists())

    def test_corruption_version_duplicate_keys_and_digest_are_ignored(self):
        self.assertTrue(self.cache.save(fixture()))
        good = self.path.read_bytes()
        damaged = [b'{', b'[]', b'{"version":1,"version":1}', good.replace(b'"version":1', b'"version":2'),
                   good.replace(b'"input":10', b'"input":11'), good.replace(b'"observed":1792072800.25', b'"observed":NaN')]
        for raw in damaged:
            with self.subTest(raw=raw[:60]):
                self.path.write_bytes(raw)
                self.assertEqual(self.cache.load(), {})
        self.path.write_bytes(good)
        self.assertEqual(self.cache.load(), fixture())

    def test_valid_digest_does_not_allow_text_unknown_fields_or_invalid_numbers(self):
        self.assertTrue(self.cache.save(fixture()))
        good = fixture()
        mutations = [
            lambda v: v[SOURCE].update(content=CANARY),
            lambda v: v[SOURCE]['records'][0][1].update(text=CANARY),
            lambda v: v[SOURCE]['records'][0][1].update(key=CANARY),
            lambda v: v[SOURCE]['records'][0][1].update(model=CANARY),
            lambda v: v[SOURCE].update(reasons=[CANARY]),
            lambda v: v[SOURCE].update(fingerprint=[CANARY, 'd' * 64]),
            lambda v: v[SOURCE].update(cursor=10001),
            lambda v: v[SOURCE].update(checked=1),
            lambda v: v[SOURCE]['records'][0][1]['tokens'].update(input=2**53),
            lambda v: v[SOURCE]['records'][0][1]['tokens'].update(output=True),
            lambda v: v[SOURCE]['records'][0][1]['tokens'].update(total=999),
            lambda v: v[SOURCE].update(observed=float('inf')),
            lambda v: v[SOURCE]['records'].append(deepcopy(v[SOURCE]['records'][0])),
        ]
        for i, mutate in enumerate(mutations):
            value = deepcopy(good)
            mutate(value)
            with self.subTest(mutation=i):
                self.assertFalse(self.cache.save(value))
                if i != 11:
                    self.rewrite(self.envelope(value))
                    self.assertEqual(self.cache.load(), {})
        self.assertTrue(self.cache.save(good))
        self.assertEqual(self.cache.load(), good)

    def test_codex_quota_and_counter_invariants_are_validated(self):
        self.assertTrue(self.cache.save(fixture('codex')))
        cases = [
            lambda r: r['quota'][0].update(used_percent=101),
            lambda r: r['quota'][0].update(plan_type=CANARY),
            lambda r: r['quota'][0].update(resets_at=CANARY),
            lambda r: r['quota'].append(deepcopy(r['quota'][0])),
            lambda r: r['tokens'].update(reasoning=6),
            lambda r: r['tokens'].update(cache_creation=1, total=18),
        ]
        for index, mutate in enumerate(cases):
            value = fixture('codex')
            mutate(value[SOURCE]['records'][2][1])
            with self.subTest(mutation=index):
                self.rewrite(self.envelope(value))
                self.assertEqual(self.cache.load(), {})
                self.assertFalse(self.cache.save(value))

    def test_source_and_record_limits_are_global(self):
        with patch.object(cache, 'MAX_SOURCES', 0):
            self.assertFalse(self.cache.save(fixture()))
        value = fixture()
        value['e' * 64] = deepcopy(value[SOURCE])
        with patch.object(cache, 'MAX_RECORDS', 1):
            self.assertFalse(self.cache.save(value))
            self.assertTrue(self.cache.save(fixture()))
        self.assertEqual(self.cache.load(), fixture())

    def test_every_owned_parent_mode_is_checked_without_chmod_repair(self):
        self.assertTrue(self.cache.save(fixture()))
        for path in (self.home, self.home / '.cache', self.home / '.cache/squadra', self.cache.folder):
            original = stat.S_IMODE(path.stat().st_mode)
            for mode in (0o775, 0o707):
                with self.subTest(path=path.name, mode=mode):
                    path.chmod(mode)
                    self.assertEqual(self.cache.load(), {})
                    self.assertFalse(self.cache.save(fixture()))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                    path.chmod(original)
        self.cache.folder.chmod(0o755)
        self.assertEqual(self.cache.load(), {})
        self.assertFalse(self.cache.save(fixture()))
        self.cache.folder.chmod(0o700)
        for path in (self.home, self.home / '.cache', self.home / '.cache/squadra'):
            path.chmod(0o755)
        self.assertTrue(self.cache.save(fixture()))
        self.assertEqual(self.cache.load(), fixture())

    def test_every_parent_symlink_is_refused(self):
        self.assertTrue(self.cache.save(fixture()))
        for path in (self.cache.folder, self.home / '.cache/squadra', self.home / '.cache'):
            moved = path.with_name(path.name + '-aside')
            path.rename(moved)
            path.symlink_to(moved, target_is_directory=True)
            with self.subTest(parent=path.name):
                self.assertEqual(self.cache.load(), {})
                self.assertFalse(self.cache.save(fixture()))
                self.assertTrue(path.is_symlink())
            path.unlink()
            moved.rename(path)
        alias = self.home / 'home-link'
        alias.symlink_to(self.home, target_is_directory=True)
        linked = cache.Cache(KEY, home=alias)
        self.assertEqual(linked.load(), {})
        self.assertFalse(linked.save(fixture()))

    def test_foreign_owner_on_each_parent_entry_and_lock_is_refused(self):
        self.assertTrue(self.cache.save(fixture()))
        original, original_stat = os.fstat, os.stat
        targets = [self.home, self.home / '.cache', self.home / '.cache/squadra', self.cache.folder,
                   self.path, self.cache.folder / '.lock']
        for path in targets:
            inode = path.stat().st_ino
            def foreign_result(info):
                if info.st_ino == inode:
                    values = list(info)
                    values[4] = os.getuid() + 1
                    return os.stat_result(values)
                return info
            with self.subTest(target=path.name), \
                    patch.object(cache.os, 'fstat', side_effect=lambda fd: foreign_result(original(fd))), \
                    patch.object(cache.os, 'stat', side_effect=lambda *a, **kw: foreign_result(original_stat(*a, **kw))):
                self.assertEqual(self.cache.load(), {})
                self.assertFalse(self.cache.save(fixture()))
        self.assertEqual(self.cache.load(), fixture())

    def test_symlink_hardlink_fifo_and_public_entry_or_lock_are_refused(self):
        self.assertTrue(self.cache.save(fixture()))
        for path in (self.path, self.cache.folder / '.lock'):
            saved = path.read_bytes()
            for kind in ('symlink', 'hardlink', 'fifo', 'mode'):
                other = self.home / 'outside'
                other.write_bytes(saved)
                other.chmod(0o600)
                path.unlink()
                if kind == 'symlink':
                    path.symlink_to(other)
                elif kind == 'hardlink':
                    os.link(other, path)
                elif kind == 'fifo':
                    os.mkfifo(path, 0o600)
                else:
                    path.write_bytes(saved)
                    path.chmod(0o644)
                with self.subTest(path=path.name, kind=kind):
                    self.assertEqual(self.cache.load(), {})
                    self.assertFalse(self.cache.save(fixture()))
                    self.assertEqual(other.read_bytes(), saved)
                path.unlink()
                path.write_bytes(saved)
                path.chmod(0o600)
                other.unlink()

    def test_excluded_repository_or_registry_overlap_disables_all_persistence(self):
        destinations = (self.home, self.cache.folder, self.cache.folder / 'project')
        for excluded in destinations:
            with self.subTest(excluded=excluded):
                disabled = cache.Cache(KEY, home=self.home, excluded=[excluded])
                self.assertEqual(disabled.load(), {})
                self.assertFalse(disabled.save(fixture()))
        self.assertEqual(list(self.home.iterdir()), [])
        allowed = cache.Cache(KEY, home=self.home, excluded=[self.home / 'project'])
        self.assertTrue(allowed.save(fixture()))

    def test_nonblocking_lock_preserves_disk_and_returns_empty_on_contention(self):
        self.assertTrue(self.cache.save(fixture()))
        original = self.path.read_bytes()
        fd = os.open(self.cache.folder / '.lock', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        cache.fcntl.flock(fd, cache.fcntl.LOCK_EX | cache.fcntl.LOCK_NB)
        self.assertEqual(self.cache.load(), {})
        self.assertFalse(self.cache.save(fixture('codex')))
        self.assertEqual(self.path.read_bytes(), original)
        cache.fcntl.flock(fd, cache.fcntl.LOCK_UN)
        self.assertEqual(self.cache.load(), fixture())

    def test_entry_size_bound_retains_old_file_and_oversized_read_is_ignored(self):
        self.assertTrue(self.cache.save(fixture()))
        original = self.path.read_bytes()
        with patch.object(cache, 'MAX_ENTRY_BYTES', len(original) - 1):
            self.assertFalse(self.cache.save(fixture()))
            self.assertEqual(self.cache.load(), {})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.cache.load(), fixture())

    def test_total_bound_includes_atomic_replacement_and_evicts_oldest_entry(self):
        self.assertTrue(self.cache.save(fixture()))
        size = self.path.stat().st_size
        second = cache.Cache('e' * 64, home=self.home)
        third = cache.Cache('f' * 64, home=self.home)
        with patch.object(cache, 'MAX_TOTAL_BYTES', size * 2):
            self.assertTrue(second.save(fixture()))
            os.utime(self.path, (10, 10))
            self.assertTrue(third.save(fixture()))
            self.assertFalse(self.path.exists())
            self.assertTrue((second.folder / ('e' * 64 + '.json')).exists())
            write = os.write
            peaks = []
            def measured_write(fd, value):
                result = write(fd, value)
                peaks.append(sum(p.stat().st_size for p in third.folder.iterdir()))
                return result
            with patch.object(cache.os, 'write', side_effect=measured_write):
                self.assertTrue(third.save(fixture()))
            self.assertTrue(peaks)
            self.assertLessEqual(max(peaks), size * 2)
            self.assertFalse((second.folder / ('e' * 64 + '.json')).exists())
        self.assertEqual(third.load(), fixture())

    def test_failed_atomic_replace_keeps_previous_entry_and_removes_temp(self):
        self.assertTrue(self.cache.save(fixture()))
        original = self.path.read_bytes()
        with patch.object(cache.os, 'replace', side_effect=OSError('synthetic interruption')):
            self.assertFalse(self.cache.save(fixture('codex')))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(any(p.name.startswith('.tmp-') for p in self.cache.folder.iterdir()))
        stale = self.cache.folder / ('.tmp-' + KEY + '-' + 'e' * 32)
        stale.write_bytes(b'abandoned replacement')
        stale.chmod(0o600)
        self.assertTrue(self.cache.save(fixture('codex')))
        self.assertFalse(stale.exists())
        self.assertEqual(self.cache.load(), fixture('codex'))

    def test_unknown_file_is_counted_but_never_evicted(self):
        self.assertTrue(self.cache.save(fixture()))
        unknown = self.cache.folder / 'not-managed.json'
        unknown.write_bytes(b'keep me')
        unknown.chmod(0o600)
        with patch.object(cache, 'MAX_TOTAL_BYTES', 1):
            self.assertFalse(self.cache.save(fixture()))
        self.assertEqual(unknown.read_bytes(), b'keep me')
        self.assertEqual(self.cache.load(), fixture())

    def test_a_foreign_file_with_another_mode_is_counted_and_does_not_block_saves(self):
        self.assertTrue(self.cache.save(fixture()))
        stray = self.cache.folder / 'notes.txt'
        stray.write_bytes(b'not a cache entry')
        stray.chmod(0o644)
        self.assertTrue(self.cache.save(fixture('codex')))
        self.assertEqual(self.cache.load(), fixture('codex'))
        self.assertEqual(stray.read_bytes(), b'not a cache entry')

    def test_invalid_keys_never_become_paths(self):
        for key in ('../outside', CANARY, 'a' * 63, 'A' * 64):
            with self.subTest(key=key):
                bad = cache.Cache(key, home=self.home)
                self.assertEqual(bad.load(), {})
                self.assertFalse(bad.save(fixture()))
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

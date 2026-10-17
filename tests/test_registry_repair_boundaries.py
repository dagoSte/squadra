"""Package guards use exact marker names and real layouts, never arbitrary ancestors."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
import registry_cache as cache
import registry_current
import registry_prepare
from recovery import save


class RepairBoundaries(unittest.TestCase):
    def setUp(self):
        # Outside a proof's registry/Git ancestors so the scratch-location test
        # measures marker recognition, not the independent registry/Git guard.
        temporary = tempfile.TemporaryDirectory(prefix='sq-repair-boundary-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.project = self.base / 'webapp'
        self.project.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir(mode=0o700)
        env = os.environ | {'HOME': str(self.home), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_AUTHOR_NAME': 'Synthetic',
            'GIT_COMMITTER_NAME': 'Synthetic', 'GIT_AUTHOR_EMAIL': 's@example.invalid',
            'GIT_COMMITTER_EMAIL': 's@example.invalid'}
        (self.project / 'code.py').write_text('value = 1\n')
        for argv in (['init', '-q'], ['add', 'code.py'], ['-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', *argv], cwd=self.project, env=env, check=True, capture_output=True, timeout=20)
        self.root = self.project / '.squadra'
        self.store = registry.Store(self.root, create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Repair fixture', 'project': str(self.project),
            'limits': {'plan': 3, 'code': 2, 'build': 3}})

    def forge(self):
        current = registry_current.build(self.store.events(), registry.apply, registry.initial)
        current['information_count'] += 1
        tree = cache.Tree.build(self.store.db, current, domain='current')
        row = self.store.db.execute('SELECT body FROM derived_state_meta WHERE id=1').fetchone()
        metadata = json.loads(row[0])
        metadata['current'] = tree.flush()
        self.store.db.execute('UPDATE derived_state_meta SET body=?,digest=? WHERE id=1',
                             (cache.canonical(metadata), cache.metadata_digest(metadata)))
        self.assertEqual(self.store.audit()['cache'], 'mismatch')

    def repair(self):
        try:
            return self.store.repair_cache()['cache']
        except registry.Error as exc:
            return 'refused: ' + str(exc)

    def test_divergent_cache_repairs_below_pwa_and_unrelated_ancestor_manifests(self):
        (self.project / 'manifest.json').write_text('{"name":"PWA"}\n')
        (self.base / 'MANIFEST.json').write_text('{"unrelated":true}\n')
        self.forge()
        before = self.store.events()
        self.assertEqual(self.repair(), 'matched')
        self.assertEqual(self.store.events(), before)

    def test_lowercase_checksum_file_is_not_a_checkpoint_marker(self):
        (self.root / 'checksums.json').write_text('{"ordinary":true}\n')
        self.forge()
        self.assertEqual(self.repair(), 'matched')

    def test_scratch_accepts_ordinary_manifest_ancestors_but_refuses_real_packages(self):
        ordinary = self.base / 'ordinary'
        ordinary.mkdir()
        (ordinary / 'manifest.json').write_text('{"name":"PWA"}\n')
        (self.base / 'MANIFEST.json').write_text('{"unrelated":true}\n')
        candidate = ordinary / 'scratch'
        candidate.mkdir()
        with patch.object(registry_prepare.tempfile, 'gettempdir', return_value=str(candidate)):
            self.assertEqual(registry_prepare.scratch_parent(self.store), candidate)
        checkpoint = self.base / 'checkpoint'
        self.store.checkpoint(checkpoint)
        package = self.base / 'saved'
        self.assertEqual(save(self.store, package, temporary=True)['status'], 'saved')
        for folder in (checkpoint, package, package / 'registry'):
            with self.subTest(folder=folder.name), \
                    patch.object(registry_prepare.tempfile, 'gettempdir', return_value=str(folder)):
                chosen = registry_prepare.scratch_parent(self.store)
                self.assertNotEqual(chosen, folder)
                self.assertNotIn(folder, chosen.parents)

    def test_real_checkpoint_and_save_refuse_repair_without_changing_package_bytes(self):
        checkpoint = self.base / 'checkpoint'
        self.store.checkpoint(checkpoint)
        package = self.base / 'saved'
        self.assertEqual(save(self.store, package, temporary=True)['status'], 'saved')

        def files(folder):
            return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in folder.rglob('*') if p.is_file() and
                    p.name not in {'registry.sqlite3-wal', 'registry.sqlite3-shm'}}

        for folder, root in ((checkpoint, checkpoint), (package, package / 'registry')):
            with self.subTest(folder=folder.name):
                before = files(folder)
                reader = registry.Store(root, read_only=True)
                try:
                    with self.assertRaisesRegex(registry.Error, 'saved package or checkpoint'):
                        reader.repair_cache()
                finally:
                    reader.close()
                self.assertEqual(files(folder), before)


if __name__ == '__main__':
    unittest.main()

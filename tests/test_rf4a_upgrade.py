"""The published 3.11 registry upgrades without a cache repair or a lost retained delivery."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import test_board as support
from frozen_candidate import inventory
from registry import Store


PUBLISHED = '5099f0c96a9f319d5a3758d2a03af4d3784b0bad'
# Byte copies of the published files this run loads; MANIFEST.json records their SHA-256 and Git blobs.
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'published-5099f0c'


class PublishedRegistryUpgrade(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    deliver = support.Board.deliver

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='squadra-upgrade-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        def writable():
            for path in [self.base, *self.base.rglob('*')]:
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o700)
        self.addCleanup(writable)
        home = self.base / 'home'; home.mkdir()
        temporary = self.base / 'tmp'; temporary.mkdir()
        self.enterContext(patch.dict(os.environ, {
            'HOME': str(home), 'TMPDIR': str(temporary), 'XDG_STATE_HOME': str(home / 'state'),
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'PYTHONDONTWRITEBYTECODE': '1'}))

    def published_source(self):
        """The published 5099f0c sources: from SQ_RF4A_BASE_REPO when set, else the verified versioned fixture."""
        old_source = self.base / 'published'; old_source.mkdir()
        if os.environ.get('SQ_RF4A_BASE_REPO'):
            repository = Path(os.environ['SQ_RF4A_BASE_REPO'])
            qualified = subprocess.run(['git', '-C', str(repository), 'merge-base', '--is-ancestor',
                                        PUBLISHED, 'HEAD'], capture_output=True, timeout=30)
            self.assertEqual(qualified.returncode, 0,
                             'SQ_RF4A_BASE_REPO must contain the published 5099f0c as an ancestor of HEAD.\n' +
                             qualified.stderr.decode(errors='replace'))
            archived = subprocess.run(['git', '-C', str(repository), 'archive', PUBLISHED],
                                      capture_output=True, check=True, timeout=30)
            with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
                archive.extractall(old_source, filter='data')
            return old_source
        for name, data in self.verified_fixture(FIXTURE).items():
            target = old_source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return old_source

    def verified_fixture(self, fixture):
        """Bytes of every manifest entry; any symlink, special file or unlisted file refuses the fixture.
        Only MANIFEST.json at the fixture root is outside the manifest."""
        manifest = json.loads((fixture / 'MANIFEST.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['commit'], PUBLISHED)
        present = set()
        for path in fixture.rglob('*'):
            name = path.relative_to(fixture).as_posix()
            if path.is_symlink():
                self.fail(f'Fixture symlink refused: {name}')
            if path.is_dir():
                continue
            if not path.is_file():
                self.fail(f'Fixture special file refused: {name}')
            if name != 'MANIFEST.json':
                present.add(name)
        self.assertEqual(present, set(manifest['files']), 'Fixture files differ from MANIFEST.json')
        files = {}
        for name, entry in manifest['files'].items():
            data = (fixture / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'], f'{name} differs from 5099f0c')
            files[name] = data
        return files

    def test_fixture_refuses_unlisted_files_and_symlinks(self):
        def copy(label):
            target = self.base / label
            shutil.copytree(FIXTURE, target, symlinks=True)
            return target
        self.assertEqual(set(self.verified_fixture(copy('clean'))), set(json.loads(
            (FIXTURE / 'MANIFEST.json').read_text(encoding='utf-8'))['files']))
        nested = copy('nested'); (nested / 'scripts' / 'MANIFEST.json').write_text('{}\n')
        cache = copy('cache'); (cache / 'scripts' / '__pycache__').mkdir(); (cache / 'scripts' / '__pycache__' / 'extra.py').write_text('')
        for fixture, unlisted in [(nested, 'scripts/MANIFEST.json'), (cache, 'scripts/__pycache__/extra.py')]:
            with self.subTest(unlisted=unlisted):
                with self.assertRaisesRegex(AssertionError, 'differ from MANIFEST.json') as raised:
                    self.verified_fixture(fixture)
                self.assertIn(unlisted, str(raised.exception))
        # A symlink to identical bytes still refuses: the fixture holds the bytes, never a path to them.
        linked = copy('linked'); listed = linked / 'scripts' / 'runtime.py'
        outside = self.base / 'runtime.py'; outside.write_bytes(listed.read_bytes())
        listed.unlink(); listed.symlink_to(outside)
        with self.subTest(symlink='scripts/runtime.py'), \
                self.assertRaisesRegex(AssertionError, 'symlink refused: scripts/runtime.py'):
            self.verified_fixture(linked)

    def test_published_delivery_needs_no_repair_and_its_old_sha_can_be_retired(self):
        old_source = self.published_source()
        env = os.environ.copy()
        code = '''import json,sys
sys.path.insert(0,sys.argv[1]+'/tests')
from test_board import Board
from registry import Store
case=Board();case.setUp();case.temp._finalizer.detach()
case.task();case.start();case.deliver()
store=Store(case.root)
try:
    audit=store.audit();task=store.state()['tasks']['T']
    print(json.dumps({'root':str(case.root),'repo':str(case.repo),'cache':audit['cache'],
                      'hashes':[event['hash'] for event in audit['events']],
                      'frozen':task['delivery']['frozen']}))
finally:store.close()
'''
        old = subprocess.run([sys.executable, '-B', '-c', code, str(old_source)],
                             env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(old.returncode, 0, old.stderr)
        original = json.loads(old.stdout)
        self.assertEqual(original['cache'], 'matched')
        self.root = Path(original['root']); self.repo = Path(original['repo'])
        before = original['frozen']; self.assertTrue(Path(before['repo']).is_dir())
        store = Store(self.root)
        try:
            store.append('decision', 'C', {'id': 'upgrade-without-repair',
                                          'reason': 'The published registry remains writable after upgrade'})
            audit = store.audit()
            self.assertEqual(audit['cache'], 'matched', 'Upgrade must not require doctor --repair')
            self.assertEqual([event['hash'] for event in audit['events'][:-1]], original['hashes'])
            self.assertEqual(audit['events'][-1]['data']['id'], 'upgrade-without-repair')
        finally:
            store.close()
        (self.repo / 'value.txt').write_text('New candidate after upgrading the published registry\n')
        self.deliver()
        current = self.state()['tasks']['T']['delivery']['frozen']
        self.assertNotEqual(current['sha256'], before['sha256'])
        self.cli('candidate', 'retire', '--actor', 'C', '--sha256', before['sha256'],
                 '--reason', 'Retire the published delivery after a new delivery')
        self.assertFalse(Path(before['repo']).exists())
        self.assertTrue(Path(current['repo']).is_dir())
        store = Store(self.root)
        try:
            self.assertEqual(store.audit()['cache'], 'matched')
            status = {row['sha256']: row['status'] for row in inventory(store)}
            self.assertEqual(status[before['sha256']], 'retired')
            self.assertEqual(status[current['sha256']], 'available')
        finally:
            store.close()

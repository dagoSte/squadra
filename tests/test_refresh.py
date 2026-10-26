"""Live-installation regressions, exercised against synthetic registries and homes."""
import json
from pathlib import Path
from unittest.mock import patch

import test_ops_seams as ops
import test_plugin_store as plugin_support


class Receipts(ops.Squad):
    def test_candidate_status_is_one_json_document(self):
        rc, out, err = self.cli('candidate', 'status')
        self.assertEqual(rc, 0, err)
        self.assertIsInstance(json.loads(out), dict)

    def test_lesson_list_is_one_json_document(self):
        rc, out, err = self.cli('lesson', '--list')
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)['lessons'], [])

    def test_manual_transport_is_short_and_keeps_ack_instructions(self):
        self.store.append('message', 'C', {'id': 'M', 'to': 'X', 'body': 'Synthetic mail'})
        rc, out, err = self.cli('transport', '--actor', 'C', '--message', 'M', '--via', 'manual')
        self.assertEqual(rc, 0, err)
        receipt = json.loads(out)
        self.assertEqual((receipt['message'], receipt['delivered']), ('M', None))
        self.assertIn('--actor X --message M', receipt['instruction'])
        self.assertLess(len(out), 1500)
        self.assertIsNone(self.store.state()['messages']['M']['ack'])

    def test_queue_receipt_is_short_but_evidence_and_failure_survive(self):
        self.store.append('message', 'C', {'id': 'M', 'to': 'X', 'body': 'Synthetic mail'})
        evidence = {'delivered': False, 'stage': 'queue', 'native_rc': 7,
                    'receipt': 'synthetic stdout ' * 200, 'stderr': 'synthetic error ' * 200,
                    'receipt_length': 3400, 'stderr_length': 3200, 'reason': 'queue failed'}
        with patch('squadra.codex_queue', return_value=evidence):
            rc, out, err = self.cli('transport', '--actor', 'C', '--message', 'M', '--via', 'codex-queue')
        self.assertEqual(rc, 1, err)
        receipt = json.loads(out)
        self.assertEqual((receipt['message'], receipt['delivered'], receipt['native_rc']), ('M', False, 7))
        self.assertLess(len(out), 1200)
        self.assertNotIn('receipt', receipt)
        self.assertNotIn('stderr', receipt)
        record = [e['data'] for e in self.store.events() if e['kind'] == 'transport'][-1]
        for key, value in evidence.items():
            self.assertEqual(record[key], value)
        self.assertIsNone(self.store.state()['messages']['M']['ack'])


class Adoption(plugin_support.Fixture):
    def test_unmanaged_installation_names_folder_and_adoption_steps(self):
        self.target.mkdir(parents=True, mode=0o700)
        result = self.run_cli('install', 'example')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        for text in ('unmanaged_installation', str(self.target), 'settings.json', 'secrets/',
                     'move', 'install', 'compatib'):
            self.assertIn(text, result.stderr)
        self.assertEqual(list(self.target.iterdir()), [])


class DashboardStartup(ops.Squad):
    def test_real_dash_releases_startup_read_before_waiting_and_between_polls(self):
        import contextlib
        import http.client
        import selectors
        import sqlite3
        import subprocess
        import sys
        from urllib.parse import urlsplit
        with contextlib.closing(sqlite3.connect(self.root / 'registry.sqlite3')) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        process = subprocess.Popen([sys.executable, '-B', str(ops.CLI), '--root', str(self.root),
                                    'dash', '--port', '0'], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(20), 'dashboard did not announce its URL')
            line = process.stdout.readline()
            self.assertIn('Squadra dash: ', line)
            url = urlsplit(line.split('Squadra dash: ', 1)[1].strip())
            for turn, route in enumerate((None, '/', '/api/state', '/api/reads', '/api/state')):
                if route:
                    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=15)
                    try:
                        conn.request('GET', route, headers={'X-Squadra-Token': url.fragment})
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200, response.read())
                    finally:
                        conn.close()
                rc, _, err = self.cli('decision', '--actor', 'C', '--reason', 'WAL write ' + str(turn))
                self.assertEqual(rc, 0, err)
                with contextlib.closing(sqlite3.connect(self.root / 'registry.sqlite3', isolation_level=None)) as db:
                    busy, frames, copied = db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
                self.assertEqual((busy, copied), (0, frames), (route, busy, frames, copied))
        finally:
            process.terminate()
            process.communicate(timeout=10)


class RestoreInput(ops.Squad):
    def test_checkpoint_is_distinguished_from_a_save_package(self):
        checkpoint = self.base / 'checkpoint'
        self.store.checkpoint(checkpoint)
        rc, out, err = self.cli('restore-check', '--from', str(checkpoint), '--to', str(self.base / 'restored'))
        self.assertEqual(rc, 1)
        self.assertIn('save package', err)
        self.assertNotIn('tampered', err)
        self.assertFalse((self.base / 'restored').exists())


class SaveAllowance(ops.Squad):
    def tracked_module(self):
        path = self.repo / 'deploy/credentials.py'
        path.parent.mkdir()
        path.write_text('def read_credentials():\n    return "synthetic placeholder"\n')
        self.git('add', 'deploy/credentials.py')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic credentials module')
        return path

    def save_args(self, name='saved', actor='C'):
        return ('save', '--actor', actor, '--to', str(self.base / name), '--repo', str(self.repo), '--temporary')

    def test_without_allowance_refusal_names_explicit_remedy(self):
        self.tracked_module()
        rc, _, err = self.cli(*self.save_args())
        self.assertEqual(rc, 1)
        self.assertIn('deploy/credentials.py', err)
        self.assertIn('--allow-name', err)
        self.assertIn('--reason', err)

    def test_one_save_allowance_is_recorded_with_exact_path_and_hash(self):
        import hashlib
        path = self.tracked_module()
        (self.repo / '.env').write_text('SYNTHETIC_UNTRACKED=excluded\n')
        before = len(self.store.events())
        rc, _, err = self.cli(*self.save_args(), '--allow-name', 'deploy/credentials.py',
                              '--reason', 'Synthetic source module, not a credentials store')
        self.assertEqual(rc, 0, err)
        manifest = json.loads((self.base / 'saved/MANIFEST.json').read_text())
        receipt = manifest['name_allowance']
        self.assertEqual(receipt['files'][0]['path'], 'deploy/credentials.py')
        self.assertEqual(receipt['files'][0]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        events = self.store.events()[before:]
        self.assertEqual([e['kind'] for e in events], ['decision', 'save'])
        self.assertEqual(events[0]['data']['id'], receipt['decision'])
        self.assertEqual(events[0]['data']['save_name_allowance']['destination'], str(self.base / 'saved'))
        self.assertIn('.env', manifest['repositories'][0]['excluded'])
        from recovery import restore_check
        restored = restore_check(self.base / 'saved', self.base / 'restored')
        self.assertEqual((Path(restored['repositories'][0]['restored']) /
                          'deploy/credentials.py').read_bytes(), path.read_bytes())
        rc, _, err = self.cli(*self.save_args('second'))
        self.assertEqual(rc, 1)
        self.assertIn('--allow-name', err)

    def test_allowance_requires_coordinator_reason_and_literal_tracked_path(self):
        self.tracked_module()
        for i, path in enumerate(('deploy/*', '../credentials.py', '/credentials.py', 'deploy',
                                  './deploy/credentials.py', 'deploy/missing.py')):
            before = len(self.store.events())
            rc, _, _ = self.cli(*self.save_args('bad-' + str(i)), '--allow-name', path, '--reason', 'Synthetic')
            self.assertNotEqual(rc, 0, path)
            self.assertFalse((self.base / ('bad-' + str(i))).exists(), path)
            self.assertFalse([e for e in self.store.events()[before:] if e['kind'] in ('decision', 'save')], path)
        for name, extra in (('builder', ('--allow-name', 'deploy/credentials.py', '--reason', 'Synthetic')),
                            ('no-reason', ('--allow-name', 'deploy/credentials.py'))):
            rc, _, _ = self.cli(*self.save_args(name, 'A' if name == 'builder' else 'C'), *extra)
            self.assertNotEqual(rc, 0)
            self.assertFalse((self.base / name).exists())

    def test_allowance_never_permits_private_keys_in_head_index_or_worktree(self):
        path = self.tracked_module()
        benign = path.read_bytes()
        key = b'-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic\n'
        for version in ('head', 'index', 'worktree'):
            path.write_bytes(key)
            if version in ('head', 'index'):
                self.git('add', 'deploy/credentials.py')
            if version == 'head':
                self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic historical key')
            if version != 'worktree':
                path.write_bytes(benign)
            if version == 'head':
                self.git('add', 'deploy/credentials.py')
            rc, _, err = self.cli(*self.save_args('key-' + version), '--allow-name', 'deploy/credentials.py',
                                  '--reason', 'Must never override key detection')
            self.assertEqual(rc, 1, (version, err))
            self.assertIn('Private-key', err)
            self.assertFalse((self.base / ('key-' + version)).exists())
            path.write_bytes(benign)
            self.git('add', 'deploy/credentials.py')
            self.git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'Restore synthetic fixture')

    def test_repeatable_allowance_preserves_each_path_and_version_hash(self):
        import hashlib
        path = self.tracked_module()
        extra = path.with_name('credentials_extra.py')
        extra.write_text('EXAMPLE = 2\n')
        self.git('add', 'deploy/credentials_extra.py')
        rc, _, err = self.cli(*self.save_args(), '--allow-name', 'deploy/credentials.py',
                              '--allow-name', 'deploy/credentials_extra.py', '--reason', 'Two synthetic source modules')
        self.assertEqual(rc, 0, err)
        files = json.loads((self.base / 'saved/MANIFEST.json').read_text())['name_allowance']['files']
        self.assertEqual([row['path'] for row in files], ['deploy/credentials.py', 'deploy/credentials_extra.py'])
        self.assertEqual(files[1]['sha256'], hashlib.sha256(extra.read_bytes()).hexdigest())
        self.assertEqual(files[1]['index_sha256'], files[1]['sha256'])
        self.assertIsNone(files[1]['head_sha256'])

    def test_recorded_allowance_refuses_another_destination_and_content_drift(self):
        from recovery import allow_names, save
        from registry import Error
        path = self.tracked_module()
        decision = allow_names(self.store, 'C', self.base / 'saved', [str(self.repo)],
                               ['deploy/credentials.py'], 'Synthetic explicit allowance')
        with self.assertRaisesRegex(Error, 'one exact save'):
            save(self.store, self.base / 'other', [str(self.repo)], temporary=True, name_allowance=decision)
        self.assertFalse((self.base / 'other').exists())
        path.write_text('VALUE = "changed after recorded allowance"\n')
        with self.assertRaisesRegex(Error, 'Allowed file changed'):
            save(self.store, self.base / 'saved', [str(self.repo)], temporary=True, name_allowance=decision)
        self.assertFalse((self.base / 'saved/MANIFEST.json').exists())


class SymlinkStart(ops.Squad):
    def test_real_entrypoint_keeps_its_import_tree_after_skill_link_switch(self):
        import os
        from pathlib import Path
        import shutil
        import subprocess
        import sys
        import time
        versions = [self.base / name for name in ('version-A', 'version-B')]
        for version in versions:
            shutil.copytree(ops.ROOT / 'scripts', version / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            (version / 'scripts/dashboard.py').write_text(
                'from pathlib import Path\n'
                'def serve(*args, **kwargs):\n'
                '    print("BOUND=" + str(Path(__file__).resolve()), flush=True)\n')
        link = self.home / 'skill-link'
        link.symlink_to(versions[0], target_is_directory=True)
        bootstrap = self.base / 'bootstrap'; bootstrap.mkdir()
        ready, resume = self.base / 'ready', self.base / 'resume'
        (bootstrap / 'sitecustomize.py').write_text(
            'import importlib.abc, os, sys, time\nfrom pathlib import Path\n'
            'class Pause(importlib.abc.MetaPathFinder):\n'
            '    def find_spec(self, fullname, path, target=None):\n'
            '        if fullname == "dashboard":\n'
            '            Path(os.environ["SQ_TEST_READY"]).touch()\n'
            '            deadline = time.monotonic() + 20\n'
            '            while not Path(os.environ["SQ_TEST_RESUME"]).exists():\n'
            '                if time.monotonic() > deadline: raise RuntimeError("test resume timeout")\n'
            '                time.sleep(.01)\n'
            '        return None\n'
            'sys.meta_path.insert(0, Pause())\n')
        env = self.env | {'PYTHONPATH': str(bootstrap), 'SQ_TEST_READY': str(ready), 'SQ_TEST_RESUME': str(resume)}
        process = subprocess.Popen([sys.executable, '-B', str(link / 'scripts/squadra.py'),
                                    '--root', str(self.root), 'dash'], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 20
            while not ready.exists():
                self.assertIsNone(process.poll(), 'entry point exited before lazy import')
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            replacement = self.home / 'replacement'
            replacement.symlink_to(versions[1], target_is_directory=True)
            os.replace(replacement, link)
            resume.touch()
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn('BOUND=' + str(versions[0] / 'scripts/dashboard.py'), out)
            self.assertNotIn('BOUND=' + str(versions[1]), out)
        finally:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=10)

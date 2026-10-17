"""Real synthetic files and CLI: release store, private upgrades and inert payloads."""
import errno
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import plugin_store as store
import plugins
import plugin_settings

FIXTURE = ROOT / 'tests/fixtures/plugin-store/example'


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sq-store-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir(mode=0o755)
        self.release = self.base / 'release'; self.release.mkdir()
        for folder in ('scripts', '.codex-plugin'):
            shutil.copytree(ROOT / folder, self.release / folder, ignore=shutil.ignore_patterns('__pycache__'))
        self.source = self.release / 'plugins/example'
        shutil.copytree(FIXTURE, self.source)
        self.target = self.home / '.squadra/plugins/example'
        self.env = {**os.environ, 'HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}

    def json(self, path, **updates):
        value = json.loads(path.read_text()); value.update(updates)
        path.write_text(json.dumps(value))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.release / 'scripts/squadra.py'), 'plugin', *args],
                              env=self.env, cwd=self.home, capture_output=True, text=True, timeout=20)

    def install(self):
        return store.install(self.home, 'example', self.release)

    def catalogue(self):
        return store.catalogue(self.home, self.release)

    def refuse(self, code, call=None):
        with self.assertRaises(plugins.Refused) as caught:
            (call or self.install)()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.home), str(caught.exception))

    def upgrade(self):
        self.json(self.source / 'store.json', accepted_version='2.0.0')
        value = json.loads((self.source / 'plugin.json').read_text()); value['about']['version'] = '2.0.0'
        (self.source / 'plugin.json').write_text(json.dumps(value))
        (self.source / 'programs/info.py').write_text('raise SystemExit("must never run")\n')

    def pending_failure(self):
        self.install(); self.upgrade()
        atomic = plugin_settings.atomic
        def fail(parent, name, body):
            if name == 'info.py': raise OSError(errno.ENOSPC, 'synthetic')
            return atomic(parent, name, body)
        with patch.object(plugin_settings, 'atomic', side_effect=fail):
            self.refuse('install_incomplete')


class Catalogue(Fixture):
    def test_stubbed_case_colliding_listing_refused_before_receipt_validation(self):
        body = (self.source / 'README.md').read_bytes()
        # Both spellings resolve on APFS; Linux gets two real files with equal bytes.
        (self.source / 'readme.md').write_bytes(body)
        (self.source / 'extra.md').write_bytes(body)
        root = self.source.stat()
        scandir = store.os.scandir
        names = ['README.md', 'extra.md']

        @contextmanager
        def listing(fd):
            with scandir(fd) as entries:
                rows = list(entries)
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) == (root.st_dev, root.st_ino):
                rows = [entry for entry in rows if entry.name.casefold() not in {'readme.md', 'extra.md'}]
                rows.extend(SimpleNamespace(name=name, is_dir=lambda **kwargs: False) for name in names)
            yield iter(rows)

        with patch.object(store.os, 'scandir', listing):
            self.assertIn('extra.md', store.bundle(self.release, 'example')['files'])
            names[:] = ['README.md', 'readme.md']
            with self.assertRaises(plugins.Refused) as caught:
                store.bundle(self.release, 'example')
            self.assertEqual(caught.exception.code, 'invalid_bundle', 'walk must reject the case collision')
            names[:] = ['README.md', 'extra.md']
            self.assertIn('extra.md', store.bundle(self.release, 'example')['files'])

    def test_valid_metadata_and_release_root(self):
        with patch.object(store, 'RELEASE_ROOT', self.release), patch('os.getcwd', return_value=str(self.home)):
            result = store.catalogue(self.home)
        row = result['plugins'][0]
        self.assertEqual(row['about']['publisher'], 'Synthetic tests')
        self.assertEqual(row['about']['version'], row['store']['accepted_version'])
        self.assertEqual(result['refused'], [])
        self.assertFalse(row['installed'])
        self.assertFalse(self.target.exists())
        self.assertFalse((self.home / '.squadra').exists())

    def test_cli_list_from_unrelated_cwd_does_not_create_registry(self):
        run = self.run_cli('list'); self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['plugins'][0]['name'], 'example')
        self.assertFalse((self.home / '.squadra').exists())

    def test_mcp_first_then_title(self):
        self.json(self.source / 'store.json', mcp=False)
        for name,title in [('zeta','Zebra'), ('beta','Bee')]:
            dest=self.source.parent/name; shutil.copytree(self.source,dest)
            self.json(dest/'plugin.json', name=name, title={'it':title,'en':title})
            self.json(dest/'store.json', mcp=True)
        self.assertEqual([x['name'] for x in self.catalogue()['plugins']], ['beta','zeta','example'])

    def test_incomplete_metadata_is_not_listed(self):
        value=json.loads((self.source/'store.json').read_text()); del value['licence']
        (self.source/'store.json').write_text(json.dumps(value))
        self.assertEqual(self.catalogue()['plugins'], [])
        self.assertEqual(len(self.catalogue()['refused']), 1)

    def test_empty_catalogue(self):
        shutil.rmtree(self.source.parent)
        self.assertEqual(self.catalogue()['plugins'], [])

    def test_strict_store_metadata_contrasts(self):
        original=(self.source/'store.json').read_bytes()
        for change in [{'schema':True},{'schema':2},{'mcp':1},{'price':'gratis'},{'licence':''},
                       {'accepted_version':'2.0.0'},{'review_date':'2026-02-30'},{'review_date':'20261012'},
                       {'source':'http://example.invalid'},{'source':'https://user:secret@example.invalid'},
                       {'publisher':'duplicate'},{'extra':'value'},{'licence':'X\nY'}]:
            with self.subTest(change=change):
                (self.source/'store.json').write_bytes(original); self.json(self.source/'store.json', **change)
                self.assertEqual(self.catalogue()['plugins'], [])
        (self.source/'store.json').write_bytes(original)
        self.assertEqual(len(self.catalogue()['plugins']),1)

    def test_duplicate_json_and_oversize(self):
        original=(self.source/'store.json').read_bytes()
        for body in [original[:-2]+b',"schema":1}', b'{"schema":NaN}', b' '*(16384+1), b'\xff']:
            with self.subTest(body=body[:30]):
                (self.source/'store.json').write_bytes(body)
                self.assertEqual(self.catalogue()['plugins'], [])

    def test_p6_document_bounds_and_utf8(self):
        for name in ['AGENTS.md','README.md']:
            original=(self.source/name).read_bytes()
            for body in [b'', b'  ', b'\xff', b'a'*(65536+1)]:
                with self.subTest(name=name,body=body[:10]):
                    (self.source/name).write_bytes(body)
                    self.assertEqual(self.catalogue()['plugins'], [])
            (self.source/name).write_bytes(original)
        self.assertEqual(len(self.catalogue()['plugins']),1)
        (self.source/'README.md').unlink(); self.assertEqual(self.catalogue()['plugins'], [])

    def test_p2_discovery_files_and_case_aliases_refused(self):
        for name in ['SKILL.md','skill.MD','.claude-plugin','.codex-plugin','.agents','.mcp.json']:
            for prefix in ['', 'programs/']:
                with self.subTest(name=name,prefix=prefix):
                    path=self.source/(prefix+name)
                    if name.startswith('.') and name!='.mcp.json': path.mkdir()
                    else: path.write_text('inert forbidden fixture')
                    self.assertEqual(self.catalogue()['plugins'], [])
                    if path.is_dir(): path.rmdir()
                    else: path.unlink()
        self.assertEqual(len(self.catalogue()['plugins']),1)

    def test_p6_reserved_payload_names_refused(self):
        for name in sorted(store.RESERVED):
            with self.subTest(name=name):
                path=self.source/name; path.write_text('must not copy')
                self.assertEqual(self.catalogue()['plugins'], [])
                path.unlink()
        self.assertEqual(len(self.catalogue()['plugins']),1)

    def test_source_symlinks_and_fifo_refused(self):
        path=self.source/'programs/linked'; path.symlink_to(self.source/'README.md')
        self.assertEqual(self.catalogue()['plugins'], []); path.unlink()
        os.mkfifo(path); self.assertEqual(self.catalogue()['plugins'], [])

    def test_bundle_bounds(self):
        with patch.object(store,'FILES',3): self.assertEqual(self.catalogue()['plugins'], [])
        with patch.object(store,'BUNDLE_BYTES',2): self.assertEqual(self.catalogue()['plugins'], [])
        with patch.object(store,'DEPTH',1): self.assertEqual(self.catalogue()['plugins'], [])
        with patch.object(store,'ENTRIES',0): self.refuse('too_large', self.catalogue)

    def test_invalid_name_never_creates_home_tree(self):
        for name in ['../example','EXAMPLE','absent']:
            with self.subTest(name=name), self.assertRaises(plugins.Refused):
                store.install(self.home,name,self.release)
        self.assertFalse((self.home/'.squadra').exists())

    def test_fallback_command_is_quoted_and_release_specific(self):
        moved=self.base/'release with spaces'; self.release.rename(moved)
        with patch.dict(os.environ,{'PATH':'/nonexistent'}):
            command=store.commands(moved,'example')
        import shlex
        self.assertEqual(shlex.split(command),['python3',str(moved/'scripts/squadra.py'),'plugin','install','example'])

    def test_verified_standalone_launcher_only(self):
        launcher=self.base/'launcher'; script=self.release/'scripts/squadra.py'
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 -B '+str(script)+' "$@"\n')
        with patch.object(shutil,'which',return_value=str(launcher)):
            self.assertEqual(store.commands(self.release,'example'),'squadra plugin install example')
            launcher.write_text('#!/bin/sh\nexec malicious "$@"\n')
            self.assertTrue(store.commands(self.release,'example').startswith('python3 '))


class Installation(Fixture):
    def test_install_modes_receipt_and_no_execution(self):
        (self.source/'programs/info.py').write_text('raise RuntimeError("NEVER EXECUTE")')
        result=self.install(); self.assertEqual(result['state'],'complete'); self.assertFalse(result['automatic_execution'])
        for path in [self.target,*self.target.rglob('*')]:
            self.assertFalse(path.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700 if path.is_dir() else 0o600)
        receipt=json.loads((self.target/store.RECEIPT).read_text())
        self.assertEqual(receipt['state'],'complete'); self.assertEqual(receipt['old'],{})
        self.assertEqual((self.target/'store.json').read_bytes(),(self.source/'store.json').read_bytes())
        self.assertTrue(self.catalogue()['plugins'][0]['managed'])

    def test_cli_install_and_list_receipt_release(self):
        run=self.run_cli('install','example'); self.assertEqual(run.returncode,0,run.stderr)
        out=json.loads(run.stdout); self.assertEqual(out['plugin']['direction'],'install')
        run=self.run_cli('list'); self.assertEqual(run.returncode,0,run.stderr)
        row=json.loads(run.stdout)['plugins'][0]
        self.assertEqual(row['receipt']['release'],json.loads(run.stdout)['release'])
        self.assertFalse((self.home/'.squadra/registry.sqlite3').exists())

    def test_p1_safe_0755_parents_are_allowed(self):
        root=self.home/'.squadra/plugins'; root.mkdir(parents=True)
        for path in [self.home,root.parent,root]: path.chmod(0o755)
        run=self.run_cli('install','example'); self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o755)

    def test_p1_writable_parents_refuse(self):
        root=self.home/'.squadra/plugins'; root.mkdir(parents=True)
        for path in [self.home,root.parent,root]:
            with self.subTest(path=path.name):
                path.chmod(0o777); self.refuse('unsafe_permissions'); path.chmod(0o755)
        self.assertFalse(self.target.exists())

    def test_p1_foreign_owner_check(self):
        with patch.object(plugins.os,'getuid',return_value=os.getuid()+1): self.refuse('unsafe_permissions')
        self.assertFalse(self.target.exists())

    def test_symlinked_parent_refused_without_effect(self):
        other=self.base/'other'; other.mkdir()
        (self.home/'.squadra').symlink_to(other,target_is_directory=True)
        self.refuse('symlink'); self.assertEqual(list(other.iterdir()),[])

    def test_home_symlink_followed_and_final_plugin_symlink_refused(self):
        alias=self.base/'alias'; alias.symlink_to(self.home,target_is_directory=True)
        self.assertEqual(store.install(alias,'example',self.release)['state'],'complete')
        shutil.rmtree(self.target)
        self.target.symlink_to(self.base/'elsewhere',target_is_directory=True)
        self.refuse('symlink'); self.assertFalse((self.base/'elsewhere').exists())

    def test_unmanaged_folder_not_adopted(self):
        self.target.mkdir(parents=True,mode=0o700)
        self.refuse('unmanaged_installation'); self.assertEqual(list(self.target.iterdir()),[])
        row=self.catalogue()['plugins'][0]; self.assertTrue(row['installed']); self.assertFalse(row['managed'])

    def test_changed_managed_file_refused(self):
        self.install(); (self.target/'README.md').write_text('my edits')
        self.refuse('modified_installation'); self.assertEqual((self.target/'README.md').read_text(),'my edits')

    def test_unowned_collision_refused(self):
        self.install(); (self.target/'extra.txt').write_text('mine'); (self.target/'extra.txt').chmod(0o600)
        (self.source/'extra.txt').write_text('release')
        self.refuse('modified_installation'); self.assertEqual((self.target/'extra.txt').read_text(),'mine')

    def test_p6_runtime_data_and_locks_are_preserved(self):
        self.install()
        paths=['settings.json','secrets/key','actions/request.json','panels/result.json','.settings.lock','user.txt']
        before={}
        for name in paths:
            dest=self.target/name; dest.parent.mkdir(exist_ok=True,mode=0o700)
            dest.write_text('synthetic-'+name); dest.chmod(0o600)
            before[name]=(dest.read_bytes(),dest.stat().st_mtime_ns)
        self.upgrade(); self.install()
        self.assertEqual(before,{name:((self.target/name).read_bytes(),(self.target/name).stat().st_mtime_ns) for name in paths})

    def test_obsolete_managed_file_removed_unowned_preserved(self):
        self.install(); (self.source/'programs/info.py').unlink()
        (self.target/'programs/user.txt').write_text('mine')
        self.install(); self.assertFalse((self.target/'programs/info.py').exists())
        self.assertEqual((self.target/'programs/user.txt').read_text(),'mine')

    def test_unsafe_private_directory_and_files_refused(self):
        self.install()
        for path in [self.target,self.target/'programs']:
            path.chmod(0o755); self.refuse('unsafe_permissions'); path.chmod(0o700)
        path=self.target/'README.md'; path.chmod(0o644); self.refuse('unsafe_permissions'); path.chmod(0o600)
        os.link(path,self.base/'hardlink'); self.refuse('unsafe_permissions')

    def test_final_file_symlink_does_not_write_target(self):
        self.install(); outside=self.base/'outside'; outside.write_text('unchanged')
        (self.target/'README.md').unlink(); (self.target/'README.md').symlink_to(outside)
        self.refuse('symlink'); self.assertEqual(outside.read_text(),'unchanged')

    def test_corrupt_or_unsafe_receipt_is_not_adopted(self):
        self.install(); path=self.target/store.RECEIPT; original=path.read_bytes()
        for body in [b'{}',b'{"schema":1,"schema":1}',b'[]',b'x']:
            path.write_bytes(body); self.refuse('unmanaged_installation')
        path.write_bytes(original); path.chmod(0o644); self.refuse('unmanaged_installation')

    def test_partial_failure_has_no_complete_receipt_and_exact_retry(self):
        self.pending_failure()
        record=json.loads((self.target/store.RECEIPT).read_text()); self.assertEqual(record['state'],'upgrading')
        self.assertEqual(self.catalogue()['plugins'][0]['receipt']['state'],'upgrading')
        self.install(); self.assertEqual(json.loads((self.target/store.RECEIPT).read_text())['state'],'complete')
        self.assertEqual((self.target/'programs/info.py').read_bytes(),(self.source/'programs/info.py').read_bytes())

    def test_partial_failure_different_bundle_or_local_change_refused(self):
        self.pending_failure(); body=(self.source/'README.md').read_bytes()
        (self.source/'README.md').write_text('another bundle'); self.refuse('install_incomplete')
        (self.source/'README.md').write_bytes(body)
        (self.target/'README.md').write_text('local change'); self.refuse('modified_installation')

    def test_complete_receipt_write_failure_stays_recoverable(self):
        atomic=plugin_settings.atomic
        def fail(parent,name,body):
            if name==store.RECEIPT and json.loads(body)['state']=='complete': raise OSError(errno.EIO,'synthetic')
            return atomic(parent,name,body)
        with patch.object(plugin_settings,'atomic',side_effect=fail): self.refuse('install_incomplete')
        self.assertEqual(json.loads((self.target/store.RECEIPT).read_text())['state'],'upgrading')
        self.install(); self.assertEqual(json.loads((self.target/store.RECEIPT).read_text())['state'],'complete')

    def test_repeat_upgrade_and_downgrade_directions(self):
        self.install(); self.assertEqual(self.install()['plugin']['direction'],'same')
        self.upgrade(); self.assertEqual(self.install()['plugin']['direction'],'upgrade')
        self.json(self.source/'store.json',accepted_version='1.0.0')
        value=json.loads((self.source/'plugin.json').read_text()); value['about']['version']='1.0.0'
        (self.source/'plugin.json').write_text(json.dumps(value))
        self.json(self.release/'.codex-plugin/plugin.json',version='3.4.0')
        result=self.install(); self.assertEqual(result['plugin'],{'from':'2.0.0','to':'1.0.0','direction':'downgrade'})
        self.assertEqual(result['squadra']['direction'],'downgrade')
        self.assertEqual(store.direction('3.9','3.10'),'upgrade')
        self.assertEqual(store.direction('label-z','label-a'),'unordered')

    def test_process_lock_refuses_competing_installer(self):
        self.install()
        with (self.target.parent/store.LOCK).open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run=self.run_cli('install','example')
        self.assertEqual(run.returncode,1); self.assertIn('busy',run.stderr)
        self.assertEqual(self.run_cli('install','example').returncode,0)

    def test_preflight_checks_all_files_before_replacing_any(self):
        self.install(); self.upgrade()
        before=(self.target/'plugin.json').read_bytes()
        (self.target/'programs/info.py').write_text('local edit')
        self.refuse('modified_installation')
        self.assertEqual((self.target/'plugin.json').read_bytes(),before)
        self.assertEqual(json.loads((self.target/store.RECEIPT).read_text())['state'],'complete')

    def test_parent_lock_symlink_and_hardlink_refused(self):
        self.install(); lock=self.target.parent/store.LOCK
        other=self.base/'outside'; other.write_text('intact'); other.chmod(0o600)
        lock.unlink(); lock.symlink_to(other)
        self.refuse('symlink'); self.assertEqual(other.read_text(),'intact')
        lock.unlink(); os.link(other,lock); self.refuse('unsafe_permissions')

    def test_receipt_cannot_claim_reserved_paths(self):
        self.install(); path=self.target/store.RECEIPT
        record=json.loads(path.read_text()); record['files']['settings.json']='0'*64
        record['bundle']=store.digest(store.canonical(record['files']))
        path.write_text(json.dumps(record)); self.refuse('unmanaged_installation')

    def test_unsafe_existing_folder_is_present_but_not_managed(self):
        self.target.mkdir(parents=True,mode=0o755)
        self.target.chmod(0o755)
        row=self.catalogue()['plugins'][0]
        self.assertTrue(row['installed']); self.assertFalse(row['managed'])
        self.assertEqual(row['installation_error'],'unsafe_permissions')
        self.refuse('unsafe_permissions')

    def test_release_drift_is_visible_without_implicit_upgrade(self):
        self.install(); old=json.loads((self.target/store.RECEIPT).read_text())
        installed=json.loads((self.release/'.codex-plugin/plugin.json').read_text())['version']
        self.json(self.release/'.codex-plugin/plugin.json',version='99.0.0')
        result=self.catalogue(); self.assertEqual(result['release']['version'],'99.0.0')
        self.assertEqual(result['plugins'][0]['receipt']['release']['version'],installed)
        self.assertNotEqual(installed,'99.0.0')
        self.assertEqual(json.loads((self.target/store.RECEIPT).read_text()),old)

    def test_shared_directory_helper_is_used_at_every_level(self):
        calls=[]; check=plugins.check_directory
        def record(fd,private=False):
            calls.append((os.fstat(fd).st_ino,private)); return check(fd,private)
        with patch.object(plugins,'check_directory',side_effect=record): self.install()
        for path in [self.home,self.home/'.squadra',self.target.parent]:
            self.assertIn((path.stat().st_ino,False),calls)
        for path in [self.target,self.target/'programs']:
            self.assertIn((path.stat().st_ino,True),calls)


if __name__=='__main__': unittest.main()

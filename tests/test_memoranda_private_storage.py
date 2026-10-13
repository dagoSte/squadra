"""Private output admission, descriptor checks, pinned imports and uncertain delivery."""
import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_memoranda_bundle import ROOT, fx
from squadra_memories import cli, connector, source
from squadra_memories.source import Refusal

REGISTRY_ID = 'a' * 64
BINDING = {'endpoint': 'https://mcp.example.invalid/t/acme/mcp', 'tenant': 'acme',
           'scope': 'squadra/fixture', 'key_id_sha256': 'b' * 64}
ITEM = {'sentence': 'Synthetic safe declaration.', 'type': 'constraint', 'certainty': 'sure',
        'evidence': [], 'declared_by': {'seq': 1}, 'item': 0}


class Fake:
    def __init__(self):
        self.proposals = []

    def open(self):
        pass

    def tool(self, name, arguments, write=False):
        assert name == 'propose' and write
        self.proposals.append(arguments)
        return {'status': 'pending', 'proposal_id': 'prp_' + '0' * 26}


class PrivateStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir(mode=0o700)
        self.out = self.base / 'out'; self.out.mkdir(mode=0o700)
        self.reg = self.base / 'registry'; self.reg.mkdir(mode=0o700)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, HOME=str(self.home)).start()
        fx.install(self.home)
        self.args = SimpleNamespace(registry=str(self.reg), out_root=str(self.out))
        self.fake = Fake()

    def ledger_path(self, suffix='json'):
        return self.out / ('ledger-' + REGISTRY_ID + '.' + suffix)

    def send(self):
        with connector.Ledger(self.out, REGISTRY_ID) as ledger:
            ledger.bind(BINDING)
            self.assertIsNone(connector.send(ledger, self.fake, [ITEM], BINDING['scope'], REGISTRY_ID, {}))

    def refused(self, code, call):
        with self.assertRaises(Refusal) as failure:
            call()
        self.assertEqual(failure.exception.code, code)

    def test_private_explicit_path_retains_duplicate_suppression(self):
        self.assertEqual(cli._locations(self.args), (self.reg, self.out))
        self.send(); self.send()
        self.assertEqual(len(self.fake.proposals), 1)
        data = connector.read_ledger(self.ledger_path())
        self.assertEqual(data['items']['1:0']['status'], 'sent')
        for suffix in ('json', 'lock'):
            self.assertEqual(stat.S_IMODE(self.ledger_path(suffix).stat().st_mode), 0o600)

    def test_shared_explicit_output_refuses_before_ledger_or_proposal(self):
        for mode in (0o777, 0o770, 0o707):
            with self.subTest(mode=oct(mode)):
                self.out.chmod(mode)
                self.refused('E-OUTPUT', lambda: cli._locations(self.args))
                self.refused('E-OUTPUT', self.send)
                self.assertEqual(list(self.out.iterdir()), [])
                self.assertEqual(self.fake.proposals, [])
        self.out.chmod(0o700)
        self.send()
        self.assertEqual(len(self.fake.proposals), 1)

    def test_foreign_output_owner_refuses_on_descriptor(self):
        original = os.fstat
        identity = self.out.stat().st_ino
        def foreign(fd):
            st = original(fd)
            if st.st_ino == identity:
                fields = list(st); fields[4] = os.getuid() + 1
                return os.stat_result(fields)
            return st
        with patch.object(os, 'fstat', side_effect=foreign):
            self.refused('E-OUTPUT', lambda: cli._locations(self.args))
            self.refused('E-OUTPUT', self.send)
        self.assertEqual(list(self.out.iterdir()), [])
        self.send()
        self.assertEqual(len(self.fake.proposals), 1)

    def test_user_symlink_leaf_and_ancestor_refuse(self):
        alias = self.base / 'alias'; alias.symlink_to(self.out)
        nested = self.out / 'nested'; nested.mkdir(mode=0o700)
        for path in (alias, alias / 'nested'):
            with self.subTest(path=str(path)):
                args = SimpleNamespace(registry=str(self.reg), out_root=str(path))
                self.refused('E-OUTPUT', lambda: cli._locations(args))
        self.assertEqual(cli._locations(self.args)[1], self.out)
        self.assertEqual(list(nested.iterdir()), [])

    def test_unsafe_ledger_and_lock_modes_refuse(self):
        self.send()
        for suffix in ('json', 'lock'):
            path = self.ledger_path(suffix)
            for mode in (0o666, 0o640, 0o604):
                with self.subTest(suffix=suffix, mode=oct(mode)):
                    path.chmod(mode)
                    try:
                        self.refused('E-LEDGER', self.send)
                        if suffix == 'json':
                            self.refused('E-LEDGER', lambda: connector.read_ledger(path))
                    finally:
                        path.chmod(0o600)
                    self.assertEqual(len(self.fake.proposals), 1)
        self.send()
        self.assertEqual(len(self.fake.proposals), 1)

    def test_foreign_ledger_and_lock_owners_refuse_on_descriptors(self):
        self.send()
        original = os.fstat
        for suffix in ('json', 'lock'):
            with self.subTest(suffix=suffix):
                inode = self.ledger_path(suffix).stat().st_ino
                def foreign(fd):
                    st = original(fd)
                    if st.st_ino == inode:
                        fields = list(st); fields[4] = os.getuid() + 1
                        return os.stat_result(fields)
                    return st
                with patch.object(os, 'fstat', side_effect=foreign):
                    self.refused('E-LEDGER', self.send)
                self.send()
                self.assertEqual(len(self.fake.proposals), 1)

    def test_symlink_ledger_and_lock_refuse_without_following(self):
        self.send()
        for suffix in ('json', 'lock'):
            with self.subTest(suffix=suffix):
                path = self.ledger_path(suffix); target = self.out / ('saved-' + suffix)
                path.rename(target); path.symlink_to(target.name)
                before = target.read_bytes()
                try:
                    self.refused('E-LEDGER', self.send)
                    self.assertEqual(target.read_bytes(), before)
                finally:
                    path.unlink(); target.rename(path)
                self.send()
                self.assertEqual(len(self.fake.proposals), 1)

    def test_nonregular_lock_refuses_without_waiting(self):
        lock = self.ledger_path('lock'); os.mkfifo(lock, 0o600)
        self.refused('E-LEDGER', self.send)
        self.assertFalse(self.ledger_path().exists())
        self.assertEqual(self.fake.proposals, [])
        lock.unlink(); self.send()
        self.assertEqual(len(self.fake.proposals), 1)

    def test_nonregular_ledger_refuses_without_waiting(self):
        path = self.ledger_path(); os.mkfifo(path, 0o600)
        self.refused('E-LEDGER', self.send)
        path.unlink(); path.mkdir(mode=0o700)
        self.refused('E-LEDGER', self.send)
        path.rmdir(); self.send()
        self.assertEqual(len(self.fake.proposals), 1)

    def test_opened_directory_is_kept_for_save_despite_path_replacement(self):
        with connector.Ledger(self.out, REGISTRY_ID) as ledger:
            saved = self.base / 'saved'; self.out.rename(saved)
            self.out.mkdir(mode=0o777); self.out.chmod(0o777)
            ledger.bind(BINDING)
            self.assertEqual(list(self.out.iterdir()), [])
            self.assertIsNotNone(connector.read_ledger(saved / self.ledger_path().name))
        self.refused('E-OUTPUT', self.send)

    def test_post_delivery_save_error_reports_uncertainty_and_preserves_recovery(self):
        read = {'out_root': self.out, 'registry_id': REGISTRY_ID, 'name': 'fixture',
                'result': {'candidates': [ITEM]}, 'refs': {}}
        original = connector.Ledger.save
        def fail_after_acceptance(ledger):
            if ledger.data['items'].get('1:0', {}).get('status') == 'sent':
                raise Refusal('E-LEDGER')
            return original(ledger)
        stderr, stdout = io.StringIO(), io.StringIO()
        with patch.object(cli, 'read_registry', return_value=read), \
                patch.object(cli, '_connect', return_value=(SimpleNamespace(key_id_sha256='b'*64), BINDING['endpoint'], 'acme')), \
                patch.object(connector, 'Client', return_value=self.fake), \
                patch.object(connector.Ledger, 'save', fail_after_acceptance), \
                redirect_stderr(stderr), redirect_stdout(stdout):
            rc = cli.main(['send', '--auto', '--registry', str(self.reg)])
        self.assertEqual(rc, 3)
        self.assertEqual(len(self.fake.proposals), 1)
        self.assertIn('E-LEDGER:', stderr.getvalue())
        self.assertIn('delivery may be uncertain', stderr.getvalue())
        self.assertIn('recovery', stderr.getvalue())
        self.assertNotIn('nothing sent', stderr.getvalue())
        self.assertEqual(connector.read_ledger(self.ledger_path())['items']['1:0']['status'], 'sending')
        self.send()
        self.assertEqual(len(self.fake.proposals), 1)
        with connector.Ledger(self.out, REGISTRY_ID) as ledger:
            self.assertEqual(ledger.data['items']['1:0']['status'], 'uncertain')
            connector.resolve(ledger, '1:0', proposal='prp_'+'0'*26)
        self.send()
        self.assertEqual(len(self.fake.proposals), 1)


class BundledReader(unittest.TestCase):
    def test_only_bundled_reader_is_imported_after_pin_and_tampered_code_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); bridge = base / 'bridge'
            shutil.copytree(ROOT / 'plugins/memoranda/bridge', bridge)
            marker = base / 'executed'
            code = "import sys;sys.path.insert(0,sys.argv[1]);from squadra_memories.source import load_registry,Refusal\ntry:\n m,e=load_registry();print(m.__file__)\nexcept Refusal as e:\n print(e.code);sys.exit(3)"
            argv = [sys.executable, '-I', '-B', '-c', code, str(bridge)]
            positive = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            self.assertEqual(positive.returncode, 0, positive.stderr)
            self.assertEqual(positive.stdout.strip(), str(bridge / 'registry.py'))
            with (bridge / 'registry.py').open('a') as handle:
                handle.write('\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\n')
            refused = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            self.assertEqual(refused.returncode, 3, refused.stderr)
            self.assertEqual(refused.stdout.strip(), 'E-PIN')
            self.assertFalse(marker.exists())

    def test_external_reader_option_is_refused_for_every_command(self):
        for verb in ('collect', 'send', 'status', 'resolve'):
            with self.subTest(verb=verb):
                args = [verb, '--registry', '/synthetic-not-read', '--squadra-scripts', '/synthetic-not-read']
                if verb == 'resolve': args += ['--item', '1:0', '--abandon']
                result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'plugins/memoranda/bridge/memoranda_plugin.py'), *args],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertIn('unrecognized arguments: --squadra-scripts', result.stderr)
        self.assertFalse(hasattr(source, 'locate_scripts'))

    def test_bundle_has_no_dynamic_code_execution_calls(self):
        for path in (ROOT / 'plugins/memoranda').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('exec', 'eval', 'compile'), str(path))

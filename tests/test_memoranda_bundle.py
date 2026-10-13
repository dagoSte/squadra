"""Public Store boundary, isolated installed commands and stopped-writer migration."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('SQMEM_TEST_RELEASE', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'tests/memoranda_cases'))
sys.path.insert(0, str(ROOT / 'plugins/memoranda/bridge'))
sys.path.insert(0, str(ROOT / 'scripts'))
import fx
import fake_mcp
import plugin_store
import plugin_settings
import plugins
from squadra_memories import connector, panel, source
from squadra_memories.source import Refusal


def migration_module():
    spec = importlib.util.spec_from_file_location('memoranda_migration', ROOT / 'plugins/memoranda/bridge/migrate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Installed(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.home = self.tmp / 'home'; self.home.mkdir(mode=0o700)
        self.reg = fx.copy_registry(self.tmp)
        fx.install(self.home)
        self.plugin = self.home / '.squadra/plugins/memoranda'
        self.data = self.plugin / 'data'
        self.fake = fake_mcp.Fake(fx.TOKEN)
        self.addCleanup(self.fake.close)

    def item(self, sentence='Preserve the recorded decision.', **extra):
        return {'type': 'constraint', 'sentence': sentence, 'certainty': 'sure', 'private_content': 'no',
                'evidence': [{'event': fx.info()['review_seq']}], **extra}

    def run_bridge(self, command, *args, home=None, token=fx.TOKEN):
        home = home or self.home
        entry = home / '.squadra/plugins/memoranda/bridge/memoranda_plugin.py'
        env = fx.connector_env(home, token)
        trap = self.tmp / 'untrusted'; trap.mkdir(exist_ok=True)
        (trap / 'squadra_memories.py').write_text('raise RuntimeError("CWD/PYTHONPATH MUST NOT LOAD")\n')
        env['PYTHONPATH'] = str(trap)
        argv = [sys.executable, '-I', '-B', str(entry), command, '--registry', str(self.reg.root), *args]
        if command in ('send', 'status'):
            argv += ['--endpoint', self.fake.url, '--timeout', '3']
        return subprocess.run(argv, cwd=trap, env=env, capture_output=True, text=True, timeout=30)

    def ledger(self, home=None):
        return (home or self.home) / '.squadra/plugins/memoranda/data' / ('ledger-' + self.reg.event(1)['hash'] + '.json')

    def test_installed_isolated_journey_reinstall_preserves_managed_and_mutable_bytes(self):
        seq = fx.declare(self.reg, 'b1', [self.item('<b>Literal declaration</b>')])
        plugin_settings.save(self.home, 'memoranda', {'endpoint': 'https://mcp.example.invalid/t/fixture/mcp', 'key': fx.TOKEN})
        managed = plugin_store.bundle(ROOT, 'memoranda')['files']
        before = {name: fx.inventory(self.plugin)[name] for name in managed}
        registry_before = fx.inventory(self.reg.root)
        for command, args in [('collect', []), ('send', ['--auto']), ('send', ['--auto'])]:
            done = self.run_bridge(command, *args, token=None)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn(fx.TOKEN, done.stdout + done.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        entry = json.loads(self.ledger().read_text())['items'][f'{seq}:0']
        self.fake.decide(entry['proposal_id'], 'approved', memory_id='mem_' + '1' * 26)
        done = self.run_bridge('status', token=None)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(self.ledger().read_text())['items'][f'{seq}:0']['status'], 'approved')
        self.assertEqual(registry_before, fx.inventory(self.reg.root))
        self.assertEqual(before, {name: fx.inventory(self.plugin)[name] for name in managed})
        all_bytes = {str(p.relative_to(self.plugin)): p.read_bytes() for p in self.plugin.rglob('*') if p.is_file()}
        plugin_store.install(self.home, 'memoranda', ROOT)
        self.assertEqual(all_bytes, {str(p.relative_to(self.plugin)): p.read_bytes() for p in self.plugin.rglob('*') if p.is_file()})
        key = next(x for x in plugin_settings.read(self.home, 'memoranda') if x['id'] == 'key')
        self.assertEqual(key, {'id': 'key', 'set': True})
        views = ''.join(p.read_text() for p in (self.plugin / 'panels').glob('*.json'))
        self.assertNotIn(fx.TOKEN, views)
        for name in managed:
            p = self.plugin / name
            self.assertFalse(p.is_symlink())
            self.assertTrue(stat.S_ISREG(p.stat().st_mode))
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_actual_store_panel_settings_in_both_languages_and_themes(self):
        import http.client
        import threading
        import dashboard
        fx.declare(self.reg, 'b1', [self.item('<img src=x onerror=alert(1)>')])
        plugin_settings.save(self.home, 'memoranda', {'key': fx.TOKEN})
        done = self.run_bridge('collect'); self.assertEqual(done.returncode, 0, done.stderr)
        with patch.object(Path, 'home', return_value=self.home):
            server, token = dashboard.make_server(self.reg.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            def get(path):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    conn.request('GET', path, headers={'X-Squadra-Token': token})
                    reply = conn.getresponse(); self.assertEqual(reply.status, 200)
                    body = reply.read(); self.assertNotIn(fx.TOKEN.encode(), body)
                    return json.loads(body)
                finally: conn.close()
            state = self.tmp / 'state.json'; state.write_text(json.dumps(get('/api/state')))
            payload = self.tmp / 'payload.json'; payload.write_text(json.dumps({
                'catalogue': get('/api/plugin-store'), 'panel': get('/api/plugin/memoranda')}))
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
        self.assertFalse(thread.is_alive())
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node required; no skips')
        run = subprocess.run([node, str(ROOT / 'tests/memoranda_store_contract.js'),
                              str(ROOT / 'assets/dashboard.html'), str(state), 'all', str(payload)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('8 variants passed', run.stdout)

    def test_missing_key_refuses_before_registry_or_network_and_tampered_reader_refuses(self):
        done = self.run_bridge('send', '--auto', token=None)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertTrue(done.stderr.startswith('E-KEY:'), done.stderr)
        self.assertFalse(self.data.exists()); self.assertEqual(self.fake.requests, [])
        fx.declare(self.reg, 'b1', [self.item()])
        reader = self.plugin / 'bridge/registry.py'
        reader.write_bytes(reader.read_bytes() + b'\nraise RuntimeError("UNPINNED CODE")\n')
        done = self.run_bridge('collect')
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertTrue(done.stderr.startswith('E-PIN:'), done.stderr)
        self.assertNotIn('UNPINNED CODE', done.stderr)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_managed_instructions_are_not_replaced_and_unsafe_data_refuses(self):
        agents = self.plugin / 'AGENTS.md'; agents.write_text('LOCAL INSTRUCTIONS\n')
        done = self.run_bridge('collect')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(agents.read_text(), 'LOCAL INSTRUCTIONS\n')
        with self.assertRaises(plugins.Refused):
            plugin_store.install(self.home, 'memoranda', ROOT)
        agents.write_bytes((fx.BUNDLE / 'AGENTS.md').read_bytes())
        self.data.rename(self.tmp / 'saved-data')
        elsewhere = self.tmp / 'elsewhere'; elsewhere.mkdir()
        self.data.symlink_to(elsewhere)
        done = self.run_bridge('collect')
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertTrue(done.stderr.startswith('E-OUTPUT:'), done.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_public_example_is_a_selectable_draft(self):
        example = json.loads((fx.BUNDLE / 'examples/da-ricordare.json').read_text())
        example['task'] = 'T1'
        example['items'][0]['evidence'] = [{'event': fx.info()['review_seq']}]
        seq = fx._post(self.reg, 'b1', example, 'Public example, synthetic bindings only.')
        done = self.run_bridge('collect')
        self.assertEqual(done.returncode, 0, done.stderr)
        view = json.loads(next((self.plugin / 'panels').glob('*.json')).read_text())
        row = next(x for x in view['items'] if x['id'] == f'{seq}:0')
        self.assertEqual(row['status'], 'draft'); self.assertTrue(row['selectable'])
        self.assertEqual(row['text'], example['items'][0]['sentence'])
        self.assertEqual(self.fake.requests, [])

    def old(self):
        seq = fx.declare(self.reg, 'b1', [self.item()])
        fx.write_settings(self.home, 'https://mcp.example.invalid/t/fixture/mcp', fx.TOKEN)
        self.fake.script = ['lost']
        done = self.run_bridge('send', '--auto')
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertTrue(done.stderr.startswith('E-UNCERTAIN:'), done.stderr)
        old_plugin, old_data = self.tmp / 'old-plugin', self.tmp / 'old-data'
        old_plugin.mkdir(mode=0o700)
        for name in ('settings.json', 'secrets'):
            p = self.plugin / name
            if p.is_dir(): shutil.copytree(p, old_plugin / name)
            else: shutil.copy2(p, old_plugin / name)
        shutil.copytree(self.data, old_data)
        fresh = self.tmp / 'fresh-home'; fresh.mkdir(mode=0o700); fx.install(fresh)
        return seq, fresh, old_plugin, old_data

    def test_migration_carries_uncertain_binding_exact_bytes_and_never_resends(self):
        seq, fresh, old_plugin, old_data = self.old()
        before = fx.inventory(old_plugin), fx.inventory(old_data)
        original = next(old_data.glob('ledger-*.json')).read_bytes()
        script = fresh / '.squadra/plugins/memoranda/bridge/migrate.py'
        run = subprocess.run([sys.executable, '-I', '-B', str(script), '--from-plugin', str(old_plugin),
                              '--from-data', str(old_data)], env=fx.env(fresh), cwd=self.tmp,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.ledger(fresh).read_bytes(), original)
        self.assertEqual(fx.saved_files(fresh)['key'][-1], fx.saved_files(self.home)['key'][-1])
        self.assertEqual(before, (fx.inventory(old_plugin), fx.inventory(old_data)))
        again = self.run_bridge('send', '--auto', home=fresh, token=None)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        self.assertEqual(json.loads(self.ledger(fresh).read_text())['items'][f'{seq}:0']['status'], 'uncertain')
        refused = self.run_bridge('send', '--auto', '--scope', 'squadra/other', home=fresh, token=None)
        self.assertEqual(refused.returncode, 3)
        self.assertTrue(refused.stderr.startswith('E-BINDING:'), refused.stderr)
        self.assertEqual(len(self.fake.proposes), 1)

    def test_partial_migration_blocks_send_until_same_input_retry(self):
        _, fresh, old_plugin, old_data = self.old()
        module = migration_module(); atomic = module.files.atomic
        def fail(parent, name, body):
            if name.startswith('ledger-'): raise OSError('synthetic storage fault')
            return atomic(parent, name, body)
        with patch.object(module.files, 'atomic', side_effect=fail):
            with self.assertRaises(OSError): module.migrate(fresh, old_plugin, old_data)
        marker = fresh / '.squadra/plugins/memoranda/.migration-pending'
        self.assertTrue(marker.is_file())
        stopped = self.run_bridge('send', '--auto', home=fresh)
        self.assertEqual(stopped.returncode, 3)
        self.assertTrue(stopped.stderr.startswith('E-MIGRATION:'), stopped.stderr)
        self.assertEqual(len(self.fake.proposes), 1)
        original = next(old_data.glob('ledger-*.json')); body = original.read_bytes()
        original.write_bytes(body + b'\n')
        with self.assertRaises(plugins.Refused): module.migrate(fresh, old_plugin, old_data)
        original.write_bytes(body)
        self.assertTrue(module.migrate(fresh, old_plugin, old_data)['migrated'])
        self.assertFalse(marker.exists()); self.assertEqual(self.ledger(fresh).read_bytes(), body)

    def test_migration_refuses_conflict_missing_ledgers_and_busy_or_unsafe_source(self):
        _, fresh, old_plugin, old_data = self.old()
        module = migration_module()
        empty = self.tmp / 'empty'; empty.mkdir(mode=0o700)
        with self.assertRaises(plugins.Refused): module.migrate(fresh, old_plugin, empty)
        alias = self.tmp / 'alias'; alias.symlink_to(old_data)
        with self.assertRaises(plugins.Refused): module.migrate(fresh, old_plugin, alias)
        with connector.Ledger(old_data, self.reg.event(1)['hash']):
            with self.assertRaises(plugins.Refused): module.migrate(fresh, old_plugin, old_data)
        plugin_settings.save(fresh, 'memoranda', {'endpoint': 'https://example.invalid/t/other/mcp'})
        before = fx.inventory(fresh)
        with self.assertRaises(plugins.Refused): module.migrate(fresh, old_plugin, old_data)
        self.assertEqual(before, fx.inventory(fresh))
        self.assertFalse(self.ledger(fresh).exists())


class BundleContract(unittest.TestCase):
    def test_bundled_helpers_are_exact_source_bytes_and_pin_refuses_modified_code(self):
        for name in ('registry.py', 'plugins.py', 'plugin_settings.py'):
            self.assertEqual((fx.ROOT / name).read_bytes(), (ROOT / 'scripts' / name).read_bytes(), name)
        module, entry = source.load_registry(fx.ROOT)
        self.assertEqual(hashlib.sha256((fx.ROOT / 'registry.py').read_bytes()).hexdigest(), entry['registry_py_sha256'])
        for kind in entry['kinds']:
            try: module.validate(module.initial(), kind, 'x', {})
            except Exception as exc: self.assertNotEqual(str(exc), 'Unknown event kind', kind)
        with self.assertRaises(Exception) as failure: module.validate(module.initial(), 'unknown.synthetic', 'x', {})
        self.assertEqual(str(failure.exception), 'Unknown event kind')
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / 'scripts'; scripts.mkdir()
            (scripts / 'registry.py').write_bytes((fx.ROOT / 'registry.py').read_bytes())
            (scripts.parent / 'SOURCE_COMMIT').write_text(entry['source_commit'])
            self.assertEqual(source.load_registry(scripts)[1], entry)
            (scripts.parent / 'SOURCE_COMMIT').write_text('not-the-pinned-commit')
            with self.assertRaises(Refusal): source.load_registry(scripts)

    def test_trial_closed_enum_and_bilingual_price_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            shutil.copytree(fx.BUNDLE, release / 'plugins/memoranda')
            path = release / 'plugins/memoranda/store.json'; original = json.loads(path.read_text())
            for value in ('free', 'paid', 'trial', 'trial-for-a-week', True, None):
                path.write_text(json.dumps({**original, 'price': value}))
                if value in ('free', 'paid', 'trial'):
                    self.assertEqual(plugin_store.bundle(release, 'memoranda')['store']['price'], value)
                else:
                    with self.assertRaises(plugins.Refused): plugin_store.bundle(release, 'memoranda')
        manifest = json.loads((fx.BUNDLE / 'plugin.json').read_text())
        self.assertEqual(manifest['about']['publisher'], 'memoranda')
        price = next(s for s in manifest['about']['sections'] if s['id'] == 'price')
        self.assertIn('free', price['text']['en'].lower())
        self.assertIn('paid', price['text']['en'].lower())
        self.assertIn('gratuit', price['text']['it'].lower())
        self.assertIn('pagamento', price['text']['it'].lower())

    def test_agents_and_public_docs_keep_permission_and_stopped_send_boundaries(self):
        text = (fx.BUNDLE / 'AGENTS.md').read_text()
        for marker in ('never widens permissions', 'publication or decisions reserved to the user',
                       'Run `status` even when `send --auto` stops', 'No selection or command is required',
                       'Never use `--resend`, `--accept-scope-policy` or `resolve` without'):
            self.assertIn(marker, text)
        self.assertEqual(text.count('python3 -I -B'), 4)
        for filename in ('README.md', 'README.it.md'):
            doc = (fx.BUNDLE / filename).read_text()
            self.assertNotIn('You can replace a pending choice', doc)
            self.assertNotIn('Puoi sostituire o annullare la richiesta', doc)
            self.assertIn('migrate.py', doc)
            self.assertIn('secrets/key', doc)
        self.assertIn('propose-only', (fx.BUNDLE / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()

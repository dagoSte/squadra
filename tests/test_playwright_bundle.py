"""Store installation, isolated bridge execution and the single authoring contract."""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import test_playwright_plugin as carried

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'plugins/playwright'
HELPERS = ('plugins', 'plugin_actions', 'plugin_settings', 'registry')


class PlaywrightBundle(unittest.TestCase):
    @contextmanager
    def fixture(self):
        case = carried.PlaywrightBridge()
        case.setUp()
        try:
            yield case
        finally:
            case.doCleanups()

    def probe(self, case, args, expected=0):
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env['HOME'] = str(case.home)
        run = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'tests/playwright_bridge_probe.py'),
                              str(case.plugin / 'bridge'), json.dumps(args)],
                             env=env, cwd=case.folder, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(data['trap_calls'], [], data)
        self.assertTrue(data['readonly_calls'], data)
        self.assertTrue(all(v is True for v in data['readonly_calls']), data)
        self.assertIsNone(data['error'], data)
        self.assertEqual(data['rc'], expected, data)
        return data

    def write(self, case, name, value):
        path = case.folder / name
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return str(path)

    def test_helpers_match_same_tree_bytes(self):
        for name in HELPERS:
            with self.subTest(helper=name):
                self.assertEqual((BUNDLE / 'bridge' / (name + '.py')).read_bytes(),
                                 (ROOT / 'scripts' / (name + '.py')).read_bytes())
        self.assertEqual((BUNDLE / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())

    def test_installed_commands_import_only_the_bundle_in_isolated_python(self):
        with self.fixture() as c:
            spec = self.write(c, 'input.json', c.spec)
            prepared = self.probe(c, ['prepare', '--root', str(c.root), '--file', spec])
            ids = json.loads(prepared['stdout'])['ids']
            carried.actions.save(c.home, 'playwright', 'check', c.key, [ids[0]])
            pending = self.probe(c, ['pending', '--root', str(c.root)])
            snapshot = self.write(c, 'pending.json', json.loads(pending['stdout']))
            result = self.write(c, 'result.json', {'schema': 1, 'results': [c.result(ids[0])]})
            reported = self.probe(c, ['record', '--root', str(c.root), '--snapshot', snapshot, '--file', result])
            self.assertTrue(json.loads(reported['stdout'])['consumed'])
            for reply in (prepared, pending, reported):
                self.assertEqual(set(reply['modules']), {'playwright_plugin', *HELPERS})
                self.assertTrue(all(Path(v).parent == (c.plugin / 'bridge').resolve() for v in reply['modules'].values()))
            direct = subprocess.run([sys.executable, '-I', '-B', str(c.plugin / 'bridge/playwright_plugin.py'),
                                     'pending', '--root', str(c.root), '--home', str(c.home)],
                                    cwd=c.folder, env={'PATH': os.environ['PATH'], 'HOME': str(c.home)},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertIsNone(json.loads(direct.stdout))
            self.assertFalse(list(c.plugin.rglob('__pycache__')))

    def test_every_command_preserves_registry_rows_bytes_and_mtimes(self):
        def snapshot(root):
            return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_size)
                    for p in root.rglob('*') if p.is_file() and p.name != 'registry.sqlite3-shm'}
        for writer_open in (False, True):
            with self.subTest(writer_open=writer_open), self.fixture() as c:
                receipt = c.receipt()
                c.store.project()
                rows = c.store.events()
                if not writer_open:
                    c.store.close()
                    self.assertFalse((c.root / 'registry.sqlite3-wal').exists())
                    self.assertFalse((c.root / 'registry.sqlite3-shm').exists())
                def command(args, expected=0):
                    before = snapshot(c.root)
                    data = self.probe(c, args, expected)
                    after = snapshot(c.root)
                    wal = 'registry.sqlite3-wal'
                    if wal not in before and wal in after:
                        self.assertEqual(after[wal][2], 0)
                        del after[wal]
                    self.assertEqual(before, after)
                    reader = carried.Store(c.root, read_only=True)
                    try: self.assertEqual(rows, reader.events())
                    finally: reader.close()
                    return data
                spec = self.write(c, 'input.json', c.spec)
                reply = command(['prepare', '--root', str(c.root), '--file', spec])
                first = json.loads(reply['stdout'])['ids'][0]
                command(['pending', '--root', str(c.root)])
                bad = self.write(c, 'bad.json', {**c.spec, 'task': 'UNKNOWN'})
                command(['prepare', '--root', str(c.root), '--file', bad], 1)
                carried.actions.save(c.home, 'playwright', 'check', c.key, [first])
                pending = command(['pending', '--root', str(c.root)])
                selection = self.write(c, 'pending.json', json.loads(pending['stdout']))
                partial = self.write(c, 'partial.json', {'schema': 1, 'results': []})
                command(['record', '--root', str(c.root), '--snapshot', selection, '--file', partial])
                invalid = self.write(c, 'invalid.json', {'schema': 1, 'results': [c.result(first, receipt='absent')]})
                command(['record', '--root', str(c.root), '--snapshot', selection, '--file', invalid], 1)
                outcome = self.write(c, 'outcome.json', {'schema': 1, 'results': [c.result(first, receipt=receipt)]})
                args = ['record', '--root', str(c.root), '--snapshot', selection, '--file', outcome]
                command(args); command(args)

    def test_missing_registry_is_never_created_by_any_command(self):
        with self.fixture() as c:
            body = self.write(c, 'empty.json', {})
            for folder_exists in (False, True):
                missing = c.folder / ('empty-registry' if folder_exists else 'absent-registry')
                if folder_exists: missing.mkdir()
                before = missing.stat().st_mtime_ns if folder_exists else None
                for name in ('prepare', 'pending', 'record'):
                    args = [name, '--root', str(missing)]
                    if name != 'pending': args += ['--file', body]
                    if name == 'record': args += ['--snapshot', body]
                    data = self.probe(c, args, 1)
                    self.assertEqual(json.loads(data['stderr']), {'error': 'registry_invalid'})
                    self.assertEqual(missing.exists(), folder_exists)
                    if folder_exists:
                        self.assertEqual(list(missing.iterdir()), [])
                        self.assertEqual(missing.stat().st_mtime_ns, before)

    def test_real_store_bundle_catalogue_installation_and_receipt(self):
        with self.fixture() as c:
            value = carried.plugin_store.bundle(ROOT, 'playwright')
            self.assertTrue(value['store']['mcp'])
            self.assertEqual(value['store']['accepted_version'], value['manifest']['about']['version'])
            self.assertEqual(value['manifest']['about']['homepage'], value['store']['source'])
            date.fromisoformat(value['store']['review_date'])
            self.assertEqual(value['store']['price'], 'free')
            self.assertEqual(value['store']['licence'], 'MIT')
            self.assertIn('examples/playwright-checks.json', value['files'])
            for path in value['files']:
                self.assertFalse(set(x.casefold() for x in Path(path).parts) & carried.plugin_store.DISCOVERY)
                self.assertNotIn(Path(path).parts[0].casefold(), carried.plugin_store.RESERVED)
            run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/squadra.py'), 'plugin', 'list'],
                                 env={**os.environ, 'HOME': str(c.home)}, cwd=c.folder,
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            catalog = json.loads(run.stdout)
            self.assertEqual(catalog['refused'], [])
            matching = [row for row in catalog['plugins'] if row['name'] == 'playwright']
            self.assertEqual(len(matching), 1)
            self.assertIn('memoranda', [row['name'] for row in catalog['plugins']])
            self.assertEqual([row['name'] for row in catalog['plugins'][:2]], ['memoranda', 'playwright'])
            row = matching[0]
            self.assertEqual(row['name'], 'playwright'); self.assertTrue(row['installed'])
            self.assertEqual(row['receipt']['state'], 'complete')
            receipt = json.loads((c.plugin / '.store-receipt.json').read_text())
            self.assertEqual(receipt['files'], value['files'])
            self.assertEqual(receipt['release'], catalog['release'])

    def test_store_page_contains_the_real_playwright_bundle(self):
        import shutil
        with self.fixture() as c:
            state = c.store.state()
            state['events'] = c.store.events()
            payload = self.write(c, 'catalogue.json', carried.plugin_store.catalogue(c.home, ROOT))
            fixture = self.write(c, 'state.json', state)
            node = shutil.which('node'); self.assertIsNotNone(node, 'Node required; no skips')
            proc = subprocess.run([node, str(ROOT / 'tests/plugin_store_contract.js'),
                                   str(ROOT / 'assets/dashboard.html'), fixture, 'playwright', payload],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('store contract passed: playwright', proc.stdout)

    def test_italian_overview_links_to_the_single_english_contract(self):
        text = (ROOT / 'docs/plugin-authoring.it.md').read_text()
        self.assertNotIn('```', text); self.assertNotIn('~~~', text)
        self.assertNotRegex(text, r'(?m)^(?: {4}|\t)\S')
        self.assertNotRegex(text, r'\b\d+\b|KiB|MiB|chmod|LOCK_|0600|0700')
        self.assertIn('pull request', text); self.assertIn('indirizzo di contatto', text)
        links = re.findall(r'\]\(([^)]+)\)', text)
        self.assertGreaterEqual(len(links), 10)
        for target in links:
            name, _, anchor = target.partition('#')
            dest = ROOT / 'docs' / name
            self.assertTrue(dest.is_file(), target)
            if anchor: self.assertIn('id="' + anchor + '"', dest.read_text(), target)

    def test_docs_use_store_install_and_document_legacy_migration(self):
        self.assertFalse((ROOT / 'scripts/playwright_plugin.py').exists())
        for name in ['README.md', 'README.it.md', 'AGENTS.md']:
            text = (BUNDLE / name).read_text()
            commands = re.findall(r'(?m)^python3[^\n]*playwright_plugin\.py[^\n]*', text)
            self.assertEqual(len(commands), 3)
            self.assertTrue(all(c.startswith('python3 -I -B ') for c in commands))
        for path in ROOT.rglob('*.md'):
            if '.git' in path.parts: continue
            self.assertNotIn('scripts/playwright_plugin.py install', path.read_text(), str(path))
        english, italian = [(BUNDLE / n).read_text() for n in ('README.md', 'README.it.md')]
        self.assertIn('move it aside', english); self.assertIn('sposta la cartella', italian)
        self.assertNotIn('settings', json.loads((BUNDLE / 'plugin.json').read_text()))
        en_prose, it_prose = [' '.join(text.split()) for text in (english, italian)]
        self.assertIn('does not declare settings', en_prose)
        self.assertIn('non dichiara impostazioni', it_prose)
        self.assertIn('Earlier reports remain in the moved-aside folder', en_prose)
        self.assertIn('Le relazioni precedenti restano nella cartella spostata', it_prose)
        self.assertIn('Copying `checks/` into the new Store installation is not supported', en_prose)
        self.assertIn('`checks/` nella nuova installazione dello Store non è supportato', it_prose)
        for text in (english, italian):
            self.assertIn('checks/<registry-key>.json', text)
            first_section = text.split('## 1.', 1)[1].split('## 2.', 1)[0]
            self.assertIn('https://github.com/microsoft/playwright-mcp', first_section)
            self.assertIn('plugin install playwright', text)
            self.assertIn('(examples/playwright-checks.json)', text)

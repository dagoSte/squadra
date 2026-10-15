"""Usage reads preserve the registry, the API boundary and private transcript content."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dashboard
import usage
from squadra import main
from registry import Store
from test_reads_value import seed

SESSION = '11111111-1111-4111-8111-111111111111'
CANARY = 'PRIVATE-CONTENT-TOKEN-CANARY-DoNotKeep'


class UsageRoutes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-usage-routes-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'; self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'CODEX_HOME': str(self.home / '.codex'),
                                         'CLAUDE_CONFIG_DIR': str(self.home / '.claude')})
        self.env.start(); self.addCleanup(self.env.stop)
        usage._COLLECTORS.clear()
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        seed(self.store, self.base)
        # A registered identity is enough for collection; no transcript content is recorded.
        self.store.append('agent', 'coord', {'id': 'tokens', 'identity': 'claude-window-' + SESSION,
                          'role': 'executor', 'client': 'claude', 'model': '', 'session': SESSION,
                          'repo': str(self.base), 'skills': []})
        self.store.project()
        folder = self.home / '.claude/projects/project'; folder.mkdir(parents=True)
        self.source = folder / (SESSION + '.jsonl')
        self.source.write_text(json.dumps({'type': 'assistant', 'sessionId': SESSION, 'timestamp': '2026-10-15T10:00:00Z',
             'message': {'id': 'message-1', 'model': 'claude-sonnet-4-5', 'content': CANARY,
                         'usage': {'input_tokens': 200, 'output_tokens': 25, 'cache_read_input_tokens': 750}}}) + '\n')

    def frozen(self):
        return {str(p.relative_to(self.store.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.store.root.rglob('*') if p.is_file() and not p.name.endswith(('-shm', '-wal'))}

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['usage', '--root', str(self.store.root), *args])
        self.assertNotIn(CANARY, out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def start_server(self):
        server, token = dashboard.make_server(self.store.root)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        def stop():
            server.shutdown(); server.server_close(); worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.addCleanup(stop)
        return server, token

    def get(self, server, token, **headers):
        req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/usage',
                                     headers={'X-Squadra-Token': token, **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.load(response), dict(response.headers)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc), dict(exc.headers)

    def test_cli_json_text_api_share_numbers_and_no_registry_writes(self):
        before = self.frozen()
        fixed = usage.instant('2026-10-15T11:00:00Z')
        with patch.object(usage.time, 'time', return_value=fixed):
            code, out, err = self.cli(['--json'])
            self.assertEqual(code, 0, err); obj = json.loads(out)
            self.assertEqual(obj['windows'][-1]['totals']['total'], 975)
            code, out, err = self.cli([])
            self.assertEqual(code, 0, err); self.assertIn('975 tokens', out)
            server, token = self.start_server()
            status, api, headers = self.get(server, token)
            self.assertEqual(status, 200)
            self.assertEqual(api, obj)
            self.assertIsNone(api['cost'])
            self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.frozen(), before)
        self.assertNotIn(CANARY, json.dumps(api))
        self.assertNotIn(CANARY, repr([s.records for c in usage._COLLECTORS.values() for s in c.sources.values()]))

    def test_api_auth_and_price_header_does_not_set_cost(self):
        server, token = self.start_server()
        self.assertEqual(self.get(server, 'wrong')[0], 401)
        self.assertEqual(self.get(server, token, Host='evil.invalid')[0], 403)
        status, value, _ = self.get(server, token, **{'X-Squadra-Prices': CANARY})
        self.assertEqual(status, 200); self.assertIsNone(value['cost'])
        self.assertNotIn(CANARY, json.dumps(value))

    def test_state_does_not_collect_transcripts_and_usage_verifies_registry(self):
        server, token = self.start_server()
        with patch.object(usage, 'collect', side_effect=AssertionError('must not scan from state')):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/state', headers={'X-Squadra-Token': token})
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 200)
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1"); self.store.db.commit()
        self.assertEqual(self.get(server, token)[0], 409)

    def test_missing_registry_and_bad_prices_do_not_create_or_append(self):
        missing = self.base / 'absent'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['usage', '--root', str(missing), '--json']), 1)
        self.assertFalse(missing.exists())
        price = self.base / 'prices.json'; price.write_text(CANARY)
        before = self.frozen()
        code, out, err = self.cli(['--prices', str(price)])
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'Squadra: usage_unavailable')
        self.assertEqual(self.frozen(), before)

    def test_changed_format_is_unknown_and_private_text_never_written(self):
        self.source.write_text(json.dumps({'type': 'assistant', 'message': {'id': 'm', 'content': CANARY,
                              'model': 'claude-sonnet-4-5', 'usage': {'new_count': 900}}}) + '\n')
        before = self.frozen()
        code, out, err = self.cli(['--json']); self.assertEqual(code, 0, err)
        value = json.loads(out)
        window = next(w for w in value['windows'] if w['identity'].endswith(SESSION))
        self.assertEqual(window['status'], 'not_measured'); self.assertIsNone(window['totals'])
        self.assertTrue(window['reasons']); self.assertEqual(self.frozen(), before)
        for p in self.store.root.rglob('*'):
            if p.is_file() and not p.name.endswith(('-wal', '-shm')):
                self.assertNotIn(CANARY.encode(), p.read_bytes())


if __name__ == '__main__':
    unittest.main()

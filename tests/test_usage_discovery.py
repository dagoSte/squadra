"""Discovery walks only registered clients and resumes; a window without a session says so; no window starves."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import balance
import usage
from squadra import main
import test_squadra as support
from test_usage import CLAUDE, CODEX, NOW, claude, codex, model


class UsageDiscovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sq-usage-discovery-')
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.roots = {k: self.folder / k for k in ('claude', 'codex')}
        for root in self.roots.values():
            root.mkdir(mode=0o700)
        self.state = {'agents': {}, 'tasks': {}}
        usage._COLLECTORS.clear()
        self.addCleanup(usage._COLLECTORS.clear)

    def seat(self, actor, *, client, session=None, identity=None):
        self.state['agents'][actor] = {'identity': identity or actor, 'client': client, 'session': session}

    def write(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
        path.chmod(0o600)
        return path

    def codex_file(self):
        return self.write(self.roots['codex'] / '2026/10/15' / ('rollout-2026-10-15T12-00-00-' + CODEX + '.jsonl'),
                          [{'type': 'session_meta', 'payload': {'id': CODEX}}, model(NOW - 300),
                           codex(NOW - 200, 0, 0), codex(NOW - 100, 100, 20)])

    def folders(self, count):
        for n in range(count):
            (self.roots['claude'] / ('project-%02d' % (n % 26)) / ('s-%05d' % n)).mkdir(parents=True, mode=0o700)

    def collect(self, budget):
        return usage.collect([], self.state, roots=self.roots, now=NOW, budget_seconds=budget)

    def until_settled(self, budget, calls):
        seen = []
        for _ in range(calls):
            data = self.collect(budget)
            seen.append(data['coverage']['status'])
            if seen[-1] != 'collecting':
                break
        return data, seen

    def window(self, data, identity):
        return next(w for w in data['windows'] if w['identity'] == identity)

    def test_a_codex_only_squad_beside_thousands_of_claude_folders_is_measured(self):
        self.folders(6000)
        self.codex_file()
        self.seat('cx', client='codex', session=CODEX)
        data, _ = self.until_settled(0.02, 500)
        cx = self.window(data, 'cx')
        self.assertNotIn('discovery_limited', cx['reasons'])
        self.assertEqual(cx['totals']['total'], 120)

    def test_a_long_claude_walk_resumes_and_says_collecting_until_measured(self):
        self.folders(6000)
        self.write(self.roots['claude'] / 'zz-project' / (CLAUDE + '.jsonl'), [claude(NOW - 100, inp=40, out=2)])
        self.seat('cl', client='claude', session=CLAUDE)
        data, seen = self.until_settled(0.02, 2000)
        self.assertIn('collecting', seen)
        self.assertNotIn('limited', seen[:-1])
        cl = self.window(data, 'cl')
        self.assertEqual((cl['status'], cl['totals']['total']), ('measured', 42))

    def test_a_session_created_after_a_completed_miss_is_found(self):
        self.seat('cl', client='claude', session=CLAUDE)
        (self.roots['claude'] / 'project').mkdir(mode=0o700)
        data, _ = self.until_settled(0.5, 50)
        self.assertIn('session_not_found', self.window(data, 'cl')['reasons'])
        self.write(self.roots['claude'] / 'project' / (CLAUDE + '.jsonl'), [claude(NOW - 50, inp=7, out=3)])
        data, _ = self.until_settled(0.5, 50)
        self.assertEqual(self.window(data, 'cl')['totals']['total'], 10)

    def test_a_window_registered_without_a_session_names_session(self):
        self.seat('builder', client='claude', identity='window-02')
        data, _ = self.until_settled(0.5, 50)
        builder = self.window(data, 'window-02')
        self.assertEqual(builder['status'], 'not_measured')
        self.assertIn('session_not_registered', builder['reasons'])
        self.assertIn('--session', usage.text_report(data))

    def test_a_large_claude_file_does_not_starve_a_codex_window(self):
        rows = [claude(NOW - 1000 + n * 0.01, key='m-%06d' % n, inp=1, out=1) for n in range(60000)]
        self.write(self.roots['claude'] / 'project' / (CLAUDE + '.jsonl'), rows)
        self.codex_file()
        self.seat('a-claude', client='claude', session=CLAUDE)
        self.seat('b-codex', client='codex', session=CODEX)
        for _ in range(3):
            data = self.collect(0.05)
        self.assertEqual(self.window(data, 'b-codex')['totals'] and self.window(data, 'b-codex')['totals']['total'], 120)

    def test_the_cli_loops_give_each_slice_the_time_left(self):
        budgets = []
        def fake(events, state, **kwargs):
            budgets.append(kwargs.get('budget_seconds', 0.25))
            return {'coverage': {'status': 'ready'}}
        with patch('usage.collect', side_effect=fake):
            balance.collect_usage([], {})
        self.assertGreater(budgets[0], 1)


class UsageCommand(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def test_the_usage_command_gives_each_slice_the_time_left(self):
        budgets = []
        def fake(events, state, **kwargs):
            budgets.append(kwargs.get('budget_seconds', 0.25))
            return {'coverage': {'status': 'ready'}}
        with patch('usage.collect', side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--root', str(self.root), 'usage', '--json']), 0)
        self.assertGreater(budgets[0], 1)


if __name__ == '__main__':
    unittest.main()

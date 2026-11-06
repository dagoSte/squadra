"""task --paths with the real analysis engine: independent packages become lanes in one command."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import registry
import squadra
from registry import Store, plain


class AutomaticLanes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='lanes-e2e-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / 'project'
        self.env = os.environ | {'HOME': str(self.base), 'GIT_CONFIG_NOSYSTEM': '1'}
        self.repo.mkdir()
        self.git(self.repo, 'init', '-q')
        self.git(self.repo, 'config', 'user.name', 'Synthetic')
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        hub = 'def require(value):\n    """Shared helper used by every package of the synthetic project."""\n    return value\n'
        files = {'core/hub.py': hub,
                 'alpha/__init__.py': '', 'beta/__init__.py': '',
                 'alpha/engine.py': 'from core.hub import require\nfrom alpha import parts\n\nVALUE = require(parts.SIZE)\n',
                 'alpha/parts.py': 'SIZE = 1  # alpha parts, independent of beta\n',
                 'beta/policy.py': 'from core.hub import require\nfrom beta import rules\n\nLIMIT = require(rules.MAX)\n',
                 'beta/rules.py': 'MAX = 2  # beta rules, independent of alpha\n',
                 'tests/test_engine.py': 'from alpha import engine\n',
                 'tests/test_policy.py': 'from beta import policy\n',
                 # A third importer: fan-in 3 is the contract's minimum for a shared hub.
                 'tests/test_hub.py': 'from core.hub import require\n\nassert require(1) == 1\n'}
        for name, body in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(body)
        self.commit('fixture')
        for round_ in range(3):
            for group in (('alpha/engine.py', 'alpha/parts.py', 'tests/test_engine.py'),
                          ('beta/policy.py', 'beta/rules.py', 'tests/test_policy.py')):
                for name in group:
                    with (self.repo / name).open('a') as handle:
                        handle.write('# change %d\n' % round_)
                self.commit('change ' + group[0])
        self.head = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'Synthetic lanes', 'project': str(self.repo),
                          'version': registry.VERSION, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
        self.agent('C', 'coordinator', 'coord', 'claude', self.repo)
        for name in ('X', 'Y'):
            checkout = self.base / name
            self.git(self.base, 'clone', '-q', '--no-hardlinks', str(self.repo), str(checkout))
            self.agent(name, 'builder', name, 'codex', checkout)
        self.agent('R', 'reviewer', 'codex-reader', 'codex', self.base / 'X')
        self.scope = self.base / 'scope.txt'
        self.scope.write_text('Change alpha and beta behind the shared hub\n')
        self.queue = patch.object(squadra, 'codex_queue', return_value={'delivered': True, 'native_rc': 0})
        self.queue.start()
        self.addCleanup(self.queue.stop)

    def git(self, repo, *args):
        return subprocess.check_output(['git', *args], cwd=repo, env=self.env, stderr=subprocess.PIPE).decode()

    def commit(self, message):
        self.git(self.repo, 'add', '-A')
        self.git(self.repo, '-c', 'commit.gpgsign=false', 'commit', '-qm', message)

    def agent(self, name, role, identity, client, repo):
        self.store.append('agent', name, {'id': name, 'role': role, 'identity': identity, 'client': client,
                          'repo': str(repo), 'skills': [], 'session': 'synthetic-' + name})

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = squadra.main(['--root', str(self.store.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_task_paths_creates_contract_and_two_lanes_with_the_real_engine(self):
        requirements = self.base / 'requirements.json'
        requirements.write_text('[{"id": "R1", "text": "Integrated behaviour"}]')
        code, out, err = self.cli('task', '--actor', 'C', '--owner', 'C', '--id', 'F', '--title', 'Two packages',
                                  '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R',
                                  '--requirements-file', str(requirements),
                                  '--paths', 'alpha/*.py', '--paths', 'beta/*.py', '--paths', 'core/*.py',
                                  '--paths', 'tests/*.py', '--proof', sys.executable, '-B', '-c', 'print(1)')
        self.assertEqual(code, 0, err)
        state = plain(self.store.view())
        tasks = {k: v for k, v in state['tasks'].items() if k.startswith('F')}
        self.assertEqual(sorted(tasks), ['F', 'F.L0', 'F.L1', 'F.L2'])
        self.assertEqual(tasks['F']['requirements'][0]['id'], 'R1')
        self.assertTrue(all(tasks[k]['requirements'] == [] for k in ('F.L0', 'F.L1', 'F.L2')))
        self.assertEqual(tasks['F.L0']['owner'], 'C')
        self.assertEqual(tasks['F.L0']['reviewer'], 'R')
        lanes = {tasks[k]['owner']: k for k in ('F.L1', 'F.L2')}
        self.assertEqual(sorted(lanes), ['X', 'Y'])
        for lane in ('F.L1', 'F.L2'):
            self.assertEqual(tasks[lane]['reviewer'], 'C', 'Codex builders are read by the Claude coordinator')
            self.assertEqual(tasks[lane]['depends'], ['F.L0'])
            self.assertEqual(Path(tasks[lane]['repo']).name, tasks[lane]['owner'])
        scopes = {lane: (self.store.root / 'artifacts' / tasks[lane]['scope']['sha256']).read_text()
                  for lane in ('F.L0', 'F.L1', 'F.L2')}
        self.assertIn('core/hub.py', scopes['F.L0'])
        owned = [line for text in scopes.values() for line in text.splitlines() if '/' in line and ':' not in line]
        self.assertEqual(len(owned), len(set(owned)), 'Each path belongs to exactly one lane')
        alpha = next(l for l in ('F.L1', 'F.L2') if 'alpha/engine.py' in scopes[l])
        self.assertIn('alpha/parts.py', scopes[alpha])
        self.assertIn('tests/test_engine.py', scopes[alpha])
        self.assertNotIn('beta/policy.py', scopes[alpha])
        self.assertIn('lanes-F', state['refs']['decision'])
        for owner in ('X', 'Y'):
            self.assertIn('assign-' + lanes[owner], state['messages'])

    def test_lanes_none_keeps_a_single_task(self):
        code, out, err = self.cli('task', '--actor', 'C', '--owner', 'C', '--id', 'G', '--title', 'One task',
                                  '--repo', str(self.repo), '--scope-file', str(self.scope), '--reviewer', 'R',
                                  '--paths', 'alpha/*.py', '--paths', 'beta/*.py', '--lanes', 'none',
                                  '--proof', sys.executable, '-B', '-c', 'print(1)')
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(k for k in plain(self.store.view())['tasks'] if k.startswith('G')), ['G'])


if __name__ == '__main__':
    unittest.main()

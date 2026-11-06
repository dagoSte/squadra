"""Council addendum before 3.14.0: budgets across successors, inherited plans, the resource FIFO, import layers, language."""
import ast
import contextlib
import datetime as dt
import errno
import io
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_squadra as support
import ripresa
import runtime
from registry import Store, plain, review_limit, snapshot
from replacement import execute as replace
from squadra import main

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class Addendum(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    proof = support.Workflow.proof
    delivered = support.Workflow.delivered
    cli = support.Workflow.cli

    def run_cli(self, *args, root=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--root', str(root or self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def review(self, task, verdict, text):
        self.report.write_text(text + '\n')
        return self.run_cli('review', '--actor', 'R', '--task', task, '--verdict', verdict, '--report', str(self.report))

    def extend(self, task, by=1):
        self.assertEqual(self.cli('decision', '--actor', 'C', '--reason', 'More reads', '--extend-budget', task,
                                  '--stage', 'code', '--by', str(by), '--owner-words', 'Una lettura in piu',
                                  '--channel', 'typed-here'), 0)

    def take_over(self):
        request = {'map': {'A': 'B'}, 'sessions': {'B': 'new-session'}, 'client': 'codex', 'carry': 'useful',
                   'force': True, 'reason': 'Window exhausted its budget'}
        with patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            return replace(self.store, 'C', request, False)['successors']['T']

    # R1
    def test_R1_an_owner_extension_survives_a_replacement_takeover(self):
        self.delivered()
        self.assertEqual(self.review('T', 'changes-required', 'first')[0], 0)
        self.assertEqual(self.review('T', 'changes-required', 'second')[0], 0)
        self.extend('T')
        successor = self.take_over()
        state = self.store.state()
        self.assertEqual(state['tasks'][successor]['rounds']['code'], 2, 'the successor inherits the spent rounds')
        self.assertEqual(review_limit(state, successor, 'code'), 3)
        self.store.append('ack', 'B', {'message': 'assign-' + successor})
        self.assertEqual(self.proof(actor='B', task=successor)['status'], 'passed')
        self.assertEqual(self.cli('deliver', '--actor', 'B', '--task', successor), 0)
        code, _, err = self.review(successor, 'accepted', 'third, within the extension granted before the takeover')
        self.assertEqual(code, 0, err)

    def test_R1_an_owner_extension_survives_a_supersede(self):
        self.extend('T', by=2)
        data = {'id': 'N', 'title': 'Replacement', 'owner': 'A', 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope), 'proof': None,
                'expected': None, 'depends': [], 'full_gate': False}
        self.store.append('task', 'C', data)
        self.assertEqual(self.cli('supersede', '--actor', 'C', '--task', 'T', '--by', 'N', '--reason', 'Synthetic'), 0)
        state = self.store.state()
        global_code = state['workspace']['limits']['code']
        self.assertEqual(review_limit(state, 'N', 'code'), global_code + 2)
        self.assertEqual(review_limit(state, 'N', 'plan'), state['workspace']['limits']['plan'])

    # R2
    def test_R2_an_inherited_plan_is_not_activity_of_the_new_owner(self):
        self.ack()
        plan = self.base / 'plan.md'
        plan.write_text('Synthetic plan by the first owner\n')
        self.store.append('plan', 'A', {'task': 'T', 'artifact': self.store.artifact(plan)})
        successor = self.take_over()
        self.store.append('ack', 'B', {'message': 'assign-' + successor})
        state = plain(self.store.view())
        self.store.release_views()
        self.assertTrue(state['tasks'][successor].get('plan'), 'the plan is inherited')
        accepted = dt.datetime.fromisoformat(state['messages']['assign-' + successor]['ack']['utc'])
        late = accepted + dt.timedelta(minutes=45)
        self.assertIn(successor, {row['task'] for row in ripresa.idle_tasks(state, late)},
                      'a stalled successor is reported even though it inherited a plan')
        own = self.base / 'own-plan.md'
        own.write_text('A new plan by the successor\n')
        self.store.append('plan', 'B', {'task': successor, 'artifact': self.store.artifact(own)})
        state = plain(self.store.view())
        self.store.release_views()
        self.assertNotIn(successor, {row['task'] for row in ripresa.idle_tasks(state, late)})

    # R3
    def test_R3_another_task_of_the_identity_waits_in_the_fifo_queue(self):
        identity = self.store.state()['agents']['C']['identity']
        self.store.append('agent', 'C-alias', {'id': 'C-alias', 'role': 'builder', 'identity': identity,
                          'client': 'claude', 'repo': str(self.repo), 'skills': [], 'session': 'synthetic-alias'})
        self.task('U')
        self.assertEqual(self.cli('resource', 'acquire', '--actor', 'C', '--name', 'cpu-heavy', '--task', 'T',
                                  '--reason', 'Suite for T'), 0)
        env = os.environ | {'HOME': str(self.home)}

        def waiter(actor, seconds, *extra):
            return subprocess.Popen([sys.executable, '-B', str(SCRIPTS / 'squadra.py'), '--root', str(self.root),
                                     'resource', 'acquire', '--actor', actor, '--name', 'cpu-heavy',
                                     '--reason', 'Queued suite', '--wait', str(seconds), *extra],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

        started = time.monotonic()
        alias = waiter('C-alias', 8, '--task', 'U')
        time.sleep(1.5)
        other = waiter('R', 4)
        time.sleep(1.5)
        self.store.append('resource.release', 'C', {'name': 'cpu-heavy', 'reason': 'T finished', 'task': 'T'})
        alias_err = alias.communicate(timeout=30)[1].decode()
        alias_seconds = time.monotonic() - started
        other_rc = other.wait(timeout=30)
        self.assertEqual(alias.returncode, 0, alias_err)
        self.assertLess(alias_seconds, 8 + 2, 'one --wait deadline in total')
        self.assertEqual(other_rc, 1, 'the later waiter of another identity stays behind in the queue')
        holder = plain(self.store.state()['resources']['cpu-heavy'])
        self.assertEqual((holder['owner'], holder['task']), ('C-alias', 'U'))

    # R5
    def test_R5_the_storage_modules_have_no_import_cycle(self):
        names = {'frozen_candidate', 'recovery', 'storage_policy', 'storage_reuse', 'storage_compact', 'recovery_package'}
        edges = {}
        for name in names:
            path = SCRIPTS / (name + '.py')
            if not path.exists():
                continue
            found = set()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and node.module in names:
                    found.add(node.module)
                elif isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names if alias.name in names)
            edges[name] = found - {name}

        def cycle(start):
            stack, seen = [(start, [start])], set()
            while stack:
                node, path = stack.pop()
                for nxt in edges.get(node, ()):
                    if nxt == start:
                        return path + [nxt]
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append((nxt, path + [nxt]))
            return None

        self.assertEqual([c for c in map(cycle, sorted(edges)) if c], [])

    # R6
    def test_R6_the_idle_notice_follows_the_registry_language(self):
        self.ack()
        state = plain(self.store.view())
        self.store.release_views()
        late = dt.datetime.fromisoformat(state['messages']['assign-T']['ack']['utc']) + dt.timedelta(minutes=45)
        italian = ripresa.idle_tasks(state, late)[0]['next']
        state['workspace']['lang'] = 'en'
        english = ripresa.idle_tasks(state, late)[0]['next']
        self.assertIn("Verificare l'inattività", italian)
        self.assertIn('Check the inactivity', english)
        notices = ripresa.readiness(state, late)['notices']
        self.assertTrue(any(n.startswith('T: no recorded activity') and 'Check the inactivity' in n for n in notices))
        state['workspace']['lang'] = 'it'
        notices = ripresa.readiness(state, late)['notices']
        self.assertTrue(any(n.startswith('T: nessuna attività registrata') for n in notices), notices)

    def english_registry(self):
        root = self.base / 'english'
        store = Store(root, create=True)
        self.addCleanup(store.close)
        store.append('init', 'system', {'name': 'English team', 'project': str(self.repo), 'version': '2.0.0',
                     'limits': {'plan': 3, 'code': 2, 'build': 3}, 'lang': 'en'})
        for name, role, identity in (('C', 'coordinator', 'coord'), ('A', 'builder', 'author')):
            store.append('agent', name, {'id': name, 'role': role, 'identity': identity, 'client': 'codex',
                         'repo': str(self.repo), 'skills': [], 'session': ''})
        return root, store

    def agreed_suite(self, store):
        data = {'id': 'P', 'title': 'Full suite proof', 'owner': 'A', 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': store.artifact(self.scope),
                'proof': [sys.executable, '-B', 'tools/parallel_tests.py'], 'expected': 0, 'depends': [],
                'full_gate': False}
        store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-P', 'to': 'A', 'task': 'P',
                     'body': 'Scoped work'}, None)])
        store.append('ack', 'A', {'message': 'assign-P'})

    def test_R6_the_bind_refusal_follows_the_registry_language(self):
        root, store = self.english_registry()
        self.agreed_suite(store)
        self.agreed_suite(self.store)
        denied = PermissionError(errno.EPERM, 'synthetic denied socket')
        with patch.object(runtime.socket, 'socket', side_effect=denied):
            code_en, _, err_en = self.run_cli('prove', '--actor', 'A', '--task', 'P', root=root)
            code_it, _, err_it = self.run_cli('prove', '--actor', 'A', '--task', 'P')
        self.assertEqual((code_en, code_it), (1, 1))
        self.assertIn('Local bind on 127.0.0.1 is not available', err_en)
        self.assertNotIn('limite del sandbox', err_en)
        self.assertIn('limite del sandbox', err_it)


if __name__ == '__main__':
    unittest.main()

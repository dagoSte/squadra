"""A fix closes only with its own discriminating test or a declared exception."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.request

import test_squadra as support
from fix_challenge import execute, register, prepare, evidence, binding, disposition
from registry import Error, canonical, digest, fix_status, snapshot
from squadra import deliver
from source_manifests import transform_packet

TEST = '''import pathlib, unittest
class Bug(unittest.TestCase):
    def test_value(self):
        self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')
'''


class FixChallenge(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    cli = support.Workflow.cli
    proof = support.Workflow.proof

    def configure(self, body=TEST, ids=None, name='T'):
        tests = self.repo / 'tests'; tests.mkdir(exist_ok=True)
        (tests / 'test_bug.py').write_text(body)
        config = self.base / 'oracle.json'
        config.write_text(json.dumps({'ids': ids or ['test_bug.Bug.test_value'], 'files': ['tests/test_bug.py']}))
        task = self.store.state()['tasks'][name]
        if task['status'] == 'waiting-ack':
            self.ack(name)
        register(self.store, 'C', name, config, 'Regression test for the reported value')
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        return config

    def challenge(self, label='challenge', name='T', **kw):
        return execute(self.store, 'A', name, self.base / label, **kw)

    def review(self, name='T'):
        self.assertEqual(self.proof(task=name)['status'], 'passed')
        handoff = deliver(self.store, 'A', name)['handoff']
        if handoff:
            self.store.append('ack', 'R', {'message': handoff})
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', name, '--verdict', 'accepted',
                                  '--report', str(self.report)), 0)

    def test_original_assertion_and_dirty_remedy_pass_with_exact_test_overlay(self):
        config = self.configure(); before = snapshot(self.repo)
        result = self.challenge()
        self.assertEqual(result['status'], 'matched')
        self.assertEqual([r['native_rc'] for r in result['runs']], [1, 0])
        self.assertEqual(result['runs'][0]['assertions'], ['test_bug.Bug.test_value'])
        self.assertEqual(snapshot(self.repo), before)
        self.assertEqual((self.base / 'challenge/original/code.py').read_text(), 'VALUE = 1\n')
        self.assertEqual((self.base / 'challenge/original/tests/test_bug.py').read_text(), TEST)
        self.review()
        t = self.store.state()['tasks']['T']
        self.assertEqual(fix_status(t), 'matched')
        self.assertEqual(self.cli('check', '--actor', 'C', '--task', 'T'), 0)
        self.store.append('close', 'C', {'task': 'T'})
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'done')
        with self.assertRaisesRegex(Error, 'not_open'):
            register(self.store, 'C', 'T', config, 'Cannot reopen a closed fix')

    def test_original_is_registered_base_even_after_remedy_commit(self):
        self.configure(); base = self.store.state()['tasks']['T']['base']
        self.git('add', '.'); self.git('commit', '-qm', 'remedy')
        self.assertNotEqual(snapshot(self.repo)['head'], base)
        self.assertEqual(self.challenge()['status'], 'matched')
        self.assertEqual(self.store.state()['tasks']['T']['fix_result']['base'], base)

    def test_always_passing_test_never_matches(self):
        self.configure(TEST.replace("self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')", 'self.assertTrue(True)'))
        result = self.challenge()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([r['native_rc'] for r in result['runs']], [0, 0])

    def test_unregistered_shadow_method_cannot_replace_the_agreed_test(self):
        (self.repo / 'test_bug.py').write_text(TEST)
        self.git('add', '.'); self.git('commit', '-qm', 'preexisting module name collision')
        self.task('U')
        agreed = TEST.replace("self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')", 'self.assertTrue(True)')
        self.configure(agreed, name='U')
        result = self.challenge(name='U')
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(all(r['outcome'] == 'instrument-error' for r in result['runs']))

    def test_errors_skips_xfail_and_setup_assertions_never_match(self):
        bodies = {
            'error': TEST.replace("self.assertEqual(pathlib.Path('code.py').read_text(), 'VALUE = 2\\n')", 'raise RuntimeError("broken setup")'),
            'skip': TEST.replace('    def test_value', '    @unittest.skip("unavailable")\n    def test_value'),
            'xfail': TEST.replace('    def test_value', '    @unittest.expectedFailure\n    def test_value'),
            'setup': TEST.replace('    def test_value', '    def setUp(self):\n        self.fail("setup")\n    def test_value'),
            'import': 'raise ImportError("missing")\n' + TEST,
            'import-assertion': 'assert False, "setup assertion"\n' + TEST,
        }
        for label, body in bodies.items():
            with self.subTest(label=label):
                self.configure(body)
                result = self.challenge(label)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any(r['outcome'] == 'instrument-error' for r in result['runs']))

    def test_unknown_id_and_whole_class_are_not_a_test(self):
        for label, ids in [('unknown', ['test_bug.Bug.test_missing']), ('class', ['test_bug.Bug'])]:
            if label == 'class':
                with self.assertRaisesRegex(Error, 'test_ids'):
                    self.configure(ids=ids)
            else:
                self.configure(ids=ids)
                result = self.challenge(label)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['runs'][0]['outcome'], 'instrument-error')

    def test_every_selected_id_must_fail_on_original_and_execute_on_remedy(self):
        body = TEST + '    def test_safe(self):\n        self.assertTrue(True)\n'
        self.configure(body, ['test_bug.Bug.test_value', 'test_bug.Bug.test_safe'])
        result = self.challenge()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['runs'][0]['tests'], 2)

    def test_subtest_assertion_is_a_real_test_failure(self):
        body = TEST.replace("        self.assertEqual", "        with self.subTest(case='value'):\n            self.assertEqual")
        self.configure(body)
        self.assertEqual(self.challenge()['status'], 'matched')

    def test_timeout_never_matches_and_retains_native_logs(self):
        self.configure(TEST.replace('        self.assertEqual', '        import time; time.sleep(3)\n        self.assertEqual'))
        result = self.challenge(timeout=1)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(all(r['status'] == 'interrupted' for r in result['runs']))
        self.assertTrue(all(r['stdout']['sha256'] and r['stderr']['sha256'] for r in result['runs']))

    def test_test_file_drift_refuses_before_any_child(self):
        self.configure(); (self.repo / 'tests/test_bug.py').write_text(TEST + '\n')
        with self.assertRaisesRegex(Error, 'oracle_drift'):
            self.challenge()
        self.assertFalse((self.base / 'challenge').exists())

    def test_current_candidate_drift_invalidates_match_and_close(self):
        self.configure(); self.challenge(); self.review()
        (self.repo / 'new.txt').write_text('new bytes')
        with self.assertRaisesRegex(Error, 'drift'):
            self.store.append('close', 'C', {'task': 'T'})
        self.assertNotEqual(self.cli('check', '--actor', 'C', '--task', 'T'), 0)

    def test_close_cannot_fabricate_matched_and_unmarked_challenge_cannot_qualify(self):
        self.configure()
        self.assertEqual(self.proof()['status'], 'passed')
        deliver(self.store, 'A', 'T')
        t = self.store.state()['tasks']['T']
        with self.assertRaisesRegex(Error, 'required'):
            self.store.append('review', 'R', {'task': 'T', 'stage': 'code', 'verdict': 'accepted',
                              'report': self.store.artifact(self.report), 'binding': t['candidate']['sha256'],
                              'fix_binding': binding(t), 'fix_disposition': None, 'covers': []})
        with self.assertRaises(Error):
            self.store.append('close', 'C', {'task': 'T', 'status': 'matched'})
        self.task('U'); self.ack('U')
        with self.assertRaisesRegex(Error, 'agreement'):
            self.store.append('challenge', 'A', {'task': 'U', 'status': 'matched'})

    def test_cross_task_wrong_base_argv_and_tampered_result_are_refused(self):
        config = self.configure(); self.challenge()
        t = self.store.state()['tasks']['T']; record = t['fix_result']
        self.task('U'); self.ack('U'); register(self.store, 'C', 'U', config, 'Another fix')
        for field, value in [('task', 'U'), ('base', '0' * 40), ('binding', '0' * 64)]:
            with self.subTest(field=field), self.assertRaisesRegex(Error, 'binding'):
                self.store.append('challenge', 'A', {**record, field: value})
            rebound = {k: v for k, v in record.items() if k != 'result'}
            rebound[field] = value
            path = self.base / (field + '-rebound.json'); path.write_text(canonical(rebound))
            with self.subTest(rehashed=field), self.assertRaisesRegex(Error, 'binding'):
                self.store.append('challenge', 'A', {**rebound, 'result': self.store.artifact(path)})
        forged = deepcopy(record); forged['runs'][0]['native_rc'] = 0
        with self.assertRaisesRegex(Error, 'result_binding'):
            self.store.append('challenge', 'A', forged)
        bad = deepcopy(t['fix']); bad['agreement']['argv'] = [sys.executable, '-c', 'exit(0)']
        with self.assertRaisesRegex(Error, 'runner_unsupported'):
            self.store.append('fix', 'C', {'task': 'T', 'reason': 'wrong argv', 'fix': bad, 'previous': binding(t)})
        sha = record['result']['sha256']; (self.root / 'artifacts' / sha).write_text('{}')
        with self.assertRaisesRegex(Error, 'artifact_changed'):
            evidence(t, snapshot(self.repo), self.root)

    def test_exception_needs_coordinator_reason_and_current_independent_read(self):
        self.configure()
        for actor, reason in [('A', 'No automated reach'), ('C', ' ')]:
            with self.subTest(actor=actor), self.assertRaises(Error):
                execute(self.store, actor, 'T', None, exception=reason)
        result = execute(self.store, 'C', 'T', None, exception='Hardware-only observation; manual evidence in scope')
        self.assertTrue(result['exception'])
        self.review()
        t = self.store.state()['tasks']['T']; self.assertEqual(fix_status(t), 'exception')
        execute(self.store, 'C', 'T', None, exception='A different justification')
        with self.assertRaisesRegex(Error, 'current_read'):
            self.store.append('close', 'C', {'task': 'T'})

    def test_new_receipt_after_delivery_requires_redelivery_before_read(self):
        self.configure(); self.assertEqual(self.proof()['status'], 'passed')
        deliver(self.store, 'A', 'T'); self.challenge()
        self.assertNotEqual(self.cli('review', '--actor', 'R', '--task', 'T', '--verdict', 'accepted', '--report', str(self.report)), 0)
        self.review()
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)

    def test_register_is_one_way_coordinator_only_and_invalidates_old_proof(self):
        self.ack(); self.assertEqual(self.proof()['status'], 'passed'); deliver(self.store, 'A', 'T')
        with self.assertRaisesRegex(Error, 'Coordinator'):
            register(self.store, 'A', 'T', None, 'mark fix')
        register(self.store, 'C', 'T', None, 'mark fix')
        with self.assertRaisesRegex(Error, 'passing proof'):
            deliver(self.store, 'A', 'T')
        t = self.store.state()['tasks']['T']; self.assertEqual(t['fix']['v'], 1)
        with self.assertRaisesRegex(Error, 'policy'):
            self.store.append('fix', 'C', {'task': 'T', 'fix': None, 'reason': 'unmark', 'previous': binding(t)})

    def test_new_replacement_must_explicitly_remain_a_defect_fix(self):
        self.configure()
        original = next(e['data'] for e in self.store.events() if e['kind'] == 'task' and e['data']['id'] == 'T')
        before = len(self.store.events())
        with self.assertRaisesRegex(Error, 'requires.*--defect-fix'):
            self.store.append('task', 'C', {**original, 'id': 'U', 'supersedes': 'T'})
        self.assertEqual(len(self.store.events()), before)
        argv = ['task', '--actor', 'C', '--id', 'U', '--title', 'Replacement', '--owner', 'A',
                '--repo', str(self.repo), '--scope-file', str(self.scope), '--supersedes', 'T']
        self.assertEqual(self.cli(*argv), 1)
        refused = self.store.events()[before:]
        self.assertEqual([e['kind'] for e in refused], ['diagnostic'])
        self.assertIn('--defect-fix', refused[0]['data']['error'])
        self.assertNotIn('U', self.store.state()['tasks'])
        self.assertNotIn('superseded_by', self.store.state()['tasks']['T'])
        self.assertEqual(self.cli(*argv, '--defect-fix'), 0)
        tasks = self.store.state()['tasks']
        self.assertEqual(tasks['T']['superseded_by'], 'U')
        self.assertEqual(tasks['U']['fix'], {'v': 1, 'agreement': None})
        self.assertIsNotNone(tasks['T']['fix']['agreement'])
        self.assertEqual(fix_status(tasks['U']), 'pending')
        with self.assertRaisesRegex(Error, 'required'):
            evidence(tasks['U'], snapshot(self.repo), self.root)

    def test_existing_replacement_must_explicitly_remain_a_defect_fix(self):
        self.configure(); self.task('U')
        data = {'task': 'T', 'by': 'U', 'reason': 'Work moved to U'}
        before = len(self.store.events())
        with self.assertRaisesRegex(Error, 'requires.*--defect-fix'):
            self.store.append('supersede', 'C', data)
        self.assertEqual(len(self.store.events()), before)
        argv = ['supersede', '--actor', 'C', '--task', 'T', '--by', 'U', '--reason', data['reason']]
        self.assertEqual(self.cli(*argv), 1)
        refused = self.store.events()[before:]
        self.assertEqual([e['kind'] for e in refused], ['diagnostic'])
        self.assertIn('--defect-fix', refused[0]['data']['error'])
        self.assertNotIn('superseded_by', self.store.state()['tasks']['T'])
        self.ack('U'); register(self.store, 'C', 'U', None, 'Replacement remains a defect fix')
        self.assertEqual(self.cli(*argv), 0)
        tasks = self.store.state()['tasks']
        self.assertEqual(tasks['T']['superseded_by'], 'U')
        self.assertEqual(tasks['U']['fix'], {'v': 1, 'agreement': None})
        self.assertEqual(fix_status(tasks['U']), 'pending')

    def test_old_receipt_cannot_close_a_newly_proved_delivered_and_read_candidate(self):
        self.assertEqual(self.cli('task', '--actor', 'C', '--id', 'F', '--title', 'Fix', '--owner', 'A',
                                  '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope),
                                  '--defect-fix'), 0)
        body = "import pathlib, unittest\nclass Bug(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(pathlib.Path('code.py').read_text().splitlines()[0], 'VALUE = 2')\n"
        self.configure(body, name='F'); self.assertEqual(self.challenge(name='F')['status'], 'matched')
        old = self.store.state()['tasks']['F']['fix_result']['candidate']
        (self.repo / 'code.py').write_text('VALUE = 2\n# later change\n')
        self.assertNotEqual(snapshot(self.repo), old)
        self.assertEqual(self.proof(task='F')['status'], 'passed')
        handoff = deliver(self.store, 'A', 'F')['handoff']; self.store.append('ack', 'R', {'message': handoff})
        rc = self.cli('review', '--actor', 'R', '--task', 'F', '--verdict', 'accepted', '--report', str(self.report))
        try:
            self.store.append('close', 'C', {'task': 'F'})
            closed = True
        except Error:
            closed = False
        self.assertNotEqual(rc, 0, 'A new read cannot approve the receipt for the older candidate')
        self.assertFalse(closed, 'A newly proved candidate still needs its own challenge')
        self.assertEqual(self.challenge(label='fresh', name='F')['status'], 'matched')
        self.review('F'); self.store.append('close', 'C', {'task': 'F'})
        self.assertEqual(self.store.state()['tasks']['F']['status'], 'done')

    def test_custom_oracle_keeps_assertion_distinct_from_import_assertion(self):
        self.ack(); oracle = self.base / 'oracle.py'; config = self.base / 'custom.json'
        config.write_text(json.dumps({'runner': 'python-check-v1', 'oracle': str(oracle)}))
        for label, body, expected in [('check', "def check(repo):\n assert (repo/'code.py').read_text() == 'VALUE = 2\\n'\n", 'matched'),
                                      ('setup', 'assert False\ndef check(repo): pass\n', 'failed')]:
            oracle.write_text(body); register(self.store, 'C', 'T', config, 'Custom oracle')
            (self.repo / 'code.py').write_text('VALUE = 2\n')
            self.assertEqual(self.challenge(label)['status'], expected)

    def test_oracle_cannot_mutate_its_input_and_still_match(self):
        self.ack(); oracle = self.base / 'oracle.py'; config = self.base / 'custom.json'
        oracle.write_text("def check(repo):\n old=(repo/'code.py').read_text()\n (repo/'changed').write_text('changed')\n assert old == 'VALUE = 2\\n'\n")
        config.write_text(json.dumps({'runner': 'python-check-v1', 'oracle': str(oracle)}))
        register(self.store, 'C', 'T', config, 'Mutation must invalidate execution')
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        result = self.challenge()
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(all(r['status'] == 'source-changed' for r in result['runs']))
        self.assertFalse((self.repo / 'changed').exists())

    def test_task_challenge_cli_records_failure_and_refuses_unbound_overrides(self):
        config = self.configure()
        self.assertEqual(self.cli('challenge', '--actor', 'C', '--task', 'T', '--register',
                                  '--contract-file', str(config), '--reason', 'Same explicit oracle'), 0)
        self.assertEqual(self.cli('challenge', '--actor', 'A', '--task', 'T', '--output', str(self.base / 'cli-run')), 0)
        self.assertNotEqual(self.cli('challenge', '--actor', 'A', '--task', 'T', '--original', str(self.repo),
                                     '--output', str(self.base / 'never-created')), 0)
        self.assertFalse((self.base / 'never-created').exists())
        (self.repo / 'code.py').write_text('VALUE = 1\n')
        self.assertNotEqual(self.cli('challenge', '--actor', 'A', '--task', 'T', '--output', str(self.base / 'red-remedy')), 0)
        self.assertEqual(self.store.state()['tasks']['T']['fix_result']['runs'][1]['native_rc'], 1)

    def test_declared_argv_cannot_be_replaced_in_a_rehashed_receipt(self):
        self.configure(); self.challenge()
        record = deepcopy(self.store.state()['tasks']['T']['fix_result']); record.pop('result')
        record['runs'][0]['argv'] = [sys.executable, '-c', 'exit(1)']
        path = self.base / 'forged.json'; path.write_text(canonical(record))
        with self.assertRaisesRegex(Error, 'argv_changed'):
            self.store.append('challenge', 'A', {**record, 'result': self.store.artifact(path)})

    def test_new_task_cli_flag_and_configured_packet(self):
        self.assertEqual(self.cli('task', '--actor', 'C', '--id', 'F', '--title', 'Fix', '--owner', 'A',
                                  '--reviewer', 'R', '--repo', str(self.repo), '--scope-file', str(self.scope), '--defect-fix'), 0)
        self.configure(name='F'); self.challenge(name='F'); self.review('F')
        t = self.store.state()['tasks']['F']
        packet = t['delivery']['packet_data']
        stored = (self.root / 'artifacts' / t['delivery']['packet']['sha256']).read_bytes()
        self.assertEqual(packet, json.loads(stored))
        self.assertEqual(t['delivery']['handoff'], 'review-' + digest(canonical(packet).encode()))
        compared = transform_packet(packet, self.store.resolve_snapshot)
        self.assertEqual(compared['fix_result'], t['fix_result'])
        self.assertEqual(packet, json.loads(stored))
        self.assertEqual(t['reviews'][-1]['fix_disposition'], disposition(t))
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'F'), 0)

    def test_light_fix_missing_challenge_and_promotion_keep_obligation(self):
        from test_light_route import LightRoute
        from light_route import contract
        data = {'id': 'L', 'title': 'Text fix', 'owner': 'A', 'reviewer': 'R', 'repo': str(self.repo),
                'base': snapshot(self.repo)['head'], 'scope': self.store.artifact(self.scope),
                'proof': [sys.executable, '-c', "print('proof')"], 'requirements': [{'id': 'R1', 'text': 'text'}],
                'depends': [], 'full_gate': False, 'expected': None, 'route': 'light',
                'light_policy': {'version': 'light-v1', 'lines': 80, 'files': 4, 'profile': 'P2'}, 'fix': prepare(self.store, {})}
        data['contract'] = contract(data)
        self.store.batch([('task', 'C', data, None), ('message', 'C', {'id': 'assign-L', 'task': 'L', 'to': 'A', 'body': 'fix'}, None)])
        self.ack('L'); (self.repo / 'README.md').write_text('correct text\n')
        self.assertEqual(LightRoute.proof(self, 'L')['status'], 'passed')
        handoff = deliver(self.store, 'A', 'L')['handoff']; self.store.append('ack', 'R', {'message': handoff})
        self.assertNotEqual(self.cli('review', '--actor', 'R', '--task', 'L', '--verdict', 'accepted',
                                     '--report', str(self.report), '--covers', 'R1'), 0)
        LightRoute.promote(self)
        t = self.store.state()['tasks']['L']
        self.assertEqual(t['fix'], data['fix']); self.assertEqual(t['base'], data['base'])
        self.assertEqual(t['rounds']['code'], 0)
        with self.assertRaisesRegex(Error, 'required'):
            evidence(t, snapshot(self.repo), self.root)

    def test_mirror_and_old_readers_load_fix_events_without_write_helper(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / 'scripts/registry.py').read_bytes(), (root / 'plugins/playwright/bridge/registry.py').read_bytes())
        self.configure(); self.challenge(); self.review()
        for label, source in [('old', root / 'tests/fixtures/squadra-3.6.0'), ('new', root / 'plugins/playwright/bridge')]:
            isolated = self.base / label; isolated.mkdir(); shutil.copy(source / 'registry.py', isolated)
            state_call = "s.state(resolve=False)" if label == 'new' else "s.state()"
            probe = "import registry,sys;s=registry.Store(sys.argv[1],read_only=True);state=" + state_call + ";s.close();assert 'fix_challenge' not in sys.modules;assert state['tasks']['T']['base'];print('ok')"
            run = subprocess.run([sys.executable, '-B', '-c', probe, str(self.root)], cwd=isolated, capture_output=True, text=True)
            if label == 'old':
                self.assertNotEqual(run.returncode, 0)
                self.assertIn('integrity', run.stderr.lower())
            else:
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_dashboard_translates_bound_match_exception_and_pending(self):
        from dashboard_test_support import make_server
        self.task('L'); self.configure(name='L'); self.challenge(name='L'); self.review('L')
        server, token = make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for compact in (False, True):
                request = urllib.request.Request('http://127.0.0.1:' + str(server.server_port) + '/api/state',
                    headers={'X-Squadra-Token': token, **({'X-Squadra-View': 'compact'} if compact else {})})
                with urllib.request.urlopen(request, timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state['tasks']['L']['fix_status'], 'matched')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        fixture = self.base / 'page-state.json'; fixture.write_text(json.dumps(state))
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node required; no skipped page proof')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, str(root / 'tests/fix_challenge_contract.js'), str(root / 'assets/dashboard.html'), str(fixture)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

"""Opt-in model selection preserves task gates and explains every applied choice."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_light_route
import test_squadra as support
import model_policy
from registry import Error, plain
from runtime import cli_command, validate_reply
from squadra import main


class ModelPolicy(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack
    register = test_light_route.LightRoute.register
    change = test_light_route.LightRoute.change

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--root', str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def select(self, task='T', mode='build'):
        return model_policy.choose(self.store.resolved(), 'A', task, mode, root=self.root)

    def test_first_question_does_not_enable_or_write_and_explicit_toggle_is_durable(self):
        count = len(self.store.events())
        code, out, _ = self.cli('models', '--actor', 'A', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['mode'], 'ask')
        self.assertIn('prima di lavorare', json.loads(out)['question'])
        self.assertEqual(len(self.store.events()), count)
        code, out, err = self.cli('models', '--actor', 'A', '--auto', 'on', '--json')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['mode'], 'on')
        self.assertEqual(self.select()['profile'], 'quality')
        self.assertEqual(self.cli('models', '--actor', 'A', '--auto', 'off')[0], 0)
        self.assertIsNone(self.select())

    def test_preference_belongs_to_identity_session_and_client_not_role_alias(self):
        state = self.store.resolved()
        alias = dict(state['agents']['A']) | {'id': 'builder-alias', 'role': 'executor'}
        self.store.append('agent', 'builder-alias', alias)
        model_policy.configure(self.store, 'A', 'on')
        self.assertEqual(model_policy.preference(self.store.resolved(), 'builder-alias')['mode'], 'on')
        model_policy.configure(self.store, 'builder-alias', 'off')
        self.assertEqual(model_policy.preference(self.store.resolved(), 'A')['mode'], 'off')
        newer = dict(self.store.resolved()['agents']['A']) | {'session': 'new-session'}
        self.store.append('agent', 'A', newer)
        self.assertEqual(model_policy.preference(self.store.resolved(), 'A')['mode'], 'ask')

    def test_new_cli_window_asks_and_refuses_model_work_until_answered(self):
        code, out, err = self.cli('agent', '--id', 'fresh', '--identity', 'fresh-session',
                                  '--role', 'builder', '--client', 'codex', '--session', 'fresh-session',
                                  '--repo', str(self.repo))
        self.assertEqual(code, 0, err)
        self.assertIn('prima di lavorare', err)
        with self.assertRaisesRegex(Error, 'Choose automatic models before work'):
            model_policy.choose(self.store.resolved(), 'fresh', 'T', 'build')
        self.assertEqual(self.cli('models', '--actor', 'fresh', '--auto', 'off')[0], 0)
        self.assertIsNone(model_policy.choose(self.store.resolved(), 'fresh', 'T', 'build'))
        self.assertLessEqual(len(out.encode()), 200, 'registration receipt stays bounded')

    def test_light_contract_can_save_and_larger_or_protected_changes_use_quality(self):
        model_policy.configure(self.store, 'A', 'on')
        self.register()
        self.assertEqual(self.select('L')['reason'], 'bounded_light_task')
        self.change(1)
        small = self.select('L')
        self.assertEqual(small['profile'], 'economy')
        self.assertEqual(small['evidence']['lines'], 1)
        self.assertEqual(self.select('L', 'read')['profile'], 'quality')
        self.change(81)
        self.assertEqual(self.select('L')['profile'], 'quality')

    def test_complex_unknown_and_plan_never_use_economy_or_change_task_contract(self):
        model_policy.configure(self.store, 'A', 'on')
        before = deepcopy(dict(self.store.resolved()['tasks']['T']))
        chosen = self.select()
        self.assertEqual((chosen['profile'], chosen['effort']), ('quality', 'max'))
        self.assertEqual(chosen['reason'], 'complex_task')
        self.assertEqual(dict(self.store.resolved()['tasks']['T']), before)
        self.register()
        chosen = model_policy.choose(self.store.resolved(), 'A', 'L', 'build', 'plan', root=self.root)
        self.assertEqual(chosen['profile'], 'quality')

    def test_disabled_and_legacy_profiles_preserve_native_commands(self):
        original = cli_command('codex', 'read', self.base)
        self.assertIsNone(self.select())
        model_policy.configure(self.store, 'A', 'off')
        self.assertIsNone(self.select())
        self.assertEqual(cli_command('codex', 'read', self.base), original)
        changed = cli_command('codex', 'read', self.base, 'configured-model', effort='max')
        for flag in ('--ignore-user-config', '--output-schema', '-s'):
            self.assertIn(flag, changed)
        self.assertIn('model_reasoning_effort="max"', changed)
        claude = cli_command('claude', 'read', self.base, 'opus', effort='max')
        for flag in ('--safe-mode', '--strict-mcp-config', '--setting-sources', '--permission-mode'):
            self.assertIn(flag, claude)
        self.assertEqual(claude[claude.index('--effort') + 1], 'max')

    def test_invalid_profiles_do_not_record_a_preference(self):
        count = len(self.store.events())
        for row in ({'model': '-option', 'effort': 'low'}, {'model': 'model', 'effort': 'PRIVATE_TEXT'},
                    {'model': 'model', 'effort': 'ultra'}, {'model': 'model', 'effort': True}):
            file = self.base / 'profiles.json'
            file.write_text(json.dumps({'schema': 1, 'clients': {'codex': {'economy': row, 'quality': row}}}))
            with self.assertRaises(Error):
                model_policy.configure(self.store, 'A', 'on', file)
        self.assertEqual(len(self.store.events()), count)

    def test_turning_off_and_back_on_preserves_custom_profiles(self):
        chosen = {'economy': {'model': 'gpt-6.1-sol', 'effort': 'low'},
                  'quality': {'model': 'gpt-6-astra', 'effort': 'xhigh'}}
        file = self.base / 'custom-profiles.json'
        file.write_text(json.dumps({'schema': 1, 'clients': {'codex': chosen}}))
        model_policy.configure(self.store, 'A', 'on', file)
        model_policy.configure(self.store, 'A', 'off')
        model_policy.configure(self.store, 'A', 'on')
        self.assertEqual(model_policy.preference(self.store.resolved(), 'A')['profiles'], chosen)

    def test_concurrent_alias_writers_respect_the_last_committed_off_after_reopening(self):
        self.cli('agent', '--id', 'Z', '--identity', 'author', '--role', 'executor',
                 '--client', 'codex', '--repo', str(self.repo))
        worker = self.base / 'writer.py'
        worker.write_text('''import sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from registry import Store
from squadra import main
root, actor, mode, sync = sys.argv[2:]
sync = Path(sync)
def wait(name, seconds=10):
    end = time.monotonic() + seconds
    while not (sync / name).exists() and time.monotonic() < end:
        time.sleep(.01)
    return (sync / name).exists()
append = Store.append
def scheduled(store, kind, author, data, event_id=None):
    if kind == 'agent' and data.get('model_policy', {}).get('mode') == mode:
        (sync / (mode + '.prepared')).touch()
        if mode == 'on':
            wait('off.prepared', 1)
        else:
            assert wait('on.done')
    return append(store, kind, author, data, event_id)
with patch.object(Store, 'append', scheduled):
    rc = main(['--root', root, 'models', '--actor', actor, '--auto', mode, '--json'])
assert rc == 0
(sync / (mode + '.done')).touch()
''')
        sync = self.base / 'sync'
        sync.mkdir()
        scripts = str(Path(model_policy.__file__).parent)
        first = subprocess.Popen([sys.executable, '-B', str(worker), scripts, str(self.root), 'A', 'on', str(sync)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        end = time.monotonic() + 10
        while not (sync / 'on.prepared').exists() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue((sync / 'on.prepared').exists())
        second = subprocess.Popen([sys.executable, '-B', str(worker), scripts, str(self.root), 'Z', 'off', str(sync)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: second.kill() if second.poll() is None else None)
        for proc in (first, second):
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, (out, err))
        from registry import Store
        reopened = Store(self.root)
        self.addCleanup(reopened.close)
        state = reopened.resolved()
        history = [e for e in reopened.events() if e['kind'] == 'agent' and e['data'].get('model_policy')]
        self.assertEqual(history[-1]['data']['model_policy']['mode'], 'off')
        for actor in ('A', 'Z'):
            self.assertEqual(model_policy.preference(state, actor)['mode'], 'off')
        self.ack()
        with contextlib.redirect_stderr(io.StringIO()):
            result = model_policy.execute(reopened, 'A', 'T', mode='build', prompt='Scoped work',
                                          executable=str(self.fake_client()), timeout=10)
        self.assertEqual(result['status'], 'passed')
        run = reopened.resolved()['runs'][result['id']]
        self.assertIsNone(run.get('model_selection'))
        self.assertNotIn('-m', run['argv'])
        self.assertFalse(any('model_reasoning_effort=' in arg for arg in run['argv']))
        self.assertNotEqual(reopened.audit()['cache'], 'mismatch')

    def test_replace_requires_a_fresh_answer_and_keeps_existing_target_preferences(self):
        from replacement import execute
        req = {'map': {'A': 'B'}, 'sessions': {'B': 'new-session'}, 'client': 'codex',
               'carry': 'useful', 'force': False, 'reason': 'Synthetic replacement'}
        with patch('replacement.deliver_outbox', return_value=None):
            result = execute(self.store, 'C', req)
        successor = result['successors']['T']
        with self.assertRaisesRegex(Error, 'Choose automatic models before work'):
            model_policy.choose(self.store.resolved(), 'B', successor, 'build')
        model_policy.configure(self.store, 'B', 'off')
        self.assertIsNone(model_policy.choose(self.store.resolved(), 'B', successor, 'build'))
        with patch('replacement.deliver_outbox', return_value=None):
            execute(self.store, 'C', req | {'map': {'R': 'B'}})
        self.assertEqual(model_policy.preference(self.store.resolved(), 'B')['mode'], 'off')

    def test_handover_requires_a_fresh_answer_including_its_reader_alias(self):
        import handover
        from types import SimpleNamespace
        outside = self.base / 'outside.md'
        outside.write_text('\n'.join(h + '\nnessuno\n' for h in handover.HEADINGS))
        with patch('checklist.run', return_value={'ready': True, 'items': []}), \
                patch.dict(sys.modules, {'handover_conditions': SimpleNamespace(evaluate=lambda *args: [])}), \
                patch('replacement.deliver_outbox', return_value=None), \
                patch('squadra.codex_queue', return_value={'delivered': True, 'native_rc': 0}):
            ident = handover.prepare(self.store, 'C', 'C', 'New', outside_file=outside,
                                     owner_words='Apri una nuova finestra', channel='typed-here')['id']
            handover.accept(self.store, 'New', ident, 'new-session', 'codex')
        self.assertEqual(self.cli('agent', '--id', 'New-read', '--identity', 'new-session', '--session', 'new-session',
                                  '--role', 'reviewer', '--client', 'codex', '--repo', str(self.repo))[0], 0)
        for actor in ('New', 'New-read'):
            with self.assertRaisesRegex(Error, 'Choose automatic models before work'):
                model_policy.choose(self.store.resolved(), actor, 'T', 'read')
        model_policy.configure(self.store, 'New', 'off')
        for actor in ('New', 'New-read'):
            self.assertIsNone(model_policy.choose(self.store.resolved(), actor, 'T', 'read'))

    def test_history_does_not_follow_a_reused_actor_into_a_new_session(self):
        state = plain(self.store.resolved())
        old_window = model_policy.binding(state['agents']['A'])
        state['runs']['old'] = {'id': 'old', 'agent': 'A', 'utc': '2026-11-11T10:00:00Z',
                               'model_selection': {'window': old_window, 'model': 'gpt-6-astra', 'effort': 'max',
                                                   'reason': 'complex_task', 'profile': 'quality'}}
        state['agents']['A']['session'] = 'replacement-session'
        self.assertNotIn('last_selection', model_policy.report(state, 'A'))

    def test_turning_off_during_a_rejected_run_prevents_an_automatic_retry(self):
        import runtime
        native = runtime.execute
        model_policy.configure(self.store, 'A', 'on')
        self.register()
        def stop_after_first(*args, **kwargs):
            result = native(*args, **kwargs)
            model_policy.configure(self.store, 'A', 'off')
            return result
        with patch.object(runtime, 'execute', side_effect=stop_after_first), contextlib.redirect_stderr(io.StringIO()):
            result = model_policy.execute(self.store, 'A', 'L', mode='build', prompt='Scoped work',
                                          executable=str(self.fake_client()), timeout=10)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.store.resolved()['runs']), 1)

    def test_reregistration_keeps_choice_and_an_alias_cannot_bypass_the_first_question(self):
        model_policy.configure(self.store, 'A', 'on')
        self.assertEqual(self.cli('agent', '--id', 'A', '--identity', 'author', '--role', 'builder',
                                  '--client', 'codex', '--repo', str(self.repo))[0], 0)
        self.assertEqual(model_policy.preference(self.store.resolved(), 'A')['mode'], 'on')
        self.cli('agent', '--id', 'fresh', '--identity', 'fresh', '--role', 'executor',
                 '--client', 'codex', '--repo', str(self.repo))
        self.cli('agent', '--id', 'fresh-alias', '--identity', 'fresh', '--role', 'reviewer',
                 '--client', 'codex', '--repo', str(self.repo))
        with self.assertRaisesRegex(Error, 'Choose automatic models before work'):
            model_policy.choose(self.store.resolved(), 'fresh-alias', 'T', 'read')

    def test_structured_model_quota_is_distinct_from_generic_text_and_account_limits(self):
        folder = self.base / 'failure'
        folder.mkdir()
        row = {'status': 'failed', 'before': {'sha256': 'same'}, 'after': {'sha256': 'same'},
               'folder': str(folder), 'requested_model': 'selected'}
        for error, expected in (({'code': 'rate_limit_exceeded', 'scope': 'model', 'model': 'selected'}, 'model_quota'),
                                ({'code': 'quota_exceeded', 'scope': 'account'}, None),
                                ({'message': 'model_not_available'}, None)):
            (folder / 'stdout.log').write_text(json.dumps({'type': 'error', 'error': error}))
            self.assertEqual(model_policy.failure_reason(row), expected)
        row['after'] = {'sha256': 'changed'}
        (folder / 'stdout.log').write_text(json.dumps({'type': 'error', 'error': {'code': 'model_not_available'}}))
        self.assertIsNone(model_policy.failure_reason(row))

    def test_claude_observations_include_actual_model_usage_and_preserve_custom_models(self):
        folder = self.base / 'claude-result'
        folder.mkdir()
        result = {'type': 'result', 'subtype': 'success', 'session_id': '87654321-1234-4567-8123-123456789abc',
                  'result': 'Completed', 'usage': {}, 'model': 'custom-provider-model',
                  'modelUsage': {'claude-opus-5': {'inputTokens': 100}}}
        (folder / 'stdout.log').write_text(json.dumps(result))
        self.assertEqual(validate_reply('claude', folder, structured=False)['observed_models'],
                         ['claude-opus-5', 'custom-provider-model'])

    def fake_client(self, *, operative=False, shared_quota=False):
        path = self.base / 'fake-codex'
        error = {'code': 'quota_exceeded', 'scope': 'account'} if shared_quota else {'code': 'model_not_available'}
        body = """#!/usr/bin/env python3
import json, pathlib, sys, uuid
if '--version' in sys.argv:
    print('fake-codex 1'); raise SystemExit(0)
name = sys.argv[sys.argv.index('-m') + 1] if '-m' in sys.argv else ''
print(json.dumps({'type': 'thread.started', 'thread_id': str(uuid.uuid4())}))
if name == 'gpt-6-luna':
    if OPERATIVE: print(json.dumps({'type': 'item.started', 'item': {'type': 'command_execution'}}))
    print(json.dumps({'type': 'error', 'error': ERROR})); raise SystemExit(1)
pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('Completed scoped work')
print(json.dumps({'type': 'turn.completed', 'usage': {}}))
""".replace('OPERATIVE', repr(operative)).replace('ERROR', repr(error))
        path.write_text(body)
        path.chmod(0o700)
        return path

    def test_prework_rejection_switches_once_records_reason_and_keeps_effort_and_budget(self):
        model_policy.configure(self.store, 'A', 'on')
        self.register()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = model_policy.execute(self.store, 'A', 'L', mode='build', prompt='Scoped work',
                                          executable=str(self.fake_client()), timeout=10)
        self.assertEqual(result['status'], 'passed', result)
        runs = list(self.store.resolved()['runs'].values())
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]['requested_model'], 'gpt-6-luna')
        self.assertEqual(runs[1]['requested_model'], 'gpt-6-astra')
        self.assertEqual(runs[1]['requested_effort'], 'max')
        self.assertEqual(runs[1]['model_selection']['reason'], 'model_unavailable')
        self.assertEqual(runs[1]['model_selection']['previous_run'], runs[0]['id'])
        self.assertIn('motivo:', err.getvalue())
        self.assertEqual(self.store.resolved()['tasks']['L']['rounds']['code'], 0)

    def test_fallback_without_an_explicit_timeout_keeps_the_native_default_budget(self):
        model_policy.configure(self.store, 'A', 'on')
        self.register()
        with contextlib.redirect_stderr(io.StringIO()):
            result = model_policy.execute(self.store, 'A', 'L', mode='build', prompt='Scoped work',
                                          executable=str(self.fake_client()))
        self.assertEqual(result['status'], 'passed')
        runs = list(self.store.resolved()['runs'].values())
        self.assertEqual(runs[0]['timeout'], 120)
        self.assertLessEqual(runs[1]['timeout'], runs[0]['timeout'] - runs[0]['seconds'])

    def test_no_retry_after_operational_output_or_for_shared_account_quota(self):
        model_policy.configure(self.store, 'A', 'on')
        self.register()
        for index, options in enumerate(({'operative': True}, {'shared_quota': True})):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                result = model_policy.execute(self.store, 'A', 'L', mode='build', prompt='Scoped work',
                                              executable=str(self.fake_client(**options)), timeout=10)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(len(self.store.resolved()['runs']), index + 1)


if __name__ == '__main__':
    unittest.main()

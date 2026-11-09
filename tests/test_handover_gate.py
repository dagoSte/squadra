"""The handover gate observes recorded work across aliases; conditions never act on it."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'scripts')]
import checklist
import usage
from handover_conditions import evaluate
from registry import attention, board, pending_handovers, plain, table
from squadra import main
import test_squadra as support

NOW = '2026-11-09T12:00:00+00:00'
BEFORE = '2026-11-09T11:59:59+00:00'


def handover(conditions=()):
    return {'id': 'handover-test', 'from': 'A', 'to': 'next-A', 'by': 'A', 'utc': BEFORE,
            'status': 'pending', 'conditions': list(conditions), 'outside': {'unknown': 'Synthetic fixture'}}


class Conditions(unittest.TestCase):
    def test_each_task_condition_requires_done_not_merely_superseded(self):
        state = {'tasks': {'closed': {'status': 'done'}, 'open': {'status': 'working'},
                           'old': {'status': 'ready', 'superseded_by': 'closed'}}}
        h = handover([{'n': n, 'kind': 'task', 'value': name}
                      for n, name in enumerate(['closed', 'open', 'old', 'missing'], 1)])
        original = deepcopy((state, h))
        self.assertEqual([r['met'] for r in evaluate(state, h, NOW)], [True, False, False, False])
        self.assertEqual((state, h), original)

    def test_after_crosses_the_exact_instant_and_compares_timezones(self):
        h = handover([{'n': 3, 'kind': 'after', 'value': '2026-11-09T13:00:00+01:00'}])
        self.assertFalse(evaluate({}, h, BEFORE)[0]['met'])
        self.assertTrue(evaluate({}, h, NOW)[0]['met'])
        for value in ['2026-11-09T12:00:00', 'not a date']:
            h['conditions'][0]['value'] = value
            self.assertFalse(evaluate({}, h, NOW)[0]['met'])

    def test_confirm_needs_a_recorded_confirmation_and_preserves_order_and_values(self):
        conditions = [{'n': 8, 'kind': 'confirm', 'value': 'The owner confirms'},
                      {'n': 2, 'kind': 'after', 'value': NOW}]
        h = handover(conditions)
        self.assertEqual([r['met'] for r in evaluate({}, h, NOW)], [False, True])
        conditions[0]['confirmed'] = 'not a recorded confirmation'
        self.assertFalse(evaluate({}, h, NOW)[0]['met'])
        conditions[0]['confirmed'] = {'by': 'C', 'utc': NOW, 'reason': 'Owner confirmed'}
        rows = evaluate({}, h, NOW)
        self.assertTrue(all(r['met'] is True for r in rows))
        self.assertEqual([(r['n'], r['kind'], r['value']) for r in rows],
                         [(c['n'], c['kind'], c['value']) for c in conditions])
        self.assertTrue(all(set(r) == {'n', 'kind', 'value', 'met', 'detail'} for r in rows))
        self.assertEqual(evaluate({}, handover(), NOW), [])
        conditions[0]['kind'] = 'unsupported'
        self.assertFalse(evaluate({}, h, NOW)[0]['met'])


class Gate(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def state(self):
        result = plain(self.store.view())
        self.store.release_views()
        return result

    def check(self, state=None, actor='A'):
        state = state if state is not None else self.state()
        original, events = deepcopy(state), self.store.events()
        result = checklist.handover_checks(state, actor, NOW)
        self.assertEqual(state, original)
        self.assertEqual(self.store.events(), events)
        self.assertEqual(result['ready'], not any(i['status'] == 'fail' for i in result['items']))
        return {i['id']: i for i in result['items']}

    def test_clean_gate_names_every_item_and_the_cli_accepts_the_new_moment(self):
        items = self.check()
        self.assertEqual(set(items), {'actor', 'runs', 'resources', 'reading', 'release', 'worktree',
                                      'conditions', 'pending_acks', 'service_wait'})
        self.assertFalse(any(i['status'] == 'fail' for i in items.values()))
        events = self.store.events()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['--root', str(self.root), 'checklist', '--moment', 'handover', '--actor', 'A'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())['ready'])
        self.assertEqual(self.store.events(), events)
        self.assertEqual(self.check(actor='missing')['actor']['status'], 'fail')

    def test_running_and_uncertain_cleanup_of_aliases_block_until_terminal_receipt(self):
        state = self.state()
        state['agents']['A']['session'] = 'session-one'
        state['agents']['A-alias']['session'] = 'session-two'
        state['agents']['linked'] = {**state['agents']['R'], 'id': 'linked', 'session': 'session-two'}
        for status in ['running', 'cleanup-unknown']:
            with self.subTest(status=status):
                state['runs']['run'] = {'id': 'run', 'agent': 'linked', 'task': 'T', 'status': status}
                self.assertEqual(self.check(state)['runs']['status'], 'fail')
        state['runs']['run']['status'] = 'passed'
        self.assertEqual(self.check(state)['runs']['status'], 'ok')
        state['runs']['run'].update(agent='C', status='running')
        self.assertEqual(self.check(state)['runs']['status'], 'ok', 'Empty sessions never connect unrelated seats')

    def test_resources_of_an_alias_block_and_other_windows_do_not(self):
        state = self.state()
        state['resources']['cpu-heavy'] = {'owner': 'A-alias'}
        self.assertEqual(self.check(state)['resources']['status'], 'fail')
        state['resources']['cpu-heavy']['owner'] = 'C'
        self.assertEqual(self.check(state)['resources']['status'], 'ok')

    def test_acknowledged_read_requires_a_verdict_bound_to_the_current_handoff(self):
        state = self.state()
        read = deepcopy(state['tasks']['T'])
        read.update(id='READ', owner='C', delivery={'handoff': 'current'}, reviewer='A')
        state['tasks']['READ'] = read
        message = {'id': 'current', 'from': 'C', 'to': 'A', 'utc': NOW, 'body': 'Read', 'ack': None}
        state['messages']['current'] = message
        self.assertEqual(self.check(state)['reading']['status'], 'ok')
        message['ack'] = {'agent': 'A-alias', 'utc': NOW}
        self.assertEqual(self.check(state)['reading']['status'], 'fail')
        read['reviews'] = [{'stage': 'code', 'handoff': 'old', 'reviewer': 'A', 'verdict': 'accepted'}]
        self.assertEqual(self.check(state)['reading']['status'], 'fail')
        read['reviews'][0].update(handoff='current', verdict='changes-required')
        self.assertEqual(self.check(state)['reading']['status'], 'ok')

    def test_pool_read_and_release_gate_of_aliases_are_included(self):
        state = self.state()
        state['tasks']['T']['delivery'] = {'handoffs': ['pool-A', 'pool-R']}
        state['messages']['pool-A'] = {'id': 'pool-A', 'from': 'C', 'to': 'A-alias', 'utc': NOW,
                                      'ack': {'agent': 'A-alias', 'utc': NOW}}
        self.assertEqual(self.check(state)['reading']['status'], 'fail')
        state['tasks']['T']['delivery'] = {}
        state['tasks']['T'].update(owner='A-alias', release_gate=True)
        self.assertEqual(self.check(state)['release']['status'], 'fail')
        state['tasks']['T']['status'] = 'done'
        self.assertEqual(self.check(state)['release']['status'], 'ok')

    def test_dirty_and_untracked_wip_block_until_committed_without_modifying_it(self):
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        self.assertEqual(self.check()['worktree']['status'], 'fail')
        self.assertEqual((self.repo / 'code.py').read_text(), 'VALUE = 2\n')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qam', 'committed WIP')
        self.assertEqual(self.check()['worktree']['status'], 'ok')
        (self.repo / 'untracked.txt').write_text('WIP\n')
        state = self.state()
        state['saves'].append({'declared': 'A save does not bind current WIP'})
        self.assertEqual(self.check(state)['worktree']['status'], 'fail')

    def test_unobservable_worktree_is_explicit_and_does_not_hide_a_dirty_checkout(self):
        state = self.state()
        state['tasks']['T']['repo'] = str(self.base / 'missing')
        self.assertEqual(self.check(state)['worktree']['status'], 'unverifiable')
        with patch.object(checklist, '_git', side_effect=subprocess.TimeoutExpired('git', 10)):
            self.assertEqual(self.check()['worktree']['status'], 'unverifiable')
        state['tasks']['DIRTY'] = {**state['tasks']['T'], 'id': 'DIRTY', 'repo': str(self.repo)}
        (self.repo / 'code.py').write_text('VALUE = 2\n')
        self.assertEqual(self.check(state)['worktree']['status'], 'fail')

    def test_unmet_pending_conditions_block_until_verified(self):
        state = self.state()
        condition = {'n': 1, 'kind': 'task', 'value': 'T'}
        state['handovers'] = {'handover-test': handover([condition])}
        self.assertEqual(self.check(state)['conditions']['status'], 'fail')
        state['tasks']['T']['status'] = 'done'
        self.assertEqual(self.check(state)['conditions']['status'], 'ok')
        condition.update(kind='confirm', value='Owner consent')
        self.assertEqual(self.check(state)['conditions']['status'], 'fail')
        state['handovers']['handover-test']['status'] = 'cancelled'
        self.assertEqual(self.check(state)['conditions']['status'], 'ok')

    def test_outgoing_acknowledgements_and_service_wait_are_only_notices(self):
        state = self.state()
        state['messages']['mail'] = {'id': 'mail', 'from': 'A-alias', 'to': 'R', 'ack': None}
        state['agents']['A-alias']['service_wait'] = {'cause': '429', 'until': '2026-11-09T13:00:00+00:00'}
        items = self.check(state)
        self.assertEqual(items['pending_acks']['status'], 'notice')
        self.assertIn('mail', items['pending_acks']['detail'])
        self.assertEqual(items['service_wait']['status'], 'notice')
        self.assertIn('429', items['service_wait']['detail'])

    def test_views_use_only_cached_tiredness_and_the_explicit_gate_can_measure(self):
        tired = {'tired': True, 'reasons': ['Sessione lunga']}
        with patch.object(usage, 'cached_tiredness', return_value={'A': tired}, create=True), \
                patch.object(usage, 'window_tiredness', side_effect=AssertionError('Views must not read transcripts'), create=True):
            self.assertEqual(self.check()['tiredness']['status'], 'notice')
        with patch.object(usage, 'window_tiredness', return_value=tired, create=True) as measure:
            result = checklist.run(self.store, 'handover', actor='A')
            self.assertTrue(result['ready'])
            self.assertTrue(any(i['id'] == 'tiredness' for i in result['items']))
            self.assertEqual(measure.call_args.args[2], 'A')

    def test_pending_handover_moves_from_waiting_to_owner_without_changing_state(self):
        state = self.state()
        state['handovers'] = {'handover-test': handover([{'n': 1, 'kind': 'after', 'value': NOW}])}
        original = deepcopy(state)
        waiting = attention(state, BEFORE)
        self.assertTrue(any(w['kind'] == 'handover' for w in waiting['waiting']))
        self.assertFalse(any(w['kind'] == 'handover' for w in waiting['for_owner']))
        ready = attention(state, NOW)
        self.assertTrue(any(w['kind'] == 'handover' for w in ready['for_owner']))
        self.assertFalse(any(w['kind'] == 'handover' for w in ready['waiting']))
        self.assertIn('riprendi la sede next-A', board(state, read_at=NOW))
        self.assertIn('Chi aspetta:', table(state, read_at=BEFORE))
        self.assertIn('riprendi la sede next-A', table(state, read_at=NOW).split('Serve a te:')[1])
        self.assertEqual(state, original)
        state['handovers']['handover-test']['status'] = 'accepted'
        self.assertEqual(pending_handovers(state, NOW), [])

    def test_ordinary_summary_contains_the_pending_handover(self):
        state = self.state()
        state['handovers'] = {'handover-test': handover()}
        events = self.store.events()
        with patch.object(self.store, 'view', return_value=state):
            self.store.publish()
        self.assertIn('riprendi la sede next-A', (self.root / 'stato.md').read_text())
        self.assertEqual(self.store.events(), events)


if __name__ == '__main__':
    unittest.main()

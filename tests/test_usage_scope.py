"""Project joins exclude earlier session spending without rewriting history."""
from copy import deepcopy
import unittest

import test_usage as support

usage, NOW = support.usage, support.NOW
JOIN = NOW - 600


class UsageScope(unittest.TestCase):
    setUp = support.UsageCollector.setUp
    seat = support.UsageCollector.seat
    write = support.UsageCollector.write
    cx = support.UsageCollector.cx
    collect = support.UsageCollector.collect
    one = support.UsageCollector.one
    files = support.UsageCollector.files

    def join(self, actor='builder', at=JOIN):
        self.events.append(support.event('agent', at, id=actor,
                                         **self.state['agents'][actor]))

    def project(self, at=NOW - 1200):
        self.events.append(support.event('init', at, actor='system'))

    def test_old_days_excluded_from_all_project_aggregates_and_sources_unchanged(self):
        self.seat(); self.project(); self.join()
        self.write('claude', [support.claude(NOW - 2 * 86400, key='old', inp=9000),
                              support.claude(JOIN - 1, key='before', inp=1000),
                              support.claude(JOIN, key='at-join'),
                              support.claude(NOW - 1, key='new')])
        before, events = self.files(), deepcopy(self.events)
        data, window = self.one()
        self.assertEqual(window['totals']['total'], 30)
        for group in ('models', 'days', 'tasks'):
            self.assertEqual(sum(r['totals']['total'] for r in data[group]), 30)
        self.assertEqual([d['day'] for d in data['days']], [support.stamp(NOW)[:10]])
        self.assertEqual(sum(r['total'] or 0 for r in window['hourly']), 30)
        self.assertEqual(window['rolling']['d7']['total'], 30)
        self.assertEqual(data.get('scope', {}).get('kind'), 'project')
        self.assertEqual(self.files(), before)
        self.assertEqual(self.events, events)

    def test_codex_straddling_delta_excluded_then_post_join_delta_counts(self):
        self.seat(client='codex', session=support.CODEX); self.project(); self.join()
        self.cx([support.codex(JOIN - 100, 1000, 200),
                 support.codex(JOIN + 1, 1100, 220),
                 support.codex(JOIN + 10, 1120, 222)])
        data, window = self.one()
        self.assertEqual(window['totals']['total'], 22)
        self.assertEqual(window['status'], 'partial')
        self.assertIn('project_boundary_overlap', window['reasons'])
        self.assertEqual(sum(t['totals']['total'] for t in data['tasks']), 22)

    def test_pre_join_codex_counter_remains_a_baseline_not_current_spend(self):
        self.seat(client='codex', session=support.CODEX); self.project(); self.join()
        self.cx([support.codex(JOIN - 10, 100000, 0),
                 support.codex(JOIN, 100000, 0),
                 support.codex(NOW - 1, 100010, 2)])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 12)
        self.assertNotIn('project_boundary_overlap', window['reasons'])

    def test_account_quota_history_retained_even_before_project_join(self):
        self.seat(client='codex', session=support.CODEX); self.project(); self.join()
        quota = {'primary': {'used_percent': 70, 'window_minutes': 300,
                             'resets_at': int(NOW + 3600)}}
        self.cx([support.codex(JOIN - 10, 0, 0, quota=quota),
                 support.codex(JOIN - 1, 100, 10)])
        data, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertEqual(window['quota'][0]['used_percent'], 70)
        self.assertEqual(data['quota_history'][0]['used_percent'], 70)
        self.assertEqual(data['days'], [])

    def test_late_role_alias_and_reregistration_do_not_reset_same_session_join(self):
        self.seat(); self.project(); self.join()
        self.seat('reader'); self.join('reader', NOW - 10); self.join(at=NOW - 5)
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='new')])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)
        self.assertEqual(window['actors'], ['builder', 'reader'])

    def test_actor_rebound_to_new_session_cannot_use_old_session_join(self):
        self.seat(); self.project(); self.join(at=JOIN - 100)
        self.seat(session=support.CHILD); self.join(at=JOIN + 100)
        self.write('claude', [support.claude(JOIN + 1, key='before', session=support.CHILD),
                              support.claude(JOIN + 101, key='after', session=support.CHILD)],
                   session=support.CHILD)
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)

    def test_prefix_upgraded_to_full_session_keeps_original_join(self):
        self.seat(session=support.CLAUDE[:8], identity='same-window'); self.project(); self.join()
        self.seat(identity='same-window'); self.join(at=NOW - 10)
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='first'),
                              support.claude(NOW - 1, key='last')])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 30)

    def test_original_alias_can_leave_while_identity_keeps_its_join(self):
        self.seat(); self.project(); self.join()
        self.seat('replacement'); self.join('replacement', NOW - 10)
        self.seat(session=support.CHILD, identity='other-identity'); self.join(at=NOW - 5)
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='first'),
                              support.claude(NOW - 1, key='last')])
        data = self.collect()
        window = next(w for w in data['windows'] if w['actors'] == ['replacement'])
        self.assertEqual(window['totals']['total'], 30)

    def test_ambiguous_historical_prefix_cannot_backdate_a_new_full_session(self):
        self.seat(session=support.CLAUDE[:8], identity='same-window'); self.project(); self.join()
        self.write('claude', [support.claude(JOIN + 1)], session=support.CLAUDE)
        self.seat(session=support.CHILD, identity='same-window'); self.join(at=NOW - 10)
        self.write('claude', [support.claude(JOIN + 1, key='early', session=support.CHILD),
                              support.claude(NOW - 1, key='later', session=support.CHILD)],
                   session=support.CHILD)
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)

    def test_stream_update_of_pre_join_claude_message_is_not_new_project_usage(self):
        self.seat(); self.project(); self.join()
        self.write('claude', [support.claude(JOIN - 1, out=1),
                              support.claude(JOIN + 1, out=9),
                              support.claude(JOIN + 2, key='new')])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)
        self.assertIn('project_boundary_overlap', window['reasons'])

    def test_unregistered_child_inherits_parent_project_join(self):
        self.seat(); self.project(); self.join()
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='new')],
                   path=self.roots['claude'] / 'project' / support.CLAUDE /
                        'subagents' / 'agent-abcdef.jsonl')
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)

    def test_missing_registration_never_falls_back_to_whole_session_in_project(self):
        self.seat(); self.project()
        self.write('claude', [support.claude(JOIN + 1)])
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('project_registration_unknown', window['reasons'])

    def test_missing_project_anchor_is_explicitly_session_history(self):
        self.seat(); self.join()
        self.write('claude', [support.claude(JOIN - 86400)])
        data, _ = self.one()
        self.assertEqual(data.get('scope', {}).get('kind'), 'session')
        self.assertEqual(data.get('scope', {}).get('reason'), 'project_boundary_unknown')
        self.assertIn('Session history; project boundary unavailable', usage.text_report(data))

    def test_registration_is_clamped_to_project_start(self):
        self.seat(); self.project(at=JOIN + 1); self.join()
        self.write('claude', [support.claude(JOIN, key='before'),
                              support.claude(JOIN + 1, key='after')])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)

    def test_warm_collector_recomputes_scope_and_preserves_full_numeric_cache(self):
        self.seat()
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='new')])
        _, before = self.one()
        self.assertEqual(before['totals']['total'], 30)
        self.project(); self.join()
        _, after = self.one()
        self.assertEqual(after['totals']['total'], 15)
        self.assertTrue(any(len(src.records) == 2 for c in usage._COLLECTORS.values()
                            for src in c.sources.values()))

    def test_session_prefix_and_http_compact_response_obey_project_scope(self):
        self.seat(session=support.CLAUDE[:8]); self.project(); self.join()
        self.write('claude', [support.claude(JOIN - 1, key='old'),
                              support.claude(JOIN + 1, key='new')])
        data = usage.response(self.events, self.state, roots=self.roots, now=NOW,
                              budget_seconds=2, compact=True)
        self.assertEqual(data['windows'][0]['totals']['total'], 15)
        self.assertEqual(data.get('scope', {}).get('kind'), 'project')
        self.assertNotIn('hourly', data['windows'][0])


if __name__ == '__main__':
    unittest.main()

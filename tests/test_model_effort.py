"""Window settings follow verified turns, independently of token accounting."""
from copy import deepcopy
import json
import unittest

import test_usage as support
import usage_cache

usage, NOW = support.usage, support.NOW


class ModelEffort(unittest.TestCase):
    setUp = support.UsageCollector.setUp
    seat = support.UsageCollector.seat
    write = support.UsageCollector.write
    cx = support.UsageCollector.cx
    collect = support.UsageCollector.collect
    one = support.UsageCollector.one

    def turn(self, at, name='gpt-6-luna', effort='low'):
        row = support.model(at, name)
        row['payload']['effort'] = effort
        return row

    def test_latest_turn_not_most_used_model_and_no_token_sample_required(self):
        self.seat(client='codex', session=support.CODEX)
        self.cx([self.turn(NOW - 30), self.turn(NOW - 10, 'gpt-6-astra', 'max')])
        data, window = self.one()
        config = window['configuration']
        self.assertEqual((config['model'], config['effort']), ('gpt-6-astra', 'max'))
        self.assertEqual(config['source'], 'codex_turn_context')
        self.assertEqual(config['observed_at'], support.stamp(NOW - 10))
        self.assertEqual(config['switch']['reason'], 'client_reason_unavailable')
        self.assertEqual(config['switch']['effort'], 'low')
        self.assertIsNone(window['totals'])
        self.assertIn('Client switch:', usage.text_report(data))
        self.assertIn('reason: not exposed by client', usage.text_report(data))

    def test_appended_effort_change_updates_conditional_response_without_registry_change(self):
        self.seat(client='codex', session=support.CODEX)
        path = self.cx([self.turn(NOW - 30)])
        first = usage.response(self.events, self.state, roots=self.roots, now=NOW, budget_seconds=2)
        with path.open('a') as f:
            f.write(json.dumps(self.turn(NOW - 5, effort='high')) + '\n')
        after = usage.response(self.events, self.state, roots=self.roots, now=NOW + 1,
                               revision=first['revision'], budget_seconds=2)
        self.assertNotIn('unchanged', after)
        self.assertEqual(after['windows'][0]['configuration']['effort'], 'high')
        self.assertEqual(first['windows'][0]['totals'], after['windows'][0]['totals'])

    def test_missing_invalid_and_future_effort_never_inherit_or_leak_text(self):
        self.seat(client='codex', session=support.CODEX)
        for effort in (None, support.CANARY, {'text': support.CANARY}, True):
            with self.subTest(effort=effort):
                self.cx([self.turn(NOW - 30), self.turn(NOW - 10, effort=effort),
                         self.turn(NOW + 60, 'gpt-6-astra', 'max')])
                _, window = self.one()
                config = window['configuration']
                self.assertEqual(config['model'], 'gpt-6-luna')
                self.assertIsNone(config['effort'])
                self.assertNotIn(support.CANARY, json.dumps(window))

    def test_ambiguous_or_wrong_session_does_not_identify_window(self):
        self.seat(client='codex', session=support.CODEX)
        self.write('codex', [{'type': 'session_meta', 'payload': {'id': support.CLAUDE}},
                             self.turn(NOW - 10)], session=support.CODEX)
        _, window = self.one()
        self.assertIsNone(window['configuration']['model'])
        self.assertIsNone(window['configuration']['source'])

    def test_child_does_not_replace_parent_model_and_claude_effort_stays_unknown(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 30, model='claude-sonnet-4-5')])
        self.write('claude', [support.claude(NOW - 5, key='child', model='claude-opus-4-5')],
                   path=self.roots['claude'] / 'project' / support.CLAUDE / 'subagents/agent-abcdef.jsonl')
        _, window = self.one()
        self.assertEqual(window['configuration']['model'], 'claude-sonnet-4-5')
        self.assertIsNone(window['configuration']['effort'])

    def claude_copies(self, first, second, *, same_source=False):
        self.write('claude', first + second if same_source else first)
        self.write('claude', [] if same_source else second,
                   path=self.roots['claude'] / 'copy' / (support.CLAUDE + '.jsonl'))

    def test_discordant_duplicate_models_never_supply_a_configuration_or_switch(self):
        self.seat()
        for same_source in (False, True):
            for delay in (0, 5):
                with self.subTest(same_source=same_source, delay=delay):
                    self.claude_copies(
                        [support.claude(NOW - 20, model='claude-sonnet-4-5')],
                        [support.claude(NOW - 20 + delay, model='claude-opus-4-5')],
                        same_source=same_source)
                    _, window = self.one()
                    self.assertEqual(window['configuration'], dict(
                        model=None, effort=None, source=None, observed_at=None, switch=None))

    def test_valid_claude_history_ignores_only_the_discordant_message(self):
        self.seat()
        for same_source in (False, True):
            for after_model in ('claude-haiku-4-5', 'claude-sonnet-4-5'):
                with self.subTest(same_source=same_source, after_model=after_model):
                    self.claude_copies([
                        support.claude(NOW - 60, key='before', model='claude-haiku-4-5'),
                        support.claude(NOW - 40, key='conflict', model='claude-sonnet-4-5'),
                        support.claude(NOW - 10, key='after', model=after_model)],
                        [support.claude(NOW - 30, key='conflict', model='claude-opus-4-5')],
                        same_source=same_source)
                    _, window = self.one()
                    config = window['configuration']
                    self.assertEqual(config['model'], after_model)
                    self.assertEqual(config['observed_at'], support.stamp(NOW - 10))
                    if after_model == 'claude-haiku-4-5':
                        self.assertIsNone(config['switch'])
                    else:
                        self.assertEqual(config['switch']['model'], 'claude-haiku-4-5')
                        self.assertEqual(config['switch']['observed_at'], support.stamp(NOW - 10))

    def test_token_only_conflicts_keep_agreed_claude_model_and_valid_history(self):
        self.seat()
        for same_source in (False, True):
            with self.subTest(same_source=same_source):
                self.claude_copies([
                    support.claude(NOW - 60, key='before', model='claude-haiku-4-5'),
                    support.claude(NOW - 20, inp=20, model='claude-sonnet-4-5')],
                    [support.claude(NOW - 10, inp=10, model='claude-sonnet-4-5')],
                    same_source=same_source)
                _, window = self.one()
                self.assertIn('duplicate_conflict', window['reasons'])
                config = window['configuration']
                self.assertEqual(config['model'], 'claude-sonnet-4-5')
                self.assertEqual(config['observed_at'], support.stamp(NOW - 10))
                self.assertEqual(config['switch']['model'], 'claude-haiku-4-5')

    def test_future_duplicate_model_cannot_invalidate_a_current_claude_observation(self):
        self.seat()
        self.claude_copies([
            support.claude(NOW - 60, key='before', model='claude-haiku-4-5'),
            support.claude(NOW - 20, model='claude-sonnet-4-5')],
            [support.claude(NOW + 3, model='claude-opus-4-5')])
        first = usage.response(self.events, self.state, roots=self.roots, now=NOW, budget_seconds=2)
        self.assertEqual(first['windows'][0]['configuration']['model'], 'claude-sonnet-4-5')
        after = usage.response(self.events, self.state, roots=self.roots, now=NOW + 3,
                               revision=first['revision'], budget_seconds=2)
        config = after['windows'][0]['configuration']
        self.assertEqual(config['model'], 'claude-haiku-4-5')
        self.assertIsNone(config['switch'])

    def test_child_duplicate_model_does_not_invalidate_parent_configuration(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 30, model='claude-sonnet-4-5')])
        self.write('claude', [support.claude(NOW - 5, model='claude-opus-4-5')],
                   path=self.roots['claude'] / 'project' / support.CLAUDE / 'subagents/agent-abcdef.jsonl')
        _, window = self.one()
        self.assertIn('duplicate_conflict', window['reasons'])
        self.assertEqual(window['configuration']['model'], 'claude-sonnet-4-5')
        self.assertIsNone(window['configuration']['switch'])

    def test_missing_claude_message_identity_is_rejected_before_configuration(self):
        self.seat()
        rows = [support.claude(NOW - 20, model='claude-sonnet-4-5'),
                support.claude(NOW - 10, model='claude-opus-4-5')]
        for row in rows:
            del row['message']['id']
            del row['requestId']
            with self.assertRaisesRegex(usage.Invalid, 'sample_identity_missing'):
                usage.project(json.dumps(row).encode(), 'claude')
        self.write('claude', rows)
        _, window = self.one()
        self.assertIn('sample_identity_missing', window['reasons'])
        self.assertEqual(window['configuration'], dict(
            model=None, effort=None, source=None, observed_at=None, switch=None))
        projected = usage.project(json.dumps(support.claude(NOW - 10)).encode(), 'claude')
        projected['key'] = None
        with self.assertRaises(ValueError):
            usage_cache.record(projected, 'claude')

    def test_missing_claude_model_is_unavailable_without_creating_a_model_conflict(self):
        self.seat()
        for same_source in (False, True):
            for missing_last in (False, True):
                for missing in (None, support.CANARY):
                    with self.subTest(same_source=same_source, missing_last=missing_last, missing=missing):
                        known = support.claude(NOW - (20 if missing_last else 10), model='claude-sonnet-4-5')
                        unknown = support.claude(NOW - (10 if missing_last else 20), model=missing)
                        if missing is None:
                            del unknown['message']['model']
                        first, last = (known, unknown) if missing_last else (unknown, known)
                        self.claude_copies([first], [last], same_source=same_source)
                        _, window = self.one()
                        config = window['configuration']
                        self.assertEqual(config['model'], None if missing_last else 'claude-sonnet-4-5')
                        self.assertEqual(config['source'], 'claude_assistant')
                        self.assertEqual(config['observed_at'], support.stamp(NOW - 10))
                        self.assertIsNone(config['effort'])
                        self.assertIsNone(config['switch'])
                        self.assertNotIn(support.CANARY, json.dumps(window))

    def test_effort_cache_projection_is_allowlisted(self):
        row = usage.project(json.dumps(self.turn(NOW)).encode(), 'codex')
        self.assertEqual(usage_cache.record(row, 'codex'), row)
        bad = deepcopy(row)
        bad['effort'] = support.CANARY
        with self.assertRaises(ValueError):
            usage_cache.record(bad, 'codex')

    def test_future_turn_becomes_visible_at_its_timestamp_without_source_change(self):
        self.seat(client='codex', session=support.CODEX)
        self.cx([self.turn(NOW - 30), self.turn(NOW + 3, effort='high')])
        first = usage.response(self.events, self.state, roots=self.roots, now=NOW, budget_seconds=2)
        after = usage.response(self.events, self.state, roots=self.roots, now=NOW + 3,
                               revision=first['revision'], budget_seconds=2)
        self.assertNotIn('unchanged', after)
        self.assertEqual(after['windows'][0]['configuration']['effort'], 'high')


if __name__ == '__main__':
    unittest.main()

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

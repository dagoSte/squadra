"""Conditional usage reads follow sources and actual time boundaries."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_usage as support

usage = support.usage
NOW = support.NOW


class UsageConditional(unittest.TestCase):
    setUp = support.UsageCollector.setUp
    seat = support.UsageCollector.seat
    write = support.UsageCollector.write
    cx = support.UsageCollector.cx

    def response(self, now=NOW, revision=None):
        return usage.response(self.events, self.state, roots=self.roots, now=now,
                              revision=revision, budget_seconds=2)

    def test_unchanged_skips_aggregation_parsing_and_persistence(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 7200), support.claude(NOW - 10, key='two')])
        first = self.response()
        collector = next(iter(usage._COLLECTORS.values()))
        with patch.object(collector, '_collect', side_effect=AssertionError('recalculated')):
            with patch.object(usage, 'project', side_effect=AssertionError('reparsed')):
                again = self.response(NOW + 1, first['revision'])
        self.assertEqual(again, dict(schema=1, unchanged=True, revision=first['revision']))
        self.assertLessEqual(len(json.dumps(again).encode()), 256)

    def test_quota_expiration_invalidates_without_new_registry_or_source_bytes(self):
        self.seat(client='codex', session=support.CODEX)
        quota = {'primary': {'used_percent': 95, 'window_minutes': 300,
                             'resets_at': int(NOW + 3)}}
        self.cx([support.codex(NOW - 7200, 0, 0), support.codex(NOW - 1, 20, 2, quota=quota)])
        first = self.response()
        self.assertEqual(first['windows'][0]['quota'][0]['status'], 'measured')
        expired = self.response(NOW + 3, first['revision'])
        self.assertNotIn('unchanged', expired)
        self.assertEqual(expired['windows'][0]['quota'][0]['status'], 'expired')
        self.assertEqual(first['windows'][0]['quota'][0]['observed_at'],
                         expired['windows'][0]['quota'][0]['observed_at'])
        self.assertNotIn('quota', [s['id'] for s in expired['suggestions']])

    def test_recent_window_expires_a_sample_without_clamping_its_lifetime_total(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 7200, key='early'),
                             support.claude(NOW - 899, key='recent', inp=100, out=0)])
        first = self.response()
        self.assertEqual(first['windows'][0]['recent']['total_tokens'], 100)
        after = self.response(NOW + 2, first['revision'])
        self.assertNotIn('unchanged', after)
        self.assertEqual(after['windows'][0]['recent']['total_tokens'], 0)
        self.assertEqual(after['windows'][0]['totals'], first['windows'][0]['totals'])

    def test_nested_new_source_is_found_before_inventory_ttl(self):
        self.seat()
        folder = self.roots['claude'] / 'project' / support.CLAUDE / 'subagents'
        folder.mkdir(parents=True)
        self.write('claude', [support.claude(NOW - 10)])
        first = self.response()
        self.write('claude', [support.claude(NOW - 5, key='child', inp=100, out=0)],
                   path=folder / 'agent-abcdef.jsonl')
        after = self.response(NOW + 1, first['revision'])
        self.assertNotIn('unchanged', after)
        self.assertEqual(after['windows'][0]['totals']['total'], 115)
        self.assertEqual(after['coverage']['sources'], 2)

    def test_missing_client_root_stays_cheap_then_appearing_root_invalidates(self):
        self.seat()
        self.roots['claude'].rmdir()
        first = self.response()
        self.assertEqual(first['status'], 'not_measured')
        again = self.response(NOW + 1, first['revision'])
        self.assertTrue(again.get('unchanged'))
        self.write('claude', [support.claude(NOW - 10)])
        after = self.response(NOW + 2, first['revision'])
        self.assertEqual(after['windows'][0]['totals']['total'], 15)

    def test_clock_reversal_never_reuses_a_future_observation(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 1)])
        first = self.response()
        prior = self.response(NOW - 60, first['revision'])
        self.assertNotIn('unchanged', prior)
        self.assertIsNone(prior['windows'][0]['totals'])
        self.assertEqual(prior['observed_at'], usage.utc(NOW - 60))

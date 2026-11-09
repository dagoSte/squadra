"""Notice cache writes follow changed data or expiry, while status stays fresh."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import registry
import usage
import usage_cache
import test_tiredness_fix as support
import test_usage as fixtures

NOW = support.NOW


class NoticeWrites(unittest.TestCase):
    setUp = support.TiredStatus.setUp
    status = support.TiredStatus.status

    def collect(self, at, seconds=2):
        return usage.collect(self.events, self.state, now=at, budget_seconds=seconds)

    def spy(self):
        writes = []
        replace = os.replace

        def measured(source, target, **kwargs):
            result = replace(source, target, **kwargs)
            if str(target).endswith('.notice'):
                writes.append(str(target))
            return result

        return writes, patch.object(usage_cache.os, 'replace', side_effect=measured)

    def test_unchanged_polling_avoids_writes_and_keeps_cross_process_status_fresh(self):
        before = self.store.events()
        writes, replace = self.spy()
        with replace, patch.object(usage_cache.os, 'fsync', wraps=os.fsync) as fsync:
            self.collect(NOW)
            self.assertEqual(len(writes), 1)
            initial_syncs = fsync.call_count
            for seconds in range(2, 22, 2):
                self.collect(NOW + seconds)
            self.assertEqual(len(writes), 1, 'unchanged observations must not rewrite the notice')
            self.assertEqual(fsync.call_count, initial_syncs, 'unchanged polls must not fsync')
            for seconds in range(22, 120, 2):
                self.collect(NOW + seconds)
                self.assertIn('builder', usage.status_tiredness(self.state, NOW + seconds))
            self.assertLessEqual(len(writes), 3, 'only freshness renewals are needed in two minutes')
        self.assertIn('conviene ripartire', self.status(at=NOW + 119))
        self.assertEqual(self.store.events(), before)

    def test_restart_reuses_fresh_notice_and_changes_are_published_immediately(self):
        writes, replace = self.spy()
        with replace:
            self.collect(NOW)
            usage._COLLECTORS.clear()
            self.collect(NOW + 2)
            self.assertEqual(len(writes), 1, 'a restarted collector can reuse the fresh disk notice')
            fixtures.UsageCollector.write(self, 'claude', [fixtures.claude(NOW - 9 * 3600, inp=200)])
            self.collect(NOW + 4)
            self.assertEqual(len(writes), 2)
            self.assertEqual(usage.status_tiredness(self.state, NOW + 4)['builder']['context_tokens'], 200)
            self.store.append('settings', 'builder', registry.wait_settings({'tired_hours': 10}))
            self.state = registry.plain(self.store.state())
            self.collect(NOW + 6)
            self.assertEqual(len(writes), 3)
            self.assertNotIn('conviene ripartire', self.status(at=NOW + 6))

    def test_fractional_timestamps_do_not_prevent_expiry_renewal(self):
        start = NOW + .0000007  # UTC strings round observations to microseconds.
        for seconds in range(0, 120, 2):
            self.collect(start + seconds)
            self.assertIn('builder', usage.status_tiredness(self.state, start + seconds + .001))

    def test_stale_source_is_not_renewed_until_measured_and_clock_reversal_recovers(self):
        writes, replace = self.spy()
        with replace:
            self.collect(NOW)
            for seconds in (50, 54, 58, 62):
                self.collect(NOW + seconds, seconds=0)
            self.assertEqual(len(writes), 1, 'rewriting cannot refresh an unobserved source')
            self.assertEqual(usage.status_tiredness(self.state, NOW + 62), {})
            self.collect(NOW + 64)
            self.assertEqual(len(writes), 2)
            self.assertIn('builder', usage.status_tiredness(self.state, NOW + 64))
            self.collect(NOW - 1)
            self.assertEqual(len(writes), 3)
            self.assertIn('builder', usage.status_tiredness(self.state, NOW - 1))

    def test_crossing_the_age_threshold_is_published_before_the_refresh_deadline(self):
        fixtures.UsageCollector.write(self, 'claude', [fixtures.claude(NOW - 8 * 3600 + 1)])
        writes, replace = self.spy()
        with replace:
            self.collect(NOW)
            self.assertFalse(usage.status_tiredness(self.state, NOW)['builder']['tired'])
            self.collect(NOW + 2)
            self.assertEqual(len(writes), 2)
            self.assertIn('conviene ripartire', self.status(at=NOW + 2))

    def test_failed_write_is_retried_on_the_next_unchanged_collect(self):
        writes, replace = self.spy()
        with replace:
            self.collect(NOW)
            fixtures.UsageCollector.write(self, 'claude', [fixtures.claude(NOW - 9 * 3600, inp=200)])
            original = usage_cache.os.replace

            def interrupted(source, target, **kwargs):
                if str(target).endswith('.notice'):
                    raise OSError('interrupted notice write')
                return original(source, target, **kwargs)

            with patch.object(usage_cache.os, 'replace', side_effect=interrupted):
                self.collect(NOW + 2)
            self.assertEqual(len(writes), 1)
            self.assertEqual(usage.status_tiredness(self.state, NOW + 2)['builder']['context_tokens'], 10)
            self.collect(NOW + 4)
            self.assertEqual(len(writes), 2)
            self.assertEqual(usage.status_tiredness(self.state, NOW + 4)['builder']['context_tokens'], 200)


if __name__ == '__main__':
    unittest.main()

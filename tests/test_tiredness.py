"""Session age/current context use numeric transcripts, never lifetime token totals."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import usage
import usage_cache
import registry
import ripresa
import test_usage as support

NOW = support.NOW


def codex(at, total=900000, current=60000, window=100000):
    row = support.codex(at, total, 100)
    info = row['payload']['info']
    info['last_token_usage'] = {'input_tokens': current}
    info['model_context_window'] = window
    return row


class Tiredness(unittest.TestCase):
    setUp = support.UsageCollector.setUp
    seat = support.UsageCollector.seat
    write = support.UsageCollector.write
    cx = support.UsageCollector.cx
    collect = support.UsageCollector.collect
    one = support.UsageCollector.one

    def fatigue(self):
        return self.one()[1]['tiredness']

    def test_codex_current_context_not_cumulative_or_delta_and_threshold(self):
        self.seat(client='codex', session=support.CODEX)
        self.cx([codex(NOW - 3600, current=59000)])
        value = self.fatigue()
        self.assertEqual(value['context_tokens'], 59000)
        self.assertEqual(value['context_fraction'], .59)
        self.assertFalse(value['tired'])
        self.cx([codex(NOW - 3600, current=59000), codex(NOW, 900100, 60000)])
        value = self.fatigue()
        self.assertEqual(value['context_fraction'], .6)
        self.assertEqual(value['reasons'], ['context'])
        self.assertEqual(value['age_hours'], 1)

    def test_session_age_uses_first_baseline_before_project_join(self):
        self.seat(client='codex', session=support.CODEX)
        self.events = [support.event('init', NOW - 600), support.event('agent', NOW - 500,
                       id='builder', **self.state['agents']['builder'])]
        self.cx([codex(NOW - 9 * 3600, current=1000), codex(NOW - 1, 900100, 1000)])
        value = self.fatigue()
        self.assertEqual(value['age_hours'], 9)
        self.assertEqual(value['reasons'], ['age'])
        self.assertEqual(value['session_started'], support.stamp(NOW - 9 * 3600))

    def test_claude_last_input_includes_caches_without_undeclared_percentage(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 3600, key='one', inp=40000),
                             support.claude(NOW, key='two', inp=1000, cached=49000, created=10000)])
        value = self.fatigue()
        self.assertEqual(value['context_tokens'], 60000)
        self.assertNotIn('context_window', value)
        self.assertNotIn('context_fraction', value)
        self.assertFalse(value['tired'])
        self.state['workspace'] = {'context_window': {'claude-sonnet-4-5': 100000}}
        value = self.fatigue()
        self.assertEqual(value['context_fraction'], .6)
        self.assertTrue(value['tired'])

    def test_age_and_fraction_settings_are_independent_at_boundaries(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 8 * 3600, inp=60000)])
        self.state['workspace'] = {'tired_hours': 9, 'tired_context_fraction': .7,
                                   'context_window': {'claude-sonnet-4-5': 100000}}
        self.assertFalse(self.fatigue()['tired'])
        self.state['workspace'].update(tired_hours=8, tired_context_fraction=.6)
        self.assertEqual(self.fatigue()['reasons'], ['age', 'context'])

    def test_missing_invalid_and_latest_missing_codex_context_are_unknown(self):
        self.seat(client='codex', session=support.CODEX)
        for current, window in [(True, 100000), (-1, 100000), (60000, 0), (60000, float('inf'))]:
            with self.subTest(current=current, window=window):
                self.cx([codex(NOW, current=current, window=window)])
                value = self.fatigue()
                self.assertNotIn('context_fraction', value)
                self.assertFalse(value['tired'])
        self.cx([codex(NOW - 60), support.codex(NOW, 900100, 100)])
        self.assertIsNone(self.fatigue()['context_tokens'])
        self.assertNotIn('context_fraction', self.fatigue())

    def test_identity_mismatch_and_unverified_restored_sources_are_not_measured(self):
        self.seat(client='codex', session=support.CODEX)
        self.write('codex', [{'type': 'session_meta', 'payload': {'id': support.CLAUDE}}, codex(NOW)])
        self.assertIsNone(self.fatigue()['source'])
        source = usage.Source(self.roots['codex'] / 'test.jsonl', self.roots['codex'], 'codex', support.CODEX)
        source.checked = True
        source.restored = True
        self.assertIsNone(usage.tiredness([source], NOW, {})['source'])

    def test_child_transcripts_do_not_supply_parent_context(self):
        parent = self.write('claude', [support.claude(NOW - 3600, inp=10)])
        child = self.write('claude', [support.claude(NOW, inp=90000)],
                           path=parent.parent / support.CLAUDE / 'subagents' / 'agent-child123.jsonl')
        sources = [usage.Source(path, self.roots['claude'], 'claude', support.CLAUDE) for path in (parent, child)]
        for source in sources:
            source.read(time.monotonic() + 2, NOW)
        self.assertEqual(usage.tiredness(sources, NOW, {})['context_tokens'], 10)
        sources[0].cursor = 100
        self.assertIsNone(usage.tiredness(sources, NOW, {})['age_hours'])

    def test_cache_projects_only_optional_numeric_context_and_keeps_old_records(self):
        row = usage.project(json.dumps(codex(NOW)).encode(), 'codex')
        self.assertEqual(usage_cache.record(row, 'codex'), row)
        self.assertNotIn(support.CANARY, json.dumps(row))
        old = usage.project(json.dumps(support.codex(NOW, 10, 5)).encode(), 'codex')
        self.assertEqual(usage_cache.record(old, 'codex'), old)
        for key, value in [('context_tokens', -1), ('context_window', 0), ('context_tokens', True)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                usage_cache.record({**row, key: value}, 'codex')
        with self.assertRaises(ValueError):
            usage_cache.record({**row, 'prompt': support.CANARY}, 'codex')

    def test_cache_is_fresh_bound_to_session_and_settings_and_reads_no_transcript(self):
        self.seat()
        self.write('claude', [support.claude(NOW - 9 * 3600)])
        value = self.fatigue()
        with patch.object(usage.Source, 'read', side_effect=AssertionError('no I/O')):
            self.assertEqual(usage.cached_tiredness(self.state, NOW + 59)['builder'], value)
            self.assertEqual(usage.window_tiredness([], self.state, 'builder', now=NOW), value)
            self.assertEqual(usage.cached_tiredness(self.state, NOW + 61), {})
            self.assertEqual(usage.cached_tiredness(self.state, NOW - 1), {})
            self.state['workspace'] = {'tired_hours': 10}
            self.assertEqual(usage.cached_tiredness(self.state, NOW), {})
            self.state.pop('workspace')
            self.state['agents']['builder']['session'] = support.CHILD
            self.assertEqual(usage.cached_tiredness(self.state, NOW), {})

    def test_resume_notices_are_advisory_and_preserve_state(self):
        state = registry.initial()
        state['agents'] = {'builder': {'id': 'builder', 'identity': 'one', 'client': 'claude',
                                      'session': support.CLAUDE, 'role': 'executor'}}
        fatigue = {'session_started': support.stamp(NOW - 9 * 3600), 'age_hours': 9,
                   'context_tokens': 200, 'reasons': ['age'], 'tired': True, 'source': {'client': 'claude'}}
        original = deepcopy(state)
        with patch.object(usage, 'collect', return_value={'windows': [{'actors': ['builder'], 'tiredness': fatigue}]}):
            result = ripresa.readiness(state, datetime.fromtimestamp(NOW, timezone.utc))
        self.assertTrue(result['ready'])
        self.assertEqual(len(result['tired_windows']), 1)
        self.assertTrue(any('conviene ripartire' in n and 'handover prepare' in n for n in result['notices']))
        self.assertFalse(any('%' in n for n in result['notices']))
        self.assertEqual(state, original)


class TiredSettings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-tired-settings-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = registry.Store(self.root / 'registry', create=True)
        self.addCleanup(self.store.close)
        self.store.append('init', 'system', {'name': 'test', 'project': str(self.root), 'lang': 'it'})
        self.store.append('agent', 'C', {'id': 'C', 'identity': 'coord', 'client': 'claude',
                                       'role': 'coordinator', 'repo': str(self.root), 'skills': []})

    def test_settings_persist_and_legacy_wait_settings_still_work(self):
        settings = {'alive': 100, 'tired_hours': 4.5, 'tired_context_fraction': .75,
                    'context_window': {'claude-opus-4-6': 200000}}
        self.store.append('settings', 'C', registry.wait_settings(settings))
        view = self.store.state()['workspace']
        self.assertEqual(view['wait_seconds']['alive'], 100)
        for key in ('tired_hours', 'tired_context_fraction', 'context_window'):
            self.assertEqual(view[key], settings[key])
        self.store.append('settings', 'C', registry.wait_settings({'alive': 200}))
        self.assertEqual(self.store.state()['workspace']['context_window'], settings['context_window'])

    def test_settings_reject_invalid_thresholds_and_windows_without_writes(self):
        for data in [{'tired_hours': 0}, {'tired_hours': True}, {'tired_hours': float('nan')},
                     {'tired_context_fraction': 1.1}, {'tired_context_fraction': float('inf')},
                     {'context_window': {'claude-opus-4-6': 0}},
                     {'context_window': {'gpt-5.4': 100000}}, {'context_window': {'claude-opus-4-6': True}}]:
            with self.subTest(data=data):
                before = len(self.store.events())
                with self.assertRaises(registry.Error):
                    self.store.append('settings', 'C', registry.wait_settings(data))
                self.assertEqual(len(self.store.events()), before)


if __name__ == '__main__':
    unittest.main()

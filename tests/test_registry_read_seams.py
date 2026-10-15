"""Current CLI reads retain forecast history and bounded pause observations."""
from contextlib import redirect_stdout
import io
import json
from unittest.mock import Mock, patch
import unittest

import test_source_consumers as source_support

import balance
import benchmark
import forecast
import registry_current
import squadra
from registry import Error


class RegistryReadSeams(unittest.TestCase):
    setUp = source_support.SourceConsumers.setUp
    git = source_support.SourceConsumers.git
    task = source_support.SourceConsumers.task
    corpus = source_support.SourceConsumers.corpus
    referenced = source_support.SourceConsumers.referenced
    def indexed(self):
        state, events, expected = self.referenced()
        class Indexed(dict):
            pass
        view = Indexed(state)
        view.current = registry_current.empty()
        metadata = {'messages': {}}
        for event in events:
            if event['kind'] == 'message':
                metadata['messages'][event['data']['id']] = event['data']
            registry_current._forecast(view.current, metadata, event)
        return view, events, expected

    def fake_store(self, state, events):
        store = Mock(read_only=True)
        store.view.return_value = state
        store.events.return_value = events
        store.state.return_value = state
        return store

    def run_main(self, store, argv):
        stream = io.StringIO()
        with patch.object(squadra, 'Store', return_value=store), redirect_stdout(stream):
            rc = squadra.main(argv + ['--root', str(self.root)])
        self.assertEqual(rc, 0, stream.getvalue())
        return stream.getvalue()

    def test_explicit_forecast_uses_warm_index_without_full_history(self):
        state, events, expected = self.indexed()
        store = self.fake_store(state, events)
        self.assertEqual(json.loads(self.run_main(store, ['forecast', '--task', 'T'])), expected)
        store.view.assert_called_once_with()
        store.events.assert_not_called()
        store.state.assert_not_called()

    def test_explicit_forecast_keeps_history_on_missing_index(self):
        state, events, expected = self.referenced()
        store = self.fake_store(state, events)
        self.assertEqual(json.loads(self.run_main(store, ['forecast', '--task', 'T'])), expected)
        store.events.assert_called_once_with()

    def test_automatic_forecast_keeps_history_only_on_index_fallback(self):
        state, events, expected = self.indexed()
        for indexed in (True, False):
            with self.subTest(indexed=indexed):
                store = self.fake_store(state if indexed else dict(state), events)
                with patch.object(squadra, 'dispatch', return_value=None), \
                     patch.object(squadra, 'table', return_value=''), \
                     patch.object(balance, 'calculate', return_value={}):
                    output = self.run_main(store, ['start', '--actor', 'A', '--task', 'T'])
                decoder = json.JSONDecoder()
                actual, _ = decoder.raw_decode(output.lstrip())
                self.assertEqual(actual['forecast'], expected)
                self.assertEqual(store.events.call_count, 0 if indexed else 1)

    def test_usage_reads_raw_state_for_historical_attribution(self):
        state, events, _ = self.referenced()
        store = self.fake_store(state, events)
        result = {'coverage': {'status': 'measured'}}
        with patch('usage.collect', return_value=result) as collect:
            self.assertEqual(json.loads(self.run_main(store, ['usage', '--json'])), result)
        store.state.assert_called_once_with(events, resolve=False)
        collect.assert_called_once_with(events, state)

    def test_balance_resolves_only_forecast_bindings_from_raw_state(self):
        state, events, expected = self.referenced()
        for task in state['tasks'].values():
            task.setdefault('owner', 'A')
            task.setdefault('reviews', [])
            task.setdefault('authors', ['A'])
        store = self.fake_store(state, events)
        with patch.object(balance, 'collect_usage', return_value=None):
            result = json.loads(self.run_main(store, ['balance', '--task', 'T']))
        store.state.assert_called_once_with(events, resolve=False)
        self.assertEqual(result['task']['history']['status'], expected['status'])
        self.assertEqual(result['task']['history']['samples'], [s['task'] for s in expected['samples']])

    def test_benchmark_pause_is_fresh_and_releases_each_view(self):
        store = Mock()
        store.view.side_effect = [{'paused': False}, {'paused': True}]
        benchmark.check_pause(store, 'paused control')
        with self.assertRaisesRegex(Error, 'paused control'):
            benchmark.check_pause(store, 'paused control')
        self.assertEqual(store.view.call_count, 2)
        self.assertEqual(store.release_views.call_count, 2)
        store.state.assert_not_called()
        store.events.assert_not_called()

    def test_benchmark_releases_view_when_read_refuses(self):
        store = Mock()
        store.view.side_effect = Error('integrity control')
        with self.assertRaisesRegex(Error, 'integrity control'):
            benchmark.check_pause(store, 'unused')
        store.release_views.assert_called_once_with()
        benchmark.check_pause(None, 'no registry')

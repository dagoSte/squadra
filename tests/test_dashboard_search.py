"""Search human fields, chronology and the accessible result menu in both runtimes."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dashboard
from registry import initial
import test_dashboard_performance as performance
import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / 'tests/dashboard_search_cases.json'


def history():
    state = initial()
    state['tasks']['old'] = {'id': 'old', 'title': 'Canary task', 'status': 'done', 'reviews': [{'notes': 'Reviewonly'}]}
    state['runs']['run'] = {'id': 'run', 'task': 'old', 'agent': 'Canary', 'status': 'passed'}
    state['messages']['mail'] = {'id': 'mail', 'body': 'Canary message', 'task': None}
    state['outputs'] = [{'event': 'out', 'text': 'Canary output'}]
    state['asks']['ask'] = {'id': 'ask', 'question': 'Canary question'}
    records = [('task', {'id': 'old'}), ('decision', {'id': 'decision', 'reason': 'Canary decision'}),
               ('close', {'task': 'old'}), ('run.start', {'id': 'run'}), ('message', {'id': 'mail'}),
               ('output', {}), ('ask', {'id': 'ask'}), ('review', {'task': 'old', 'notes': 'Reviewonly Canary review'})]
    events = [{'kind': kind, 'data': data, 'id': 'out' if kind == 'output' else 'event' + str(i),
               'seq': i, 'utc': '2026-10-16T12:%02d:00Z' % i, 'actor': 'closer' if kind == 'close' else 'reader'}
              for i, (kind, data) in enumerate(records, 1)]
    return state, events


class DashboardSearch(unittest.TestCase):
    def require_search(self):
        fn = getattr(dashboard, 'search_records', None)
        self.assertTrue(callable(fn), 'Search must index human records and expose chronological results')
        return fn

    def test_shared_unicode_word_start_and_digest_cases(self):
        fn = getattr(dashboard, 'search_match', None)
        self.assertTrue(callable(fn), 'One human-text search rule is required')
        for case in json.loads(CASES.read_text())['parity']:
            with self.subTest(case=case):
                self.assertEqual(bool(fn(case['fields'], case['query'])), case['match'])

    def test_every_allowed_leaf_and_structural_negative(self):
        self.require_search()
        fixture = json.loads(CASES.read_text())
        for group, expected in [('positive', True), ('negative', False)]:
            for case in fixture[group]:
                with self.subTest(group=group, kind=case['kind'], path=case['path']):
                    self.assertEqual(bool(dashboard.search_match(dashboard.search_fields(case['kind'], case['record']), 'canary')), expected)

    def test_seven_kinds_newest_first_with_position_time_and_actor(self):
        fn = self.require_search()
        state, events = history()
        result = fn(state, events[:3], 'canary')
        rows = [r for r in result['results'] if r['kind'] in ('tasks', 'decisions')]
        self.assertEqual([(r['kind'], r['seq']) for r in rows], [('tasks', 3), ('decisions', 2)])
        self.assertEqual((rows[0]['utc'], rows[0]['actor']), (events[2]['utc'], 'closer'))
        result = fn(state, events, 'canary')
        self.assertEqual({r['kind'] for r in result['results']}, set(('tasks', 'runs', 'messages', 'outputs', 'asks', 'decisions', 'reviews')))
        self.assertEqual(result['total'], 7)
        self.assertEqual([r['seq'] for r in result['results']], sorted([r['seq'] for r in result['results']], reverse=True))
        review = fn(state, events, 'reviewonly')
        self.assertEqual(review['tasks'], [])
        self.assertEqual(review['reviews'], ['event8'])

    def test_lifecycle_positions_do_not_use_heartbeats_or_unrelated_references(self):
        fn = self.require_search()
        state, events = history()
        def add(kind, data):
            i = len(events) + 1
            events.append({'kind': kind, 'data': data, 'id': 'extra' + str(i), 'seq': i, 'utc': str(i), 'actor': 'last'})
        add('run.end', {'id': 'run'})
        add('run.progress', {'id': 'run'})
        self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'runs'), 9)
        add('ack', {'message': 'mail'})
        add('transport', {'message': 'mail'})
        self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'messages'), 12)
        add('decision', {'id': 'answer', 'reason': 'unrelated', 'answers': 'ask', 'tasks': ['old']})
        result = fn(state, events, 'canary')
        self.assertEqual(next(r['seq'] for r in result['results'] if r['kind'] == 'asks'), 13)
        self.assertEqual(next(r['seq'] for r in result['results'] if r['kind'] == 'tasks'), 8)
        for kind in ('brief', 'plan', 'start', 'deliver', 'route', 'fix', 'close', 'supersede'):
            add(kind, {'task': 'old'})
            self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'tasks'), len(events))
        add('task', {'id': 'replacement', 'supersedes': 'old'})
        self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'tasks'), len(events))
        state['messages']['assignment'] = {'id': 'assignment', 'task': 'old'}
        add('ack', {'message': 'assignment'})
        self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'tasks'), len(events))
        for kind in ('run.cancel', 'run.abandon'):
            add(kind, {'id': 'run'})
            self.assertEqual(next(r['seq'] for r in fn(state, events, 'canary')['results'] if r['kind'] == 'runs'), len(events))

    def test_empty_query_does_no_projection_and_never_iterates_events(self):
        fn = self.require_search()
        class NoEvents:
            def __iter__(self):
                raise AssertionError('An empty search must not build position maps')
        with patch.object(dashboard, 'search_fields', side_effect=AssertionError('projection')):
            result = fn({}, NoEvents(), '  ')
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['results'], [])

    def test_one_projection_per_record_all_ids_and_only_100_rows(self):
        fn = self.require_search()
        state = initial()
        state['tasks'] = {str(i): {'id': str(i), 'title': 'Canary'} for i in range(125)}
        events = [{'kind': 'task', 'data': {'id': str(i)}, 'id': str(i), 'seq': i + 1, 'utc': '', 'actor': 'a'} for i in range(125)]
        with patch.object(dashboard, 'search_fields', wraps=dashboard.search_fields) as project:
            result = fn(state, events, 'canary')
        self.assertEqual(project.call_count, 125)
        self.assertEqual(len(result['tasks']), 125)
        self.assertEqual(result['total'], 125)
        self.assertEqual(len(result['results']), 100)
        self.assertEqual((result['results'][0]['id'], result['results'][-1]['id']), ('124', '25'))


class DashboardSearchHTTP(unittest.TestCase):
    setUp = performance.DashboardPerformance.setUp
    stop_server = performance.DashboardPerformance.stop_server
    get = performance.DashboardPerformance.get
    state = performance.DashboardPerformance.state
    stamp = performance.DashboardPerformance.stamp
    task_id = performance.DashboardPerformance.task_id
    run_id = performance.DashboardPerformance.run_id
    source_path = performance.DashboardPerformance.source_path
    source_hash = performance.DashboardPerformance.source_hash

    def search(self, query):
        return self.state(**{'X-Squadra-View': 'compact', 'X-Squadra-Search': quote(query, safe='')})['search']

    def test_field_name_is_not_a_hit_and_human_title_still_is(self):
        result = self.search('tag')
        self.assertEqual(result['tasks'], [], 'stage/tags keys cannot make a task a hit')
        self.assertEqual(self.search('inspect')['tasks'], [self.task_id])
        self.assertEqual(self.search('sha256')['tasks'], [])

    def test_accent_prefix_and_reordered_terms_reach_compact_http(self):
        self.assertEqual(self.search('CAFFE task')['tasks'], [self.task_id])
        self.assertEqual(self.search('inspect retained')['tasks'], [self.task_id])
        self.assertEqual(self.search('spect')['tasks'], [])

    def test_comms_sentence_words_and_metadata_never_enter_global_results(self):
        self.store.append('message', 'C', {'id': 'comms-control', 'to': 'A', 'body': 'ordinary prose', 'ping': True})
        for query in ('chiamata di presenza', 'presence call', '14:34', '12:34', self.source_path):
            with self.subTest(query=query):
                result = self.search(query)
                self.assertEqual(result['total'], 0)
                self.assertEqual(result['results'], [])
                for kind in ('tasks', 'runs', 'messages', 'outputs', 'decisions', 'asks', 'reviews'):
                    self.assertEqual(result[kind], [])
        self.assertEqual(self.search('ordinary')['messages'], ['comms-control'])

    def test_decision_ask_and_review_are_searchable_beyond_task_json(self):
        self.store.append('ask', 'C', {'id': 'ASK', 'question': 'Needle question', 'options': [{'id': 'a', 'label': 'First', 'say': 'First'}, {'id': 'b', 'label': 'Second', 'say': 'Second'}]})
        self.store.append('decision', 'C', {'id': 'DEC', 'reason': 'Needle choice', 'answers': 'ASK', 'choice': 'a', 'channel': 'typed-here'})
        self.store.append('review', 'R', {'task': self.task_id, 'stage': 'code', 'verdict': 'accepted', 'notes': 'Needle review', 'binding': self.fingerprint['sha256'], 'covers': [], 'report': self.artifact})
        result = self.search('needle')
        self.assertEqual(result.get('asks'), ['ASK'])
        self.assertEqual(result.get('decisions'), ['DEC'])
        self.assertEqual(result['tasks'], [], 'A review matches itself, never the task container')
        self.assertEqual(len(result.get('reviews', [])), 1)
        self.assertEqual([r['kind'] for r in result['results']], ['reviews', 'asks', 'decisions'])


class DashboardSearchJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-search-contract-')
        cls.fixture = Path(cls.temp.name) / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(Path(cls.temp.name))))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def contract(self, case):
        result = subprocess.run([shutil.which('node'), str(ROOT / 'tests/dashboard_search_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(self.fixture), case, str(CASES)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('search contract passed: ' + case, result.stdout)

    def test_shared_fields_and_unicode_parity_including_turkish(self):
        self.contract('parity')

    def test_results_expand_cap_footer_copy_and_keyboard(self):
        self.contract('menu')

    def test_open_record_guards_and_integrity_failure(self):
        self.contract('records')

    def test_row_head_race_refreshes_and_retries_once_without_raw_errors(self):
        self.contract('retry')

    def test_comms_search_omits_metadata_and_keeps_global_results_separate(self):
        self.contract('comms')

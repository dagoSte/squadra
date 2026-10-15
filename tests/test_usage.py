"""Synthetic, numeric-only usage counting, attribution and collection contracts."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import usage

NOW = datetime(2026, 10, 15, 12, 37, tzinfo=timezone.utc).timestamp()
CLAUDE = '12345678-1234-4567-8123-123456789abc'
CODEX = '87654321-1234-4567-8123-123456789abc'
CHILD = '12345678-1234-4567-8123-123456789def'
CANARY = 'PRIVATE_CANARY_bearer_token_do_not_retain@example.invalid'


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace('+00:00', 'Z')


def claude(at, key='message-1', *, inp=10, out=5, cached=0, created=0,
           model='claude-sonnet-4-5', session=CLAUDE):
    return {'type': 'assistant', 'timestamp': stamp(at), 'sessionId': session,
            'requestId': 'request-' + key, 'message': {'id': key, 'model': model,
            'content': [{'type': 'text', 'text': CANARY}], 'usage': {
                'input_tokens': inp, 'output_tokens': out,
                'cache_read_input_tokens': cached, 'cache_creation_input_tokens': created}}}


def codex(at, inp, out, cached=0, reasoning=0, *, quota=None):
    payload = {'type': 'token_count', 'info': {'total_token_usage': {
        'input_tokens': inp, 'output_tokens': out, 'cached_input_tokens': cached,
        'reasoning_output_tokens': reasoning, 'total_tokens': inp + out}},
        'message': CANARY}
    if quota is not None:
        payload['rate_limits'] = quota
    return {'type': 'event_msg', 'timestamp': stamp(at), 'payload': payload}


def model(at, name='gpt-5.4'):
    return {'type': 'turn_context', 'timestamp': stamp(at), 'payload': {'model': name, 'instructions': CANARY}}


def event(kind, at, actor='builder', **data):
    return {'kind': kind, 'utc': stamp(at), 'actor': actor, 'data': data}


class UsageCollector(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='sq-usage-tests-')
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()
        self.roots = {k: self.folder / k for k in ('claude', 'codex')}
        for root in self.roots.values():
            root.mkdir(mode=0o700)
        self.state = {'agents': {}, 'tasks': {}}
        self.events = []
        usage._COLLECTORS.clear()
        self.addCleanup(usage._COLLECTORS.clear)

    def seat(self, actor='builder', *, client='claude', session=CLAUDE, identity=None):
        self.state['agents'][actor] = {'identity': identity or client + '-window-' + session,
                                       'client': client, 'session': session}

    def write(self, client, rows, *, session=None, path=None, suffix=b''):
        if path is None:
            session = session or (CLAUDE if client == 'claude' else CODEX)
            path = self.roots[client] / ('project/' + session + '.jsonl' if client == 'claude'
                    else '2026/10/15/rollout-2026-10-15T12-00-00-' + session + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows) + suffix)
        path.chmod(0o600)
        return path

    def cx(self, rows, *, session=CODEX):
        return self.write('codex', [{'type': 'session_meta', 'payload': {'id': session}},
                                   model(NOW - 7200)] + rows, session=session)

    def collect(self, *, seconds=2, now=NOW):
        with patch.object(usage.time, 'time', return_value=now):
            return usage.collect(self.events, self.state, roots=self.roots, now=now, budget_seconds=seconds)

    def one(self, **kwargs):
        result = self.collect(**kwargs)
        self.assertEqual(len(result['windows']), 1)
        return result, result['windows'][0]

    def files(self):
        return {str(p.relative_to(self.folder)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.folder.rglob('*') if p.is_file() and not p.is_symlink()}

    def test_aggregate_integer_limit_is_unknown_not_rounded_or_advised(self):
        self.seat()
        half = usage.MAX_NUMBER // 2 + 1
        self.write('claude', [claude(NOW-7200, key='large-1', inp=half, out=0),
                              claude(NOW-1, key='large-2', inp=half, out=0)])
        data, window = self.one()
        self.assertIsNone(window['totals']['total'])
        self.assertIn('counter_limit', window['reasons'])
        self.assertEqual(window['status'], 'partial')
        self.assertIsNone(window['rolling']['h5']['total'])
        self.assertEqual(window['rolling']['h5']['status'], 'not_measured')
        self.assertEqual(data['suggestions'], [])
        self.assertNotIn('None tokens', usage.text_report(data))

    def test_record_cache_limit_is_global_and_reports_partial_coverage(self):
        self.seat()
        self.write('claude', [claude(NOW-60+i, key='many-'+str(i)) for i in range(6)])
        with patch.object(usage, 'MAX_RECORDS', 3):
            data, window = self.one()
        self.assertEqual(window['status'], 'partial')
        self.assertIn('sample_limit', window['reasons'])
        self.assertLessEqual(sum(len(src.records) for c in usage._COLLECTORS.values()
                                 for src in c.sources.values()), 3)

    def test_claude_aliases_stream_updates_and_parent_duplicates_count_once(self):
        self.seat(); self.seat('reader')
        rows = [claude(NOW - 120, inp=10, out=2), claude(NOW - 100, inp=10, out=7)]
        self.write('claude', rows)
        self.write('claude', [rows[-1], claude(NOW - 60, key='child-message', inp=3, out=1)],
                   path=self.roots['claude'] / 'project' / CLAUDE / 'subagents' / 'agent-abcdef.jsonl')
        data, window = self.one()
        self.assertEqual(window['actors'], ['builder', 'reader'])
        self.assertEqual(window['totals']['total'], 21)
        self.assertEqual(data['coverage']['sources'], 2)
        self.assertEqual(data['coverage']['measured'], 2)
        self.assertEqual(data['tasks'][0]['task'], None)
        self.assertEqual(sum(t['totals']['total'] for t in data['tasks']), 21)
        self.assertIsNone(data['cost'])

    def test_numeric_projection_discards_text_even_when_it_looks_like_usage(self):
        self.seat()
        row = claude(NOW - 10, key=CANARY, model=CANARY)
        row['message']['content'].append({'text': json.dumps(claude(NOW, inp=999999))})
        row['unrelated'] = {'api_key': CANARY}
        self.write('claude', [{'type': 'user', 'message': {'content': CANARY}}, row])
        before = self.files()
        state, events = deepcopy(self.state), deepcopy(self.events)
        data, window = self.one()
        self.assertEqual(window['totals']['total'], 15)
        self.assertIsNone(window['models'][0]['model'])
        cache = [s.records for collector in usage._COLLECTORS.values() for s in collector.sources.values()]
        for retained in (json.dumps(data), usage.text_report(data), json.dumps(cache)):
            self.assertNotIn(CANARY, retained)
            self.assertNotIn('api_key', retained)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.state, state)
        self.assertEqual(self.events, events)

    def test_codex_deltas_repeats_quota_only_and_disjoint_subsets(self):
        self.seat(client='codex', session=CODEX)
        q = {'primary': {'used_percent': 42, 'window_minutes': 300, 'resets_at': int(NOW + 3600)}, 'plan_type': 'pro'}
        only_quota = codex(NOW - 10, 0, 0, quota=q); only_quota['payload']['info'] = None
        self.cx([codex(NOW - 200, 100, 30, 60, 10),
                 codex(NOW - 100, 150, 50, 80, 15),
                 codex(NOW - 90, 150, 50, 80, 15), only_quota])
        data, window = self.one()
        self.assertEqual(window['totals'], dict(input=30, cache_read=20, cache_creation=0, output=20, reasoning=5, total=70))
        self.assertIn('baseline_unattributed', window['reasons'])
        self.assertEqual(window['quota'][0]['used_percent'], 42)
        self.assertEqual(window['quota'][0]['model'], 'gpt-5.4')
        self.assertEqual(sum(t['totals']['total'] for t in data['tasks']), 70)

    def test_codex_reset_starts_new_baseline_and_cross_model_delta_is_unknown(self):
        self.seat(client='codex', session=CODEX)
        self.cx([codex(NOW - 500, 100, 10), codex(NOW - 400, 120, 20),
                 codex(NOW - 300, 5, 2), codex(NOW - 200, 15, 7),
                 model(NOW - 150, 'gpt-5.5'), codex(NOW - 100, 25, 12)])
        data, window = self.one()
        self.assertEqual(window['totals']['total'], 60)
        self.assertIn('counter_reset', window['reasons'])
        by_model = {m['model']: m for m in window['models']}
        self.assertEqual(by_model['gpt-5.4']['totals']['total'], 45)
        self.assertEqual(by_model[None]['totals']['total'], 15)
        self.assertNotIn('gpt-5.5', by_model)
        self.assertTrue(any(t['estimated'] for t in data['tasks']))

    def test_independently_registered_subagent_has_exact_precedence(self):
        self.seat()
        self.seat('child', session=CHILD)
        self.write('claude', [claude(NOW - 100, inp=8, out=2)])
        for session_field in (CHILD, CLAUDE):
            with self.subTest(session_field=session_field):
                self.write('claude', [claude(NOW - 50, key='child-message', inp=3, out=1, session=session_field)],
                           path=self.roots['claude'] / 'project' / CLAUDE / 'subagents' / (CHILD + '.jsonl'))
                data = self.collect()
                by_actor = {w['actors'][0]: w for w in data['windows']}
                self.assertEqual(by_actor['builder']['totals']['total'], 10)
                self.assertIsNotNone(by_actor['child']['totals'], 'an exact registered child owns its source')
                self.assertEqual(by_actor['child']['totals']['total'], 4)
                self.assertEqual(sum(t['totals']['total'] for t in data['tasks']), 14)

    def test_ambiguous_prefix_and_mismatched_session_have_no_counts(self):
        self.seat(session='12345678')
        self.write('claude', [claude(NOW - 20)])
        self.write('claude', [claude(NOW - 10, session=CHILD)], session=CHILD)
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('identity_ambiguous', window['reasons'])
        self.state['agents']['builder']['session'] = CLAUDE
        self.write('claude', [claude(NOW - 20, session=CHILD)])
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('identity_mismatch', window['reasons'])

    def test_cursor_and_missing_session_are_not_observed_zero(self):
        self.seat(client='cursor')
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('client_unsupported', window['reasons'])
        self.assertTrue(all(b['total'] is None for b in window['hourly']))
        self.state['agents']['builder']['client'] = 'claude'
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('session_not_found', window['reasons'])
        self.assertEqual(window['recent']['status'], 'not_measured')

    def test_symlink_source_is_refused_without_opening_target(self):
        self.seat()
        target = self.folder / 'outside.jsonl'
        self.write('claude', [claude(NOW - 10)], path=target)
        link = self.roots['claude'] / (CLAUDE + '.jsonl'); link.symlink_to(target)
        before = self.files()
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertTrue({'unsafe_source', 'source_unavailable'} & set(window['reasons']))
        self.assertEqual(self.files(), before)

    def test_foreign_owned_source_is_refused(self):
        self.seat()
        path = self.write('claude', [claude(NOW - 10)])
        original = Path.lstat
        def foreign(p):
            result = original(p)
            if p == path:
                values = list(result); values[4] = os.getuid() + 1
                return os.stat_result(values)
            return result
        with patch.object(Path, 'lstat', foreign):
            _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertTrue({'unsafe_source', 'source_unavailable'} & set(window['reasons']))

    def test_nonregular_source_is_refused_without_blocking(self):
        self.seat()
        fifo = self.roots['claude'] / (CLAUDE + '.jsonl')
        os.mkfifo(fifo, 0o600)
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertTrue({'unsafe_source', 'source_unavailable'} & set(window['reasons']))

    def test_changed_format_invalid_numbers_and_duplicate_keys_have_fixed_reasons(self):
        self.seat()
        for invalid in [True, -1, 1.25, float('nan'), 2**53]:
            with self.subTest(value=repr(invalid)):
                self.write('claude', [claude(NOW - 10, inp=invalid)])
                _, window = self.one()
                self.assertIsNone(window['totals'])
                self.assertIn('counter_invalid', window['reasons'])
                self.assertNotIn(CANARY, json.dumps(window))
        self.write('claude', [dict(type='assistant', timestamp=stamp(NOW), message={'usage': {'new_counter': 99}, 'content': CANARY})])
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertTrue(window['reasons'])
        self.write('claude', [], suffix=b'{"type":"assistant","type":"user"}\n')
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('duplicate_json_key', window['reasons'])

    def test_impossible_codex_subset_is_not_counted(self):
        self.seat(client='codex', session=CODEX)
        for cached, reasoning in [(101, 1), (1, 31)]:
            with self.subTest(cached=cached, reasoning=reasoning):
                self.cx([codex(NOW - 200, 10, 3), codex(NOW - 100, 100, 30, cached, reasoning)])
                _, window = self.one()
                self.assertIsNone(window['totals'])
                self.assertIn('counter_inconsistent', window['reasons'])

    def test_bounded_depth_and_missing_sample_id_are_not_numeric_measurements(self):
        self.seat()
        deep = CANARY
        for _ in range(35):
            deep = {'next': deep}
        row = claude(NOW - 10); row['message']['content'] = deep
        self.write('claude', [row])
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('record_too_deep', window['reasons'])
        row = claude(NOW - 10); del row['message']['id']; del row['requestId']
        self.write('claude', [row])
        _, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('sample_identity_missing', window['reasons'])
        self.assertNotIn(CANARY, json.dumps(window))

    def test_codex_missing_or_wrong_session_metadata_refuses_all_samples(self):
        self.seat(client='codex', session=CODEX)
        rows = [model(NOW - 200), codex(NOW - 150, 10, 1), codex(NOW - 100, 20, 3)]
        for meta in [[], [{'type': 'session_meta', 'payload': {'id': CLAUDE}}]]:
            with self.subTest(meta=bool(meta)):
                self.write('codex', meta + rows)
                _, window = self.one()
                self.assertIsNone(window['totals'])
                self.assertTrue({'identity_unverified', 'identity_mismatch'} & set(window['reasons']))

    def test_codex_alias_prefix_resolves_to_one_full_session(self):
        self.seat(client='codex', session=CODEX[:8])
        self.cx([codex(NOW - 200, 10, 1), codex(NOW - 100, 20, 3)])
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 12)
        self.assertNotIn('identity_ambiguous', window['reasons'])

    def test_partial_last_line_is_pending_and_completed_later_once(self):
        self.seat()
        last = json.dumps(claude(NOW - 10, key='second', inp=20, out=1)).encode()
        path = self.write('claude', [claude(NOW - 20)], suffix=last)
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 15)
        self.assertIn('incomplete_record', window['reasons'])
        self.assertEqual(window['status'], 'partial')
        with path.open('ab') as stream: stream.write(b'\n')
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 36)
        self.assertNotIn('incomplete_record', window['reasons'])

    def test_cache_reuse_and_same_size_same_mtime_edit_invalidates_on_ctime(self):
        self.seat()
        path = self.write('claude', [claude(NOW - 10, inp=10)])
        _, window = self.one(); self.assertEqual(window['totals']['total'], 15)
        with patch.object(usage, 'project', side_effect=AssertionError('warm cache reparsed')):
            _, window = self.one(); self.assertEqual(window['totals']['total'], 15)
        before = path.stat()
        raw = path.read_bytes().replace(b'"input_tokens": 10', b'"input_tokens": 20')
        self.assertEqual(len(raw), before.st_size)
        path.write_bytes(raw); os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        _, window = self.one()
        self.assertEqual(window['totals']['total'], 25)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_first_codex_cumulative_balance_is_unattributed_not_usage(self):
        self.seat(client='codex', session=CODEX)
        self.events = [event('task', NOW - 1000, id='A', owner='builder'),
                       event('start', NOW - 900, task='A')]
        self.cx([codex(NOW - 10, 200000, 30000, 150000, 10000)])
        data, window = self.one()
        self.assertIsNone(window['totals'])
        self.assertIn('baseline_unattributed', window['reasons'])
        self.assertEqual(data['tasks'], [])
        self.assertEqual(data['days'], [])
        self.assertIsNone(window['recent']['total_tokens'])

    def test_second_consumer_uses_same_cache_and_busy_collector_is_nonblocking(self):
        self.seat(); self.write('claude', [claude(NOW - 10)])
        first = self.collect()
        self.assertEqual(first['windows'][0]['totals']['total'], 15)
        self.assertEqual(len(usage._COLLECTORS), 1)
        with patch.object(usage, 'project', side_effect=AssertionError('second consumer reparsed source')):
            second = self.collect(seconds=.25)
        self.assertEqual(second, first)
        collector = next(iter(usage._COLLECTORS.values()))
        done, values = threading.Event(), []
        def read():
            try:
                values.append(self.collect(seconds=.25))
            finally:
                done.set()
        collector.lock.acquire()
        worker = threading.Thread(target=read, daemon=True)
        worker.start()
        try:
            returned_without_unlock = done.wait(.5)
        finally:
            collector.lock.release()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(returned_without_unlock, 'automatic consumer must not wait behind another scan')
        self.assertEqual(values[0]['coverage']['status'], 'collecting')
        self.assertIn('collector_busy', values[0]['coverage']['reasons'])
        self.assertIsNone(values[0]['cost'])

    def test_zero_budget_returns_unknown_without_reading_payload(self):
        self.seat(); self.write('claude', [claude(NOW - 10)])
        with patch.object(usage, 'project', side_effect=AssertionError('zero-budget parse')):
            data, window = self.one(seconds=0)
        self.assertIsNone(window['totals'])
        self.assertEqual(data['status'], 'not_measured')

    def test_reads_are_chunk_bounded_and_tail_progress_never_claims_full_coverage(self):
        self.seat()
        self.write('claude', [claude(NOW - 7200, key='early'),
                             {'type': 'user', 'content': 'x' * (2 * 1024 * 1024)},
                             claude(NOW - 10, key='late')])
        sizes, original = [], usage.os.read
        def read(fd, count):
            sizes.append(count)
            return original(fd, count)
        with patch.object(usage.os, 'read', side_effect=read):
            _, window = self.one()
        self.assertEqual(window['totals']['total'], 30)
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 64 * 1024, 'read chunks are bounded even when records are larger')

    def test_large_valid_record_across_slices_does_not_discard_earlier_samples(self):
        self.seat()
        self.write('claude', [claude(NOW - 7200, key='early'),
                             {'type': 'user', 'content': 'x' * (9 * 1024 * 1024)},
                             claude(NOW - 10, key='late')])
        for _ in range(5):
            data, window = self.one()
            if data['coverage']['status'] != 'collecting':
                break
        self.assertEqual(window['totals']['total'], 30)
        self.assertNotIn('record_too_large', window['reasons'])
        self.assertEqual(window['status'], 'measured')

    def test_observed_codex_zero_deltas_are_zero_not_missing_usage(self):
        self.seat(client='codex', session=CODEX)
        self.cx([codex(NOW - 7200, 0, 0), codex(NOW - 10, 0, 0)])
        _, window = self.one()
        self.assertIsNotNone(window['totals'], 'recognized zero deltas are measurements')
        self.assertEqual(window['totals']['total'], 0)
        self.assertEqual(window['recent']['total_tokens'], 0)
        self.assertEqual(window['recent']['status'], 'measured')

    def test_conflicting_duplicate_sample_is_excluded_not_retained_as_fact(self):
        self.seat()
        self.write('claude', [claude(NOW - 20, out=10), claude(NOW - 10, out=7)])
        _, window = self.one()
        self.assertIn('duplicate_conflict', window['reasons'])
        self.assertIsNone(window['totals'], 'conflicting final counters do not select an invented final value')

    def test_parent_child_conflict_excludes_both_counters_and_model_disagreement(self):
        self.seat()
        self.write('claude', [claude(NOW - 20, out=10)])
        for child in [claude(NOW - 10, out=7), claude(NOW - 10, out=10, model='claude-opus-4-5')]:
            with self.subTest(child=child['message']['model']):
                usage._COLLECTORS.clear()
                self.write('claude', [child], path=self.roots['claude'] / 'project' / CLAUDE / 'subagents' / 'agent-abcdef.jsonl')
                _, window = self.one()
                self.assertIn('duplicate_conflict', window['reasons'])
                self.assertIsNone(window['totals'])

    def test_hourly_models_rolling_current_endpoint_unknown_and_observed_zero(self):
        self.seat()
        self.write('claude', [claude(NOW - 8 * 86400, key='baseline'),
                             claude(NOW - 18001, key='outside', inp=100, out=0),
                             claude(NOW - 18000, key='left', inp=10, out=0),
                             claude(NOW - 60, key='right', inp=20, out=0, model='claude-opus-4-5'),
                             claude(NOW, key='now', inp=30, out=0, model='claude-opus-4-5')])
        data, window = self.one()
        self.assertEqual(len(window['hourly']), 168)
        self.assertTrue(window['hourly'][-1]['partial_hour'])
        self.assertEqual(window['hourly'][-1]['total'], 50)
        self.assertEqual(window['rolling']['h5']['total'], 60)
        self.assertEqual(window['rolling']['h5']['from'], stamp(NOW - 18000))
        self.assertEqual(window['rolling']['h5']['to'], stamp(NOW))
        by_model = {m['model']: m for m in window['models']}
        self.assertEqual(by_model['claude-opus-4-5']['hourly'][-1]['total'], 50)
        self.assertEqual(by_model['claude-sonnet-4-5']['hourly'][-1]['total'], 0)
        self.assertEqual(window['recent']['total_tokens'], 50)
        self.assertAlmostEqual(window['recent']['tokens_per_minute'], 50 / 15)
        self.assertEqual(data['models'][0]['hourly'], window['models'][0]['hourly'])

    def test_pre_session_hours_unknown_idle_observed_zero_and_model_gaps_not_invented(self):
        self.seat()
        self.write('claude', [claude(NOW - 7200, inp=0, out=0)])
        _, window = self.one()
        self.assertIsNone(window['hourly'][0]['total'])
        self.assertEqual(window['hourly'][0]['status'], 'not_measured')
        self.assertEqual(window['hourly'][-1]['total'], 0)
        self.assertEqual(window['recent']['total_tokens'], 0)
        self.assertEqual(window['recent']['status'], 'measured')
        self.assertEqual(window['rolling']['d7']['total'], 0)
        self.assertEqual(window['rolling']['d7']['status'], 'partial')
        self.assertTrue(window['rolling']['d7']['reason'])

    def test_tasks_overlap_aliases_review_claim_correction_and_gaps_conserve(self):
        self.seat(); self.seat('reader')
        self.events = [event('task', NOW - 1000, id='A', owner='builder'),
                       event('task', NOW - 1000, id='B', owner='builder'),
                       event('message', NOW - 950, id='assign-A', task='A', to='builder'),
                       event('ack', NOW - 900, message='assign-A'),
                       event('start', NOW - 850, task='A'),
                       event('start', NOW - 800, task='B'),
                       event('deliver', NOW - 600, task='A', handoff='read-A'),
                       event('message', NOW - 590, id='read-A', related_task='A', to='reader'),
                       event('ack', NOW - 580, actor='reader', message='read-A'),
                       event('review', NOW - 500, actor='reader', task='A', stage='code'),
                       event('deliver', NOW - 450, task='B', handoff='read-B'),
                       event('message', NOW - 440, id='stale-A', related_task='A', to='reader'),
                       event('ack', NOW - 430, actor='reader', message='stale-A'),
                       event('start', NOW - 300, task='A'),
                       event('close', NOW - 100, task='A')]
        self.write('claude', [claude(NOW - 870, 'alone', inp=10, out=0),
                             claude(NOW - 700, 'overlap', inp=11, out=2),
                             claude(NOW - 550, 'review', inp=10, out=0),
                             claude(NOW - 400, 'gap', inp=7, out=0),
                             claude(NOW - 200, 'correction', inp=4, out=0),
                             claude(NOW - 50, 'closed', inp=3, out=0)])
        data, window = self.one()
        tasks = {r['task']: r for r in data['tasks']}
        self.assertEqual(tasks['A']['totals']['total'], 26)
        self.assertEqual(tasks['B']['totals']['total'], 11)
        self.assertEqual(tasks[None]['totals']['total'], 10)
        self.assertTrue(tasks['A']['estimated'])
        self.assertIn('overlap_equal_split', tasks['B']['reasons'])
        for kind in usage.PRICE_KINDS + ('total',):
            self.assertEqual(sum(t['totals'][kind] for t in tasks.values()), window['totals'][kind])

    def test_codex_delta_crossing_task_or_midnight_is_explicitly_estimated(self):
        midnight = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
        self.seat(client='codex', session=CODEX)
        self.events = [event('task', midnight - 60, id='A', owner='builder'),
                       event('start', midnight, task='A')]
        self.cx([codex(midnight - 30, 100, 10), codex(midnight + 30, 110, 15)])
        data, window = self.one()
        self.assertEqual(window['totals']['total'], 15)
        self.assertEqual(data['days'][0]['day'], '2026-10-15')
        self.assertTrue(data['days'][0]['estimated'])
        self.assertEqual(data['tasks'][0]['task'], 'A')
        self.assertTrue(data['tasks'][0]['estimated'])
        self.assertIn('observation_attribution', data['tasks'][0]['reasons'])

    def test_review_pool_first_current_handoff_ack_owns_read_span(self):
        self.seat()
        self.seat('review-one', session=CHILD)
        other = 'abcdefab-1234-4567-8123-123456789abc'
        self.seat('review-two', session=other)
        self.events = [event('task', NOW - 1000, id='A', owner='builder'),
                       event('start', NOW - 900, task='A'),
                       event('deliver', NOW - 700, task='A', handoffs={'review-one': 'h1', 'review-two': 'h2'}),
                       event('message', NOW - 690, id='h1', related_task='A', to='review-one'),
                       event('message', NOW - 690, id='h2', related_task='A', to='review-two'),
                       event('ack', NOW - 680, actor='review-one', message='h1'),
                       event('ack', NOW - 670, actor='review-two', message='h2'),
                       event('review', NOW - 100, actor='review-one', task='A', stage='code')]
        self.write('claude', [claude(NOW - 400, session=CHILD, inp=4, out=0)], session=CHILD)
        self.write('claude', [claude(NOW - 400, session=other, inp=7, out=0)], session=other)
        data = self.collect()
        totals = {t['task']: t['totals']['total'] for t in data['tasks']}
        self.assertEqual(totals, {'A': 4, None: 7})

    def test_quotas_from_two_windows_are_kept_separate_without_account_sum(self):
        second = 'abcdefab-1234-4567-8123-123456789abc'
        self.seat(client='codex', session=CODEX)
        self.seat('other', client='codex', session=second)
        quota = {'primary': {'used_percent': 85, 'window_minutes': 300, 'resets_at': int(NOW + 1000)}, 'plan_type': 'pro'}
        rows = [codex(NOW - 200, 0, 0), codex(NOW - 10, 1, 1, quota=quota)]
        self.cx(rows); self.cx(rows, session=second)
        data = self.collect()
        self.assertEqual([w['quota'][0]['used_percent'] for w in data['windows']], [85, 85])
        self.assertEqual(len(data['quota_history']), 2)
        self.assertEqual(len({q['identity'] for q in data['quota_history']}), 2)
        self.assertNotIn('quota_total', data)
        for suggestion in data['suggestions']:
            self.assertEqual(suggestion['metrics']['percent'], 85)
            self.assertIn(suggestion['window'], {w['identity'] for w in data['windows']})

    def test_quota_fresh_stale_expired_and_distinct_windows_not_summed(self):
        self.seat(client='codex', session=CODEX)
        def quotas(reset):
            return {'primary': {'used_percent': 80, 'window_minutes': 300, 'resets_at': int(reset)},
                    'secondary': {'used_percent': 90, 'window_minutes': 10080, 'resets_at': int(reset + 600)}, 'plan_type': 'pro'}
        self.cx([codex(NOW - 2000, 0, 0), codex(NOW - 10, 1, 1, quota=quotas(NOW + 100))])
        data, window = self.one()
        self.assertEqual([q['status'] for q in window['quota']], ['measured', 'measured'])
        self.assertEqual([s['id'] for s in data['suggestions']], ['quota', 'quota'])
        _, stale = self.one(now=NOW + 950)
        self.assertEqual([q['status'] for q in stale['quota']], ['expired', 'expired'])
        self.cx([codex(NOW - 2000, 0, 0), codex(NOW - 1000, 1, 1, quota=quotas(NOW + 2000))])
        data, stale = self.one()
        self.assertEqual(stale['quota'][0]['status'], 'stale')
        self.assertFalse(data['suggestions'])


class UsagePricesAndAdvice(unittest.TestCase):
    def counts(self, *, inp=250, cached=750, created=0, out=0, reasoning=None):
        return dict(input=inp, cache_read=cached, cache_creation=created, output=out,
                    reasoning=reasoning, total=inp + cached + created + out)

    def data(self, tokens=None, status='measured', name='claude-sonnet-4-5'):
        return {'status': status, 'models': [{'model': name, 'status': status,
                                           'totals': tokens or self.counts(), 'windows': []}]}

    def test_shared_browser_and_python_price_examples(self):
        fixtures = json.loads((ROOT / 'tests' / 'fixtures' / 'usage-prices.json').read_text())
        self.assertGreaterEqual(len(fixtures), 14)
        for case in fixtures:
            with self.subTest(example=case['name']):
                if 'error' in case:
                    with self.assertRaisesRegex(usage.Invalid, '^' + case['error'] + '$'):
                        usage.estimate(case['data'], case['prices'])
                else:
                    self.assertEqual(usage.estimate(case['data'], case['prices']), case['expected'])

    def test_advice_thresholds_measured_branches_and_missing_controls(self):
        for tokens, expected in [(self.counts(), ['cache']),
                                 (self.counts(inp=500, cached=0, out=500), ['output']),
                                 (self.counts(inp=251, cached=749), []),
                                 (self.counts(inp=250, cached=749), []),
                                 (self.counts(inp=501, cached=0, out=499), [])]:
            with self.subTest(tokens=tokens):
                self.assertEqual([r['id'] for r in usage.advice(self.data(tokens)['models'], [], NOW)], expected)
        for status, name in [('partial', 'claude-sonnet-4-5'), ('not_measured', 'claude-sonnet-4-5'), ('measured', None)]:
            with self.subTest(status=status, name=name):
                self.assertEqual(usage.advice(self.data(status=status, name=name)['models'], [], NOW), [])

    def test_user_price_arithmetic_is_disjoint_and_has_no_default_price(self):
        data = self.data(self.counts(inp=100, cached=200, created=300, out=400, reasoning=100))
        original = deepcopy(data)
        prices = {'currency': 'EUR', 'models': {'claude-sonnet-4-5': dict(input=1, cache_read=2, cache_creation=3, output=4)}}
        self.assertIsNone(usage.estimate(data, None))
        result = usage.estimate(data, prices)
        self.assertEqual(result['status'], 'estimated')
        self.assertAlmostEqual(result['amount'], .003)
        self.assertEqual(result['currency'], 'EUR')
        self.assertEqual(data, original)
        self.assertEqual(result['excluded'], [])

    def test_price_missing_models_kinds_unknown_tokens_partial_and_zero(self):
        rates = dict(input=1, cache_read=2, cache_creation=3, output=4)
        prices = {'currency': 'USD', 'models': {'claude-sonnet-4-5': rates}}
        data = self.data(); data['models'].append({'model': 'gpt-5.4', 'status': 'measured', 'totals': self.counts()})
        result = usage.estimate(data, prices)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['excluded'], [{'model': 'gpt-5.4', 'reason': 'missing_rate'}])
        missing = deepcopy(prices); del missing['models']['claude-sonnet-4-5']['input']
        self.assertEqual(usage.estimate(self.data(), missing)['status'], 'not_measured')
        unknown = self.data(); unknown['models'][0]['model'] = None
        self.assertEqual(usage.estimate(unknown, prices)['excluded'][0]['reason'], 'unknown_model')
        unknown = self.data(); unknown['models'][0]['totals']['input'] = None
        self.assertEqual(usage.estimate(unknown, prices)['excluded'][0]['reason'], 'missing_tokens')
        zero = self.data(self.counts(inp=0, cached=0, out=0))
        self.assertEqual(usage.estimate(zero, prices)['amount'], 0)
        self.assertEqual(usage.estimate(zero, prices)['status'], 'estimated')
        self.assertEqual(usage.estimate(self.data(status='partial'), prices)['status'], 'partial')

    def test_invalid_price_tables_are_refused_with_fixed_message(self):
        good = {'currency': 'USD', 'models': {'claude-sonnet-4-5': dict(input=1, cache_read=1, cache_creation=1, output=1)}}
        invalids = [None, True, -1, float('nan'), float('inf'), CANARY]
        for value in invalids:
            with self.subTest(rate=repr(value)):
                table = deepcopy(good); table['models']['claude-sonnet-4-5']['input'] = value
                with self.assertRaisesRegex(usage.Invalid, '^price_table_invalid$'):
                    usage.estimate(self.data(), table)
        for table in [{'currency': 'US', 'models': {}}, {'currency': 'USD', 'models': [], 'secret': CANARY},
                      {'currency': 'USD', 'models': {CANARY: {'input': 1}}}]:
            with self.subTest(table=table):
                with self.assertRaisesRegex(usage.Invalid, '^price_table_invalid$'):
                    usage.estimate(self.data(), table)

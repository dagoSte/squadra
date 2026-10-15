"""Synthetic append-only usage regressions and sampled-change boundaries."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('SQ_USAGE_CANDIDATE', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / 'scripts'))
import usage

NOW = datetime(2026, 10, 15, 12, 37, tzinfo=timezone.utc).timestamp()
CLAUDE = '12345678-1234-4567-8123-123456789abc'
CODEX = '87654321-1234-4567-8123-123456789abc'
CANARY = 'PRIVATE_live_transcript_canary@example.invalid'


def utc(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace('+00:00', 'Z')


def claude(key, total, at=NOW - 1):
    return {'type': 'assistant', 'timestamp': utc(at), 'sessionId': CLAUDE,
            'requestId': 'req-' + key, 'message': {'id': key, 'model': 'claude-sonnet-4-5',
            'content': [{'type': 'text', 'text': CANARY}],
            'usage': {'input_tokens': total, 'output_tokens': 0,
                      'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}}


def codex(total, at=NOW - 1):
    return {'type': 'event_msg', 'timestamp': utc(at), 'payload': {
        'type': 'token_count', 'info': {'total_token_usage': {
            'input_tokens': total, 'output_tokens': 0, 'cached_input_tokens': 0,
            'reasoning_output_tokens': 0, 'total_tokens': total}}, 'message': CANARY}}


def line(row):
    return json.dumps(row, separators=(',', ':')).encode() + b'\n'


def neutral_bytes(minimum):
    row = line({'type': 'user', 'message': {'content': CANARY + 'x' * 32768}})
    return row * (minimum // len(row) + 1)


class UsageLive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-usage-live-test-')
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.home = self.folder / 'home'
        self.home.mkdir(mode=0o700)
        env = patch.dict(os.environ, {'HOME': str(self.home),
                                     'CODEX_HOME': str(self.home / '.codex'),
                                     'CLAUDE_CONFIG_DIR': str(self.home / '.claude')})
        env.start(); self.addCleanup(env.stop)
        self.roots = {k: self.folder / k for k in ('claude', 'codex')}
        for root in self.roots.values():
            root.mkdir(mode=0o700)
        self.events = []
        self.state = {'agents': {}, 'tasks': {}}
        usage._COLLECTORS.clear()
        self.addCleanup(usage._COLLECTORS.clear)

    def seat(self, client='claude'):
        session = CLAUDE if client == 'claude' else CODEX
        self.state['agents']['builder'] = {
            'identity': client + '-window-' + session, 'client': client, 'session': session}

    def write(self, rows, client='claude', suffix=b''):
        session = CLAUDE if client == 'claude' else CODEX
        path = self.roots[client] / (session + '.jsonl')
        path.write_bytes(b''.join(line(row) for row in rows) + suffix)
        path.chmod(0o600)
        return path

    def append(self, path, rows=(), raw=b''):
        with path.open('ab') as stream:
            stream.write(b''.join(line(row) for row in rows) + raw)

    def window(self):
        data = usage.collect(self.events, self.state, roots=self.roots, now=NOW, budget_seconds=2)
        self.assertEqual(len(data['windows']), 1)
        return data['windows'][0]

    def finish(self, source):
        for _ in range(20):
            source.read(time.monotonic() + 2, NOW)
            normalized = source.normalized()
            if 'collecting' not in normalized[2] and not source.pending:
                return normalized
        self.fail('bounded synthetic source did not catch up')

    def source(self, path, client='claude'):
        return usage.Source(path, self.roots[client], client,
                            CLAUDE if client == 'claude' else CODEX)

    @staticmethod
    def total(samples):
        return sum(sample['tokens']['total'] for sample in samples)

    def test_append_keeps_previously_measured_total_across_collection_slices(self):
        """UL6: the exact same base-API oracle binds original red and candidate green."""
        self.seat()
        path = self.write([claude('old', 1000)])
        before = self.window()
        self.assertEqual(before['status'], 'measured')
        self.assertEqual(before['totals']['total'], 1000)
        self.append(path, raw=neutral_bytes(usage.SLICE_BYTES + 65536) + line(claude('new', 7)))
        current = self.window()
        self.assertIsNotNone(current['totals'])
        self.assertGreaterEqual(current['totals']['total'], before['totals']['total'],
                                'a valid append must not erase already measured usage')
        for _ in range(20):
            current = self.window()
            if current['status'] == 'measured':
                break
        self.assertEqual(current['status'], 'measured')
        self.assertEqual(current['totals']['total'], 1007)

    def test_codex_append_after_header_preserves_verified_identity_and_progress(self):
        self.seat('codex')
        header = {'type': 'session_meta', 'payload': {'id': CODEX}}
        model = {'type': 'turn_context', 'timestamp': utc(NOW - 3),
                 'payload': {'model': 'gpt-5.4'}}
        path = self.write([header, model, codex(0, NOW - 2), codex(11)], 'codex')
        original, appended = usage.project, []

        def project_with_append(raw, client):
            result = original(raw, client)
            if result and result.get('type') == 'session' and not appended:
                appended.append(True)
                self.append(path, [codex(18)])
            return result

        with patch.object(usage, 'project', side_effect=project_with_append):
            first = self.window()
        self.assertTrue(appended, 'the deterministic header/read growth race must execute')
        self.assertNotIn('identity_unverified', first['reasons'])
        for _ in range(5):
            current = self.window()
            if current['status'] == 'measured':
                break
        self.assertEqual(current['status'], 'measured')
        self.assertEqual(current['totals']['total'], 18)

    def test_small_append_does_not_reparse_old_records_and_stays_measured(self):
        self.seat()
        path = self.write([claude('old', 13)])
        self.assertEqual(self.window()['status'], 'measured')
        self.append(path, [claude('new', 5)])
        original, parsed, reads = usage.project, [], []
        real_read = usage.os.read

        def project(raw, client):
            result = original(raw, client)
            parsed.append(raw)
            return result

        def read(fd, size):
            reads.append(size)
            return real_read(fd, size)

        with patch.object(usage, 'project', side_effect=project), patch.object(usage.os, 'read', side_effect=read):
            current = self.window()
        self.assertEqual(current['status'], 'measured')
        self.assertEqual(current['totals']['total'], 18)
        self.assertEqual(sum(b'"id":"old"' in raw for raw in parsed), 0)
        self.assertEqual(sum(b'"id":"new"' in raw for raw in parsed), 1)
        self.assertTrue(reads)
        self.assertLessEqual(max(reads), 65536)

    def test_append_during_historical_backlog_keeps_both_frontiers(self):
        path = self.write([claude('old', 19)], suffix=neutral_bytes(70000) + line(claude('tail', 23)))
        source = self.source(path)
        with patch.object(usage, 'SLICE_BYTES', 65536):
            source.read(time.monotonic() + 2, NOW)
            initial = self.total(source.normalized()[0])
            initial_keys = {row['key'] for row in source.normalized()[0]}
            self.assertGreater(initial, 0)
            self.assertGreater(source.cursor, 0)
            old_cursor = source.cursor
            self.append(path, raw=neutral_bytes(70000) + line(claude('appended', 29)))
            source.read(time.monotonic() + 2, NOW)
            self.assertGreaterEqual(self.total(source.normalized()[0]), initial)
            self.assertTrue(initial_keys <= {row['key'] for row in source.normalized()[0]},
                            'new appends must retain samples from an unfinished historical scan')
            self.assertLessEqual(source.cursor, old_cursor,
                                 'an append must not restart the historical cursor')
            samples, _, reasons = self.finish(source)
        self.assertEqual(self.total(samples), 71)
        self.assertNotIn('collecting', reasons)

    def test_large_completed_source_append_reads_only_samples_and_new_bytes(self):
        path = self.write([claude('old', 43)], suffix=neutral_bytes(3 * 1024 * 1024))
        source = self.source(path)
        self.assertEqual(self.total(self.finish(source)[0]), 43)
        appended = line(claude('new', 7))
        self.append(path, raw=appended)
        original, read_bytes, parsed = usage.os.read, [], []
        project = usage.project

        def read(fd, count):
            data = original(fd, count)
            read_bytes.append(len(data))
            self.assertLessEqual(count, 65536)
            return data

        def record(raw, client):
            parsed.append(raw)
            return project(raw, client)

        with patch.object(usage.os, 'read', side_effect=read), patch.object(usage, 'project', side_effect=record):
            samples, _, reasons = self.finish(source)
        self.assertEqual(self.total(samples), 50)
        self.assertNotIn('collecting', reasons)
        self.assertLessEqual(sum(read_bytes), 4 * 65536 + len(appended),
                             'bounded pre/post fingerprint samples may not become a full-prefix read')
        self.assertEqual(len(parsed), 1)
        self.assertNotIn(b'"id":"old"', parsed[0])

    def test_unchanged_source_does_not_reparse_or_read_payload(self):
        path = self.write([claude('old', 41)])
        source = self.source(path)
        self.assertEqual(self.total(self.finish(source)[0]), 41)
        with patch.object(usage, 'project', wraps=usage.project) as parser, \
                patch.object(usage.os, 'read', wraps=usage.os.read) as reader:
            samples, _, _ = self.finish(source)
        self.assertEqual(self.total(samples), 41)
        self.assertEqual(parser.call_count, 0)
        self.assertEqual(reader.call_count, 0)

    def test_partial_line_completes_exactly_once_without_losing_previous_usage(self):
        self.seat()
        path = self.write([claude('old', 17)])
        self.assertEqual(self.window()['totals']['total'], 17)
        new = line(claude('partial', 31))
        self.append(path, raw=new[:len(new) // 2])
        incomplete = self.window()
        self.assertEqual(incomplete['totals']['total'], 17)
        self.assertIn('incomplete_record', incomplete['reasons'])
        self.append(path, raw=new[len(new) // 2:])
        for _ in range(2):
            current = self.window()
            self.assertEqual(current['totals']['total'], 48)
            self.assertEqual(current['status'], 'measured')

    def test_sampled_head_and_previous_end_rewrites_invalidate_without_max_clamp(self):
        for position in ('head', 'tail'):
            with self.subTest(position=position):
                first = line(claude('head', 79))
                last = line(claude('tail', 83))
                path = self.write([], suffix=first + neutral_bytes(150000) + last)
                source = self.source(path)
                self.assertEqual(self.total(self.finish(source)[0]), 162)
                data = path.read_bytes()
                old, new = (b'"input_tokens":79', b'"input_tokens":11') if position == 'head' else (
                    b'"input_tokens":83', b'"input_tokens":13')
                self.assertEqual(data.count(old), 1)
                path.write_bytes(data.replace(old, new))
                self.assertEqual(self.total(self.finish(source)[0]), 94 if position == 'head' else 92)

    def test_same_length_middle_rewrite_is_the_declared_sampling_limit(self):
        path = self.write([], suffix=neutral_bytes(150000) + line(claude('middle', 79)) + neutral_bytes(150000))
        source = self.source(path)
        self.assertEqual(self.total(self.finish(source)[0]), 79)
        data = path.read_bytes()
        offset = data.index(b'"input_tokens":79')
        self.assertGreater(offset, 65536)
        self.assertLess(offset + 20, len(data) - 65536)
        path.write_bytes(data.replace(b'"input_tokens":79', b'"input_tokens":11'))
        self.assertEqual(self.total(self.finish(source)[0]), 79,
                         'sampling intentionally cannot detect a same-length middle rewrite')
        replacement = path.with_suffix('.new')
        replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
        os.replace(replacement, path)
        self.assertEqual(self.total(self.finish(source)[0]), 11)

    def test_truncation_and_replacement_discard_old_contributions(self):
        path = self.write([claude('old', 91), claude('extra', 7)])
        source = self.source(path)
        self.assertEqual(self.total(self.finish(source)[0]), 98)
        path.write_bytes(line(claude('new', 5)))
        self.assertEqual(self.total(self.finish(source)[0]), 5)
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(line(claude('replacement', 3))); replacement.chmod(0o600)
        os.replace(replacement, path)
        self.assertEqual(self.total(self.finish(source)[0]), 3)

    def test_duplicate_conflict_remains_explicit_instead_of_clamping_totals(self):
        self.seat()
        path = self.write([claude('shared', 21), claude('safe', 5)])
        self.assertEqual(self.window()['totals']['total'], 26)
        self.append(path, [claude('shared', 3)])
        current = self.window()
        self.assertEqual(current['totals']['total'], 5)
        self.assertIn('duplicate_conflict', current['reasons'])
        self.assertEqual(current['status'], 'partial')

    def test_codex_reset_and_baseline_keep_their_existing_truthful_semantics(self):
        path = self.write([{'type': 'session_meta', 'payload': {'id': CODEX}},
                           codex(0, NOW - 5), codex(21, NOW - 4)], 'codex')
        source = self.source(path, 'codex')
        self.assertEqual(self.total(self.finish(source)[0]), 21)
        self.append(path, [codex(3, NOW - 3), codex(8, NOW - 2)])
        samples, _, reasons = self.finish(source)
        self.assertEqual(self.total(samples), 26)
        self.assertIn('counter_reset', reasons)


if __name__ == '__main__':
    unittest.main()

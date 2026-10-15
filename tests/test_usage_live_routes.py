"""Usage conditional envelopes retain the authenticated read-only HTTP boundary."""
import json
import unittest
from unittest.mock import patch

import test_usage_routes as routes


class UsageLiveRoutes(unittest.TestCase):
    setUp = routes.UsageRoutes.setUp
    start_server = routes.UsageRoutes.start_server
    get = routes.UsageRoutes.get
    frozen = routes.UsageRoutes.frozen

    def test_usage_conditional_headers_are_forwarded_only_after_authentication(self):
        server, token = self.start_server()
        revision = 'a' * 64
        reply = {'schema': 1, 'unchanged': True, 'revision': revision}
        with patch.object(routes.usage, 'response', return_value=reply, create=True) as adapter:
            self.assertEqual(self.get(server, 'wrong', **{'X-Squadra-Usage-Revision': revision})[0], 401)
            self.assertEqual(self.get(server, token, Host='evil.invalid')[0], 403)
            adapter.assert_not_called()
            status, value, headers = self.get(server, token, **{
                'X-Squadra-Usage-Revision': revision, 'X-Squadra-Usage-View': 'compact'})
            self.assertEqual(status, 200)
            self.assertEqual(value, reply)
            self.assertLessEqual(len(json.dumps(value).encode()), 256)
            self.assertEqual(adapter.call_args.kwargs['revision'], revision)
            self.assertIs(adapter.call_args.kwargs['compact'], True)
            self.assertIn('no-store', headers['Cache-Control'])
            self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])

    def test_usage_default_keeps_full_shape_and_busy_is_small(self):
        server, token = self.start_server()
        reply = {'schema': 1, 'busy': True}
        with patch.object(routes.usage, 'response', return_value=reply, create=True) as adapter:
            status, value, _ = self.get(server, token, **{'X-Squadra-Prices': routes.CANARY})
            self.assertEqual(status, 200)
            self.assertEqual(value, reply)
            self.assertLessEqual(len(json.dumps(value).encode()), 256)
            self.assertIsNone(adapter.call_args.kwargs['revision'])
            self.assertIs(adapter.call_args.kwargs['compact'], False)
            self.assertNotIn(routes.CANARY, repr(adapter.call_args))

    def test_usage_real_full_compact_parity_unchanged_and_busy_envelopes(self):
        server, token = self.start_server()
        now = routes.usage.instant('2026-10-15T11:00:00Z')
        with patch.object(routes.usage.time, 'time', return_value=now):
            status, full, _ = self.get(server, token)
            self.assertEqual(status, 200)
            self.assertIn('hourly', full['windows'][-1])
            self.assertIn('rolling', full['models'][0])
            status, compact, _ = self.get(server, token, **{'X-Squadra-Usage-View': 'compact'})
            self.assertEqual(status, 200)
            for field in ('schema', 'observed_at', 'status', 'coverage', 'days', 'tasks',
                          'quota_history', 'suggestions', 'cost'):
                self.assertEqual(full[field], compact[field], field)
            for original, reduced in zip(full['windows'], compact['windows'], strict=True):
                for field in ('identity', 'actors', 'client', 'status', 'totals', 'reasons', 'quota'):
                    self.assertEqual(original[field], reduced[field], field)
            for original, reduced in zip(full['models'], compact['models'], strict=True):
                for field in ('model', 'status', 'totals'):
                    self.assertEqual(original[field], reduced[field], field)
            self.assertLess(len(json.dumps(compact)), len(json.dumps(full)))
            request = {'X-Squadra-Usage-View': 'compact', 'X-Squadra-Usage-Revision': compact['revision']}
            status, unchanged, _ = self.get(server, token, **request)
            self.assertEqual(status, 200)
            self.assertEqual(unchanged, {'schema': 1, 'unchanged': True, 'revision': compact['revision']})
            self.assertLessEqual(len(json.dumps(unchanged).encode()), 256)
            collector = next(iter(routes.usage._COLLECTORS.values()))
            with collector.lock:
                status, busy, _ = self.get(server, token, **request)
            self.assertEqual(status, 200)
            self.assertEqual(busy, {'schema': 1, 'busy': True})
            self.assertEqual(self.get(server, token, **request)[1], unchanged)

    def test_usage_source_append_advances_its_token_without_registry_events(self):
        server, token = self.start_server()
        before = self.frozen()
        now = routes.usage.instant('2026-10-15T11:00:00Z')
        with patch.object(routes.usage.time, 'time', return_value=now):
            status, first, _ = self.get(server, token, **{'X-Squadra-Usage-View': 'compact'})
            self.assertEqual(status, 200)
            with self.source.open('a') as stream:
                stream.write(json.dumps({'type': 'assistant', 'sessionId': routes.SESSION,
                    'timestamp': '2026-10-15T10:15:00Z', 'message': {
                        'id': 'message-2', 'model': 'claude-sonnet-4-5', 'content': routes.CANARY,
                        'usage': {'input_tokens': 5, 'output_tokens': 2}}}) + '\n')
            status, updated, _ = self.get(server, token, **{
                'X-Squadra-Usage-View': 'compact', 'X-Squadra-Usage-Revision': first['revision']})
            self.assertEqual(status, 200)
            self.assertNotEqual(updated['revision'], first['revision'])
            self.assertEqual(updated['windows'][-1]['totals']['total'], 982)
            self.assertNotIn(routes.CANARY, json.dumps(updated))
        self.assertEqual(self.frozen(), before)

"""Run the published consumer itself across cancellation and equal-content replacement."""
import fcntl
import json
import os
from unittest.mock import patch
import unittest

import test_plugin_authoring_boundary as guide


class GuideRequestIdentity(unittest.TestCase):
    def exercise(self, change):
        with guide.GuideBoundary().example() as (home, folder, registry, key, consumer):
            request = folder / 'actions/send.json'
            before = request.read_bytes()
            def deliver(*args):
                with (folder / 'actions/send.lock').open('rb') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    change(request, before)
            with patch.object(consumer, 'deliver', side_effect=deliver):
                reply = consumer.consume(registry)
            return reply, request.read_bytes() if request.exists() else None

    def test_literal_consumer_keeps_identical_replacement(self):
        def replace(path, body):
            temporary = path.with_suffix('.new')
            temporary.write_bytes(body); temporary.chmod(0o600)
            os.replace(temporary, path)
        reply, body = self.exercise(replace)
        self.assertEqual(reply, 'sent; a newer choice stays pending')
        self.assertIsNotNone(body)

    def test_literal_consumer_keeps_cancel_and_identical_reselection(self):
        def replace(path, body):
            path.unlink(); path.write_bytes(body); path.chmod(0o600)
        reply, body = self.exercise(replace)
        self.assertEqual(reply, 'sent; a newer choice stays pending')
        self.assertIsNotNone(body)

    def test_literal_consumer_preserves_cancellation_and_changed_selection(self):
        reply, body = self.exercise(lambda p, b: p.unlink())
        self.assertEqual(reply, 'sent; the request had already been cancelled')
        self.assertIsNone(body)
        def changed(path, body):
            value = json.loads(body); value['ids'] = ['note-103']
            path.write_text(json.dumps(value))
        reply, body = self.exercise(changed)
        self.assertEqual(reply, 'sent; a newer choice stays pending')
        self.assertEqual(json.loads(body)['ids'], ['note-103'])

    def test_literal_consumer_clears_unchanged_request(self):
        reply, body = self.exercise(lambda p, b: None)
        self.assertEqual(reply, 'sent; request cleared')
        self.assertIsNone(body)

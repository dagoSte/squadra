"""Atomic replacement remains retryable without accepting unsafe plugin files."""
import json
import os
import queue
import threading
import time
import unittest
from unittest.mock import patch

import test_plugin_settings as support
import plugin_settings as settings
import plugins


class Race(support.SettingsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / 'codex'),
                         CLAUDE_CONFIG_DIR=str(self.home / 'claude'))
        env.start(); self.addCleanup(env.stop)
        self.save({})

    def replace_open(self, mode=0o600):
        path = self.base / 'settings.json'
        path.chmod(mode)
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        replacement = self.base / 'replacement.json'
        replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
        os.replace(replacement, path)
        self.assertEqual(os.fstat(fd).st_nlink, 0)
        return fd

    def test_replaced_private_fd_is_busy_and_current_file_is_safe(self):
        fd = self.replace_open()
        self.error(lambda: settings.private(fd), 'busy')
        current = os.open(self.base / 'settings.json', os.O_RDONLY)
        try:
            settings.private(current)
        finally:
            os.close(current)

    def test_real_unsafe_files_still_refuse_before_transient_busy(self):
        self.error(lambda: settings.private(self.replace_open(0o660)), 'unsafe_permissions')
        fd = self.replace_open()
        actual_uid = os.getuid()
        with patch.object(settings.os, 'getuid', return_value=actual_uid + 1):
            self.error(lambda: settings.private(fd), 'unsafe_permissions')
        path = self.base / 'settings.json'
        os.link(path, self.base / 'hardlink.json')
        linked = os.open(path, os.O_RDONLY)
        try:
            self.assertEqual(os.fstat(linked).st_nlink, 2)
            self.error(lambda: settings.private(linked), 'unsafe_permissions')
        finally:
            os.close(linked)

    def test_current_reopens_only_replaced_snapshots_with_a_three_attempt_bound(self):
        original_open = plugins._open
        parent = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent)
        for replacements in (0, 1, 2, 3):
            with self.subTest(replacements=replacements):
                self.save({'label': 'before'})
                opens = []

                def opened(directory, name, directory_flag=False):
                    fd = original_open(directory, name, directory_flag)
                    if name == 'settings.json':
                        opens.append(fd)
                        if len(opens) <= replacements:
                            path = self.base / name
                            saved = json.loads(path.read_bytes())
                            saved['values']['label'] = 'replacement-' + str(len(opens))
                            temp = self.base / 'replacement.json'
                            temp.write_text(json.dumps(saved)); temp.chmod(0o600)
                            os.replace(temp, path)
                            self.assertEqual(os.fstat(fd).st_nlink, 0)
                    return fd

                with patch.object(plugins, '_open', side_effect=opened):
                    try:
                        values, _ = settings.current(parent, support.declared()['settings'])
                        outcome = ('ok', values['label'])
                    except plugins.Refused as exc:
                        outcome = ('refused', exc.code)
                expected = ('refused', 'busy') if replacements == 3 else (
                    'ok', 'replacement-' + str(replacements) if replacements else 'before')
                self.assertEqual(outcome, expected)
                self.assertEqual(len(opens), min(replacements + 1, 3))

        (self.base / 'settings.json').chmod(0o660)
        with patch.object(plugins, '_open', wraps=original_open) as opened:
            self.error(lambda: settings.current(parent, support.declared()['settings']), 'unsafe_permissions')
        self.assertEqual(opened.call_count, 1)

    def test_bounded_concurrent_reads_and_preflight_writes_survive_replacement(self):
        deadline = time.monotonic() + 20
        stop, errors = threading.Event(), queue.Queue()
        original_open = plugins._open
        gate = {}

        def retry(call):
            for _ in range(500):
                try:
                    return call()
                except plugins.Refused as exc:
                    if exc.code != 'busy':
                        raise
                if time.monotonic() >= deadline:
                    break
                stop.wait(.001)
            raise AssertionError('bounded busy retries exhausted')

        def opened(parent, name, directory=False):
            fd = original_open(parent, name, directory)
            current = gate.get(threading.current_thread().name)
            if name == 'settings.json' and current and not current['used'].is_set():
                current['used'].set(); current['opened'].set()
                if not current['replaced'].wait(max(0, deadline - time.monotonic())):
                    os.close(fd)
                    raise AssertionError('writer did not complete the forced interleaving')
            return fd

        def reader():
            try:
                while not stop.is_set() and time.monotonic() < deadline:
                    retry(self.read)
                    stop.wait(.001)
            except BaseException as exc:
                errors.put(exc)

        def writer(value):
            try:
                retry(lambda: self.save({'label': value}))
            except BaseException as exc:
                errors.put(exc)

        readers = [threading.Thread(target=reader, name='reader-' + str(i)) for i in range(2)]
        writers = []
        try:
            with patch.object(plugins, '_open', side_effect=opened):
                for thread in readers:
                    thread.start()
                for i in range(12):
                    name = 'preflight-' + str(i)
                    entry = {key: threading.Event() for key in ('used', 'opened', 'replaced')}
                    gate[name] = entry
                    thread = threading.Thread(target=writer, name=name, args=('writer-' + str(i),))
                    writers.append(thread); thread.start()
                    try:
                        self.assertTrue(entry['opened'].wait(max(0, deadline - time.monotonic())),
                                        'the preflight reader must really open settings.json')
                        address = 'https://example.invalid/' + str(i)
                        retry(lambda: self.save({'address': address}))
                    finally:
                        entry['replaced'].set()
                    thread.join(max(0, deadline - time.monotonic()))
                    self.assertFalse(thread.is_alive(), 'owned writer must finish within the bound')
                    self.assertTrue(errors.empty(), list(errors.queue))
                    values = retry(self.read)
                    self.assertEqual((values[0]['value'], values[2]['value']), (address, 'writer-' + str(i)))
        finally:
            stop.set()
            for entry in gate.values():
                entry['replaced'].set()
            for thread in readers + writers:
                if thread.ident is not None:
                    thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in readers + writers))
        self.assertTrue(errors.empty(), list(errors.queue))
        self.assertLess(time.monotonic(), deadline)


if __name__ == '__main__':
    unittest.main()

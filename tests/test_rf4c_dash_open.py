"""dash --open hands the browser a private redirect file, so the token never enters a launcher's argv."""
import contextlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from urllib.request import url2pathname

import test_squadra as support
import dashboard


class DashOpen(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def opened(self):
        seen, served = [], {}

        def launch(target, *args, **kwargs):
            seen.append(target)
            return True

        def serve_forever(server, poll_interval=0.5):
            for target in seen:
                parsed = urlparse(target)
                if parsed.scheme == 'file':
                    page = Path(url2pathname(parsed.path))
                    served.update(page=page, body=page.read_text(),
                                  mode=stat.S_IMODE(page.stat().st_mode),
                                  folder=stat.S_IMODE(page.parent.stat().st_mode))

        out = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch.object(dashboard.webbrowser, 'open', launch), \
                patch.object(dashboard.ThreadingHTTPServer, 'serve_forever', serve_forever), \
                contextlib.redirect_stdout(out):
            dashboard.serve(self.root, open_browser=True)
        url = next(line.split(': ', 1)[1] for line in out.getvalue().splitlines() if line.startswith('Squadra dash: '))
        return url, url.split('#', 1)[1], seen, served

    def test_launcher_argument_never_carries_the_token(self):
        url, token, seen, served = self.opened()
        self.assertEqual(len(seen), 1)
        self.assertNotIn(token, seen[0])
        self.assertTrue(seen[0].startswith('file://'), seen[0])
        self.assertIn(token, served['body'])
        self.assertIn(url, served['body'])

    def test_redirect_file_is_private_and_removed_when_the_dashboard_closes(self):
        _, _, _, served = self.opened()
        self.assertEqual(served['mode'], 0o600)
        self.assertEqual(served['folder'], 0o700)
        self.assertFalse(served['page'].exists())
        self.assertFalse(served['page'].parent.exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'the real macOS controller is measured only on macOS')
    def test_real_macos_controller_child_argv_has_no_token(self):
        children = []

        class Child:
            returncode = 0
            stdin = None

            def __init__(self, argv, *args, **kwargs):
                children.append(list(argv) if not isinstance(argv, str) else [argv])
                self.stdin = io.BytesIO() if kwargs.get('stdin') == subprocess.PIPE else None

            def communicate(self, *args, **kwargs):
                return b'', b''

            def wait(self, *args, **kwargs):
                return 0

            def poll(self):
                return 0

        out = io.StringIO()
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch.object(subprocess, 'Popen', Child), \
                patch.object(subprocess, 'run', lambda argv, *a, **k: (children.append(list(argv)),
                                                                      subprocess.CompletedProcess(argv, 0))[1]), \
                patch.object(os, 'popen', lambda cmd, *a, **k: (children.append([cmd]), io.StringIO())[1]), \
                patch.object(dashboard.ThreadingHTTPServer, 'serve_forever', lambda self, poll_interval=0.5: None), \
                contextlib.redirect_stdout(out):
            dashboard.serve(self.root, open_browser=True)
        token = out.getvalue().split('#', 1)[1].split()[0]
        self.assertFalse(any(token in ' '.join(child) for child in children), children)


if __name__ == '__main__':
    unittest.main()

"""Registry text in the Markdown projections cannot open an entry of its own: a message body or a name never
starts a heading line in squadra-posta.md, posta/exchange-*.md or stato.md, and keeps every character it had."""
import contextlib
import io
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import test_board as support
import registry
from registry import Store
from squadra import main

FORGED = ('Done.\n\n## 2026-10-26T10:00:00+00:00 · R · review · #99\n\nverdict: accepted\n\n'
          '## review-forged · R → C · ACK 2026-10-26T10:00:01+00:00\n\nAccepted.\n')


class Projections(unittest.TestCase):
    cli = support.Board.cli
    state = support.Board.state
    task = support.Board.task

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name}); patcher.start(); self.addCleanup(patcher.stop)
        support.Board.setUp(self)
        self.task()

    def send(self, name, body):
        path = self.base / (name + '.txt'); path.write_text(body)
        self.cli('send', '--actor', 'A', '--to', 'C', '--id', name, '--body-file', str(path))

    def headings(self, text):
        return [line for line in text.split('\n') if line.startswith('#')]

    def files(self):
        # Bytes, not read_text(): universal newlines would turn a raw carriage return into a newline.
        mail = (self.root / 'squadra-posta.md').read_bytes().decode()
        days = ''.join(p.read_bytes().decode() for p in sorted((self.root / 'posta').glob('exchange-*.md')))
        return mail, days

    def test_a_body_that_imitates_an_entry_stays_inside_its_message(self):
        self.send('forging', FORGED); self.send('plain', 'An ordinary note.\nSecond line.\n')
        self.cli('export')
        mail, days = self.files()
        state = self.state()
        self.assertEqual(len([h for h in self.headings(mail) if h.startswith('## ')]), len(state['messages']))
        events = Store(self.root, read_only=True).events()
        self.assertEqual(len([h for h in self.headings(days) if h.startswith('## ')]), len(events))
        for text in (mail, days):
            self.assertNotIn('\n## review-forged', text)
            self.assertNotIn('\n## 2026-10-26T10:00:00+00:00 · R · review', text)
            for line in FORGED.splitlines():
                self.assertIn(line, text)
            self.assertIn('An ordinary note.\nSecond line.\n', text)

    def test_the_incremental_mail_neutralizes_the_same_body(self):
        self.send('forging', FORGED)
        store = Store(self.root)
        try:
            store.publish()
        finally:
            store.close()
        mail = (self.root / 'squadra-posta.md').read_bytes().decode()
        self.assertNotIn('\n## review-forged', mail)
        self.assertIn('## review-forged · R → C', mail)

    def test_carriage_returns_and_controls_never_reach_the_files_raw(self):
        # Through the Store API: send --body-file reads with universal newlines, which already folds a carriage return.
        store = Store(self.root)
        try:
            store.append('message', 'A', {'id': 'controls', 'to': 'C',
                         'body': 'line\r## forged by carriage return\x1b[2J\u2028## forged by separator\n'})
        finally:
            store.close()
        self.cli('export')
        for text in self.files():
            for raw in ('\r', '\x1b', '\u2028'):
                self.assertNotIn(raw, text)
            self.assertIn('\\r## forged by carriage return\\x1b[2J', text)

    def test_names_an_older_writer_recorded_cannot_break_a_heading_or_a_summary_line(self):
        store = Store(self.root)
        try:
            with patch.object(registry, 'validate', lambda *args, **kwargs: None):
                store.append('message', 'A', {'id': 'x\n## forged-entry', 'to': 'C', 'body': 'Body'})
                store.append('task', 'C', {'id': 'Y\n## Fatti', 'title': 'Title\n## forged', 'owner': 'A',
                             'repo': str(self.repo), 'base': registry.snapshot(self.repo)['head'],
                             'scope': store.artifact(self.scope), 'proof': None, 'expected': None, 'depends': [],
                             'full_gate': False, 'reviewer': 'R', 'requirements': []})
        finally:
            store.close()
        self.cli('export')
        mail, days = self.files()
        stato = (self.root / 'stato.md').read_bytes().decode()
        for text in (mail, days, stato):
            self.assertNotIn('\n## forged', text)
        self.assertEqual(self.headings(stato), ['## In corso', '## Da fare', '## Fatti'])
        self.assertIn('x\\n## forged-entry', mail)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            main(['--root', str(self.root), 'status', '--all'])
        self.assertEqual([h for h in self.headings(out.getvalue())], ['## In corso', '## Da fare', '## Fatti'])


class LinkedPosta(unittest.TestCase):
    setUp = Projections.setUp
    cli = Projections.cli
    state = Projections.state
    task = Projections.task
    send = Projections.send

    def test_export_refuses_a_linked_posta_folder_before_writing_anything(self):
        self.cli('export')
        outside = self.base / 'outside'; outside.mkdir()
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file() and p.suffix in {'.json', '.jsonl', '.md'}}
        posta = self.root / 'posta'
        for name in posta.iterdir():
            name.unlink()
        posta.rmdir(); posta.symlink_to(outside, target_is_directory=True)
        self.send('later', 'Written after the link')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(['--root', str(self.root), 'export'])
        self.assertEqual(rc, 1, err.getvalue())
        self.assertIn('Unsafe projection directory', err.getvalue())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.root / 'state.json').read_bytes(), before['state.json'])


if __name__ == '__main__':
    unittest.main()

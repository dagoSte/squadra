"""Lesson listing preserves persistent bytes; lesson mutations still publish."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_errata as support
import squadra
from registry import Error


class LessonReadOnly(support.Fixture, unittest.TestCase):
    def inventory(self):
        return {str(path.relative_to(self.root)):
                (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns,
                 path.stat().st_mode & 0o777)
                for path in self.root.rglob('*')
                if path.is_file() and not path.name.endswith('-shm')}

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / 'codex'),
                        CLAUDE_CONFIG_DIR=str(self.home / 'claude')), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = squadra.main(['--root', str(self.root), 'lesson', *args])
        return result, out.getvalue(), err.getvalue()

    def test_listing_existing_lessons_preserves_events_files_and_mtimes(self):
        self.lesson('L0001', paths=['scripts/*.py'])
        events, files = self.store.events(), self.inventory()
        result, out, err = self.invoke('--list')
        self.assertEqual(result, 0, err)
        listing, _ = json.JSONDecoder().raw_decode(out)
        self.assertEqual([item['id'] for item in listing['lessons']], ['L0001'])
        self.assertEqual(self.store.events(), events)
        self.assertEqual(self.inventory(), files)

    def test_failed_listing_records_no_diagnostic_or_projection(self):
        self.lesson('L0001', paths=['scripts/*.py'])
        events, files = self.store.events(), self.inventory()
        with patch.object(squadra, 'lesson', side_effect=Error('Synthetic listing refusal')):
            result, _, err = self.invoke('--list')
        self.assertEqual(result, 1)
        self.assertIn('Synthetic listing refusal', err)
        self.assertEqual(self.store.events(), events)
        self.assertEqual(self.inventory(), files)

    def test_retiring_a_lesson_keeps_its_write_and_publication(self):
        self.lesson('L0001', paths=['scripts/*.py'])
        before = self.store.events()
        result, _, err = self.invoke('--actor', 'C', '--retire', 'L0001', '--reason', 'Replaced')
        self.assertEqual(result, 0, err)
        events = self.store.events()
        self.assertEqual(len(events), len(before) + 1)
        self.assertEqual(events[-1]['kind'], 'lesson.retire')
        self.assertTrue(self.store.state()['lessons']['L0001']['retired'])
        exported = [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(exported, events)

    def test_full_verification_tables_explicitly_include_export(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('SKILL.md', 'README.md', 'README.it.md', 'references/recovery.md'):
            with self.subTest(document=name):
                rows = [line for line in (root / name).read_text().splitlines()
                        if line.startswith('|') and '`doctor`' in line and '`restore-check`' in line]
                self.assertEqual(len(rows), 1)
                self.assertIn('`export`', rows[0])


if __name__ == '__main__':
    unittest.main()

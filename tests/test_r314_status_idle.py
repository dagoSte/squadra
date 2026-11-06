"""status lists accepted assignments without recorded work (P4), like doctor --resume."""
import contextlib
import datetime as dt
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import squadra
import test_squadra as support


class StatusIdle(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    ack = support.Workflow.ack

    def status(self, at):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(squadra, 'now', return_value=at), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = squadra.main(['--root', str(self.root), 'status'])
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def test_status_names_an_accepted_idle_assignment_only_after_the_threshold(self):
        self.ack()
        accepted = dt.datetime.fromisoformat(self.store.view()['messages']['assign-T']['ack']['utc'])
        self.store.release_views()
        self.assertNotIn('Accettati ma fermi', self.status((accepted + dt.timedelta(minutes=29)).isoformat()))
        late = self.status((accepted + dt.timedelta(minutes=31)).isoformat())
        self.assertIn('Accettati ma fermi', late)
        self.assertIn('- T · A · 31 min', late)


if __name__ == '__main__':
    unittest.main()

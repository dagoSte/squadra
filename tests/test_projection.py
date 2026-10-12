"""Check generated file bytes and the state returned to the CLI."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from registry import Store, canonical, initial, apply, table  # noqa: E402
from squadra import main  # noqa: E402
from test_supersede import new_store, add_task  # noqa: E402


def expected_mail(state):
    text = "# Squadra — posta condivisa\n\nRegistro SQLite autorevole; consegna e ACK sono distinti.\n"
    for m in state["messages"].values():
        ack = m["ack"]["utc"] if m["ack"] else "PENDING"
        text += f"\n## {m['id']} · {m['from']} → {m['to']} · ACK {ack}\n\n{m['body']}\n"
    return text


def expected_days(events):
    days = {}
    for e in events:
        day = e["utc"][:10]
        days.setdefault(day, "# Squadra — scambi del giorno UTC\n\nProiezione ricostruibile del registro comune; non una seconda fonte.\n")
        days[day] += f"\n## {e['utc']} · {e['actor']} · {e['kind']} · evento {e['seq']}\n\n```json\n{canonical(e['data'])}\n```\n"
    return days


class Projection(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="squadra-projection-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "record"
        self.store = new_store(self.root, self.base)
        self.addCleanup(lambda: self.store.close())
        for name in ("T", "U"):
            add_task(self.store, self.base, name)
        self.store.append("ack", "A", {"message": "assign-T"})
        # Use a large payload like a delivery source fingerprint.
        self.store.append("message", "C", {"id": "big", "to": "A", "task": "U", "body": "x" * 200_000})

    def test_mail_and_daily_files_keep_their_bytes(self):
        self.store.project()
        events = self.store.events()
        state = initial()
        for event in events:
            apply(state, event)
        self.assertEqual((self.root / "squadra-posta.md").read_bytes(), expected_mail(state).encode())
        days = expected_days(events)
        self.assertTrue(days)
        written = sorted(p.name for p in (self.root / "posta").iterdir())
        self.assertEqual(written, sorted("exchange-" + d + ".md" for d in days))
        for day, body in days.items():
            self.assertEqual((self.root / "posta" / ("exchange-" + day + ".md")).read_bytes(), body.encode())

    def test_project_returns_the_state_it_wrote(self):
        state = self.store.project()
        folded = initial()
        for event in self.store.events():
            apply(folded, event)
        self.assertEqual(canonical(state), canonical(folded))
        self.assertEqual((self.root / "state.json").read_bytes(), (canonical(state) + "\n").encode())
        self.assertNotIn("root", state)

    def test_cli_table_uses_the_projected_state(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "status"]), 0)
        printed = out.getvalue()
        reference = table(self.store.state(), read_at="1970-01-01T00:00:00+00:00")
        # Only the read-time lines may differ between the two renderings.
        def stable(text):
            return [line for line in text.splitlines() if "1970-01-01" not in line and "Attese calcolate" not in line
                    and not line.startswith("- ack ·")]
        for line in stable(reference):
            self.assertIn(line, printed)


if __name__ == "__main__":
    unittest.main()

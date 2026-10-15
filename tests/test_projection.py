"""Check generated file bytes and the state returned to the CLI."""
import contextlib
import io
import json
from pathlib import Path
import re
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
        days.setdefault(day, []).append(e)
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
        exported = [json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()]
        self.assertEqual(exported, events, "Every complete payload remains available in the verified raw export")
        for day, day_events in days.items():
            body = (self.root / "posta" / ("exchange-" + day + ".md")).read_text()
            sections = body.split("\n## ")[1:]
            self.assertEqual(len(sections), len(day_events), "No event is omitted or duplicated in the readable day")
            self.assertEqual([int(n) for n in re.findall(r" · #(\d+)\n", body)], [e["seq"] for e in day_events])
            for event, section in zip(day_events, sections):
                self.assertTrue(section.startswith(f"{event['utc']} · {event['actor']} · {event['kind']} · #{event['seq']}\n"))
                self.assertIn(f"events.jsonl · #{event['seq']} · SHA-256 {event['hash']}", section)
                self.assertEqual(exported[event["seq"] - 1], event, "The link recovers the exact complete event")
                for key in ("body", "text", "reason"):
                    text = event["data"].get(key)
                    if isinstance(text, str):
                        if len(text.encode()) <= 4096:
                            self.assertIn(text, section)
                        else:
                            self.assertIn(text[:512], section)
                            self.assertNotIn(text, section, "Large bodies are referenced instead of duplicated")
                            self.assertIn("Full text in source event", section)
                if event["kind"] == "ack":
                    self.assertIn(event["data"]["message"], section)
            self.assertLess(len(body.encode()), 100_000, "The 200 KB message must not be copied into its daily summary")

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
            self.assertEqual(main(["--root", str(self.root), "status", "--all"]), 0)
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

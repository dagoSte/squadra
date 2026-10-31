"""R312: after a stand-by the composition advice follows the real builder and reader roles, and keeps the pairs.

Observed in R3112-PROVA: with claude-rf4a in stand-by, the only Claude window was a builder and both Codex windows
were readers, yet status said the lone Claude window would read what the Codex windows build, and the pairing lines
of the open tasks disappeared."""
import unittest

from registry import composition, composition_lines
from test_composition import COORD, squad


def observed_squad():
    state = squad(COORD, ("rf4a", "builder", "claude"), ("b1", "builder", "claude"),
                  ("x1", "reviewer", "codex"), ("x2", "reviewer", "codex"))
    state["tasks"] = {
        "T-NOREAD": {"id": "T-NOREAD", "owner": "b1", "reviewer": None, "status": "waiting-ack"},
        "T-READ": {"id": "T-READ", "owner": "b1", "reviewer": "x2", "status": "working"},
        "T-ALT": {"id": "T-ALT", "owner": "b1", "reviewer": "x2", "status": "waiting-ack"}}
    return state


class AfterStandby(unittest.TestCase):
    def test_before_the_stand_by_the_squad_is_balanced_and_paired(self):
        value = composition(observed_squad())
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("balanced", 2, 2))
        self.assertEqual([p["task"] for p in value["pairs"]], ["T-ALT", "T-READ", "T-NOREAD"])

    def test_lone_claude_builder_read_by_two_codex_readers_is_not_called_a_reader(self):
        state = observed_squad()
        state["agents"]["rf4a"]["availability"] = {"status": "stand-by"}
        value = composition(state)
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("imbalance", 1, 2))
        self.assertNotIn("rileggerà", value["message"])
        self.assertEqual(value["message"], "Squilibrio: 1 Claude e 2 Codex. Costruiscono: 1 Claude; rileggono: 2 "
                         "Codex. Abbina a ogni costruttore un lettore dell'altro modello.")
        self.assertEqual(composition(state, lang="en")["message"],
                         "Imbalance: 1 Claude and 2 Codex. Building: 1 Claude; reading: 2 Codex. Pair each builder "
                         "with a reader of the other model.")

    def test_the_pairs_of_open_tasks_stay_after_the_stand_by(self):
        state = observed_squad()
        state["agents"]["rf4a"]["availability"] = {"status": "stand-by"}
        value = composition(state)
        self.assertEqual([(p["task"], p["builder"], p["readers"], p["named"]) for p in value["pairs"]],
                         [("T-ALT", "b1", ["x2"], True), ("T-READ", "b1", ["x2"], True),
                          ("T-NOREAD", "b1", ["x1", "x2"], False)])
        self.assertEqual(composition_lines(value)[2:], ["- T-ALT: b1 (Claude) → x2", "- T-READ: b1 (Claude) → x2",
                                                        "- T-NOREAD: b1 (Claude) → x1, x2 (da nominare)"])

    def test_builders_on_both_sides_without_a_reader_on_the_few_side_say_who_reads(self):
        state = squad(COORD, ("b1", "builder", "claude"), ("y1", "builder", "codex"), ("y2", "builder", "codex"))
        value = composition(state, lang="en")
        self.assertEqual(value["case"], "imbalance")
        self.assertEqual(value["message"], "Imbalance: 1 Claude and 2 Codex. Building: 1 Claude, 2 Codex; reading: no "
                         "window. Pair each builder with a reader of the other model.")

    def test_census_without_roles_keeps_the_count_advice(self):
        seen = [{"pid": 1, "client": "claude"}, {"pid": 2, "client": "codex"}, {"pid": 3, "client": "codex"}]
        value = composition(None, seen)
        self.assertEqual(value["case"], "imbalance")
        self.assertTrue(value["message"].startswith("L'unica finestra Claude rileggerà"))
        self.assertEqual(value["pairs"], [])

    def test_mixed_roles_on_the_larger_side_are_summarised_not_counted_as_builders(self):
        # Codex review of 3.12-rc1: 1 Claude reader; 3 Codex, of which 1 builder and 2 readers.
        state = squad(COORD, ("r1", "reviewer", "claude"), ("y1", "builder", "codex"),
                      ("x1", "reviewer", "codex"), ("x2", "reviewer", "codex"))
        value = composition(state)
        self.assertEqual((value["case"], value["claude"], value["codex"]), ("imbalance", 1, 3))
        self.assertNotIn("collo di bottiglia", value["message"])
        self.assertEqual(value["message"], "Squilibrio: 1 Claude e 3 Codex. Costruiscono: 1 Codex; rileggono: 1 "
                         "Claude, 2 Codex. Abbina a ogni costruttore un lettore dell'altro modello.")
        self.assertEqual(composition(state, lang="en")["message"],
                         "Imbalance: 1 Claude and 3 Codex. Building: 1 Codex; reading: 1 Claude, 2 Codex. Pair each "
                         "builder with a reader of the other model.")

    def test_mixed_roles_on_the_smaller_side_are_summarised_too(self):
        state = squad(COORD, ("r1", "reviewer", "claude"), ("b1", "builder", "claude"), ("y1", "builder", "codex"),
                      ("y2", "builder", "codex"), ("y3", "builder", "codex"))
        self.assertEqual(composition(state, lang="en")["message"],
                         "Imbalance: 2 Claude and 3 Codex. Building: 1 Claude, 3 Codex; reading: 1 Claude. Pair each "
                         "builder with a reader of the other model.")


if __name__ == "__main__":
    unittest.main()

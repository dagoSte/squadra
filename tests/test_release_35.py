"""Seams of the 3.5 integration: the Sounds switch looks like the pills beside it; the Read helper keeps text widths."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SoundsSwitchStyle(unittest.TestCase):
    def test_the_sounds_switch_uses_the_pill_style_on_wide_screens_only(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        block = re.search(r'<style id="squadra-sounds-toggle">(.*?)</style>', page, re.S)
        self.assertIsNotNone(block)
        css = block.group(1)
        self.assertIn("@media(min-width:700px)", css)
        wide = css.split("@media(min-width:700px)", 1)[1]
        self.assertIn("border-radius:var(--sq-r-pill)", wide)
        self.assertIn('.sq-notify-controls button[aria-pressed="true"]{border-color:var(--sq-border)', wide)
        self.assertIn(".sq-notify-controls button:focus:not(:focus-visible){outline:none}", css)



class ReadableKeepsTheMeasure(unittest.TestCase):
    def test_the_read_helper_does_not_override_a_text_width(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        rule = re.search(r"\.sq-readable\{([^}]*)\}", page)
        self.assertIsNotNone(rule)
        self.assertNotIn("max-width", rule.group(1))
        self.assertIn(".sq-lede{max-width:60ch", page)


if __name__ == "__main__":
    unittest.main()

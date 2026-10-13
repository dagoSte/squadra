"""Check that dashboard documentation names permitted writes and execution limits."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReadOnlyClaim(unittest.TestCase):
    def test_recovery_reference_names_guarded_writes_and_plugin_projections(self):
        text = (ROOT / 'references/recovery.md').read_text()
        paragraph = ' '.join(text.split('## Dashboard and versions', 1)[1].split('\n\n', 2)[1].split()).casefold()
        for clause in ('token and exact host', 'recorded state and artifacts', 'plugin data projections',
                       'guarded posts', 'plugin settings', 'action selections',
                       'write only the declared files', 'no command or shell execution',
                       'no remote scripts or cdn'):
            self.assertIn(clause, paragraph)
        self.assertNotRegex(paragraph, r'nessun(?:a)?\s+post|no\s+post|only\s+(?:state|get)|get.only|sol[oi]\s+stato|solo\s+get|sola\s+lettura|read.only')

    def test_the_footer_names_settings_and_saved_selections_in_both_languages(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        footer = re.search(r'"static.footer2":\{it:"([^"]*)",en:"([^"]*)"\}', page)
        self.assertIsNotNone(footer)
        self.assertIn("salvo le impostazioni dei plugin e le selezioni salvate", footer.group(1))
        self.assertIn("read-only except plugin settings and saved selections", footer.group(2))
        self.assertNotIn("salvo le impostazioni dei plugin ·", page)
        self.assertNotIn("except plugin settings ·", page)

    def test_the_page_promises_execution_only_when_the_plugin_gives_instructions(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        self.assertEqual(page.count("se il plugin ne fornisce le istruzioni"), 2)
        self.assertEqual(page.count("if the plugin provides instructions for it"), 2)
        self.assertNotIn("Una finestra della squadra la eseguirà", page)
        self.assertNotIn("A squad window will carry it out", page)
        self.assertNotIn("verrà eseguita da una finestra della squadra", page)
        self.assertNotIn("a squad window will carry it out", page)


if __name__ == "__main__":
    unittest.main()

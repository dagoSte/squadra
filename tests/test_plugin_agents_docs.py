"""The optional agent instructions belong to documentation, never dashboard code."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = '$HOME/.squadra/plugins/<name>/AGENTS.md'


class PluginAgentDocs(unittest.TestCase):
    def test_agent_rule_is_complete_in_each_language_and_absent_from_runtime(self):
        italian = ('plugin installato', 'finestra coordinatrice', 'avvio', 'ripresa',
                   'solo i comandi indicati', 'produttore dopo una chiusura',
                   'consumatore quando la dashboard mostra una richiesta in attesa', 'riferisce l’esito',
                   'file regolare di proprietà dell’utente', 'non scrivibile da gruppo o altri',
                   'non un link simbolico', 'senza un file valido', 'richiesta resta in attesa',
                   'coordinatrice lo segnala', 'dashboard non legge, non scrive e non esegue',
                   'nei permessi già autorizzati', 'non amplia mai i permessi',
                   'non autorizza installazioni, push, pubblicazioni o decisioni riservate all’utente',
                   'oltre a eseguire il produttore o il consumatore di quel plugin', 'fermarsi e riferire')
        english = ('installed plugin', 'coordinator window', 'start and resume',
                   'only the commands it specifies', 'producer after a close',
                   'consumer when the dashboard shows a pending request', 'reports the outcome',
                   'regular file owned by the user', 'not group- or other-writable', 'not a symlink',
                   'without a valid file', 'request stays pending', 'coordinator says so',
                   'dashboard never reads, writes or runs', 'within existing permissions',
                   'never widens permissions', 'never authorizes installation, push, publication or decisions reserved for the user',
                   'beyond running that plugin’s producer or consumer', 'stop and report')
        references = (ROOT / 'references/plugins.md').read_text()
        sections = [('SKILL.md', (ROOT / 'SKILL.md').read_text(), english),
                    ('README.it.md', (ROOT / 'README.it.md').read_text(), italian),
                    ('README.md', (ROOT / 'README.md').read_text(), english),
                    ('references/plugins.md EN', references.split('### Agent instructions')[1].split('\n### ')[0], english)]
        for name, text, clauses in sections:
            with self.subTest(document=name):
                self.assertIn(PATH, text)
                # Check the instructions next to the path, not unrelated wording elsewhere in the guide.
                paragraph = text.split(PATH, 1)[1].split('\n\n', 1)[0]
                start = text.split(PATH, 1)[0].rsplit('\n\n', 1)[-1]
                rule = ' '.join((start + PATH + paragraph).split()).casefold()
                for clause in clauses:
                    self.assertIn(clause, rule)
        for folder in ('scripts', 'assets'):
            for path in (ROOT / folder).rglob('*'):
                # The installer copies inert plugin instructions; migliora manages only project instructions.
                if path.is_file() and path not in {ROOT / 'scripts/plugin_store.py', ROOT / 'scripts/migliora.py'}:
                    with self.subTest(runtime=str(path.relative_to(ROOT))):
                        self.assertNotIn(b'AGENTS.md', path.read_bytes())

    def test_plugin_instructions_are_copied_only_by_explicit_installer(self):
        def boundary(root):
            self.assertFalse((root / 'scripts/playwright_plugin.py').exists())
            for path in (root / 'plugins/playwright/bridge').glob('*.py'):
                self.assertNotIn('AGENTS.md', path.read_text(), str(path))
            for folder in ('scripts', 'assets'):
                for path in (root / folder).rglob('*'):
                    if path.is_file():
                        self.assertNotIn(b'playwright_plugin', path.read_bytes())
        boundary(ROOT)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)
            for directory in ('scripts', 'assets', 'plugins/playwright/bridge'):
                (clone / directory).mkdir(parents=True, exist_ok=True)
            helper = clone / 'plugins/playwright/bridge/probe.py'
            helper.write_text('marker = "AGENTS.md"')
            with self.assertRaises(AssertionError): boundary(clone)
            helper.write_text('pass')
            forbidden = clone / 'scripts/probe.py'
            forbidden.write_text('import playwright_plugin')
            with self.assertRaises(AssertionError): boundary(clone)
            forbidden.unlink()
            (clone / 'assets/probe.js').write_text('playwright_plugin')
            with self.assertRaises(AssertionError): boundary(clone)

"""Check hidden plugins, invalid keys, install rollback and bilingual error messages."""
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install as installer  # noqa: E402
import plugins  # noqa: E402
from dashboard import make_server  # noqa: E402
from registry import Store  # noqa: E402
from runtime import CURSOR_REFUSAL  # noqa: E402

GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "s@example.invalid",
           "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "s@example.invalid"}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=os.environ | GIT_ENV, check=True, capture_output=True)


class HiddenPluginEntries(unittest.TestCase):
    def test_finder_files_are_skipped_not_refused(self):
        with tempfile.TemporaryDirectory(prefix="squadra-hidden-") as temp:
            home = Path(temp)
            root = home / ".squadra/plugins"
            (root / "example").mkdir(parents=True)
            manifest = {"schema": 1, "name": "example", "title": {"it": "Esempio", "en": "Example"},
                        "description": {"it": "Prova", "en": "Test"}}
            (root / "example/plugin.json").write_text(json.dumps(manifest))
            (root / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
            (root / ".hidden-dir").mkdir()
            found = plugins.discover(home)
            self.assertEqual([p["name"] for p in found["plugins"]], ["example"])
            self.assertEqual(found["refused"], [])


class StateKeyRefusal(unittest.TestCase):
    def test_a_refused_first_event_answers_a_fixed_409(self):
        with tempfile.TemporaryDirectory(prefix="squadra-state-key-") as temp:
            base = Path(temp).resolve()
            store = Store(base / "record", create=True)
            store.append("init", "system", {"name": "Key", "project": str(base), "temporary": True,
                                            "limits": {"plan": 3, "code": 2, "build": 3}})
            store.close()
            with patch.dict(os.environ, {"HOME": str(base / "home")}), \
                    patch.object(plugins, "KEY", re.compile("x{65}")):
                server, token = make_server(base / "record")
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                thread.start()
                try:
                    port = server.server_port
                    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                                     headers={"Host": f"127.0.0.1:{port}", "X-Squadra-Token": token})
                    try:
                        with urllib.request.urlopen(request, timeout=10) as answer:
                            outcome = (answer.status, answer.read().decode())
                    except urllib.error.HTTPError as refused:
                        with refused:
                            outcome = (refused.code, json.loads(refused.read()))
                    except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as lost:
                        outcome = ("no answer", type(lost).__name__)
                    self.assertEqual(outcome, (409, {"error": "registry_invalid"}))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(5)


class CursorInstallRollback(unittest.TestCase):
    """A failure after the Cursor skill was replaced restores every target, the Cursor one included."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="squadra-rollback-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo, self.home = self.base / "repo", self.base / "home"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "SKILL.md").write_text("---\nname: squadra\n---\n")
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/squadra.py").write_text('print("synthetic installed")\n')
        git(self.repo, "add", ".")
        git(self.repo, "-c", "commit.gpgsign=false", "commit", "-qm", "candidate")
        for name in (".codex", ".claude", ".cursor"):
            dest = self.home / name / "skills/squadra"
            dest.mkdir(parents=True)
            (dest / "OLD").write_text(name)
        launcher = self.home / ".local/bin/squadra"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\necho old launcher\n")

    def fail_when(self, predicate):
        real = installer.atomic

        def atomic(path, body):
            if predicate(Path(path)):
                raise OSError("synthetic late failure")
            return real(path, body)
        return patch.object(installer, "atomic", atomic)

    def assert_restored(self):
        for name in (".codex", ".claude", ".cursor"):
            target = self.home / name / "skills/squadra"
            self.assertFalse(target.is_symlink(), name)
            self.assertTrue((target / "OLD").is_file(), name)
            self.assertEqual((target / "OLD").read_text(), name)
        self.assertEqual((self.home / ".local/bin/squadra").read_text(), "#!/bin/sh\necho old launcher\n")

    def test_failure_at_launcher_publication(self):
        with self.fail_when(lambda p: p == self.home / ".local/bin/squadra"):
            with self.assertRaises(OSError):
                installer.install(self.repo, self.home, self.base / "backup")
        self.assert_restored()

    def test_failure_at_the_install_receipt(self):
        with self.fail_when(lambda p: p.name == "INSTALL.json"):
            with self.assertRaises(OSError):
                installer.install(self.repo, self.home, self.base / "backup")
        self.assert_restored()


class AboutBidiControls(unittest.TestCase):
    def test_bidi_controls_are_refused_in_about_text(self):
        about = {"publisher": "memoranda.dev", "version": "1.0.0", "tagline": {"it": "Prova", "en": "Test"},
                 "sections": [{"id": "what", "title": {"it": "Cosa", "en": "What"}, "text": {"it": "Testo", "en": "Text"}}]}
        manifest = {"schema": 1, "name": "example", "title": {"it": "Esempio", "en": "Example"},
                    "description": {"it": "Prova", "en": "Test"}, "about": about}
        with tempfile.TemporaryDirectory(prefix="squadra-bidi-") as temp:
            home = Path(temp)
            folder = home / ".squadra/plugins/example"
            folder.mkdir(parents=True)
            (folder / "plugin.json").write_text(json.dumps(manifest))
            self.assertEqual([p["name"] for p in plugins.discover(home)["plugins"]], ["example"])
            for spoof in ("memoranda\u202e.dev", "\u2066memoranda\u2069"):
                for where in ("publisher", "tagline"):
                    with self.subTest(where=where, spoof=repr(spoof)):
                        bad = json.loads(json.dumps(manifest))
                        if where == "publisher":
                            bad["about"]["publisher"] = spoof
                        else:
                            bad["about"]["tagline"]["it"] = spoof
                        (folder / "plugin.json").write_text(json.dumps(bad))
                        found = plugins.discover(home)
                        self.assertEqual(found["plugins"], [])
                        self.assertEqual([r["name"] for r in found["refused"]], ["example"])


class HomepageHosts(unittest.TestCase):
    def test_malformed_hosts_and_invalid_ports_are_refused(self):
        for good in ("https://memoranda.dev", "https://memoranda.dev/", "https://docs.memoranda.dev:443/start", "https://a-b.example"):
            with self.subTest(good=good):
                plugins._homepage(good)
        for bad in ("https://memoranda..dev", "https://-memoranda.dev", "https://memoranda-.dev", "https://memoranda.dev:0",
                    "https://memoranda.dev:65536", "https://memoranda.dev.", "https://mem_oranda.dev"):
            with self.subTest(bad=bad):
                with self.assertRaises(plugins.Refused):
                    plugins._homepage(bad)


class ReleaseDocsSeams(unittest.TestCase):
    def test_the_footer_states_the_plugin_settings_exception(self):
        page = (ROOT / "assets/dashboard.html").read_text()
        self.assertIn("Vista locale in sola lettura, salvo le impostazioni dei plugin", page)
        footer = re.search(r'"static.footer2":\{it:"([^"]*)",en:"([^"]*)"\}', page)
        self.assertIsNotNone(footer)
        self.assertIn("salvo le impostazioni dei plugin", footer.group(1))
        self.assertIn("read-only except plugin settings", footer.group(2))

    def test_every_readme_image_resolves_and_every_image_is_referenced(self):
        docs = [ROOT / "README.md", ROOT / "README.it.md", ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]
        referenced = set()
        for doc in docs:
            referenced |= set(re.findall(r"docs/images/([A-Za-z0-9._-]+\.png)", doc.read_text()))
        files = {p.name for p in (ROOT / "docs/images").glob("*.png")}
        self.assertEqual(sorted(referenced - files), [])
        self.assertEqual(sorted(files - referenced), [])


class CursorRefusalText(unittest.TestCase):
    def test_the_refusal_speaks_both_languages(self):
        self.assertIn("Squadra does not launch cursor-agent", CURSOR_REFUSAL)
        self.assertIn("Squadra non avvia cursor-agent", CURSOR_REFUSAL)


if __name__ == "__main__":
    unittest.main()

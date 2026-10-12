"""Run the guide's own examples against synthetic settings and action files."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plugin_actions
import plugin_settings
import plugins
from registry import Store


class GuideBoundary(unittest.TestCase):
    @contextmanager
    def example(self):
        text = (ROOT / "references/plugin-authoring.md").read_text()
        blocks = re.findall(r"^```(python|json)\n(.*?)^```\s*$", text, re.M | re.S)
        programs = [body for lang, body in blocks if lang == "python"]
        self.assertEqual(len(programs), 2)
        objects = [json.loads("{" + body + "}" if body.startswith('"') else body)
                   for lang, body in blocks if lang == "json"]
        manifest = next(v for v in objects if v.get("name") == "release-notes")
        manifest.update(next(v for v in objects if "settings" in v))
        manifest.update(next(v for v in objects if "actions" in v))
        with tempfile.TemporaryDirectory(prefix="sq-guide-") as tmp:
            home = Path(tmp).resolve() / "home"
            folder = home
            for name in (None, ".squadra", "plugins", "release-notes"):
                if name:
                    folder /= name
                folder.mkdir(mode=0o700)
            (folder / "plugin.json").write_text(json.dumps(manifest))
            (folder / "plugin.json").chmod(0o600)
            registry = Path(tmp).resolve() / "registry"
            store = Store(registry, create=True)
            try:
                store.append("init", "system", {"name": "Guide fixture", "project": str(home),
                                               "temporary": True, "limits": {"plan": 3, "code": 2, "build": 3}})
                store.project()
            finally:
                store.close()
            producer, consumer = types.ModuleType("producer"), types.ModuleType("consumer")
            with patch.dict(os.environ, HOME=str(home)), patch.dict(sys.modules, producer=producer):
                exec(compile(programs[0], "guide-producer.py", "exec"), producer.__dict__)
                exec(compile(programs[1], "guide-consumer.py", "exec"), consumer.__dict__)
                producer.main(registry)
                key = producer.registry_key(registry)
                panel = plugins.load_panel(home, "release-notes", key)
                panel["items"][0] = next(v for v in objects if v.get("id") == "note-102")
                panel["items"].append(dict(panel["items"][0], id="note-103"))
                producer.write_json(folder / "panels" / (key + ".json"), panel)
                values = {"endpoint": "https://example.invalid/", "api_key": "SYNTHETIC-ONLY", "channel": "test"}
                plugin_settings.save(home, "release-notes", values)
                plugin_actions.save(home, "release-notes", "send", key, ["note-102"])
                yield home, folder, registry, key, consumer

    def test_extracted_examples_refuse_unsafe_paths_and_keep_consumer_outcomes(self):
        contrasts = [(".squadra", "symlink"), (".squadra/plugins", "symlink"),
                     (".squadra/plugins/release-notes", "mode"),
                     (".squadra/plugins/release-notes/secrets", "mode")]
        extras = [(".squadra/plugins/release-notes/secrets/api_key", "symlink"),
                  (".squadra/plugins/release-notes/settings.json", "symlink"),
                  (".squadra/plugins/release-notes/actions", "symlink"),
                  (".squadra/plugins/release-notes/actions", "mode"),
                  (".squadra/plugins/release-notes/actions/send.json", "symlink"),
                  (".squadra/plugins/release-notes/actions/send.lock", "symlink"),
                  (".squadra", "mode"), (".squadra/plugins", "mode")]
        for rel, kind in contrasts + extras:
            with self.subTest(path=rel, kind=kind), self.example() as (home, folder, registry, key, consumer):
                path = home / rel
                if kind == "symlink":
                    saved = path.with_name(path.name + "-original")
                    path.rename(saved)
                    path.symlink_to(saved, target_is_directory=saved.is_dir())
                else:
                    path.chmod(0o777)
                with patch.object(consumer, "deliver") as send:
                    if "actions" not in Path(rel).parts:
                        with self.subTest(operation="read_settings"):
                            with self.assertRaises((OSError, SystemExit)):
                                consumer.read_settings()
                    with self.subTest(operation="consume"):
                        with self.assertRaises((OSError, SystemExit)):
                            consumer.consume(registry)
                    send.assert_not_called()
                if (rel, kind) in contrasts:
                    with self.assertRaises(plugins.Refused) as refusal:
                        plugin_settings.read(home, "release-notes")
                    self.assertEqual(refusal.exception.code, "symlink" if kind == "symlink" else "unsafe_permissions")

        with self.example() as (home, folder, registry, key, consumer):
            self.assertEqual(consumer.read_settings(), ({"endpoint": "https://example.invalid/", "channel": "test"}, "SYNTHETIC-ONLY"))
            calls = []

            def send(ids, values, secret):
                self.assertEqual((ids, values, secret), (["note-102"], {"endpoint": "https://example.invalid/", "channel": "test"}, "SYNTHETIC-ONLY"))
                fd = os.open(folder / "actions/send.lock", os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                calls.append(ids)

            with patch.object(consumer, "deliver", side_effect=send):
                self.assertEqual(consumer.consume(registry), "sent; request cleared")
                self.assertEqual(consumer.consume(registry), "no request")
            self.assertEqual(calls, [["note-102"]])
            plugin_actions.save(home, "release-notes", "send", key, ["note-102"])

            def newer(*args):
                send(*args)
                plugin_actions.save(home, "release-notes", "send", key, ["note-103"])

            with patch.object(consumer, "deliver", side_effect=newer):
                self.assertEqual(consumer.consume(registry), "sent; a newer choice stays pending")
            self.assertEqual(plugin_actions.read(home, "release-notes", "send", key)["ids"], ["note-103"])
            with patch.object(consumer, "registry_key", return_value="0" * 64), patch.object(consumer, "deliver") as send:
                self.assertEqual(consumer.consume(registry), "the request belongs to another registry; left alone")
                send.assert_not_called()
            (folder / "actions/send.lock").unlink()
            with self.assertRaisesRegex(SystemExit, "request without its lock"):
                consumer.consume(registry)

    def test_partial_save_needs_deliberate_resubmission_of_the_pair(self):
        with self.example() as (home, folder, registry, key, consumer):
            old = {"endpoint": "https://old.example.invalid/", "api_key": "SYNTHETIC-OLD"}
            new = {"endpoint": "https://new.example.invalid/", "api_key": "SYNTHETIC-NEW"}
            plugin_settings.save(home, "release-notes", old)
            atomic = plugin_settings.atomic
            stages = []

            def fail_metadata(parent, name, body):
                stages.append(name)
                if name == "settings.json":
                    raise OSError("synthetic publication failure")
                atomic(parent, name, body)

            with patch.object(plugin_settings, "atomic", side_effect=fail_metadata):
                with self.assertRaises(plugins.Refused) as refused:
                    plugin_settings.save(home, "release-notes", new)
                self.assertEqual(refused.exception.code, "save_failed")
            self.assertEqual(stages, ["api_key", "settings.json"])
            values, secret = consumer.read_settings()
            self.assertEqual((values["endpoint"], secret), (old["endpoint"], new["api_key"]))
            # Presence metadata alone cannot detect a mixed pair after failed publication.
            state = plugin_settings.read(home, "release-notes")
            self.assertTrue(next(v for v in state if v["id"] == "api_key")["set"])
            plugin_settings.save(home, "release-notes", new)
            values, secret = consumer.read_settings()
            self.assertEqual((values["endpoint"], secret), (new["endpoint"], new["api_key"]))


if __name__ == "__main__":
    unittest.main()

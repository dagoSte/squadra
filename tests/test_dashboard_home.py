"""A caller's installed plugin cannot change an empty dashboard fixture."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard
import dashboard_test_support
from registry import Store
from test_plugin_about_server import about
from test_plugin_link_server import png
from test_plugins import manifest


class DashboardHome(unittest.TestCase):
    def state(self, factory, root):
        server, token = factory(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", "/api/state", headers={"X-Squadra-Token": token})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_fake_caller_plugin_with_logo_is_visible_only_to_explicit_home(self):
        with tempfile.TemporaryDirectory(prefix="sq-caller-home-") as folder:
            root = Path(folder) / "registry"
            store = Store(root, create=True)
            try:
                store.append("init", "system", {"name": "Synthetic HOME", "project": folder,
                             "limits": {"plan": 3, "code": 2, "build": 3}})
            finally:
                store.close()
            home = Path(folder) / "caller"
            plugin = home / ".squadra/plugins/example"
            plugin.mkdir(parents=True)
            value = manifest(); value["about"] = about()
            (plugin / "plugin.json").write_text(json.dumps(value))
            (plugin / "icon.png").write_bytes(png())
            stamp = lambda: {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                             for p in plugin.iterdir()}
            before = stamp()
            with patch.dict(os.environ, {"HOME": str(home)}):
                control = self.state(dashboard.make_server, root)
                self.assertEqual([p["name"] for p in control["plugins"]["plugins"]], ["example"])
                self.assertEqual(control["plugins"]["refused"], [])
                self.assertTrue(control["plugins"]["plugins"][0]["icon"].startswith("data:image/png;base64,"))
                homes = []
                original = dashboard.plugin_actions.discover

                def discover(path, key):
                    homes.append(path)
                    self.assertNotEqual(path, home)
                    self.assertTrue(path.is_dir())
                    return original(path, key)

                with patch.object(dashboard.plugin_actions, "discover", side_effect=discover):
                    isolated = self.state(dashboard_test_support.make_server, root)
                self.assertEqual(isolated["plugins"], {"plugins": [], "refused": [], "key": control["plugins"]["key"]})
                self.assertEqual(Path.home(), home)
                self.assertTrue(homes)
                self.assertTrue(all(not path.exists() for path in homes), "server close removes only its private HOME")
            self.assertEqual(stamp(), before)

    def test_empty_fixture_modules_share_the_isolated_factory(self):
        for name in ("test_integration", "test_brand", "test_cursor", "test_decisions", "test_comms",
                     "test_panel", "test_squadra"):
            with self.subTest(module=name):
                module = __import__(name)
                self.assertIs(module.make_server, dashboard_test_support.make_server)

    def test_failed_bind_cleans_private_home_and_restores_caller(self):
        caller = Path.home()
        observed = []

        def refuse(*args):
            observed.append(Path.home())
            raise OSError("synthetic bind failure")

        with patch.object(dashboard, "make_server", side_effect=refuse):
            with self.assertRaisesRegex(OSError, "synthetic bind failure"):
                dashboard_test_support.make_server(Path("unused"))
        self.assertEqual(Path.home(), caller)
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())

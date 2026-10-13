"""K-1…K-3: the memoranda.dev homepage link and logo (contract-plugin-link-logo.md), and W's checker of both."""
import copy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zlib

import contract
import fx
from squadra_memories import panel

MARK_SHA256 = "21b1c85aecb41eddc16d4d83f82a09f22732f4750bb265f31b230194d750e661"


def png(width, height, padding=0):
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    chunk = b"IHDR" + header
    return (contract.PNG_SIGNATURE + len(header).to_bytes(4, "big") + chunk
            + zlib.crc32(chunk).to_bytes(4, "big") + b"\0" * padding)


class Logo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        fx.install(self.home)

    def tearDown(self):
        fx.remove(self.tmp)

    def test_k1_the_homepage_is_memoranda_dev_and_follows_the_contract(self):
        self.assertEqual(panel.MANIFEST["about"]["homepage"], "https://memoranda.dev")
        contract.validate_manifest(panel.MANIFEST, "memoranda")
        for name, value in (("plain http", "http://memoranda.dev"), ("userinfo", "https://user@memoranda.dev"),
                            ("fragment", "https://memoranda.dev/#start"), ("space", "https://memoranda.dev /x"),
                            ("bidi", "https://memoranda.dev‮"), ("control", "https://memoranda.dev\x07"),
                            ("too long", "https://" + "a" * 200 + ".dev"), ("no host", "https:///path"),
                            ("not text", 42), ("empty", "")):
            with self.subTest(name):
                manifest = copy.deepcopy(panel.MANIFEST)
                manifest["about"]["homepage"] = value
                with self.assertRaises(contract.Broken):
                    contract.validate_manifest(manifest, "memoranda")

    def test_k2_the_logo_is_the_site_mark_installed_next_to_plugin_json(self):
        self.assertEqual(hashlib.sha256(panel.ICON.read_bytes()).hexdigest(), MARK_SHA256)
        reg = fx.copy_registry(self.tmp)
        run = fx.collect(reg, self.tmp / "out", home=self.home)
        self.assertEqual(run.rc, 0, run.stderr)
        contract.check(self.home, "memoranda", reg.event(1)["hash"])
        icon = self.home / ".squadra" / "plugins" / "memoranda" / "icon.png"
        st = os.lstat(icon)
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(hashlib.sha256(icon.read_bytes()).hexdigest(), MARK_SHA256)
        self.assertEqual(contract.check_icon(icon), (128, 128))
        icon.unlink()
        elsewhere = self.tmp / "other.png"
        elsewhere.write_bytes(png(1, 1))
        icon.symlink_to(elsewhere)
        refused = fx.collect(reg, self.tmp / "out2", home=self.home)
        self.assertEqual(refused.rc, 3)
        self.assertTrue(refused.stderr.startswith("E-PANEL:"), refused.stderr)
        self.assertEqual(elsewhere.read_bytes(), png(1, 1))

    def test_k3_the_checker_refuses_every_bad_icon(self):
        good = self.tmp / "good.png"
        good.write_bytes(png(1, 1))
        self.assertEqual(contract.check_icon(good), (1, 1))
        edge = self.tmp / "edge.png"
        edge.write_bytes(png(256, 256, padding=32 * 1024 - 33))
        self.assertEqual(len(edge.read_bytes()), 32 * 1024)
        self.assertEqual(contract.check_icon(edge), (256, 256))
        cases = {"not a PNG": b"GIF89a" + b"\0" * 40, "zero width": png(0, 10), "too wide": png(257, 10),
                 "too tall": png(10, 257), "too large": png(16, 16, padding=32 * 1024),
                 "truncated": contract.PNG_SIGNATURE + b"\0\0\0\x0dIHDR"}
        for name, body in cases.items():
            with self.subTest(name):
                bad = self.tmp / f"{name}.png"
                bad.write_bytes(body)
                with self.assertRaises(contract.Broken):
                    contract.check_icon(bad)
        link = self.tmp / "link.png"
        link.symlink_to(good)
        with self.assertRaises(contract.Broken):
            contract.check_icon(link)


if __name__ == "__main__":
    unittest.main()

"""Check bundled fonts, color contrast and two-line task cards."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dashboard_test_support import make_server
from registry import Store
import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "assets/dashboard.html").read_text(encoding="utf-8")
FONTS = ROOT / "assets/vendor/fonts"
# Vendored font files are pinned byte for byte together with their unchanged licences.
TYPEFACES = {
    "geist-var-latin.woff2": ("9b6f5ff45b278c744b5f379a2c4ecbaf858a842b8eaf82ac8d21b699ca16c608", "OFL-geist.txt",
                              "Copyright 2024 The Geist Project Authors (https://github.com/vercel/geist-font)"),
    "fraunces-var-latin.woff2": ("48282a415ec22e31beaf0a0666e6fae0c8cbddcd0b1f6e729f27c3ade8a64e43", "OFL-fraunces.txt",
                                 "Copyright 2020 The Fraunces Project Authors (github.com/undercasetype/Fraunces)"),
}
POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
          "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'")


class Typefaces(unittest.TestCase):
    def test_fonts_are_the_sites_files_byte_for_byte_with_their_ofl_licences(self):
        self.assertEqual(sorted(p.name for p in FONTS.iterdir()), sorted([*TYPEFACES, *(v[1] for v in TYPEFACES.values())]))
        bodies = set()
        for name, (sha, licence, copyright) in TYPEFACES.items():
            data = (FONTS / name).read_bytes()
            self.assertEqual(data[:4], b"wOF2", name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha, name)
            text = (FONTS / licence).read_text(encoding="utf-8")
            self.assertEqual(text.splitlines()[0], copyright, licence)
            self.assertIn("This Font Software is licensed under the SIL Open Font License, Version 1.1.", text)
            body = text[text.index("-----------------------------------------------------------\nSIL OPEN FONT LICENSE Version 1.1"):]
            self.assertTrue(body.rstrip().endswith("OTHER DEALINGS IN THE FONT SOFTWARE."), licence)
            bodies.add(body)
        self.assertEqual(len(bodies), 1, "both licences carry the same unchanged OFL 1.1 text")

    def test_page_declares_only_the_two_local_font_faces_with_system_fallbacks(self):
        faces = re.findall(r"@font-face\{([^}]*)\}", PAGE)
        self.assertEqual(len(faces), 2)
        sources = sorted(re.search(r"src:url\('([^']+)'\) format\('woff2'\)", face).group(1) for face in faces)
        self.assertEqual(sources, sorted("/fonts/" + name for name in TYPEFACES))
        for face in faces:
            self.assertIn("font-display:swap", face)
        self.assertEqual(len(re.findall(r"url\((?!#)", PAGE)), 2, "besides SVG marker references, the two font files are the only addresses")
        stack = dict(re.findall(r"--(sq-font|sq-display):([^;]+);", re.search(r"html\{--sq-font[^}]*\}", PAGE).group(0)))
        self.assertTrue(stack["sq-font"].startswith('"Geist",') and "system-ui" in stack["sq-font"] and stack["sq-font"].endswith("sans-serif"))
        self.assertTrue(stack["sq-display"].startswith('"Fraunces",') and stack["sq-display"].endswith("serif"))
        # The interface uses one sans-serif family; both licensed fonts remain available.
        bare = re.sub(r"/\*[\s\S]*?\*/", "", PAGE)
        users = [selector.strip() for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", bare) if "var(--sq-display)" in body]
        self.assertEqual(users, [])
        self.assertIn(".sq-status,.sq-work>h2,#charts>h2,.sq-story-head h2{font-family:var(--sq-font)", PAGE)
        self.assertEqual(bare.count("Fraunces"), 2, "the face and the display stack")


class Served(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="squadra-brand-")
        store = Store(Path(cls.temp.name) / "record", create=True)
        store.append("init", "system", {"name": "Brand", "project": cls.temp.name, "temporary": True, "limits": {"plan": 3, "code": 2, "build": 3}})
        store.close()
        cls.server, cls.token = make_server(Path(cls.temp.name) / "record")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def get(self, path, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers or {})) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def test_the_policy_gains_only_font_src_self(self):
        status, headers, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Security-Policy"], POLICY)

    def test_each_font_is_served_tokenless_after_the_host_check(self):
        for name, (sha, _, _) in TYPEFACES.items():
            status, headers, body = self.get("/fonts/" + name)
            self.assertEqual(status, 200, name)
            self.assertEqual(headers["Content-Type"], "font/woff2")
            self.assertEqual((headers["X-Content-Type-Options"], headers["Cache-Control"]), ("nosniff", "no-store"))
            self.assertEqual(headers["Content-Security-Policy"], POLICY)
            self.assertEqual(hashlib.sha256(body).hexdigest(), sha)
            status, _, _ = self.get("/fonts/" + name, {"Host": "localhost:%d" % self.server.server_port})
            self.assertEqual(status, 403, "the Host check comes first")

    def test_no_other_file_is_reachable_through_the_font_route(self):
        for path in ("/fonts/", "/fonts/OFL-geist.txt", "/fonts/geist-var-latin.woff", "/fonts/../scripts/dashboard.py",
                     "/assets/vendor/fonts/geist-var-latin.woff2", "/fonts/%67eist-var-latin.woff2", "/fonts/geist-var-latin.woff2/"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 401, "outside the two names the token is required")
                self.assertNotIn(b"wOF2", body)
                status, _, body = self.get(path, {"X-Squadra-Token": self.token})
                self.assertEqual(status, 404)
                self.assertNotIn(b"wOF2", body)
        request = urllib.request.Request(self.base + "/fonts/geist-var-latin.woff2", data=b"x", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 405)


# Machado, Oliveira & Fernandes (2009), severity 1.0, on linear RGB; distances are OKLab ΔE × 100.
MACHADO = {"protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
           "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881))}


def linear(hexa):
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5))]


def oklab(rgb):
    r, g, b = rgb
    l, m, s = (x ** (1 / 3) for x in (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                                     0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                                     0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta(a, b, kind=None):
    def seen(h):
        rgb = linear(h)
        if kind:
            rgb = [min(1, max(0, sum(m * c for m, c in zip(row, rgb)))) for row in MACHADO[kind]]
        return oklab(rgb)
    return 100 * sum((x - y) ** 2 for x, y in zip(seen(a), seen(b))) ** 0.5


def hue(hexa):
    import math
    _, a, b = oklab(linear(hexa))
    return math.degrees(math.atan2(b, a)) % 360


class Palette(unittest.TestCase):
    CSS = panel.Tokens.CSS

    def themes(self):
        found = panel.blocks(self.CSS)
        return {name: (lambda tokens: lambda n: panel.resolve(tokens, tokens[n])[:7])(found[name]) for name in ("light", "dark")}

    def test_surfaces_and_ink_are_the_sites_colours(self):
        c = self.themes()
        light = {"--bg": "#ebe9e1", "--panel": "#ffffff", "--text": "#1f1c1b", "--line": "#d9d6ce", "--sq-surface-2": "#f5f4f0",
                 "--sq-sidebar": "#f5f4f0", "--sq-highlight": "#d95c00"}
        dark = {"--bg": "#1f1c1b", "--panel": "#2d2724", "--text": "#ebe9e1", "--muted": "#c7c1b7", "--sq-highlight": "#ff7714"}
        self.assertEqual({k: c["light"](k) for k in light}, light)
        self.assertEqual({k: c["dark"](k) for k in dark}, dark)

    def test_the_highlighter_orange_is_the_only_accent_hue(self):
        brand = hue("#ff7714")
        for theme, colour in self.themes().items():
            statuses = [hue(colour(t)) for t in ("--green", "--orange", "--red", "--standby")]
            for token in ("--sq-accent", "--sq-highlight"):
                with self.subTest(theme=theme, token=token):
                    # Keep the accent within 12 degrees of the brand orange, away from status colors.
                    near = abs(hue(colour(token)) - brand)
                    self.assertLess(near, 12, colour(token))
                    self.assertLess(near, min(abs(hue(colour(token)) - h) for h in statuses), colour(token))
            self.assertEqual(colour("--sq-accent-solid"), colour("--text"), "filled actions are ink (or cream in the dark), never orange")

    def test_non_text_marks_meet_three_to_one_where_they_are_drawn(self):
        for theme, colour in self.themes().items():
            for mark in ("--sq-highlight", "--sq-chart-done", "--sq-border-strong"):
                for surface in ("--bg", "--panel", "--sq-surface-2", "--sq-sidebar", "--sq-accent-subtle"):
                    with self.subTest(theme=theme, pair=(mark, surface)):
                        self.assertGreaterEqual(panel.wcag(colour(mark), colour(surface)), 3)
        self.assertIn("html{--sq-font", PAGE)
        self.assertIn("--sq-focus:var(--sq-highlight)", PAGE, "the focus ring is the highlighter")
        self.assertIn(".sq-mark-neutral{fill:var(--sq-chart-neutral);stroke:var(--sq-border-strong);stroke-width:1.5}", PAGE,
                      "the light neutral square keeps a three-to-one outline")

    def test_status_marks_stay_apart_for_colour_blind_readers(self):
        for theme, colour in self.themes().items():
            marks = {t: colour(t) for t in ("--sq-chart-done", "--sq-chart-good", "--sq-chart-bad", "--sq-chart-neutral")}
            names = list(marks)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    with self.subTest(theme=theme, pair=(a, b)):
                        self.assertGreaterEqual(min(delta(marks[a], marks[b], k) for k in MACHADO), 8)
                        self.assertGreaterEqual(delta(marks[a], marks[b]), 15)
        for rule in (".sq-bar{fill:var(--sq-highlight)}", ".sq-mark-acc{fill:var(--sq-chart-done)}"):
            self.assertIn(rule, PAGE)

    def test_print_like_shapes_and_flat_surfaces(self):
        radii = dict(re.findall(r"--sq-r-(sm|md|lg|xl|pill):(\d+)px", re.search(r"html\{--sq-font[^}]*\}", PAGE).group(0)))
        self.assertEqual(radii, {"sm": "8", "md": "10", "lg": "16", "xl": "22", "pill": "8"},
                         "the approved connected layout uses rounded cards and dialogs")
        for name, tokens in panel.blocks(self.CSS).items():
            self.assertEqual(tokens["--sq-shadow-1"], "none", name)


class CardLines(unittest.TestCase):
    """Check two-line cards in Node using a fixed layout model; Node is required."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="squadra-brand-cards-")
        cls.fixture = Path(cls.temp.name) / "cards.json"
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build_cards(Path(cls.temp.name) / "cards"), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_js_every_card_text_two_rendered_lines_and_show_all_only_when_cut(self):
        node = shutil.which("node")
        if not node:
            self.fail("Node is required: the card line contract was not measured")
        argv = [node, str(ROOT / "tests/brand_contract.js"), str(ROOT / "assets/dashboard.html"), str(self.fixture), "measured"]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=os.environ)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("brand contract passed: measured", result.stdout)


if __name__ == "__main__":
    unittest.main()

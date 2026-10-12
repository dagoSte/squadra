"""Plugin branding and explicit homepage links in the shipped dashboard."""
from pathlib import Path
import shutil
import subprocess
import unittest

import test_plugin_about as about_fixture

ROOT = Path(__file__).resolve().parents[1]


class PluginLinkJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        about_fixture.PluginAboutJavaScript.setUpClass.__func__(cls)

    def run_contract(self, scenario, page=None):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard contracts must never be skipped')
        return subprocess.run([node, str(ROOT / 'tests/plugin_link_contract.js'),
                               str(page or ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                              capture_output=True, text=True, timeout=30)

    def assert_contract(self, scenario):
        result = self.run_contract(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plugin link contract passed: ' + scenario, result.stdout)

    def test_dashboard_plugin_logo_and_explicit_homepage(self):
        self.assert_contract('header_sidebar')

    def test_dashboard_plugin_refuses_non_png_sources(self):
        self.assert_contract('icon_sources')

    def test_dashboard_plugin_refuses_unsafe_homepages(self):
        self.assert_contract('homepage_rejections')

    def test_dashboard_plugin_logo_languages_themes_phone(self):
        self.assert_contract('languages_themes_phone')

    def test_dashboard_plugin_link_focus_and_manifest_refresh(self):
        self.assert_contract('refresh_focus')

    def test_dashboard_plugin_legacy_and_literal_content(self):
        self.assert_contract('legacy_and_literal')

    def test_dashboard_plugin_link_mutants_fail_by_assertion(self):
        source = (ROOT / 'assets/dashboard.html').read_text()
        mutants = [
            ('remote-image', r"!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(src)", 'false', 'icon_sources'),
            ('http-link', "url.protocol!=='https:'", 'false', 'homepage_rejections'),
            ('opener', "link.setAttribute('rel','noopener noreferrer')", "link.setAttribute('rel','noreferrer')", 'header_sidebar'),
            ('same-tab', "link.setAttribute('target','_blank')", "link.setAttribute('target','_self')", 'header_sidebar'),
            ('missing-alt', "img.setAttribute('alt',tr('plugin.logo',{title:pluginLocal(manifest.title)}))", "img.setAttribute('alt','')", 'header_sidebar'),
            ('small-header', 'logo=pluginLogo(m,64),mark=', 'logo=pluginLogo(m,18),mark=', 'header_sidebar'),
            ('missing-sidebar', "b.append(pluginLogo(p,18)||icon('grid-1x2'))", "b.append(icon('grid-1x2'))", 'header_sidebar'),
            ('lost-focus', "if(pluginView.homepage)focusKey(pluginView.homepage,'plugin:homepage')", '', 'refresh_focus'),
            ('unsafe-text', "function pluginText(tag,text,cls){const e=document.createElement(tag);e.textContent=text", "function pluginText(tag,text,cls){const e=document.createElement(tag);e.innerHTML=text", 'legacy_and_literal'),
        ]
        for name, before, after, scenario in mutants:
            with self.subTest(mutant=name):
                self.assertEqual(source.count(before), 1, 'mutation seam must be unique')
                mutant = self.folder / (name + '.html')
                mutant.write_text(source.replace(before, after, 1))
                result = self.run_contract(scenario, mutant)
                self.assertNotEqual(result.returncode, 0, 'mutant survived: ' + name)
                self.assertIn('AssertionError', result.stderr, result.stdout + result.stderr)

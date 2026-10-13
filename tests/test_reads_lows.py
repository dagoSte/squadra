"""Reads refresh/cards/spacing and numbered plugin instructions, with unchanged-oracle mutants."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import test_reads_value

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = [
    ('detached-scroll', 'readsView.table=wrap;', 'readsView.table=null;wrap.scrollLeft=readsView.tableScroll||0;', 'scroll'),
    ('missing-card-label', "td.setAttribute('data-label',tr('rv.'+k))", "td.setAttribute('data-label','')", 'cards'),
    ('table-on-phone', '.sq-reads-table,.sq-reads-table .sq-reads-task{display:block;min-width:0}',
     '.sq-reads-table,.sq-reads-table .sq-reads-task{display:table;min-width:0}', 'cards'),
    ('empty-status-row', 'if(statusText){const status=', 'if(true){const status=', 'gap'),
    ('double-marker', '.sq-plugin-section ul.sq-plugin-numbered{list-style:none;padding-left:0}',
     '.sq-plugin-section ul.sq-plugin-numbered{list-style:disc;padding-left:0}', 'markers'),
]


class ReadsLows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = test_reads_value.FindingsAndRoute('test_page_counts_labels_and_xss')
        cls.addClassCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.review(findings=[{'severity': 'grave', 'title': 'Synthetic observation'}])
        request = fixture.serve()
        cls.folder = fixture.base
        cls.state = cls.folder / 'state.json'
        cls.state.write_text(json.dumps(request('/api/state')[1]))
        cls.data = cls.folder / 'data.json'
        cls.data.write_text(json.dumps({'reads': request()[1], 'plugin': json.loads(
            (ROOT / 'plugins/memoranda/plugin.json').read_text())}))

    @classmethod
    def run_contract(cls, scenario, page=None):
        node = shutil.which('node')
        if node is None:
            raise RuntimeError('Node required; no skipped contract')
        return subprocess.run([node, str(ROOT / 'tests/reads_lows_contract.js'),
                               str(page or ROOT / 'assets/dashboard.html'), str(cls.state), scenario, str(cls.data)],
                              capture_output=True, text=True, timeout=30)

    def check(self, scenario):
        result = self.run_contract(scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('reads lows passed: ' + scenario, result.stdout)

    def test_scroll_is_restored_after_dom_attachment(self): self.check('scroll')
    def test_phone_cards_keep_all_values_and_localized_labels(self): self.check('cards')
    def test_intro_gap_removed_and_real_status_retained(self): self.check('gap')
    def test_numbered_plugin_steps_have_one_marker_in_order(self): self.check('markers')

    def test_each_mutant_fails_an_assertion_with_passing_control(self):
        source = (ROOT / 'assets/dashboard.html').read_text()
        for name, before, after, scenario in MUTANTS:
            with self.subTest(mutant=name):
                self.check(scenario)
                self.assertEqual(source.count(before), 1, 'unique mutation site: ' + name)
                page = self.folder / (name + '.html')
                page.write_text(source.replace(before, after))
                result = self.run_contract(scenario, page)
                self.assertEqual(result.returncode, 1, 'mutant survived: ' + name)
                self.assertIn('AssertionError', result.stderr)
                self.assertNotIn('SyntaxError', result.stderr)
                self.assertNotIn('TypeError', result.stderr)

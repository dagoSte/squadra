"""Configurable result labels and inert links to the current registry."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plugins
from dashboard import make_server
from registry import Store
import test_plugins as fixtures
import test_plugin_about as about_tests

ROOT = Path(__file__).resolve().parents[1]


def results():
    return {'label': {'it': 'Collaudi', 'en': 'Checks'},
            'empty': {'it': 'Nessun controllo', 'en': 'No checks'},
            'status_labels': {'draft': {'it': 'Da verificare', 'en': 'To check'},
                              'approved': {'it': 'Superato', 'en': 'Passed'}}}


class PluginResultsSchema(fixtures.Fixtures, unittest.TestCase):
    def write(self, name, value):
        (self.base / name).write_text(json.dumps(value), encoding='utf-8')

    def install(self, value):
        m = fixtures.manifest(); m['results'] = value
        self.write('plugin.json', m)
        return m

    def test_optional_results_roundtrip_and_legacy_unchanged(self):
        self.assertEqual(plugins.discover(self.home)['plugins'], [fixtures.manifest()])
        m = self.install(results())
        self.assertEqual(plugins.discover(self.home)['plugins'], [m])
        self.assertEqual(plugins.load_panel(self.home, 'example', fixtures.KEY), fixtures.panel())
        self.install({'label': {'it': 'X', 'en': 'Y'}})
        self.assertEqual(plugins.discover(self.home)['refused'], [])

    def test_results_bilingual_bounds_and_invalid_types(self):
        for field, maximum in [('label', 80), ('empty', 300)]:
            for lang in ('it', 'en'):
                for value in ('X', '🌍' * maximum):
                    r = results(); r[field][lang] = value; self.install(r)
                    self.assertEqual(plugins.discover(self.home)['refused'], [])
                for value in ('', 'X' * (maximum + 1), '\ud800', 'x\u202ey', None, 4, [], True):
                    with self.subTest(field=field, lang=lang, value=repr(value)):
                        r = results(); r[field][lang] = value; self.install(r); self.refused('bad_value')
        for value in ([], '', None, True, 5):
            self.install(value); self.refused('bad_value')
        for path in ((), ('label',), ('empty',), ('status_labels',), ('status_labels', 'draft')):
            r = results(); target = r
            for key in path: target = target[key]
            target['unknown'] = 'secret-marker'; self.install(r); self.refused('unknown_key')
        r = results(); del r['label']['en']; self.install(r); self.refused('bad_value')

    def test_status_labels_do_not_change_selection_or_lifecycle(self):
        r = results(); r['status_labels'] = {s: {'it': s, 'en': s} for s in plugins.STATUSES}
        self.install(r); self.assertEqual(plugins.discover(self.home)['refused'], [])
        for label in ('', 'x' * 81, '\ud800', None, 8):
            bad = deepcopy(r); bad['status_labels']['draft']['it'] = label
            self.install(bad); self.refused('bad_value')
        self.install(r)
        for status in ('passed', 'custom', 'running'):
            p = fixtures.panel(); p['items'][0]['status'] = status
            self.write('panels/' + fixtures.KEY + '.json', p); self.refused('bad_value')
        p = fixtures.panel(); p['items'][0].update(status='approved', selectable=True)
        self.write('panels/' + fixtures.KEY + '.json', p); self.refused('bad_value')

    def test_references_roundtrip_bounds_types_and_closed_fields(self):
        for refs in ([], [{'task': 'T'}], [{'task': '🌍' * 200, 'run': 'a' * 32}] * 8):
            p = fixtures.panel(); p['items'][0]['refs'] = refs
            self.write('panels/' + fixtures.KEY + '.json', p)
            self.assertEqual(plugins.load_panel(self.home, 'example', fixtures.KEY), p)
        invalid = [None, {}, '', True, [{'task': 'T'}] * 9, [None], [{}],
                   [{'task': ''}], [{'task': 'x' * 201}], [{'task': 'T\n'}],
                   [{'task': '\u202eT'}], [{'task': '\ud800'}], [{'task': False}]]
        invalid += [[{'task': 'T', 'run': v}] for v in ('', 'a' * 31, 'a' * 33, 'A' * 32, '../file', None, 1, [])]
        for refs in invalid:
            with self.subTest(refs=repr(refs)):
                p = fixtures.panel(); p['items'][0]['refs'] = refs
                self.write('panels/' + fixtures.KEY + '.json', p); self.refused('bad_value')
        for field in ('url', 'path', 'sha256', 'status'):
            p = fixtures.panel(); p['items'][0]['refs'] = [{'task': 'T', field: 'untrusted'}]
            self.write('panels/' + fixtures.KEY + '.json', p); self.refused('unknown_key')


class PluginResultsAPI(fixtures.Fixtures, unittest.TestCase):
    stop = fixtures.Server.stop
    get = fixtures.Server.get

    def setUp(self):
        super().setUp()
        self.registry = Path(self.temp.name) / 'registry'
        store = Store(self.registry, create=True)
        try:
            store.append('init', 'system', {'name': 'Results tests', 'project': str(self.home),
                         'temporary': True, 'limits': {'plan': 3, 'code': 2, 'build': 3}})
            self.key = store.events()[0]['hash']
        finally:
            store.close()
        self.write('panels/' + self.key + '.json', fixtures.panel())
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            self.server, self.token = make_server(self.registry)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def test_real_discovery_panel_and_refusal_preserve_boundaries(self):
        m = fixtures.manifest(); m['results'] = results(); self.write('plugin.json', m)
        p = fixtures.panel(); p['items'][0]['refs'] = [{'task': 'not-yet-recorded', 'run': 'a' * 32}]
        self.write('panels/' + self.key + '.json', p)
        before = sorted(str(x.relative_to(self.home)) for x in self.home.rglob('*'))
        for path in ('/api/state', '/api/plugins'):
            code, headers, body = self.get(path)
            self.assertEqual(code, 200)
            found = body['plugins']['plugins'] if path == '/api/state' else body['plugins']
            self.assertEqual(found, [m])
            self.assertEqual(headers['Content-Security-Policy'], fixtures.POLICY)
        self.assertEqual(self.get('/api/plugin/example')[2]['panel'], p)
        self.assertEqual(self.get('/api/plugin/example', token=False)[0], 401)
        self.assertEqual(self.get('/api/plugin/example', host='foreign.invalid')[0], 403)
        self.assertEqual(self.get('/api/plugin/example', method='POST')[0], 405)
        self.assertEqual(sorted(str(x.relative_to(self.home)) for x in self.home.rglob('*')), before)
        p['items'][0]['refs'][0]['path'] = '/private/secret'
        self.write('panels/' + self.key + '.json', p)
        self.assertEqual(self.get('/api/plugin/example')[::2], (409, {'error': 'unknown_key'}))


class PluginResultsJavaScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        about_tests.PluginAboutJavaScript.setUpClass.__func__(cls)

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node required; dashboard JS contracts cannot be skipped')
        result = subprocess.run([node, str(ROOT / 'tests/plugin_results_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(self.fixture), scenario],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('plugin results contract passed: ' + scenario, result.stdout)

    def test_dashboard_results_labels_languages_and_legacy(self):
        self.contract('labels')

    def test_dashboard_results_with_no_about_and_empty_panels(self):
        self.contract('empty')

    def test_dashboard_results_task_and_exact_run_navigation(self):
        self.contract('links')

    def test_dashboard_results_unavailable_foreign_and_prototype_refs(self):
        self.contract('refused')

    def test_dashboard_results_refresh_updates_reference_validity(self):
        self.contract('refresh')

    def test_dashboard_results_polling_and_focus_do_not_add_io(self):
        self.contract('polling')

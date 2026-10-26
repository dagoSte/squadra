"""Read counts are facts about events, never inferred defect or success counts."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from reads import read_value, validate_findings, load_findings, FINDINGS_BYTES
from registry import Store, Error
from squadra import main
from dashboard_test_support import make_server
import dashboard


def events_fixture():
    events = []
    def put(kind, **data):
        seq = len(events) + 1
        events.append({'seq': seq, 'id': 'event-' + str(seq), 'hash': str(seq), 'kind': kind,
                       'actor': 'reader', 'utc': '2026-10-13T10:00:00+00:00', 'data': data})
    put('task', id='A', title='Synthetic task')
    put('plan', task='A', artifact={'sha256': 'p1'})
    put('review', task='A', stage='plan', verdict='changes-required', binding='p1', covers=['R1'])
    put('plan', task='A', artifact={'sha256': 'p2'})
    put('deliver', task='A', snapshot={'sha256': 'v1'})
    put('review', task='A', stage='code', verdict='changes-required', binding='v1', covers=['R1', 'R2'],
        findings=[{'severity': 'grave', 'title': 'Synthetic boundary issue'}])
    put('deliver', task='A', snapshot={'sha256': 'v2'})
    put('review', task='A', stage='code', verdict='accepted', binding='v2', findings=[])
    return events


class Projection(unittest.TestCase):
    def test_exact_counts_and_linked_versions(self):
        data = read_value(events_fixture())
        self.assertEqual(data['totals']['reads'], 3)
        self.assertEqual(data['totals']['verdicts'], {'accepted': 1, 'changes-required': 2, 'blocked': 0, 'unsupported': 0})
        self.assertEqual((data['totals']['plan_revisions'], data['totals']['code_revisions']), (1, 1))
        self.assertEqual([(r['read_seq'], r['from_seq'], r['to_seq']) for r in data['revisions']], [(3, 2, 4), (6, 5, 7)])
        self.assertEqual(data['reads'][1]['findings'][0]['id'], 'event-6:0')

    def test_diagnostics_and_prose_are_not_reads(self):
        events = events_fixture()
        for kind in ('diagnostic', 'decision', 'output', 'run.end'):
            events.append({**events[2], 'kind': kind, 'data': {**events[2]['data'], 'error': 'changes-required',
                           'response': {'findings': ['defect'], 'verdict': 'changes-required'}}})
        self.assertEqual(read_value(events)['totals']['reads'], 3)

    def test_covers_are_not_findings(self):
        events = events_fixture()
        events[5]['data']['covers'] = ['x', 'y', 'z']
        self.assertEqual(read_value(events)['totals']['observations'], 1)

    def test_absent_empty_and_unknown_are_distinct(self):
        events = events_fixture()[:3]
        self.assertIsNone(read_value(events)['totals']['observations'])
        events[-1]['data']['findings'] = []
        self.assertEqual(read_value(events)['totals']['observations'], 0)
        events[-1]['data']['findings'] = [{'severity': 'future', 'title': 'Future format'}]
        data = read_value(events)
        self.assertIsNone(data['totals']['observations'])
        self.assertEqual(data['totals']['unsupported_reads'], 1)
        self.assertEqual(data['reads'][0]['findings'], [])

    def test_same_hash_submission_is_not_a_revision(self):
        events = events_fixture()
        events[6]['data']['snapshot']['sha256'] = 'v1'
        self.assertEqual(read_value(events)['totals']['code_revisions'], 0)

    def test_later_accepted_or_blocked_supersedes_request(self):
        for verdict in ('accepted', 'blocked'):
            events = events_fixture()
            events.insert(6, {**deepcopy(events[5]), 'id': 'superseding', 'seq': 60})
            events[6]['data']['verdict'] = verdict
            self.assertEqual(read_value(events)['totals']['code_revisions'], 0)

    def test_multiple_reads_before_one_revision_count_once(self):
        events = events_fixture()
        events.insert(6, {**deepcopy(events[5]), 'id': 'second-reader', 'seq': 60, 'actor': 'another-reader'})
        data = read_value(events)
        self.assertEqual(data['totals']['reads'], 4)
        self.assertEqual(data['totals']['code_revisions'], 1)
        self.assertEqual(data['revisions'][-1]['read_id'], 'second-reader')

    def test_cross_task_and_stage_do_not_create_revisions(self):
        for field, value in (('task', 'B'), ('stage', 'plan')):
            events = events_fixture()
            events[5]['data'][field] = value
            self.assertEqual(read_value(events)['totals']['code_revisions'], 0)

    def test_pending_read_and_superseded_task_are_not_erased(self):
        events = events_fixture()[:6]
        events.append({**events[0], 'kind': 'supersede', 'data': {'task': 'A', 'by': 'B'}})
        data = read_value(events)
        self.assertEqual(data['totals']['reads'], 2)
        self.assertEqual(data['totals']['code_revisions'], 0)
        self.assertEqual(len(data['tasks']), 1)

    def test_empty_and_unknown_verdict(self):
        self.assertEqual(read_value([])['totals']['reads'], 0)
        self.assertIsNone(read_value([])['totals']['accepted_with_conditions'])
        events = events_fixture()
        events[-1]['data']['verdict'] = 'accepted-with-conditions'
        self.assertEqual(read_value(events)['totals']['verdicts']['unsupported'], 1)


def seed(store, base):
    """Synthetic ordinary registry, also used by the bounded capture recipe."""
    store.append('init', 'system', {'name': 'Synthetic read evidence', 'project': 'Synthetic project',
                                  'version': '3.0.0', 'limits': {'plan': 40, 'code': 40, 'build': 3}})
    for name, role in [('C', 'coordinator'), ('A', 'builder'), ('R', 'reviewer')]:
        store.append('agent', name, {'id': name, 'identity': name, 'role': role, 'client': 'manual',
                                   'repo': str(base), 'skills': [], 'session': ''})
    artifact = store.artifact_bytes(b'Synthetic read report.\n', 'read-report.md', 'synthetic-report')
    store.append('task', 'C', {'id': 'T', 'title': 'Validate the example', 'owner': 'A', 'repo': str(base),
                             'base': 'a'*40, 'scope': artifact, 'proof': None, 'depends': []})
    store.append('message', 'C', {'id': 'assign-T', 'to': 'A', 'task': 'T', 'body': 'Synthetic assignment'})
    store.append('ack', 'A', {'message': 'assign-T'})
    store.append('start', 'A', {'task': 'T'})
    store.append('plan', 'A', {'task': 'T', 'artifact': artifact})
    store.append('review', 'R', {'task': 'T', 'stage': 'plan', 'verdict': 'changes-required',
                               'binding': artifact['sha256'], 'covers': [], 'report': artifact})
    revised = store.artifact_bytes(b'Synthetic revised plan.\n', 'plan.md', 'synthetic-plan')
    store.append('plan', 'A', {'task': 'T', 'artifact': revised})
    store.append('review', 'R', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted',
                               'binding': revised['sha256'], 'covers': [], 'report': artifact, 'findings': []})
    return artifact, revised


class FindingsAndRoute(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sq-reads-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        self.report, self.plan = seed(self.store, self.base)

    def review(self, **extra):
        self.store.append('review', 'R', {'task': 'T', 'stage': 'plan', 'verdict': 'accepted',
                                 'binding': self.plan['sha256'], 'covers': [], 'report': self.report, **extra})
        return self.store.events()[-1]

    def test_validation_boundaries(self):
        for value in ([], [{'severity': 'grave', 'title': 'a'}], [{'severity': 'minor', 'title': 'é'*200}],
                      [{'severity': 'info', 'title': 'x'}]*32):
            self.assertEqual(validate_findings(value), value)
        for value in (None, {}, True, ['x'], [{'severity': 'low', 'title': 'x'}],
                      [{'severity': [], 'title': 'x'}], [{'severity': 'minor', 'title': ''}],
                      [{'severity': 'minor', 'title': ' '*5}], [{'severity': 'minor', 'title': 'x'*201}],
                      [{'severity': 'info', 'title': 'x', 'extra': 1}], [{'severity': 'info', 'title': 'x'}]*33,
                      [{'severity': 'info', 'title': '界'*200}]*32):
            with self.subTest(value=repr(value)[:60]), self.assertRaises(ValueError):
                validate_findings(value)
        for char in ('\n', '\r', '\t', '\0', '\x7f', '\u2028', '\u2029', '\u200b', '\ud800'):
            with self.subTest(char=repr(char)), self.assertRaises(ValueError):
                validate_findings([{'severity': 'info', 'title': 'before'+char+'after'}])

    def test_file_utf8_duplicate_keys_and_size(self):
        p = self.base / 'findings.json'
        for body in (b'\xff', b'[] trailing', b'[{"title":"x","title":"y","severity":"info"}]',
                     b' '*FINDINGS_BYTES+b'[]'):
            p.write_bytes(body)
            with self.assertRaises(ValueError): load_findings(p)
        p.write_bytes(b'[]'+b' '*(FINDINGS_BYTES-2))
        self.assertEqual(load_findings(p), [])

    def test_store_boundary_and_optional_event_shape(self):
        old = self.review()
        self.assertNotIn('findings', old['data'])
        explicit = self.review(findings=[])
        self.assertEqual(explicit['data']['findings'], [])
        count = len(self.store.events())
        with self.assertRaises(ValueError): self.review(findings=[{'title': 'incomplete'}])
        self.assertEqual(len(self.store.events()), count)

    def test_cli_option_and_no_prose_inference(self):
        file = self.base / 'finding.json'; file.write_text('[{"severity":"minor","title":"A safe summary"}]')
        report = self.base / 'report.md'; report.write_text('changes-required: imaginary extra findings')
        args = ['--root', str(self.store.root), 'review', '--actor', 'R', '--task', 'T', '--stage', 'plan',
                '--verdict', 'accepted', '--report', str(report), '--findings-file', str(file)]
        # The CLI also checks the original plan's bytes. Keep that fixture explicit.
        plan = self.base / 'plan.md'; plan.write_bytes(b'Synthetic revised plan.\n')
        self.store.append('plan', 'A', {'task': 'T', 'artifact': self.store.artifact(plan)})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)
        last = [e for e in self.store.events() if e['kind'] == 'review'][-1]
        self.assertEqual(last['data']['verdict'], 'accepted')
        self.assertEqual(last['data']['findings'], [{'severity': 'minor', 'title': 'A safe summary'}])
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'working')
        file.write_text('[{"severity":"info","title":"x","title":"y"}]')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.assertEqual(len([e for e in self.store.events() if e['kind'] == 'review']), 3)

    def serve(self):
        server, token = make_server(self.store.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def stop():
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop)
        def request(route='/api/reads', authorized=True):
            req = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_port, route),
                                         headers={'X-Squadra-Token': token} if authorized else {})
            try:
                with urllib.request.urlopen(req, timeout=5) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                with exc: return exc.code, json.load(exc)
        return request

    def test_route_auth_full_history_and_cache(self):
        for i in range(130): self.store.append('diagnostic', 'system', {'command': 'review', 'error': 'synthetic refusal'})
        request = self.serve()
        self.assertEqual(request(authorized=False)[0], 401)
        with patch.object(dashboard, 'read_value', wraps=read_value) as projection:
            status, data = request()
            self.assertEqual(status, 200); self.assertEqual(data['totals']['reads'], 2)
            self.assertEqual(data['totals']['plan_revisions'], 1)
            self.assertEqual(len(request('/api/state')[1]['events']), 120)
            self.assertEqual(request()[1], data)
            self.assertEqual(projection.call_count, 1)
            self.review(findings=[{'severity': 'minor', 'title': 'Safe summary'}])
            latest = request()[1]
            self.assertEqual(latest['totals']['reads'], 3)
            self.assertNotEqual(latest['head'], data['head'])
            self.assertEqual(projection.call_count, 2)

    def test_missing_report_and_corrupt_chain_fail_visibly(self):
        request = self.serve()
        self.assertEqual(request()[0], 200)
        (self.store.root/'artifacts'/self.report['sha256']).unlink()
        self.assertEqual(request('/api/artifact/'+self.report['sha256'])[0], 409)
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=2")
        self.assertEqual(request()[0], 409)

    def browser(self, scenario):
        request = self.serve()
        self.review(findings=[{'severity': 'grave', 'title': '<img src=x onerror=alert(1)> & synthetic'}])
        state = self.base/'state.json'; state.write_text(json.dumps(request('/api/state')[1]))
        data = self.base/'reads.json'; data.write_text(json.dumps(request()[1]))
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node required; no skipped browser contracts')
        result = subprocess.run([node, str(ROOT/'tests/reads_value_contract.js'), str(ROOT/'assets/dashboard.html'),
                                 str(state), scenario, str(data)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('reads contract passed: '+scenario, result.stdout)

    def test_page_counts_labels_and_xss(self): self.browser('counts')
    def test_page_filters_and_links(self): self.browser('filters')
    def test_page_fetch_only_open_single_inflight(self): self.browser('fetches')
    def test_page_failures_and_empty(self): self.browser('states')
    def test_page_focus_expansion_and_reading_place(self): self.browser('focus')
    def test_page_mobile_and_theme(self): self.browser('shell')

    def test_page_filter_intrinsic_width_can_shrink(self): self.browser('filterSizing')
    def test_page_dedicated_read_icon(self): self.browser('readIcon')
    def test_page_missing_category_is_quiet(self): self.browser('missingCategoryStyle')

"""Observable forecasts, bounded guides, audit provenance and full-history counts."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import contextlib
import hashlib
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
import forecast
import system_skills as skills
from registry import snapshot, Error, canonical, Store
from squadra import main
import test_squadra as support
from dashboard_test_support import make_server
from test_reads_value import seed

ORIGIN = datetime(2026, 10, 14, tzinfo=timezone.utc)
NOW = ORIGIN.timestamp() + 86400


class RoutesAndPage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='sq-skills-page-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        seed(self.store, self.base)
        self.store.project()
        self.server, self.token = make_server(self.store.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def request(self, route='/api/skills', **headers):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.server.server_port, route),
                                     headers={'X-Squadra-Token': self.token, **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as result:
                return result.status, json.load(result), dict(result.headers)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc), dict(exc.headers)

    def test_api_existing_auth_host_and_no_registry_writes(self):
        def frozen():
            return {str(p.relative_to(self.store.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.store.root.rglob('*') if p.is_file() and not p.name.endswith(('-shm', '-wal'))}
        before = frozen()
        for route in ('/api/skills', '/api/forecast'):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, **{'X-Squadra-Token': 'wrong'})[0], 401)
                self.assertEqual(self.request(route, Host='example.invalid')[0], 403)
                code, body, headers = self.request(route, **{'X-Squadra-Record': 'T'})
                self.assertEqual(code, 200)
                self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
                if route.endswith('skills'):
                    self.assertEqual([s['id'] for s in body['skills']], ['errata', 'previsione', 'rasoio'])
                else:
                    self.assertEqual(body['task'], 'T')
        self.assertEqual(self.request('/api/forecast', **{'X-Squadra-Record': 'unknown'})[0], 409)
        self.assertEqual(frozen(), before)

    def test_api_rechecks_old_event_tamper_for_both_routes(self):
        self.assertEqual(self.request()[0], 200)
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data=? WHERE seq=1", ('{}',))
        self.store.db.commit()
        for route in ('/api/skills', '/api/forecast'):
            self.assertEqual(self.request(route, **{'X-Squadra-Record': 'T'})[0], 409)

    def contract(self, scenario):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; dashboard contracts cannot be skipped')
        code, state, _ = self.request('/api/state')
        self.assertEqual(code, 200)
        payload = skills.catalogue(activity_fixture(), state, read_at=NOW)
        fixture, cases = self.base / 'state.json', self.base / 'skills.json'
        fixture.write_text(json.dumps(state))
        cases.write_text(json.dumps(payload))
        result = subprocess.run([node, str(ROOT / 'tests/system_skills_contract.js'),
                                 str(ROOT / 'assets/dashboard.html'), str(fixture), scenario, str(cases)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('system skills contract passed: ' + scenario, result.stdout)

    def test_js_system_skills_directory_search_shortcut_tabs_bilingual(self):
        self.contract('directory')

    def test_js_system_skills_detail_origin_event_task_links_and_focus(self):
        self.contract('detail')

    def test_js_system_skills_safe_guide_expansion_and_no_remote_assets(self):
        self.contract('safeGuide')

    def test_js_system_skills_audit_badges_and_changed_files(self):
        self.contract('audits')

    def test_js_system_skills_open_only_serial_fetch_and_retained_focus(self):
        self.contract('polling')

    def test_js_system_skills_failure_retry_and_honest_zero(self):
        self.contract('failureAndZero')


def event(seq, kind, data, minute=0):
    return {'seq': seq, 'id': 'e' + str(seq), 'kind': kind, 'data': data, 'actor': 'A',
            'utc': (ORIGIN + timedelta(minutes=minute)).isoformat(), 'hash': str(seq)}


def history(task, correction=False):
    specs = [(0, 'task', {'id': task}), (0, 'message', {'id': 'assign-' + task, 'task': task}),
             (1, 'ack', {'message': 'assign-' + task}), (2, 'plan', {'task': task}),
             (62, 'review', {'task': task, 'stage': 'plan', 'verdict': 'accepted'}),
             (63, 'start', {'task': task}), (65, 'deliver', {'task': task}),
             (67, 'review', {'task': task, 'stage': 'code', 'verdict': 'changes-required' if correction else 'accepted'})]
    if correction:
        specs += [(69, 'deliver', {'task': task}), (71, 'review', {'task': task, 'stage': 'code', 'verdict': 'accepted'})]
    specs += [(72 if correction else 68, 'close', {'task': task})]
    return [event(i + 1, k, d, m) for i, (m, k, d) in enumerate(specs)]


def activity_fixture():
    return [event(1, 'lesson', {'id': 'L', 'text': 'Keep a valid control', 'trigger': {'paths': [], 'words': ['proof']},
                              'source': {'task': 'T', 'event': 1, 'eligible': False}}),
            event(2, 'lesson.recall', {'task': 'T', 'at': 'brief', 'lessons': ['L', 'M']}, 1),
            event(3, 'lesson.recall', {'task': 'T', 'at': 'start', 'lessons': ['L']}, 2),
            event(4, 'lesson.ineffective', {'lesson': 'L', 'recall': 2, 'reason': 'Repeated'}, 3),
            event(5, 'lesson.retire', {'lesson': 'L', 'reason': 'Replaced'}, 4),
            event(6, 'review', {'task': 'T', 'stage': 'code', 'lens': 'rasoio', 'net_lines': 7}, 5),
            event(7, 'review', {'task': 'T', 'stage': 'code', 'lens': 'rasoio'}, 6)]


class Forecast(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def corpus(self, count=3):
        state = self.store.state()
        base = state['tasks']['T']['base']
        (self.repo / 'docs').mkdir(exist_ok=True)
        (self.repo / 'docs/scope.md').write_text('A small documented change\n')
        candidate = snapshot(self.repo)
        events = []
        for i in range(count):
            name = 'H' + str(i)
            state['tasks'][name] = {'id': name, 'status': 'done', 'repo': str(self.repo), 'base': base,
                                   'delivery': {'snapshot': deepcopy(candidate)}}
            events += history(name, correction=i == 1)
        return state, events

    def test_plan_wait_and_correction_partition(self):
        value = forecast.timings(history('H'), 'H')
        self.assertEqual(value['seconds'], dict(ack=60, plan=3600, build=240, read=120, fix=0, other=60))
        again = forecast.timings(history('H', True), 'H')
        self.assertEqual(again['seconds']['build'], 240)
        self.assertEqual(again['seconds']['read'], 240)
        self.assertEqual(again['seconds']['fix'], 120)
        self.assertEqual((again['changes_required'], again['code_reads']), (1, 2))

    def test_three_samples_exact_medians_verdict_denominator_and_no_mutation(self):
        state, events = self.corpus()
        before = canonical([state, events])
        value = forecast.calculate(state, events, 'T')
        self.assertEqual(value['status'], 'available')
        self.assertEqual([s['task'] for s in value['samples']], ['H0', 'H1', 'H2'])
        self.assertEqual((value['median_build_minutes'], value['median_read_minutes']), (4, 2))
        self.assertEqual(value['changes_required'], {'numerator': 1, 'denominator': 4, 'percent': 25})
        self.assertEqual(value['suggested_route'], 'full')
        self.assertEqual(before, canonical([state, events]))

    def test_below_minimum_no_estimate(self):
        state, events = self.corpus(2)
        value = forecast.calculate(state, events, 'T')
        self.assertEqual(value['status'], 'not_enough_history')
        self.assertNotIn('median_build_minutes', value)
        self.assertNotIn('changes_required', value)
        for sample in value['samples']:
            self.assertNotIn('seconds', sample)
            self.assertNotIn('changes_required', sample)

    def test_clean_new_task_unknown_not_zero_scope(self):
        self.assertEqual(forecast.calculate(self.store.state(), [], 'T')['status'], 'scope_unavailable')

    def test_route_band_overlap_and_supersession_filter(self):
        state, events = self.corpus(5)
        state['tasks']['H0']['route'] = 'light'
        state['tasks']['H1']['superseded_by'] = 'H2'
        row = state['tasks']['H2']['delivery']['snapshot']['files'][-1]
        row['path'] = 'other/scope.md'
        rows = state['tasks']['H3']['delivery']['snapshot']['files']
        rows.extend({**row, 'path': 'docs/file-%d.md' % i} for i in range(6))
        value = forecast.calculate(state, events, 'T')
        self.assertEqual([s['task'] for s in value['samples']], ['H4'])
        self.assertEqual(len(value['excluded']), 3)

    def test_missing_history_and_incomplete_timing_are_excluded(self):
        state, events = self.corpus()
        state['tasks']['H0']['base'] = '0' * 40
        state['tasks']['H1']['delivery'] = {}
        events = [e for e in events if not (e['kind'] == 'review' and e['data'].get('task') == 'H2')]
        value = forecast.calculate(state, events, 'T')
        self.assertEqual(value['samples'], [])
        self.assertEqual({r['reason'] for r in value['excluded']}, {'historical_scope_unavailable', 'incomplete_timing'})

    def test_unavailable_scope_timeout_and_malformed_history(self):
        state, events = self.corpus()
        with patch.object(forecast, 'working_snapshot', side_effect=subprocess.TimeoutExpired('reader', 15)):
            state['tasks']['T'].pop('candidate', None)
            self.assertEqual(forecast.calculate(state, events, 'T')['status'], 'scope_unavailable')
        for task in ('H0', 'H1', 'H2'):
            state['tasks'][task]['delivery']['snapshot']['files'] = [None]
        self.assertEqual(forecast.calculate(state, events, 'T')['samples'], [])
        raw = history('X')
        raw.insert(0, event(0, 'message', {'id': 'unrelated', 'task': 'X'}))
        raw.insert(1, event(0, 'ack', {'message': 'unrelated'}))
        self.assertEqual(forecast.timings(raw, 'X')['seconds']['build'], 240)

    def test_historical_snapshot_not_current_tree_and_delete_mode_changes(self):
        state, events = self.corpus()
        saved = deepcopy(state['tasks']['H0']['delivery']['snapshot'])
        (self.repo / 'code.py').unlink()
        self.assertIn('code.py', forecast.changed_paths(state['tasks']['T'], snapshot(self.repo)))
        self.assertEqual(forecast.changed_paths(state['tasks']['H0'], saved), ['docs/scope.md'])
        (self.repo / 'code.py').write_text('VALUE = 1\n'); (self.repo / 'code.py').chmod(0o755)
        self.assertIn('code.py', forecast.changed_paths(state['tasks']['T'], snapshot(self.repo)))

    def test_unknown_closed_and_invalid_dates(self):
        state, events = self.corpus()
        with self.assertRaisesRegex(Error, 'Unknown task'):
            forecast.calculate(state, events, 'missing')
        self.assertEqual(forecast.calculate(state, events, 'H0')['status'], 'task_not_open')
        bad = history('X'); bad[-1]['utc'] = 'bad'
        self.assertIsNone(forecast.timings(bad, 'X'))
        bad = history('X'); bad[-1]['utc'] = (ORIGIN - timedelta(days=1)).isoformat()
        self.assertIsNone(forecast.timings(bad, 'X'))

    def frozen(self):
        return {str(p.relative_to(self.store.root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.store.root.rglob('*') if p.is_file() and not p.name.endswith(('-wal', '-shm'))}

    def test_cli_read_only_success_unknown_and_missing_registry(self):
        self.store.project()
        before = self.frozen()
        for task, rc in [('T', 0), ('unknown', 1)]:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['forecast', '--task', task, '--root', str(self.root)]), rc)
            self.assertEqual(before, self.frozen())
        missing = self.base / 'absent'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['forecast', '--task', 'T', '--root', str(missing)]), 1)
        self.assertFalse(missing.exists())

    def test_read_only_sqlite_sidecars_absent_and_with_writer(self):
        self.store.project()
        wal = self.store.path.with_name(self.store.path.name + '-wal')
        for writer_open in (True, False):
            if not writer_open:
                self.store.close()
            before = self.frozen()
            previous_wal = (wal.read_bytes(), wal.stat().st_mtime_ns) if wal.exists() else None
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['forecast', '--task', 'T', '--root', str(self.root)]), 0)
            self.assertEqual(before, self.frozen())
            if previous_wal is not None:
                self.assertEqual((wal.read_bytes(), wal.stat().st_mtime_ns), previous_wal)
            elif wal.exists():
                self.assertEqual(wal.stat().st_size, 0)

    def test_start_brief_and_task_output_forecast_without_activation_event(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        for args in [('start', '--actor', 'A', '--task', 'T'),
                     ('brief', '--actor', 'C', '--task', 'T', '--file', str(self.scope)),
                     ('task', '--id', 'new', '--actor', 'C', '--owner', 'A', '--repo', str(self.repo),
                      '--title', 'New', '--scope-file', str(self.scope))]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--root', str(self.root), *args]), 0)
            self.assertIn('"forecast":', out.getvalue())
        self.assertFalse(any(e['kind'].startswith('forecast') for e in self.store.events()))


class Audits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in skills.required_files('previsione'):
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('# Safe package\n')
        self.record = {'skill': 'previsione', 'scanner': skills.SCANNER, 'version': '2.2.2', 'policy': dict(skills.POLICY),
                       'scanned_at': ORIGIN.isoformat(), 'result': 'pass', 'findings': dict(critical=0, high=0, medium=0, low=0),
                       'files': {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                                 for name in skills.required_files('previsione')}}
        self.save()

    def save(self, record=None):
        (self.root / 'assets/skill-audits.json').write_text(json.dumps({'schema': 1, 'records': [self.record if record is None else record]}))

    def status(self):
        return skills.audit(self.root, 'previsione', NOW)['status']

    def test_pass_warn_fail_and_contradictory_claim(self):
        self.assertEqual(self.status(), 'pass')
        for severity, expected in [('low', 'warn'), ('medium', 'warn'), ('high', 'fail'), ('critical', 'fail')]:
            record = deepcopy(self.record); record['findings'][severity] = 1
            self.save(record); self.assertEqual(self.status(), 'not_audited')
            record['result'] = expected; self.save(record); self.assertEqual(self.status(), expected)

    def test_guide_and_dependency_hash_mutants(self):
        for name in ['references/skills/previsione.md', 'scripts/forecast.py', 'scripts/system_skills.py']:
            p = self.root / name; saved = p.read_bytes(); p.write_bytes(saved + b'changed')
            self.assertEqual(self.status(), 'not_audited', name)
            p.write_bytes(saved); self.assertEqual(self.status(), 'pass')

    def test_missing_malformed_unknown_provider_version_and_date(self):
        p = self.root / 'assets/skill-audits.json'
        p.unlink(); self.assertEqual(self.status(), 'not_audited')
        p.write_text('{'); self.assertEqual(self.status(), 'not_audited')
        for key, value in [('scanner', 'invented'), ('version', '9'), ('scanned_at', 'bad'), ('scanned_at', '2099-01-01T00:00:00Z')]:
            record = deepcopy(self.record); record[key] = value; self.save(record)
            self.assertEqual(self.status(), 'not_audited')

    def test_incomplete_extra_traversal_and_absolute_manifests(self):
        for path in ['../outside', '/tmp/file', 'scripts/extra.py']:
            record = deepcopy(self.record); record['files'][path] = '0' * 64; self.save(record)
            self.assertEqual(self.status(), 'not_audited')
        record = deepcopy(self.record); del record['files']['scripts/forecast.py']; self.save(record)
        self.assertEqual(self.status(), 'not_audited')

    def test_symlink_file_directory_and_bounded_guide(self):
        target = self.root / 'outside'; target.write_text('# Unsafe redirected guide')
        guide = self.root / 'references/skills/previsione.md'; guide.unlink(); guide.symlink_to(target)
        self.assertEqual(self.status(), 'not_audited')
        self.assertIsNone(skills.catalogue([], {'tasks': {}}, self.root, NOW)['skills'][1]['guide'])
        guide.unlink(); guide.write_text('x' * 65537)
        self.assertIsNone(skills.catalogue([], {'tasks': {}}, self.root, NOW)['skills'][1]['guide'])
        folder = self.root / 'references/skills'; moved = self.root / 'moved'; folder.rename(moved); folder.symlink_to(moved)
        self.assertIsNone(skills.catalogue([], {'tasks': {}}, self.root, NOW)['skills'][1]['guide'])

    def test_audit_json_duplicate_and_wrong_count_types(self):
        p = self.root / 'assets/skill-audits.json'; p.write_text('{"schema":1,"schema":1,"records":[]}')
        self.assertEqual(self.status(), 'not_audited')
        for value in [True, -1, 1.5, '0', None]:
            record = deepcopy(self.record); record['findings']['high'] = value; self.save(record)
            self.assertEqual(self.status(), 'not_audited')
        record = deepcopy(self.record); record['policy']['behavioral'] = 1; self.save(record)
        self.assertEqual(self.status(), 'not_audited')
        p.write_text(json.dumps({'schema': True, 'records': [self.record]}))
        self.assertEqual(self.status(), 'not_audited')
        p.write_text('[' * 2000 + ']' * 2000)
        self.assertEqual(self.status(), 'not_audited')


class Activity(unittest.TestCase):
    def test_empty_and_unrecorded_are_distinct(self):
        values = skills.catalogue([], {'tasks': {}}, read_at=NOW)['skills']
        self.assertEqual([s['total'] for s in values], [0, None, 0])
        self.assertEqual(values[0]['daily'], [0] * 56)
        self.assertIsNone(values[1]['daily']); self.assertIsNone(values[1]['first'])

    def test_distinct_recalls_lifecycle_and_declared_net_lines(self):
        values = skills.catalogue(activity_fixture(), {'tasks': {'T': {}}}, read_at=NOW)['skills']
        self.assertEqual(values[0]['total'], 5)
        self.assertEqual(values[0]['counts'], dict(lessons=1, recalled_pairs=2, recall_events=2, ineffective=1, reads=0, net_lines=0))
        self.assertEqual(values[2]['total'], 2); self.assertEqual(values[2]['counts']['net_lines'], 7)
        self.assertEqual(values[0]['last']['task_relation'], 'origin')
        self.assertEqual(values[0]['recent'][1]['task_relation'], 'origin')
        self.assertEqual(values[0]['first']['record']['data']['text'], 'Keep a valid control')
        self.assertTrue(values[0]['first']['outside_standard_sources'])

    def test_full_history_utc_boundaries_and_missing_origin(self):
        events = [event(i + 1, 'review', {'task': 'absent', 'lens': 'rasoio'}, -i) for i in range(140)]
        rows = skills.catalogue(events, {'tasks': {}}, read_at=ORIGIN.timestamp())['skills']
        self.assertEqual(rows[2]['total'], 140)
        self.assertEqual(sum(rows[2]['daily']), 140)
        self.assertEqual(rows[2]['last_24h'], 140)
        self.assertIsNone(rows[2]['last']['task'])
        edges = [event(1, 'review', {'lens': 'rasoio'}, -1440), event(2, 'review', {'lens': 'rasoio'}, -1441),
                 event(3, 'review', {'lens': 'rasoio'}, 1)]
        row = skills.catalogue(edges, {'tasks': {}}, read_at=ORIGIN.timestamp())['skills'][2]
        self.assertEqual((row['total'], row['last_24h']), (2, 1))
        self.assertEqual(sum(row['daily']), 2)

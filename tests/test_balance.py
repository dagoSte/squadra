"""Balance advice: order, independence, missing data, quota slots and the Claude estimate."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import balance
from registry import Error, canonical

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
AT = NOW.timestamp()


def iso(minutes=0):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def agent(seat, identity, role, client, model=''):
    return {'id': seat, 'identity': identity, 'role': role, 'client': client, 'model': model}


def task(task_id, owner, status='working', reviewer=None, pool=None, authors=None):
    return {'id': task_id, 'owner': owner, 'status': status, 'coordinator': 'coord', 'reviewer': reviewer,
            'pool': pool, 'reviews': [], 'authors': authors or [owner], 'proofs': []}


def base_state():
    agents = [agent('coord', 'w-coord', 'coordinator', 'claude', 'opus'),
              agent('cx-build', 'w-cx', 'builder', 'codex', 'gpt'),
              agent('cx-review', 'w-cx', 'reviewer', 'codex', 'gpt'),
              agent('cl-build', 'w-cl', 'builder', 'claude', 'opus'),
              agent('cl-review', 'w-cl', 'reviewer', 'claude', 'opus'),
              agent('cy-review', 'w-cy', 'reviewer', 'codex', 'gpt')]
    return {'agents': {a['id']: a for a in agents}, 'tasks': {'T': task('T', 'cl-build')},
            'messages': {}, 'workspace': {}}


def quota(percent, minutes=300, limit='primary', age=1, reset=60, status='measured'):
    return {'identity': None, 'model': 'gpt', 'observed_at': iso(-age), 'limit': limit, 'used_percent': percent,
            'window_minutes': minutes, 'resets_at': iso(reset) if reset is not None else None,
            'plan_type': 'pro', 'status': status}


def window(identity, client, rate=10, quotas=(), h5=None, d7=None):
    def rolling(total):
        return ({'status': 'measured', 'total': total, 'reason': None, 'from': iso(-300), 'to': iso()}
                if total is not None else {'status': 'not_measured', 'total': None, 'reason': 'no_source', 'from': None, 'to': None})
    return {'identity': identity, 'actors': [], 'client': client, 'status': 'measured', 'reasons': [], 'totals': None,
            'models': [], 'recent': {'seconds': 900, 'total_tokens': None if rate is None else rate * 15,
                                     'tokens_per_minute': rate, 'observed_until': iso(),
                                     'status': 'measured' if rate is not None else 'not_measured'},
            'quota': list(quotas), 'hourly': [{'hour': iso(-60 * i), 'status': 'measured', 'total': i, 'reason': None}
                                              for i in range(168)],
            'rolling': {'h5': rolling(h5), 'd7': rolling(d7)}}


def usage(*windows):
    return {'schema': 1, 'observed_at': iso(), 'status': 'measured',
            'coverage': {'status': 'ready', 'sources': 1, 'measured': 1, 'reasons': []},
            'windows': list(windows), 'models': [], 'tasks': [], 'days': [], 'quota_history': [],
            'suggestions': [], 'cost': None}


def full_usage(cx=(), cy=(), cl_h5=1000, cl_d7=5000):
    return usage(window('w-cx', 'codex', 10, cx), window('w-cy', 'codex', 20, cy),
                 window('w-cl', 'claude', 30, h5=cl_h5, d7=cl_d7), window('w-coord', 'claude', 5, h5=0, d7=0))


REFERENCE = {'claude': {'h5': 100000, 'week': 1000000}}


def ranked(result, slot):
    return [r['identity'] for r in result['task'][slot]['rows']]


class Order(unittest.TestCase):
    def test_lexicographic_tiers_and_stable_identity(self):
        state = base_state()
        result = balance.calculate(state, [], 'T', full_usage(cx=[quota(10)], cy=[quota(10)]), REFERENCE, AT, {'status': 'available', 'samples': []})
        build = result['task']['build']
        self.assertEqual(build['order'], ['quota', 'steps', 'owned', 'history', 'rate'])
        self.assertEqual(ranked(result, 'build'), ['w-cx', 'w-cl'], 'w-cl holds the task; w-cx holds nothing')
        self.assertEqual(result['writes'], False)
        self.assertEqual(result['assigns'], False)

    def test_steps_before_owned_before_rate(self):
        state = base_state()
        state['tasks']['A'] = task('A', 'cx-build', status='delivered', reviewer='cy-review')
        state['tasks']['B'] = task('B', 'cl-build', status='done')
        result = balance.calculate(state, [], 'T', full_usage(cx=[quota(10)], cy=[quota(10)]), REFERENCE, AT, {'status': 'available', 'samples': []})
        self.assertEqual(ranked(result, 'read'), ['w-coord', 'w-cx', 'w-cy'],
                         'w-cy holds a read step; w-coord and w-cx hold none and w-coord has the lower rate')

    def test_near_quota_demotes_when_every_row_known(self):
        state = base_state()
        result = balance.calculate(state, [], 'T', full_usage(cx=[quota(95)], cy=[quota(10)]), REFERENCE, AT, {'status': 'available', 'samples': []})
        self.assertEqual(ranked(result, 'read')[-1], 'w-cx')
        self.assertIn('quota', result['task']['read']['order'])

    def test_input_order_invariance(self):
        state = base_state()
        reversed_state = deepcopy(state)
        reversed_state['agents'] = dict(reversed(list(state['agents'].items())))
        data = full_usage(cx=[quota(95)], cy=[quota(10)])
        flipped = deepcopy(data)
        flipped['windows'].reverse()
        one = balance.calculate(state, [], 'T', data, REFERENCE, AT, {'status': 'available', 'samples': []})
        two = balance.calculate(reversed_state, [], 'T', flipped, REFERENCE, AT, {'status': 'available', 'samples': []})
        for slot in ('build', 'read'):
            self.assertEqual(ranked(one, slot), ranked(two, slot))


class MissingData(unittest.TestCase):
    """M1: a dimension missing for any eligible row is omitted for the whole list, never read as zero."""
    def test_unknown_quota_omits_the_dimension_for_the_list(self):
        state = base_state()
        state['tasks']['A'] = task('A', 'cx-build', status='delivered', reviewer='cy-review')
        state['tasks']['B'] = task('B', 'cl-build', status='delivered', reviewer='cy-review')
        data = full_usage(cx=[quota(95)], cy=[])
        result = balance.calculate(state, [], 'T', data, REFERENCE, AT, {'status': 'available', 'samples': []})
        read = result['task']['read']
        self.assertNotIn('quota', read['order'])
        self.assertEqual(read['omitted'][0], {'dimension': 'quota', 'windows': ['w-cy']})
        rows = {r['identity']: r for r in read['rows']}
        self.assertEqual(rows['w-cy']['quota']['state'], 'unknown')
        self.assertEqual(rows['w-cx']['quota']['state'], 'near', 'the known figure stays labelled')
        self.assertLess(ranked(result, 'read').index('w-cx'), ranked(result, 'read').index('w-cy'),
                        'with quota omitted, the unknown window with 2 pending reads no longer passes the 95% window with none')

    def test_no_usage_marks_not_collected_and_omits_quota_and_rate(self):
        result = balance.calculate(base_state(), [], 'T', None, None, AT, {'status': 'available', 'samples': []})
        self.assertEqual(result['usage']['status'], 'not_collected')
        self.assertEqual({o['dimension'] for o in result['task']['build']['omitted']}, {'quota', 'rate'})
        for row in result['windows']:
            self.assertEqual(row['quota']['reason'], 'not_collected')
            self.assertIsNone(row['tokens_per_hour'])
            self.assertEqual(row['tokens_status'], 'not_collected')
        self.assertIsNone(result['claude_estimate'])

    def test_missing_rate_omits_rate_for_the_list(self):
        data = full_usage(cx=[quota(10)], cy=[quota(10)])
        data['windows'][0]['recent'] = {'seconds': 900, 'total_tokens': None, 'tokens_per_minute': None,
                                        'observed_until': None, 'status': 'not_measured'}
        result = balance.calculate(base_state(), [], 'T', data, REFERENCE, AT, {'status': 'available', 'samples': []})
        self.assertIn({'dimension': 'rate', 'windows': ['w-cx']}, result['task']['build']['omitted'])
        self.assertNotIn('rate', result['task']['build']['order'])

    def test_forecast_unavailable_gives_no_history(self):
        result = balance.calculate(base_state(), [], 'T', full_usage(), REFERENCE, AT, {'status': 'scope_unavailable', 'samples': []})
        self.assertNotIn('history', result['task']['build']['order'])
        self.assertTrue(all(r['history_count'] is None for r in result['task']['build']['rows']))

    def test_history_counts_without_figures_below_minimum(self):
        state = base_state()
        for name in ('H0', 'H1'):
            state['tasks'][name] = task(name, 'cx-build', status='done')
            state['tasks'][name]['reviews'] = [{'stage': 'code', 'verdict': 'accepted', 'reviewer': 'cl-review'}]
        cohort = {'status': 'not_enough_history', 'samples': [{'task': 'H0'}, {'task': 'H1'}]}
        result = balance.calculate(state, [], 'T', full_usage(cx=[quota(10)], cy=[quota(10)]), REFERENCE, AT, cohort)
        rows = {r['identity']: r for r in result['task']['build']['rows']}
        self.assertEqual(rows['w-cx']['history_count'], 2)
        self.assertIsNone(rows['w-cx']['history']['median_build_minutes'])
        self.assertIsNone(rows['w-cx']['history']['changes_required'])
        reads = {r['identity']: r for r in result['task']['read']['rows']}
        self.assertNotIn('w-cl', reads, 'author excluded')

    def test_history_figures_from_three_samples_per_window(self):
        state = base_state()
        samples = []
        for i, verdict in enumerate(('accepted', 'changes-required', 'accepted')):
            name = 'H' + str(i)
            state['tasks'][name] = task(name, 'cx-build', status='done')
            state['tasks'][name]['reviews'] = [{'stage': 'code', 'verdict': verdict, 'reviewer': 'cy-review'}]
            samples.append({'task': name, 'seconds': {'build': 600 * (i + 1)}})
        result = balance.calculate(state, [], 'T', full_usage(cx=[quota(10)], cy=[quota(10)]), REFERENCE, AT,
                                   {'status': 'available', 'samples': samples})
        rows = {r['identity']: r for r in result['task']['build']['rows']}
        self.assertEqual(rows['w-cx']['history']['median_build_minutes'], 20)
        self.assertEqual(rows['w-cx']['history']['changes_required'], {'numerator': 1, 'denominator': 3})
        reads = {r['identity']: r for r in result['task']['read']['rows']}
        self.assertEqual(reads['w-cy']['history']['read_changes_required'], {'numerator': 1, 'denominator': 3})
        self.assertEqual(reads['w-cy']['history_count'], 3)


class Independence(unittest.TestCase):
    def test_author_and_alias_excluded_with_reason(self):
        state = base_state()
        state['tasks']['T']['authors'] = ['cl-build']
        result = balance.calculate(state, [], 'T', None, None, AT, {'status': 'available', 'samples': []})
        self.assertNotIn('w-cl', ranked(result, 'read'), 'cl-review is an alias of the author cl-build')
        self.assertIn({'identity': 'w-cl', 'reason': 'author'}, result['task']['read']['excluded'])

    def test_standby_and_wrong_role_excluded(self):
        state = base_state()
        state['agents']['cy-review']['availability'] = {'status': 'stand-by'}
        result = balance.calculate(state, [], 'T', None, None, AT, {'status': 'available', 'samples': []})
        self.assertIn({'identity': 'w-cy', 'reason': 'stand-by'}, result['task']['read']['excluded'])
        self.assertNotIn('w-coord', ranked(result, 'build'), 'a coordinator seat is not a builder candidate')

    def test_never_assigns_and_does_not_mutate(self):
        state = base_state()
        before = canonical(state)
        balance.calculate(state, [], 'T', full_usage(cx=[quota(95)]), REFERENCE, AT, {'status': 'available', 'samples': []})
        self.assertEqual(canonical(state), before)

    def test_unknown_and_closed_tasks(self):
        state = base_state()
        with self.assertRaises(Error):
            balance.calculate(state, [], 'NOPE', None, None, AT, {})
        state['tasks']['T']['status'] = 'done'
        self.assertEqual(balance.calculate(state, [], 'T', None, None, AT, {})['task']['status'], 'task_not_open')


class QuotaSlots(unittest.TestCase):
    """M2: every provider slot keeps its latest observation; any current slot at the threshold is near."""
    def test_secondary_near_hidden_behind_primary_in_both_orders(self):
        rows = [quota(10, 300, 'primary'), quota(95, 10080, 'secondary')]
        for order in (rows, list(reversed(rows))):
            result = balance.codex_quota(order, AT)
            self.assertEqual(result['state'], 'near')
            self.assertEqual(len(result['slots']), 2)

    def test_latest_per_slot_and_independent_staleness(self):
        older = quota(95, 300, 'primary', age=10)
        newer = quota(20, 300, 'primary', age=2)
        self.assertEqual(balance.codex_quota([older, newer], AT)['state'], 'clear')
        stale = quota(95, 10080, 'secondary', age=20)
        result = balance.codex_quota([newer, stale], AT)
        self.assertEqual(result['state'], 'unknown', 'a stale slot cannot prove below-limit capacity')
        expired = quota(95, 10080, 'secondary', age=1, reset=-1)
        self.assertEqual(balance.codex_quota([newer, expired], AT)['state'], 'unknown')
        self.assertEqual(balance.codex_quota([quota(80)], AT)['state'], 'near')
        self.assertEqual(balance.codex_quota([quota(79.9)], AT)['state'], 'clear')
        self.assertEqual(balance.codex_quota([], AT), {'kind': 'measured', 'state': 'unknown', 'slots': [], 'reason': 'no_observation'})
        self.assertEqual(balance.codex_quota([quota(90, status='stale')], AT)['state'], 'unknown')


class ClaudeEstimate(unittest.TestCase):
    def test_label_method_assumption_and_no_reset(self):
        result = balance.calculate(base_state(), [], None, full_usage(cl_h5=85000, cl_d7=100000), REFERENCE, AT)
        estimate = result['claude_estimate']
        self.assertEqual(estimate['kind'], 'estimate')
        self.assertEqual(estimate['assumption'], balance.ASSUMPTION)
        self.assertIsNone(estimate['reset'])
        self.assertEqual(estimate['state'], 'near')
        self.assertEqual([s['percent'] for s in estimate['slots']], [85.0, 10.0])
        rows = {r['identity']: r for r in result['windows']}
        self.assertIs(rows['w-cl']['quota'], rows['w-coord']['quota'], 'one shared estimate for all Claude windows')
        s2 = [s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == 'w-cl']
        self.assertTrue(s2 and s2[0]['metrics']['estimate'] is True and s2[0]['metrics']['reset'] is None)

    def test_week_slot_alone_is_near(self):
        result = balance.calculate(base_state(), [], None, full_usage(cl_h5=10, cl_d7=900000), REFERENCE, AT)
        self.assertEqual(result['claude_estimate']['state'], 'near')

    def test_no_reference_shows_sums_and_no_estimate(self):
        result = balance.calculate(base_state(), [], None, full_usage(cl_h5=85000), None, AT)
        estimate = result['claude_estimate']
        self.assertEqual((estimate['state'], estimate['reason'], estimate['slots']), ('unknown', 'no_reference', []))
        self.assertEqual(estimate['sums']['h5']['total'], 85000)

    def test_partial_rolling_is_never_a_lower_percentage(self):
        result = balance.calculate(base_state(), [], None, full_usage(cl_h5=None, cl_d7=None), REFERENCE, AT)
        estimate = result['claude_estimate']
        self.assertEqual(estimate['sums']['h5']['status'], 'partial', 'one Claude window measured, one not')
        self.assertEqual(estimate['sums']['h5']['total'], 0, 'the known part is shown, labelled partial')
        self.assertTrue(all(s['percent'] is None for s in estimate['slots']))
        self.assertEqual(estimate['state'], 'unknown')


class Suggestions(unittest.TestCase):
    def test_s1_names_the_task_and_respects_its_authors(self):
        state = base_state()
        state['tasks']['A'] = task('A', 'cx-build', status='delivered', reviewer='cy-review')
        state['tasks']['B'] = task('B', 'cl-build', status='delivered', reviewer='cy-review')
        state['tasks']['A']['authors'] = ['cx-build', 'coord']
        result = balance.calculate(state, [], None, full_usage(cx=[quota(10)], cy=[quota(10)]), REFERENCE, AT)
        s1 = [s for s in result['suggestions'] if s['id'] == 'S1']
        self.assertEqual(len(s1), 1)
        self.assertEqual((s1[0]['window'], s1[0]['task']), ('w-cy', 'A'))
        self.assertNotIn(s1[0]['to'], {'w-cx', 'w-coord'}, 'authors of A are never offered as its reader')
        self.assertEqual(s1[0]['metrics'], {'pending_reads': 2, 'target_pending_reads': 0})

    def test_s1_silent_at_one_pending_read(self):
        state = base_state()
        state['tasks']['A'] = task('A', 'cx-build', status='delivered', reviewer='cy-review')
        result = balance.calculate(state, [], None, None, None, AT)
        self.assertFalse([s for s in result['suggestions'] if s['id'] == 'S1'])

    def test_s3_at_three_open_tasks_and_s4_idle_model(self):
        state = base_state()
        state['agents']['cx2-build'] = agent('cx2-build', 'w-cx2', 'builder', 'codex', 'gpt-mini')
        for i in range(2):
            state['tasks']['O' + str(i)] = task('O' + str(i), 'cl-build')
        result = balance.calculate(state, [], None, None, None, AT)
        self.assertFalse([s for s in result['suggestions'] if s['id'] == 'S3'], 'T + O0 + O1 = 3 on w-cl, but no free Claude builder')
        state['agents']['cl2-build'] = agent('cl2-build', 'w-cl2', 'builder', 'claude', 'sonnet')
        result = balance.calculate(state, [], None, None, None, AT)
        s3 = [s for s in result['suggestions'] if s['id'] == 'S3']
        self.assertEqual((s3[0]['window'], s3[0]['to'], s3[0]['metrics']['owned_open']), ('w-cl', 'w-cl2', 3))
        s4 = {s['model'] for s in result['suggestions'] if s['id'] == 'S4'}
        self.assertIn('gpt-mini', s4)
        self.assertIn('sonnet', s4)
        state['agents']['cz-coord'] = agent('cz-coord', 'w-cz', 'coordinator', 'codex', 'idle-coordinator-model')
        s4 = {s['model'] for s in balance.calculate(state, [], None, None, None, AT)['suggestions'] if s['id'] == 'S4'}
        self.assertNotIn('idle-coordinator-model', s4, 'a coordinator window is not spare build or read capacity')
        del state['tasks']['O1']
        self.assertFalse([s for s in balance.calculate(state, [], None, None, None, AT)['suggestions'] if s['id'] in ('S3', 'S4')])

    def test_s2_measured_lists_only_the_hot_slot(self):
        result = balance.calculate(base_state(), [], None, full_usage(cx=[quota(10), quota(96, 10080, 'secondary')]), REFERENCE, AT)
        s2 = [s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == 'w-cx']
        self.assertEqual([slot['used_percent'] for slot in s2[0]['metrics']['slots']], [96])


class Reference(unittest.TestCase):
    def test_strict_reference(self):
        self.assertEqual(balance.parse_reference('{"claude":{"h5":5,"week":9}}'), {'claude': {'h5': 5, 'week': 9}})
        self.assertIsNone(balance.parse_reference(None))
        for bad in ('{"claude":{"h5":true,"week":9}}', '{"claude":{"h5":5,"week":9,"x":1}}', '{"claude":{"h5":5,"h5":6,"week":9}}',
                    '{"claude":{"h5":0,"week":9}}', '{"claude":{"h5":5.0,"week":9}}', '{"claude":{"h5":5,"week":1000000000001}}',
                    'not json', '[]', '{"claude":{"h5":NaN,"week":9}}', ' ' * 300 + '{}', b'\xff'):
            with self.assertRaises(Error) as caught:
                balance.parse_reference(bad)
            self.assertEqual(str(caught.exception), 'reference_invalid', 'never echoes the input')


if __name__ == '__main__':
    unittest.main()


def hourly(scale):
    return [{'hour': iso(-60 * (23 - i)), 'status': 'measured' if i % 7 else 'not_measured',
             'total': scale * i if i % 7 else None, 'reason': None if i % 7 else 'no_source'} for i in range(24)]


def page_payloads():
    state = base_state()
    state['tasks']['A'] = task('A', 'cx-build', status='delivered', reviewer='cy-review')
    state['tasks']['B'] = task('B', 'cl-build', status='delivered', reviewer='cy-review')
    data = full_usage(cx=[quota(10), quota(96, 10080, 'secondary')], cy=[], cl_h5=85000, cl_d7=100000)
    data['models'] = [{'model': 'gpt', 'status': 'measured', 'totals': None, 'windows': ['w-cx', 'w-cy'], 'hourly': hourly(100)},
                      {'model': 'opus', 'status': 'measured', 'totals': None, 'windows': ['w-cl'], 'hourly': hourly(50)}]
    cohort = {'status': 'available', 'samples': []}
    empty = {'agents': {}, 'tasks': {}, 'messages': {}, 'workspace': {}}
    return {'full': balance.calculate(state, [], 'T', data, REFERENCE, AT, cohort),
            'noReference': balance.calculate(state, [], 'T', data, None, AT, cohort),
            'empty': balance.calculate(empty, [], None, None, None, AT),
            'partial': balance.calculate(state, [], 'T', full_usage(cl_h5=None, cl_d7=None), REFERENCE, AT, cohort),
            'multi': balance.calculate(state, [], 'T', multi_usage(), REFERENCE, AT, cohort),
            **plain_payloads(cohort)}


def plain_payloads(cohort):
    calm = full_usage(cx=[quota(10)], cy=[quota(10)], cl_h5=1000, cl_d7=5000)
    blocked = base_state()
    for name in ('A', 'B'):
        blocked['tasks'][name] = task(name, 'cx-build', status='delivered', reviewer='cy-review',
                                      authors=['cx-build', 'cl-build', 'coord'])
    busy = balance.calculate(base_state(), [], 'T', calm, REFERENCE, AT, cohort)
    shared = usage(window('w-cx', 'codex', 10, [quota(10)]), window('w-cy', 'codex', 20, [quota(10)]),
                   window('w-cl', 'claude', 30, h5=60000, d7=100000), window('w-coord', 'claude', 5, h5=35000, d7=0))
    busy['usage']['coverage'] = {'status': 'collecting', 'sources': 0, 'measured': 0, 'reasons': ['collector_busy']}
    return {'balanced': balance.calculate(base_state(), [], 'T', calm, REFERENCE, AT, cohort),
            'blocked': balance.calculate(blocked, [], 'T', calm, REFERENCE, AT, cohort),
            'unknownQuota': balance.calculate(base_state(), [], 'T', full_usage(cx=[quota(10)], cy=[]), REFERENCE, AT, cohort),
            'shared': balance.calculate(base_state(), [], 'T', shared, REFERENCE, AT, cohort),
            'busy': busy}


def multi_usage():
    data = full_usage(cx=[quota(10)], cy=[quota(10)])
    rows = [{'model': 'gpt-5.4', 'status': 'measured', 'totals': {'total': 4000}},
            {'model': 'gpt-5.4-mini', 'status': 'measured', 'totals': {'total': 900}}]
    data['windows'][0]['models'] = rows
    data['models'] = [dict(r, windows=['w-cx'], hourly=hourly(10 if r['model'] == 'gpt-5.4' else 3)) for r in rows]
    return data


class FakeUsage:
    """Stands in for the SQ-TOKENS module in route tests; the real seam has its own contract test."""
    calls = 0

    @classmethod
    def collect(cls, events, state, **kwargs):
        cls.calls += 1
        return full_usage(cx=[quota(10)])


class RouteAndPage(unittest.TestCase):
    def setUp(self):
        import tempfile, threading
        from unittest.mock import patch
        from registry import Store
        from dashboard_test_support import make_server
        from test_reads_value import seed
        temp = tempfile.TemporaryDirectory(prefix='sq-balance-page-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = Store(self.base / 'registry', create=True)
        self.addCleanup(self.store.close)
        seed(self.store, self.base)
        self.store.project()
        modules = patch.dict(sys.modules, {'usage': FakeUsage})
        modules.start()
        self.addCleanup(modules.stop)
        self.server, self.token = make_server(self.store.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, route='/api/balance', token=True, **headers):
        import urllib.error, urllib.request
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.server.server_port, route),
                                     headers={**({'X-Squadra-Token': self.token} if token else {}), **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as result:
                return result.status, json.load(result)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def fingerprint(self):
        root = self.store.root
        return sorted((str(p.relative_to(root)), p.read_bytes()) for p in root.rglob('*')
                      if p.is_file() and not p.name.endswith(('-wal', '-shm')))

    def test_route_auth_reference_and_no_writes(self):
        before = self.fingerprint()
        self.assertEqual(self.request(token=False)[0], 401)
        code, body = self.request(**{'X-Squadra-Record': 'T'})
        self.assertEqual(code, 200)
        self.assertEqual((body['assigns'], body['writes'], body['task']['status']), (False, False, 'ranked'))
        code, body = self.request(**{'X-Squadra-Balance-Reference': '{"claude":{"h5":true,"week":1}}'})
        self.assertEqual((code, body), (409, {'error': 'reference_invalid'}))
        code, body = self.request(**{'X-Squadra-Balance-Reference': '{"claude":{"h5":10,"week":20}}'})
        self.assertEqual(code, 200)
        self.assertEqual(self.request(**{'X-Squadra-Record': 'NOPE'})[0], 409)
        self.assertEqual(body['usage']['schema'], 1, 'the route reads the real collector on the server roots')
        self.assertEqual(self.fingerprint(), before, 'reads leave registry, projections and artifacts unchanged')

    def contract(self, scenario):
        import shutil, subprocess
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; dashboard contracts cannot be skipped')
        code, state = self.request('/api/state')
        self.assertEqual(code, 200)
        fixture, cases = self.base / 'state.json', self.base / 'balance.json'
        fixture.write_text(json.dumps(state))
        cases.write_text(json.dumps(page_payloads()))
        result = subprocess.run([node, str(ROOT / 'tests/balance_contract.js'), str(ROOT / 'assets/dashboard.html'),
                                 str(fixture), scenario, str(cases)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('balance contract passed: ' + scenario, result.stdout)

    def test_js_balance_page_suggestions_charts_tables_and_estimate(self):
        self.contract('page')

    def test_js_balance_honest_zero_and_no_reference(self):
        self.contract('zero')

    def test_js_balance_per_task_ranking_exclusion_and_omitted_dimension(self):
        self.contract('task')

    def test_js_balance_reference_local_strict_and_prices_preserved(self):
        self.contract('reference')

    def test_js_balance_english(self):
        self.contract('english')

    def test_js_balance_open_only_serial_fetch_retained_focus_and_retry(self):
        self.contract('polling')

    def test_js_balance_data_is_text(self):
        self.contract('injection')

    def test_js_balance_small_screen_and_theme_css(self):
        self.contract('css')

    def test_js_balance_multi_model_label_and_series(self):
        self.contract('models')

    def test_js_balance_verdict_truthful_for_advice_balance_missing_and_blocked(self):
        self.contract('verdicts')

    def test_js_balance_what_it_checks_table_four_rules_four_columns(self):
        self.contract('rules')

    def test_js_balance_windows_and_missing_inputs_in_plain_words(self):
        self.contract('plain')

    def test_js_balance_italian_page_has_no_internal_names(self):
        self.contract('noInternal')

    def test_js_balance_polls_on_its_own_cadence_single_flight_keeping_last_figures(self):
        self.contract('cadence')

    def test_js_balance_charts_motion_reduced_motion_and_exact_values(self):
        self.contract('chartsMotion')

    def test_js_balance_quota_row_incomplete_when_a_quota_is_missing(self):
        self.contract('quotaRow')

    def test_js_balance_shared_claude_estimate_names_no_window_under_any_order(self):
        self.contract('sharedEstimate')

    def test_js_balance_hourly_bar_zero_transitions_have_finite_frames(self):
        self.contract('zeroMotion')


class Catalogue(unittest.TestCase):
    def test_fourth_system_skill_on_demand_with_its_files_and_audit_cap(self):
        import system_skills as skills
        values = skills.catalogue([], {'tasks': {}}, read_at=AT)['skills']
        self.assertEqual([s['id'] for s in values], ['errata', 'previsione', 'rasoio', 'bilanciamento'])
        row = values[3]
        self.assertEqual((row['mode'], row['total'], row['daily'], row['first'], row['last']), ('on_demand', None, None, None, None))
        self.assertEqual(row['command'], 'squadra balance --task TASK')
        self.assertIn('assigns nothing', row['action']['en'])
        self.assertTrue({'scripts/balance.py', 'scripts/forecast.py', 'scripts/usage.py',
                         'references/skills/bilanciamento.md'} <= set(skills.required_files('bilanciamento')))
        self.assertIsNotNone(row['guide'], 'bundled guide is readable')
        self.assertEqual(row['audit']['status'], 'not_audited', 'no placeholder audit record')


import test_squadra as support


class Cli(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def frozen(self):
        import hashlib
        return {str(p.relative_to(self.store.root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.store.root.rglob('*') if p.is_file() and not p.name.endswith(('-wal', '-shm'))}

    def run_main(self, *args):
        import contextlib, io
        from unittest.mock import patch
        from squadra import main
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {'usage': FakeUsage}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_cli_read_only_reference_unknown_and_missing_registry(self):
        self.store.project()
        before = self.frozen()
        code, out, _ = self.run_main('balance', '--task', 'T', '--root', str(self.root))
        self.assertEqual(code, 0)
        body = json.loads(out)
        self.assertEqual((body['assigns'], body['task']['status'], body['usage']['status']), (False, 'ranked', 'measured'))
        reference = Path(self.root).parent / 'reference.json'
        reference.write_text('{"claude":{"h5":10,"week":20}}')
        self.assertEqual(self.run_main('balance', '--root', str(self.root), '--reference', str(reference))[0], 0)
        reference.write_text('{"claude":{"h5":"secret-value","week":20}}')
        code, out, err = self.run_main('balance', '--root', str(self.root), '--reference', str(reference))
        self.assertEqual((code, err.strip()), (1, 'Squadra: reference_invalid'))
        self.assertNotIn('secret-value', out + err)
        self.assertEqual(self.run_main('balance', '--task', 'unknown', '--root', str(self.root))[0], 1)
        self.assertEqual(before, self.frozen(), 'no event, diagnostic, projection or artifact written')
        missing = Path(self.root).parent / 'absent'
        self.assertEqual(self.run_main('balance', '--root', str(missing))[0], 1)
        self.assertFalse(missing.exists())

    def test_task_brief_and_start_print_a_short_balance_without_collecting_or_recording(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        FakeUsage.calls = 0
        for args in [('start', '--actor', 'A', '--task', 'T'),
                     ('brief', '--actor', 'C', '--task', 'T', '--file', str(self.scope)),
                     ('task', '--id', 'new', '--actor', 'C', '--owner', 'A', '--repo', str(self.repo),
                      '--title', 'New', '--scope-file', str(self.scope))]:
            code, out, _ = self.run_main('--root', str(self.root), *args)
            self.assertEqual(code, 0)
            printed = json.loads(out[out.index('{\n  "balance"'):])['balance']
            self.assertEqual(set(printed), {'notice', 'task', 'status', 'build', 'read', 'suggestions', 'full'})
            self.assertEqual(printed['status'], 'ranked')
            self.assertEqual(printed['full'], 'squadra balance --task ' + printed['task'])
            for slot in ('build', 'read'):
                self.assertIn('quota', printed[slot]['reason'], 'tokens and quota are not collected here')
        self.assertEqual(FakeUsage.calls, 0, 'the automatic print never calls the collector')
        self.assertFalse(any(e['kind'].startswith(('balance', 'forecast')) for e in self.store.events()))


class RealCollector(unittest.TestCase):
    """The accepted SQ-TOKENS seam, not a fixture: every field balance reads exists with its pinned type or null."""
    def setUp(self):
        import tempfile
        import usage
        import test_usage as tu
        self.usage, self.tu = usage, tu
        temp = tempfile.TemporaryDirectory(prefix='sq-balance-real-')
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name).resolve()
        self.roots = {k: folder / k for k in ('claude', 'codex')}
        for root in self.roots.values():
            root.mkdir(mode=0o700)
        usage._COLLECTORS.clear()
        self.addCleanup(usage._COLLECTORS.clear)
        claude_id, codex_id = 'claude-window-' + tu.CLAUDE, 'codex-thread-' + tu.CODEX
        self.state = {'agents': {'cl': {'id': 'cl', 'identity': claude_id, 'role': 'builder', 'client': 'claude', 'session': tu.CLAUDE},
                                 'cx': {'id': 'cx', 'identity': codex_id, 'role': 'reviewer', 'client': 'codex', 'session': tu.CODEX}},
                      'tasks': {'T': task('T', 'cl')}, 'messages': {}, 'workspace': {}}
        self.ids = claude_id, codex_id

    def write(self, client, rows, name):
        path = self.roots[client] / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
        path.chmod(0o600)

    def collect(self):
        from unittest.mock import patch
        with patch.object(self.usage.time, 'time', return_value=self.tu.NOW):
            return self.usage.collect([], self.state, roots=self.roots, now=self.tu.NOW, budget_seconds=2)

    def check_shape(self, data):
        self.assertEqual(data['schema'], 1)
        self.assertIn(data['coverage']['status'], ('ready', 'collecting', 'limited'))
        windows = {w['identity']: w for w in data['windows']}
        for identity in self.ids:
            w = windows[identity]
            self.assertIn(w['recent']['status'], ('measured', 'partial', 'not_measured'))
            self.assertTrue(w['recent']['tokens_per_minute'] is None or isinstance(w['recent']['tokens_per_minute'], (int, float)))
            self.assertEqual(len(w['hourly']), 168)
            for row in w['hourly']:
                self.assertTrue({'hour', 'status', 'total', 'reason'} <= set(row))
            for slot in ('h5', 'd7'):
                self.assertTrue({'status', 'total', 'reason', 'from', 'to'} <= set(w['rolling'][slot]))
            for q in w['quota']:
                self.assertTrue({'identity', 'model', 'observed_at', 'limit', 'used_percent', 'window_minutes',
                                 'resets_at', 'plan_type', 'status'} <= set(q))
        for m in data['models']:
            self.assertTrue({'model', 'status', 'totals', 'windows', 'hourly'} <= set(m))
        return windows

    def test_empty_roots_give_not_measured_never_zero(self):
        data = self.collect()
        self.check_shape(data)
        result = balance.calculate(self.state, [], 'T', data, REFERENCE, self.tu.NOW, {'status': 'scope_unavailable', 'samples': []})
        rows = {r['identity']: r for r in result['windows']}
        self.assertEqual(rows[self.ids[1]]['quota']['state'], 'unknown')
        self.assertIsNone(rows[self.ids[0]]['tokens_per_hour'])
        self.assertEqual(result['claude_estimate']['state'], 'unknown')

    def test_measured_sources_reach_the_ranking_and_estimate(self):
        tu = self.tu
        self.write('claude', [tu.claude(tu.NOW - 600, 'm1', inp=40000, out=20000)], 'project/' + tu.CLAUDE + '.jsonl')
        quota = {'primary': {'used_percent': 85, 'window_minutes': 300, 'resets_at': int(tu.NOW + 1000)},
                 'secondary': {'used_percent': 10, 'window_minutes': 10080, 'resets_at': int(tu.NOW + 90000)}, 'plan_type': 'pro'}
        self.write('codex', [{'type': 'session_meta', 'payload': {'id': tu.CODEX}}, tu.model(tu.NOW - 7200),
                             tu.codex(tu.NOW - 200, 0, 0), tu.codex(tu.NOW - 10, 100, 50, quota=quota)],
                   '2026/10/15/rollout-2026-10-15T12-00-00-' + tu.CODEX + '.jsonl')
        data = self.collect()
        windows = self.check_shape(data)
        result = balance.calculate(self.state, [], 'T', data, {'claude': {'h5': 70000, 'week': 700000}}, tu.NOW,
                                   {'status': 'scope_unavailable', 'samples': []})
        rows = {r['identity']: r for r in result['windows']}
        codex_quota = rows[self.ids[1]]['quota']
        self.assertEqual(len(codex_quota['slots']), len(windows[self.ids[1]]['quota']), 'one row per provider slot')
        self.assertEqual(codex_quota['state'], 'near', 'the 85% primary slot is current')
        self.assertEqual(result['claude_estimate']['sums']['h5']['total'], windows[self.ids[0]]['rolling']['h5']['total'])
        h5 = windows[self.ids[0]]['rolling']['h5']
        estimate = result['claude_estimate']
        self.assertEqual(estimate['sums']['h5']['status'], 'measured' if h5['status'] == 'measured' else 'partial')
        if h5['status'] != 'measured':
            self.assertIsNone(estimate['slots'][0]['percent'], 'a partial rolling sum never yields a percentage')
            self.assertEqual(estimate['state'], 'unknown')
        self.assertNotIn(tu.CANARY, json.dumps(result), 'no message text reaches the balance output')


class ModelAttribution(unittest.TestCase):
    """B1: Usage schema 1 gives model rows as dictionaries; a measured model wins, S2 follows the hot quota row."""
    def row(self, name, total):
        return {'model': name, 'status': 'measured', 'totals': {'total': total}, 'windows': ['w-cx']}

    def window(self, models, declared='gpt', quotas=None):
        state = base_state()
        for seat in ('cx-build', 'cx-review'):
            state['agents'][seat]['model'] = declared
        data = full_usage(cx=quotas if quotas is not None else [quota(10)])
        data['windows'][0]['models'] = models
        result = balance.calculate(state, [], 'T', data, REFERENCE, AT, {'status': 'scope_unavailable', 'samples': []})
        return next(r for r in result['windows'] if r['identity'] == 'w-cx'), result

    def test_measured_row_wins_over_a_different_declaration(self):
        row, _ = self.window([self.row('gpt-5.4', 1000)])
        self.assertEqual((row['model'], row.get('model_source'), row.get('declared_models')), ('gpt-5.4', 'measured', ['gpt']))

    def test_measured_row_without_declaration(self):
        row, _ = self.window([self.row('gpt-5.4', 1000)], declared='')
        self.assertEqual((row['model'], row.get('model_source'), row.get('declared_models')), ('gpt-5.4', 'measured', []))

    def test_declaration_only_when_nothing_is_measured(self):
        row, _ = self.window([])
        self.assertEqual((row['model'], row.get('model_source')), ('gpt', 'declared'), 'positive control: unchanged fallback')
        row, _ = self.window([], declared='')
        self.assertEqual((row['model'], row.get('model_source')), (None, 'not_declared'))

    def test_several_measured_models_are_named_not_merged(self):
        row, _ = self.window([self.row('gpt-5.4-mini', 900), self.row('gpt-5.4', 4000), {'model': None, 'totals': {'total': 10}}])
        self.assertEqual((row['model'], row.get('model_source')), ('gpt-5.4', 'measured_largest_of_several'))
        self.assertEqual(row.get('measured_models'), ['gpt-5.4', 'gpt-5.4-mini'])

    def test_measured_series_feeds_the_model_chart(self):
        data_models = [{'model': 'gpt-5.4', 'status': 'measured', 'totals': None, 'windows': ['w-cx'], 'hourly': [{'hour': iso(), 'status': 'measured', 'total': 5, 'reason': None}]}]
        state = base_state()
        data = full_usage(cx=[quota(10)])
        data['windows'][0]['models'] = [self.row('gpt-5.4', 1000)]
        data['models'] = data_models
        result = balance.calculate(state, [], None, data, REFERENCE, AT)
        chart = {m['model']: m for m in result['models']}
        self.assertIn('gpt-5.4', chart, 'the measured model has its own series')
        self.assertEqual(chart['gpt-5.4']['hourly'][0]['total'], 5)

    def test_s2_takes_the_hot_quota_rows_model(self):
        hot = quota(95, 10080, 'secondary'); hot['model'] = 'gpt-5.4'
        cool = quota(10, 300, 'primary'); cool['model'] = 'gpt-5.3'
        _, result = self.window([self.row('gpt-5.3', 1000)], quotas=[cool, hot])
        s2 = next(s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == 'w-cx')
        self.assertEqual((s2['model'], s2.get('model_source')), ('gpt-5.4', 'quota_observation'))
        self.assertEqual([s.get('model') for s in s2['metrics']['slots']], ['gpt-5.4'])

    def test_s2_unknown_when_hot_rows_disagree(self):
        one = quota(95, 300, 'primary'); one['model'] = 'gpt-5.4'
        two = quota(90, 10080, 'secondary'); two['model'] = 'gpt-5.3'
        _, result = self.window([], quotas=[one, two])
        s2 = next(s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == 'w-cx')
        self.assertEqual((s2['model'], s2.get('model_source')), (None, 'unknown'))

    def test_real_collector_measured_model_reaches_the_window(self):
        case = RealCollector()
        case.setUp()
        try:
            tu = case.tu
            quota_rows = {'primary': {'used_percent': 95, 'window_minutes': 300, 'resets_at': int(tu.NOW + 1000)}, 'plan_type': 'pro'}
            case.write('codex', [{'type': 'session_meta', 'payload': {'id': tu.CODEX}}, tu.model(tu.NOW - 7200),
                                 tu.codex(tu.NOW - 200, 0, 0), tu.codex(tu.NOW - 10, 100, 50, quota=quota_rows)],
                       '2026/10/15/rollout-2026-10-15T12-00-00-' + tu.CODEX + '.jsonl')
            data = case.collect()
            observed = next(w for w in data['windows'] if w['identity'] == case.ids[1])['models'][0]['model']
            result = balance.calculate(case.state, [], 'T', data, REFERENCE, tu.NOW, {'status': 'scope_unavailable', 'samples': []})
            row = next(r for r in result['windows'] if r['identity'] == case.ids[1])
            self.assertEqual((row['model'], row.get('model_source')), (observed, 'measured'))
            s2 = next(s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == case.ids[1])
            self.assertEqual(s2['model'], observed, 'the real quota row carries the observed model')
        finally:
            case.doCleanups()


class ModelAttributionRound3(unittest.TestCase):
    """B1a: every measured model keeps its chart; B1b: an unnamed hot quota keeps S2 unattributed."""
    def s2(self, result):
        return next(s for s in result['suggestions'] if s['id'] == 'S2' and s['window'] == 'w-cx')

    def named(self, name, slot='primary', percent=95):
        row = quota(percent, 300 if slot == 'primary' else 10080, slot)
        row['model'] = name
        return row

    def test_every_measured_model_keeps_its_chart_series(self):
        result = balance.calculate(base_state(), [], None, multi_usage(), REFERENCE, AT)
        charts = {m['model']: m for m in result['models']}
        self.assertTrue(charts.get('gpt-5.4', {}).get('hourly'), 'representative model series')
        self.assertTrue(charts.get('gpt-5.4-mini', {}).get('hourly'), 'the smaller measured series is not dropped')
        self.assertEqual(charts['gpt-5.4-mini']['windows'], ['w-cx'])
        row = next(r for r in result['windows'] if r['identity'] == 'w-cx')
        self.assertEqual((row['model'], row['model_source'], row['measured_models']),
                         ('gpt-5.4', 'measured_largest_of_several', ['gpt-5.4', 'gpt-5.4-mini']))

    def test_unnamed_hot_slot_keeps_s2_unknown(self):
        data = full_usage(cx=[self.named(None), self.named('gpt-5.4', 'secondary')])
        s2 = self.s2(balance.calculate(base_state(), [], None, data, REFERENCE, AT))
        self.assertEqual((s2['model'], s2['model_source']), (None, 'unknown'))

    def test_named_hot_slot_with_unnamed_cool_slot_is_attributed(self):
        data = full_usage(cx=[self.named(None, percent=10), self.named('gpt-5.4', 'secondary')])
        s2 = self.s2(balance.calculate(base_state(), [], None, data, REFERENCE, AT))
        self.assertEqual((s2['model'], s2['model_source']), ('gpt-5.4', 'quota_observation'), 'positive control')

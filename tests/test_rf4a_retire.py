"""Historical cache retirement does not require a task to retain its current delivery."""
from pathlib import Path
import unittest

import test_rf4a_frozen as frozen_support
from registry import Error, Store, apply, plain, validate


class RetireHistory(unittest.TestCase):
    setUp = frozen_support.FrozenPolicy.setUp
    cli = frozen_support.FrozenPolicy.cli
    refuse = frozen_support.FrozenPolicy.refuse
    state = frozen_support.FrozenPolicy.state
    task = frozen_support.FrozenPolicy.task
    start = frozen_support.FrozenPolicy.start
    deliver = frozen_support.FrozenPolicy.deliver

    def test_retire_by_sha_after_redelivery_preserves_current_copy(self):
        self.task(); self.start(); self.deliver()
        before = self.state()['tasks']['T']['delivery']['frozen']; old = before['sha256']
        (self.repo / 'value.txt').write_text('New candidate\n'); self.deliver()
        current = self.state()['tasks']['T']['delivery']['frozen']
        self.cli('candidate', 'retire', '--actor', 'C', '--sha256', old, '--reason', 'Superseded delivery')
        self.assertFalse(Path(before['repo']).exists())
        self.assertTrue(Path(current['repo']).exists())
        out = self.state()['outputs'][-1]
        self.assertEqual(out['task'], 'T'); self.assertEqual(out['candidate_action']['sha256'], old)
        self.assertIn('active tasks', self.refuse('candidate', 'retire', '--actor', 'C', '--sha256',
                                                current['sha256'], '--reason', 'Must refuse current'))
        self.assertIn('retired', self.cli('candidate', 'status'))
        self.cli('doctor', '--quick')
        store = Store(self.root)
        try:
            report = {}; store.checkpoint(self.base / 'checkpoint', report=report)
            self.assertEqual(next(r for r in report['candidates'] if r['sha256'] == old)['status'], 'retired')
        finally: store.close()

    def test_retire_validator_keeps_historical_binding_after_fix_or_promotion(self):
        self.task(); self.start(); self.deliver()
        original = plain(self.state()); sha = original['tasks']['T']['delivery']['frozen']['sha256']
        for kind, data in [('fix', {'task': 'T', 'fix': {}}),
                           ('route', {'task': 'T', 'route': 'full', 'scope': {}, 'proof': {},
                                      'contract': {}, 'expected': [], 'full_gate': False})]:
            state = plain(original)
            apply(state, {'kind': kind, 'data': data, 'actor': 'C', 'utc': 'synthetic', 'id': 'synthetic'})
            self.assertNotIn('delivery', state['tasks']['T'])
            receipt = {'task': 'T', 'candidate_action': {'action': 'retire', 'sha256': sha, 'reason': 'Obsolete cache'}}
            validate(state, 'output', 'C', receipt)
            receipt['candidate_action']['sha256'] = 'f' * 64
            with self.assertRaises(Error): validate(state, 'output', 'C', receipt)

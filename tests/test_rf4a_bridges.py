"""Read frozen-v1 task history through bridges without a bundled candidate writer."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_board as board_support
from test_rf4a_git import isolated_environment
from frozen_candidate import remove

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
from pathlib import Path
import importlib.abc
import sys

class BlockCandidateImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.rsplit('.', 1)[-1] in ('frozen_candidate', 'source_manifests'):
            raise AssertionError('Unreachable bridge dependency imported: ' + fullname)

folder, name, root, phase, expected, work = sys.argv[1:]
sys.meta_path.insert(0, BlockCandidateImports())
sys.path.insert(0, folder)
assert not (Path(folder) / 'frozen_candidate.py').exists()
if name == 'memoranda':
    from squadra_memories import source
    module, pin = source.load_registry()
    copied = Path(work) / ('snapshot-' + phase)
    source.snapshot(Path(root), copied)
    events = source.verified_events(module, pin, copied)
    reader = module.Store(copied, read_only=True)
    try:
        state = reader.state(events, resolve=False)
    finally:
        reader.close()
    assert any(e['kind'] == 'deliver' and e['data'].get('frozen') for e in events)
else:
    bridge = __import__(name + '_plugin')
    identity, state = bridge.registry(root)
    assert identity
task = state['tasks']['T']
assert task['status'] == expected
assert task['delivery_policy'] == 'frozen-v1'
assert task['candidate']['sha256'] == task['delivery']['frozen']['sha256']
assert 'frozen_candidate' not in sys.modules
if phase == 'retired':
    assert any(o.get('candidate_action', {}).get('action') == 'retire'
               and o['candidate_action']['sha256'] == task['candidate']['sha256']
               for o in state['outputs'])
    assert not (Path(root) / 'candidates' / task['candidate']['sha256'] / 'repo').exists()
'''


class FrozenBridgeReaders(unittest.TestCase):
    cli = board_support.Board.cli
    state = board_support.Board.state
    task = board_support.Board.task
    start = board_support.Board.start
    deliver = board_support.Board.deliver
    review = board_support.Board.review

    def setUp(self):
        self.outer = tempfile.TemporaryDirectory(prefix='squadra-rf4a-bridges-')
        self.addCleanup(self.outer.cleanup)
        home = Path(self.outer.name) / 'home'; home.mkdir()
        self.env = isolated_environment(home)
        context = patch.dict(os.environ, self.env, clear=True)
        context.start(); self.addCleanup(context.stop)
        board_support.Board.setUp(self)
        self.addCleanup(remove, self.base)

    def readers(self, phase, status):
        for name in ('memoranda', 'playwright', 'sentry'):
            with self.subTest(phase=phase, bridge=name):
                folder = ROOT / 'plugins' / name / 'bridge'
                result = subprocess.run([sys.executable, '-I', '-B', '-c', PROBE,
                                         str(folder), name, str(self.root), phase, status, str(self.base)],
                                        cwd=self.base, env=self.env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_bridges_read_delivered_reviewed_closed_and_retired_frozen_tasks(self):
        self.task(); self.start(); self.deliver()
        self.readers('delivered', 'delivered')
        self.review(); self.readers('reviewed', 'reviewed')
        self.cli('check', '--actor', 'C', '--task', 'T')
        self.cli('close', '--actor', 'C', '--task', 'T')
        self.readers('closed', 'done')
        self.cli('candidate', 'retire', '--actor', 'C', '--task', 'T', '--reason', 'Closed synthetic delivery')
        self.readers('retired', 'done')


if __name__ == '__main__':
    unittest.main()

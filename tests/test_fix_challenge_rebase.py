"""A fix keeps review metadata and remains readable through both plugin readers."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_fix_challenge as fixes
import test_light_route as light
from fix_challenge import binding, disposition, execute, prepare, register
from squadra import deliver


class Integration(unittest.TestCase):
    setUp = fixes.FixChallenge.setUp
    git = fixes.FixChallenge.git
    task = fixes.FixChallenge.task
    ack = fixes.FixChallenge.ack
    cli = fixes.FixChallenge.cli
    configure = fixes.FixChallenge.configure
    challenge = fixes.FixChallenge.challenge

    def test_light_fix_review_keeps_findings_contract_and_fix_binding(self):
        (self.repo / 'README.md').write_text('wrong\n')
        self.git('add', 'README.md'); self.git('commit', '-qm', 'documented baseline')
        light.LightRoute.register(self, fix=prepare(self.store, {}))
        tests = self.repo / 'tests'; tests.mkdir()
        (tests / 'test_text.py').write_text(
            "import pathlib, unittest\nclass Text(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(pathlib.Path('README.md').read_text(), 'right\\n')\n")
        config = self.base / 'oracle.json'
        config.write_text(json.dumps({'ids': ['test_text.Text.test_value'], 'files': ['tests/test_text.py']}))
        register(self.store, 'C', 'L', config, 'Correct the documented text')
        (self.repo / 'README.md').write_text('right\n')
        self.assertEqual(execute(self.store, 'A', 'L', self.base / 'challenge')['status'], 'matched')
        self.assertEqual(light.LightRoute.proof(self)['status'], 'passed')
        handoff = deliver(self.store, 'A', 'L')['handoff']
        self.store.append('ack', 'R', {'message': handoff})
        findings = [{'severity': 'info', 'title': 'The documented behavior is now correct'}]
        path = self.base / 'findings.json'; path.write_text(json.dumps(findings))
        self.assertEqual(self.cli('review', '--actor', 'R', '--task', 'L', '--verdict', 'accepted',
                                  '--report', str(self.report), '--covers', 'R1', '--findings-file', str(path)), 0)
        task = self.store.state()['tasks']['L']; review = task['reviews'][-1]
        self.assertEqual(review.get('findings'), findings)
        self.assertEqual(review.get('contract'), task['contract'])
        self.assertEqual(review.get('fix_binding'), binding(task))
        self.assertEqual(review.get('fix_disposition'), disposition(task))
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'L'), 0)
        self.assertEqual(self.store.state()['tasks']['L']['status'], 'done')

    def test_both_mirrors_and_pinned_reader_accept_fix_history_read_only(self):
        root = Path(__file__).resolve().parents[1]
        body = (root / 'scripts/registry.py').read_bytes()
        for name in ('playwright', 'memoranda'):
            self.assertEqual((root / 'plugins' / name / 'bridge/registry.py').read_bytes(), body)
        bridge = root / 'plugins/memoranda/bridge'
        pin = json.loads((bridge / 'squadra_memories/pin.json').read_text())
        entry = next(row for row in pin['accepted'] if row['registry_py_sha256'] == hashlib.sha256(body).hexdigest())
        self.assertIn('fix', entry['kinds'])
        self.configure(); self.assertEqual(self.challenge()['status'], 'matched')
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        probe = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from squadra_memories.source import load_registry, snapshot, verified_events
module, entry = load_registry()
snapshot(Path(sys.argv[2]), Path(sys.argv[3]))
events = verified_events(module, entry, Path(sys.argv[3]))
assert any(e['kind'] == 'fix' for e in events)
assert any(e['kind'] == 'challenge' for e in events)
assert 'fix_challenge' not in sys.modules
print('read-only fix history verified')
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', probe, str(bridge), str(self.root),
                                 str(self.base / 'copy')], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'read-only fix history verified')
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()

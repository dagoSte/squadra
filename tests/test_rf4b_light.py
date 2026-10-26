"""The light route bounds changed bytes as well as lines, protects the token-holding dashboard page, and measures
a frozen copy under the registry its caller names, never one derived from a recorded path."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_light_route as light_support
import test_workflow_improvements as workflow_support
from registry import Error, Store
from light_route import measure, protected


class LightBytes(unittest.TestCase):
    setUp = light_support.LightRoute.setUp
    git = light_support.LightRoute.git
    ack = light_support.LightRoute.ack
    register = light_support.LightRoute.register
    task = light_support.LightRoute.task

    def write(self, body, name='README.md'):
        (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / name).write_bytes(body)

    def test_one_long_added_line_is_refused_by_bytes_not_lines(self):
        t = self.register()
        for size in (17 * 1024, 4 * 1024 * 1024):
            with self.subTest(size=size):
                self.write(b'x' * size + b'\n')
                with self.assertRaisesRegex(Error, 'light_bytes_exceeded'):
                    measure(t)
        self.write(b'x' * 1000 + b'\n')
        self.assertEqual(measure(t)['lines'], 1)

    def test_replacing_a_large_line_counts_both_sides(self):
        self.write(b'a' * (9 * 1024) + b'\n'); self.git('add', 'README.md'); self.git('commit', '-qm', 'long line')
        t = self.register()
        self.write(b'b' * (9 * 1024) + b'\n')
        with self.assertRaisesRegex(Error, 'light_bytes_exceeded: 18434 changed bytes; maximum 16384'):
            measure(t)

    def test_bytes_are_cumulative_across_files_and_distant_edits_count_only_their_lines(self):
        body = b''.join(b'%06d unchanged line of a long document\n' % i for i in range(3000))
        self.write(body); self.git('add', 'README.md'); self.git('commit', '-qm', 'long document')
        t = self.register()
        lines = body.split(b'\n')
        lines[1] = b'edited near the top'; lines[2998] = b'edited near the bottom'
        self.write(b'\n'.join(lines))
        measured = measure(t)
        self.assertEqual((measured['files'], measured['lines']), (1, 4))
        self.write(b'y' * 9000 + b'\n', 'docs/one.md'); self.write(b'z' * 9000 + b'\n', 'docs/two.md')
        with self.assertRaisesRegex(Error, 'light_bytes_exceeded'):
            measure(t)

    def test_the_dashboard_page_is_protected(self):
        self.assertTrue(protected('assets/dashboard.html'))
        self.assertTrue(protected('Assets/Dashboard.HTML'))
        t = self.register()
        self.write(b'<p>small</p>\n', 'assets/dashboard.html')
        with self.assertRaisesRegex(Error, 'light_protected_path: assets/dashboard.html'):
            measure(t)


class LightRegistryRoot(unittest.TestCase):
    cli = workflow_support.Improvements.cli
    state = workflow_support.Improvements.state
    start = workflow_support.Improvements.start

    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix='squadra-rf4b-home-'); self.addCleanup(home.cleanup)
        patcher = patch.dict(os.environ, {'HOME': home.name}); patcher.start(); self.addCleanup(patcher.stop)
        workflow_support.Improvements.setUp(self)
        requirements = self.base / 'requirements.json'
        requirements.write_text(json.dumps([{'id': 'R1', 'text': 'Preserve behavior'}]))
        self.cli('task', '--actor', 'C', '--id', 'L', '--title', 'Small descriptive change', '--owner', 'A',
                 '--repo', str(self.repo), '--scope-file', str(self.scope), '--proof-file', str(self.proof),
                 '--reviewer', 'R', '--route', 'light', '--requirements-file', str(requirements))
        self.start('L')
        (self.repo / 'README.md').write_text('One descriptive line\n')
        self.cli('prove', '--actor', 'A', '--task', 'L', '--covers', 'R1', '--', sys.executable, '-c', 'pass')
        self.cli('deliver', '--actor', 'A', '--task', 'L')

    def moved(self):
        target = self.base / 'moved-record'
        self.root.rename(target)
        self.root = target
        return target

    def test_measure_never_derives_the_registry_from_the_recorded_frozen_path(self):
        store = Store(self.root, read_only=True)
        try:
            task = store.task('L')
        finally:
            store.close()
        from frozen_candidate import current
        candidate = current(task, self.root)
        self.assertEqual(measure(task, candidate, root=self.root)['files'], 1)
        with self.assertRaisesRegex(Error, 'light_unmeasurable'):
            measure(task, candidate)

    def test_a_moved_registry_measures_its_own_frozen_copy_when_the_candidate_is_proved_again(self):
        old = Path(self.state()['tasks']['L']['delivery']['frozen']['repo'])
        self.moved()
        self.assertFalse(old.exists())
        self.cli('prove', '--actor', 'A', '--task', 'L', '--covers', 'R1', '--', sys.executable, '-c', 'pass')
        proofs = self.state()['tasks']['L']['proofs']
        self.assertEqual([p['status'] for p in proofs], ['passed', 'passed'], proofs[-1].get('error'))
        self.assertEqual(proofs[-1]['light']['files'], 1)

    def test_a_moved_registry_redelivers_against_its_own_frozen_copy(self):
        self.moved()
        self.cli('deliver', '--actor', 'A', '--task', 'L')
        task = self.state()['tasks']['L']
        self.assertTrue(task['delivery']['frozen']['repo'].startswith(str(self.root)))
        self.assertEqual(task['delivery']['light']['files'], 1)


if __name__ == '__main__':
    unittest.main()

"""A coordinator repairs the recorded project path with a format-5 event."""
from pathlib import Path
from unittest.mock import patch
import unittest
import test_quiet_options as support
import census


class ProjectSet(unittest.TestCase):
    setUp = support.QuietOptions.setUp
    git = support.QuietOptions.git
    task = support.QuietOptions.task
    invoke = support.QuietOptions.invoke

    def test_coordinator_repairs_missing_path_and_census_uses_it(self):
        moved = self.base / 'moved-project'
        self.repo.rename(moved)
        rc, _, err = self.invoke('project', '--actor', 'C', '--set', str(moved))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.store.state()['workspace']['project'], str(moved))
        event = self.store.events()[-1]
        self.assertEqual((event['kind'], event['format']), ('project.set', 5))
        before = self.store.events()
        with patch('census.collect', wraps=census.collect) as collect:
            rc, _, err = self.invoke('census')
        self.assertEqual(rc, 0, err)
        self.assertEqual(Path(collect.call_args.args[0]), moved)
        self.assertEqual(self.store.events(), before)

    def test_builder_and_non_directory_are_refused(self):
        self.assertNotEqual(self.invoke('project', '--actor', 'A', '--set', str(self.repo))[0], 0)
        self.assertNotEqual(self.invoke('project', '--actor', 'C', '--set', str(self.repo / 'code.py'))[0], 0)
        self.assertFalse(any(e['kind'] == 'project.set' for e in self.store.events()))

"""Lane analysis uses local facts, stable ownership and no registry writes."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import lanes
from registry import Error


class Analysis(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='squadra-lanes-')
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Lane fixture')
        self.git('config', 'user.email', 'lane@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args],
                                       cwd=self.repo, stderr=subprocess.PIPE, timeout=15)

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'Synthetic lane evidence')

    def test_globs_tracked_files_explicit_new_paths_and_fixture_exclusion(self):
        self.write('src/a.py', 'A = 1\n')
        self.write('src/b.py', 'B = 2\n\n')
        self.write('tests/fixtures/old.py', 'import src.a\n')
        self.commit()
        self.write('src/untracked.py', 'UNTRACKED = True\n')
        result = lanes.analyze(self.repo, ['src/*.py', 'tests/**', 'src/new.py'])
        self.assertEqual(result['files'], ['src/a.py', 'src/b.py', 'src/new.py'])
        self.assertEqual(result['lines'], {'src/a.py': 1, 'src/b.py': 2, 'src/new.py': 0})
        self.assertEqual(result['base'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertNotIn('src/new.py', sum(result['twins'], []))

    def test_python_imports_from_aliases_packages_and_relative_modules(self):
        self.write('pkg/__init__.py', '# package\n')
        self.write('pkg/dep.py', 'VALUE = 1\n')
        self.write('pkg/client.py', 'from .dep import VALUE as v\nfrom . import dep\nimport pkg.dep as other\n')
        self.write('main.py', 'from pkg.client import v\nimport pkg.dep\n')
        self.commit()
        result = lanes.analyze(self.repo, ['**/*.py'])
        edges = {tuple(row) for row in result['imports']}
        self.assertIn(('pkg/client.py', 'pkg/dep.py'), edges)
        self.assertIn(('pkg/client.py', 'pkg/__init__.py'), edges)
        self.assertIn(('main.py', 'pkg/client.py'), edges)
        self.assertIn(('main.py', 'pkg/dep.py'), edges)

    def test_js_ts_relative_imports_require_multiline_and_no_comment_edges(self):
        self.write('src/value.ts', 'export const value = 1;\n')
        self.write('src/lib/index.js', 'module.exports = {};\n')
        self.write('src/ghost.ts', 'export const ghost = 0;\n')
        self.write('src/client.ts', '''import {
  value
} from './value';
const lib = require('./lib');
// import { ghost } from './ghost';
/* require('./ghost'); */
const example = "import { ghost } from './ghost'";
''')
        self.write('src/side.js', "import './value';\n")
        self.write('src/mixed.ts', "import fallback, { value } from './value';\n")
        self.write('src/compiled.ts', "import { value } from './value.js';\n")
        self.commit()
        edges = {tuple(row) for row in lanes.analyze(self.repo, ['src/**'])['imports']}
        self.assertIn(('src/client.ts', 'src/value.ts'), edges)
        self.assertIn(('src/client.ts', 'src/lib/index.js'), edges)
        self.assertIn(('src/side.js', 'src/value.ts'), edges)
        self.assertIn(('src/mixed.ts', 'src/value.ts'), edges)
        self.assertIn(('src/compiled.ts', 'src/value.ts'), edges)
        self.assertNotIn(('src/client.ts', 'src/ghost.ts'), edges)

    def test_cochanges_are_counted_per_commit_not_per_import(self):
        self.write('a.py', 'A = 1\n')
        self.write('b.py', 'B = 1\n')
        self.write('c.py', 'C = 1\n')
        self.commit()
        self.write('a.py', 'A = 2\n')
        self.write('b.py', 'B = 2\n')
        self.commit()
        result = lanes.analyze(self.repo, ['*.py'])
        self.assertEqual(result['co_changes'], [['a.py', 'b.py', 2], ['a.py', 'c.py', 1], ['b.py', 'c.py', 1]])
        self.assertEqual(lanes.analyze(self.repo, ['*.py'], history=1)['co_changes'], [['a.py', 'b.py', 1]])
        self.assertEqual(lanes.analyze(self.repo, ['*.py'], history=0)['co_changes'], [])

    def test_byte_identical_hubs_and_their_copies_are_shared_as_a_unit(self):
        self.write('core.py', 'VALUE = 1\n')
        self.write('bridge/core.py', 'VALUE = 1\n')
        self.write('tests/fixtures/core.py', 'VALUE = 1\n')
        for i in range(3):
            self.write(f'clients/use{i}.py', f'from core import VALUE\nCLIENT = {i}\n')
        self.commit()
        analysis = lanes.analyze(self.repo, ['**'])
        result = lanes.partition(analysis)
        self.assertEqual(analysis['twins'], [['bridge/core.py', 'core.py']])
        self.assertEqual(result['shared'], ['bridge/core.py', 'core.py'])
        self.assertEqual(analysis['fan_in']['core.py'], 3)
        self.assertTrue(all(row == ['L0'] for row in result['depends'].values()))
        self.assertFalse(any('fixtures/' in p for p in result['files']))

    def test_new_missing_files_are_not_falsely_byte_identical(self):
        self.write('present.py', '# present\n')
        self.commit()
        result = lanes.analyze(self.repo, ['first.py', 'second.py'])
        self.assertEqual(result['twins'], [])
        self.assertEqual(len(lanes.partition(result)['lanes']), 2)

    def test_same_inputs_in_a_different_path_order_have_the_same_plan_and_no_writes(self):
        self.write('pkg/a.py', 'from .b import B\n')
        self.write('pkg/b.py', 'B = 1\n')
        self.commit()
        inventory = lambda: {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        before = inventory()
        first = lanes.partition(lanes.analyze(self.repo, ['pkg/b.py', 'pkg/a.py']))
        second = lanes.partition(lanes.analyze(self.repo, ['pkg/a.py', 'pkg/b.py', 'pkg/a.py']))
        self.assertEqual(first, second)
        self.assertEqual(inventory(), before)

    def test_analysis_never_imports_the_analyzed_python_source(self):
        marker = self.repo / 'executed'
        self.write('unsafe.py', f'open({str(marker)!r}, "w").write("executed")\n')
        self.commit()
        self.assertEqual(lanes.analyze(self.repo, ['unsafe.py'])['lines']['unsafe.py'], 1)
        self.assertFalse(marker.exists())

    def test_symlinks_and_paths_outside_the_repo_are_not_analyzed(self):
        self.write('a.py', 'A = 1\n')
        (self.repo / 'alias.py').symlink_to('a.py')
        self.commit()
        self.assertEqual(lanes.analyze(self.repo, ['*.py'])['files'], ['a.py'])
        with self.assertRaisesRegex(Error, 'inside the repository'):
            lanes.analyze(self.repo, ['../outside.py'])
        with self.assertRaises(Error):
            lanes.analyze(self.repo, ['a.py'], history=-1)

    def test_git_failure_after_file_discovery_refuses_a_partial_analysis(self):
        self.write('a.py', 'A = 1\n')
        self.commit()
        git, seen = lanes.git, []

        def fail_log(repo, *args, **kwargs):
            seen.append(args[0])
            if args[0] == 'log':
                raise Error('git log timed out')
            return git(repo, *args, **kwargs)

        with patch.object(lanes, 'git', side_effect=fail_log):
            with self.assertRaisesRegex(Error, 'git log timed out'):
                lanes.analyze(self.repo, ['*.py'])
        self.assertIn('ls-files', seen)
        self.assertEqual(seen[-1], 'log')


def facts(files, **extra):
    return {'repo': '/synthetic', 'base': 'a' * 40, 'files': files, 'lines': dict.fromkeys(files, 1),
            'imports': [], 'co_changes': [], 'twins': [], 'hubs': [], **extra}


class Partition(unittest.TestCase):
    def owners(self, plan):
        owners = {p: 'L0' for p in plan['shared']}
        for lane in plan['lanes']:
            self.assertTrue(set(lane['tests']) <= set(lane['files']))
            for path in lane['files']:
                self.assertNotIn(path, owners, 'A file has more than one owner')
                owners[path] = lane['id']
        self.assertEqual(sorted(owners), plan['files'])
        return owners

    def test_strong_cochanges_form_lanes_despite_an_interface_import(self):
        files = ['a/one.py', 'a/two.py', 'b/one.py', 'b/two.py']
        data = facts(files, co_changes=[['a/one.py', 'a/two.py', 4], ['b/one.py', 'b/two.py', 4]],
                     imports=[['a/two.py', 'b/one.py']])
        before = deepcopy(data)
        plan = lanes.partition(data)
        owners = self.owners(plan)
        self.assertEqual(owners['a/one.py'], owners['a/two.py'])
        self.assertEqual(owners['b/one.py'], owners['b/two.py'])
        self.assertNotEqual(owners['a/one.py'], owners['b/one.py'])
        self.assertIn(['a/two.py', 'b/one.py', 1], plan['cross_edges'])
        self.assertEqual(data, before)

    def test_test_uses_longest_module_name_before_cochanges(self):
        files = ['lib/core.py', 'lib/core_cache.py', 'tests/test_core_cache_races.py']
        plan = lanes.partition(facts(files,
            imports=[['tests/test_core_cache_races.py', p] for p in files[:2]],
            co_changes=[['lib/core.py', 'tests/test_core_cache_races.py', 100]]))
        owners = self.owners(plan)
        self.assertEqual(owners[files[1]], owners[files[2]])
        self.assertTrue(any('test_core_cache_races.py -> lib/core_cache.py' in why for why in plan['reasons']))

    def test_test_without_a_name_match_uses_cochanges_then_stable_order(self):
        files = ['a.py', 'b.py', 'tests/test_interface.py']
        data = facts(files, imports=[[files[2], 'b.py'], [files[2], 'a.py']],
                     co_changes=[['b.py', files[2], 2]])
        self.assertTrue(any('test_interface.py -> b.py' in why for why in lanes.partition(data)['reasons']))
        data['co_changes'] = []
        self.assertTrue(any('test_interface.py -> a.py' in why for why in lanes.partition(data)['reasons']))

    def test_identical_files_stay_together_and_hub_tests_follow_shared(self):
        files = ['a.py', 'copy/a.py', 'hub.py', 'tests/test_hub.py']
        plan = lanes.partition(facts(files, twins=[['a.py', 'copy/a.py']], hubs=['hub.py'],
                                     imports=[['tests/test_hub.py', 'hub.py']]))
        owners = self.owners(plan)
        self.assertEqual(owners['a.py'], owners['copy/a.py'])
        self.assertEqual(plan['shared'], ['hub.py', 'tests/test_hub.py'])

    def test_group_limit_merges_smallest_first_without_overlap(self):
        data = facts(['a.py', 'b.py', 'c.py', 'd.py'],
                     lines={'a.py': 1, 'b.py': 2, 'c.py': 5, 'd.py': 9})
        plan = lanes.partition(data, max_lanes=2)
        self.owners(plan)
        self.assertEqual([lane['files'] for lane in plan['lanes']], [['a.py', 'b.py', 'c.py'], ['d.py']])
        with self.assertRaises(Error):
            lanes.partition(data, max_lanes=0)

    def test_broad_documentation_changes_do_not_fuse_source_communities(self):
        files = ['a1.py', 'a2.py', 'b1.py', 'b2.py', 'README.md', 'guide.md']
        changes = [['a1.py', 'a2.py', 3], ['b1.py', 'b2.py', 3], ['README.md', 'guide.md', 100]]
        changes += [[p, doc, 1] for p in files[:4] for doc in files[4:]]
        plan = lanes.partition(facts(files, co_changes=changes), max_lanes=2)
        owners = self.owners(plan)
        self.assertEqual(owners['a1.py'], owners['a2.py'])
        self.assertEqual(owners['b1.py'], owners['b2.py'])
        self.assertNotEqual(owners['a1.py'], owners['b1.py'])

    def test_empty_selection_has_no_lanes(self):
        self.assertEqual(lanes.partition(facts([]))['lanes'], [])


class RepositoryPerformance(unittest.TestCase):
    def test_project_analysis_finishes_within_ten_seconds(self):
        started = time.monotonic()
        result = lanes.partition(lanes.analyze(ROOT, ['scripts/*.py', 'tools/*.py']))
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(result['files'])
        self.assertEqual(result['form'], 'squadra-lanes/1')


if __name__ == '__main__':
    unittest.main()

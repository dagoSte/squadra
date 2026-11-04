"""Recovery retention never infers permission from a directory name."""
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

import test_squadra as support
import frozen_candidate
import registry_checkpoint
import storage_policy as policy
from recovery import save, restore_check
from registry import Error, Store
from squadra import main
from squadra import deliver


class StoragePolicy(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task
    proof = support.Workflow.proof

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['--root', str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def checkpoint(self, name='checkpoint', compact=False):
        target = self.base / name
        args = ['checkpoint', '--actor', 'C', '--to', str(target)]
        if compact:
            args.append('--compact')
        code, _, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        return target

    def test_checkpoint_receipt_binds_copied_boundary_and_legacy_has_no_actor(self):
        original = len(self.store.events())
        path = self.checkpoint()
        event = self.store.events()[-1]
        self.assertEqual(event['kind'], 'output')
        action = event['data']['storage_action']
        self.assertEqual(action['action'], 'checkpoint')
        self.assertEqual(action['path'], str(path))
        self.assertEqual(action['event_count'], original)
        self.assertEqual(policy.verify_checkpoint(path)['event_head'], action['event_head'])
        self.assertGreater(action['bytes']['logical'], 0)
        self.assertEqual(action['reuse'], [])
        before = len(self.store.events())
        code, _, err = self.cli('checkpoint', '--to', str(self.base / 'legacy'))
        self.assertEqual(code, 0, err)
        self.assertIn('not registered', err)
        self.assertEqual(len(self.store.events()), before)

    def test_compact_omits_views_and_root_use_regenerates_verified_projections(self):
        path = self.checkpoint(compact=True)
        marker = json.loads((path / 'COMPACT.json').read_text())
        self.assertEqual(marker['form'], policy.FORM)
        for name in policy.OMITTED:
            self.assertFalse((path / name).exists(), name)
        self.assertTrue((path / 'events.jsonl').is_file())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(['status', '--root', str(path)]), 0)
        for name in policy.OMITTED:
            self.assertTrue((path / name).exists(), name)
        self.assertFalse((path / 'COMPACT.json').exists())
        policy.verify_checkpoint(path)
        check = Store(path)
        try:
            self.assertEqual(len(check.audit()['events']), marker['event_count'])
        finally:
            check.close()

    def test_compact_tamper_is_refused_before_view_regeneration(self):
        path = self.checkpoint(compact=True)
        (path / 'events.jsonl').write_text('tampered')
        with self.assertRaisesRegex(Error, 'checksums'):
            policy.restore_projections(path)
        self.assertFalse((path / 'state.json').exists())

    def test_reuse_is_forwarded_only_when_supplied(self):
        previous = self.checkpoint('previous')
        candidate_copy = frozen_candidate.checkpoint
        immutable_copy = registry_checkpoint.copy_immutable_tree
        seen = []

        def candidates(*args, reuse=(), **kwargs):
            seen.append(('candidate', list(reuse)))
            return candidate_copy(*args, **kwargs)

        def immutable(source, dest, *, reuse=(), **kwargs):
            seen.append((Path(source).name, list(reuse)))
            return immutable_copy(source, dest, **kwargs)

        with patch.object(frozen_candidate, 'checkpoint', candidates), patch.object(
                registry_checkpoint, 'copy_immutable_tree', immutable):
            self.store.checkpoint(self.base / 'reused', reuse=[previous])
        self.assertIn(('candidate', [previous]), seen)
        self.assertIn(('artifacts', [previous]), seen)

    def test_cli_unknown_actor_and_reuse_without_module_refuse_before_copying(self):
        target = self.base / 'refused'
        code, _, err = self.cli('checkpoint', '--actor', 'nobody', '--to', str(target))
        self.assertNotEqual(code, 0)
        self.assertFalse(target.exists())
        previous = self.checkpoint('previous')
        with patch.object(policy, 'reuse_available', return_value=False):
            code, _, err = self.cli('checkpoint', '--actor', 'C', '--to', str(target), '--reuse', str(previous))
        self.assertNotEqual(code, 0)
        self.assertIn('storage_reuse', err)
        self.assertFalse(target.exists())

    def test_cli_reuses_the_newest_recorded_checkpoint_only_when_primitives_exist(self):
        first = self.checkpoint('first')
        seen = []
        original = Store.checkpoint

        def spy(store, destination, **kwargs):
            seen.append(kwargs.get('reuse'))
            kwargs.pop('reuse', None)
            return original(store, destination, **kwargs)

        with patch.object(Store, 'checkpoint', spy), patch.object(policy, 'reuse_available', return_value=True):
            self.checkpoint('second')
        with patch.object(Store, 'checkpoint', spy), patch.object(policy, 'reuse_available', return_value=False):
            self.checkpoint('third')
        self.assertEqual(seen, [[str(first)], None])
        recorded = self.store.events()[-2]['data']['storage_action']
        self.assertEqual(recorded['reuse'], [str(first)])

    def test_retention_keeps_three_recent_age_keep_and_never_unknown(self):
        paths = [self.checkpoint('point-' + str(i)) for i in range(6)]
        policy.keep(self.store, 'A', paths[0], 'Owner keeps this recovery point')
        unknown = self.base / 'unregistered'
        self.store.checkpoint(unknown)
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
        rows = policy.plan(self.store, at=future)['entries']
        eligible = {r['path'] for r in rows if r['eligible']}
        self.assertEqual(eligible, {str(paths[1]), str(paths[2])})
        self.assertNotIn(str(unknown), {r['path'] for r in rows})
        self.assertFalse(any(r['eligible'] for r in policy.plan(self.store)['entries']))

    def test_purge_apply_requires_coordinator_and_records_intent_before_removal(self):
        path = self.checkpoint()
        config = dict(checkpoints=0, saves=0, hours=0)
        preview = policy.purge(self.store, policy=config)
        self.assertTrue(preview['dry_run'])
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(Error, 'Coordinator'):
            policy.purge(self.store, 'A', 'No authority', apply=True, policy=config)
        with self.assertRaisesRegex(Error, 'reason'):
            policy.purge(self.store, 'C', '', apply=True, policy=config)
        original = policy.remove_tree

        def remove(target):
            action = self.store.events()[-1]['data']['storage_action']
            self.assertEqual(action['phase'], 'started')
            self.assertTrue(Path(target).name.startswith('.purging-'))
            self.assertIn(str(Path(target).with_name(Path(target).name[len('.purging-'):].rsplit('-', 1)[0])), action['removed'])
            return original(target)

        with patch.object(policy, 'remove_tree', remove):
            result = policy.purge(self.store, 'C', 'Synthetic expired point', apply=True, policy=config)
        self.assertEqual(result['removed'], [str(path)])
        self.assertFalse(path.exists())
        self.assertEqual(self.store.events()[-1]['data']['storage_action']['phase'], 'completed')

    def test_replan_rejects_changed_target_and_expected_plan(self):
        path = self.checkpoint()
        original = policy.plan
        calls = []

        def changed(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                (path / 'foreign').write_text('arrived after preview')
            return original(*args, **kwargs)

        config = dict(checkpoints=0, saves=0, hours=0)
        with patch.object(policy, 'plan', changed):
            with self.assertRaisesRegex(Error, 'plan changed'):
                policy.purge(self.store, 'C', 'Synthetic race', apply=True, policy=config)
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(Error, 'plan changed'):
            policy.purge(self.store, 'C', 'Stale dry run', apply=True, expected='0' * 64)

    def test_failure_is_recorded_and_unknown_stage_and_live_paths_survive(self):
        self.checkpoint()
        stage = self.root / 'candidates/.freeze-unknown'
        stage.mkdir(parents=True)
        (stage / 'work').write_text('Unrecorded work')
        with patch.object(policy, 'remove_tree', side_effect=OSError('synthetic refusal')):
            with self.assertRaises(OSError):
                policy.purge(self.store, 'C', 'Expired', apply=True,
                             policy=dict(checkpoints=0, saves=0, hours=0))
        self.assertTrue(stage.exists())
        self.assertTrue(self.root.exists())
        self.assertTrue(self.repo.exists())
        action = self.store.events()[-1]['data']['storage_action']
        self.assertEqual(action['phase'], 'failed')
        self.assertEqual(action['removed'], [])

    def test_keep_refuses_symlink_and_unknown_directories_are_not_purge_targets(self):
        path = self.checkpoint()
        link = self.base / 'alias'
        link.symlink_to(path)
        with self.assertRaisesRegex(Error, 'symlink'):
            policy.keep(self.store, 'A', link, 'Synthetic keep')
        path.rename(self.base / 'moved')
        path.symlink_to(self.base / 'moved')
        result = policy.plan(self.store)
        self.assertEqual(result['entries'], [])
        self.assertIn('symlink', result['ignored'][0]['reason'])

    def test_storage_root_after_subcommand_and_dry_run_never_records_a_write(self):
        before = self.store.events()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(['storage', 'purge', '--dry-run', '--root', str(self.root)])
        self.assertEqual(code, 0)
        result = json.JSONDecoder().raw_decode(out.getvalue())[0]
        self.assertTrue(result['dry_run'])
        self.assertEqual(self.store.events(), before)

    def test_purge_removal_leaves_a_shared_readonly_inode_mode_unchanged(self):
        parent = self.base / 'removable'
        parent.mkdir()
        source = self.base / 'retained'
        source.write_text('Immutable bytes')
        source.chmod(0o444)
        import os
        os.link(source, parent / 'shared')
        parent.chmod(0o555)
        policy.remove_tree(parent)
        self.assertEqual(source.stat().st_mode & 0o777, 0o444)
        self.assertEqual(source.read_text(), 'Immutable bytes')

    def test_saved_package_with_last_successful_restore_is_kept(self):
        paths = []
        for i in range(4):
            path = self.base / ('save-' + str(i))
            save(self.store, path, temporary=True)
            self.store.append('save', 'C', {'path': str(path), 'manifest': self.store.artifact(path / 'MANIFEST.json')})
            paths.append(path)
        result = restore_check(paths[0], self.base / 'restored')
        self.assertEqual(result['status'], 'passed')
        self.store.append('restore.check', 'C', {'result': self.store.artifact(self.base / 'restored/RESTORE-RESULT.json')})
        preview = policy.plan(self.store, policy=dict(checkpoints=0, saves=0, hours=0))
        self.assertFalse(next(r for r in preview['entries'] if r['path'] == str(paths[0]))['eligible'])
        self.assertTrue(all(r['eligible'] for r in preview['entries'] if r['path'] != str(paths[0])))

    def test_save_reuses_previous_bundle_and_registry_with_integrated_primitives(self):
        path = self.base / 'first'
        save(self.store, path, temporary=True)
        self.store.append('save', 'C', {'path': str(path), 'manifest': self.store.artifact(path / 'MANIFEST.json')})
        original = Store.checkpoint
        seen = []

        def checkpoint(store, target, *, reuse=(), **kwargs):
            seen.append(('registry', list(reuse)))
            return original(store, target, **kwargs)

        def place(source, target, **kwargs):
            seen.append(('bundle', str(source), kwargs))
            shutil.copy2(source, target)
            return 'copy'

        with patch.dict(sys.modules, {'storage_reuse': types.SimpleNamespace(place=place, ReuseError=OSError)}), patch.object(
                Store, 'checkpoint', checkpoint):
            save(self.store, self.base / 'second', temporary=True)
        self.assertIn(('registry', [path / 'registry']), seen)
        self.assertTrue(any(r[0] == 'bundle' for r in seen))
        self.assertEqual(restore_check(self.base / 'second', self.base / 'second-restore')['status'], 'passed')

    def test_compact_command_missing_optional_module_has_plain_refusal(self):
        with patch.dict(sys.modules, {'storage_reuse': None}):
            code, _, err = self.cli('storage', 'compact')
        self.assertEqual(code, 1)
        self.assertIn('integrated release', err)
        self.assertNotIn('Traceback', err)

    def test_open_candidate_and_live_run_survive_while_archived_closed_run_is_purgable(self):
        self.store.append('ack', 'A', {'message': 'assign-T'})
        result = self.proof()
        folder = self.root / 'runs' / result['id']
        self.assertTrue(folder.is_dir())
        self.store.append('run.start', 'A', {'id': 'live', 'task': 'T', 'mode': 'probe'})
        live = self.root / 'runs/live'
        live.mkdir()
        (live / 'stdout.log').write_text('Still running')
        preview = policy.plan(self.store, policy=dict(checkpoints=0, saves=0, hours=0))
        self.assertTrue(next(row for row in preview['entries'] if row['path'] == str(folder))['eligible'])
        self.assertFalse(any(row['path'] == str(live) for row in preview['entries']))
        (folder / 'not-archived').write_text('Unrecorded evidence')
        self.assertFalse(any(row['path'] == str(folder) for row in policy.plan(self.store)['entries']))

    def test_delivered_candidate_remains_until_no_open_task_needs_it(self):
        data = {'id': 'F', 'title': 'Frozen synthetic task', 'owner': 'A', 'repo': str(self.repo),
                'base': self.git('rev-parse', 'HEAD').decode().strip(), 'scope': self.store.artifact(self.scope),
                'proof': None, 'expected': None, 'depends': [], 'full_gate': False, 'delivery_policy': 'frozen-v1'}
        self.store.append('task', 'C', data)
        self.store.append('message', 'C', {'id': 'assign-F', 'to': 'A', 'task': 'F', 'body': 'Synthetic frozen input'})
        self.store.append('ack', 'A', {'message': 'assign-F'})
        self.proof(task='F')
        deliver(self.store, 'A', 'F')
        candidate = self.store.state()['tasks']['F']['delivery']['frozen']['sha256']
        config = dict(checkpoints=0, saves=0, hours=0)
        row = next(r for r in policy.plan(self.store, policy=config)['entries'] if r['kind'] == 'candidate')
        self.assertFalse(row['eligible'])
        self.store.append('task', 'C', {**data, 'id': 'N', 'title': 'Synthetic successor'})
        self.assertEqual(self.cli('supersede', '--actor', 'C', '--task', 'F', '--by', 'N',
                                  '--reason', 'Synthetic final read owns it')[0], 0)
        row = next(r for r in policy.plan(self.store, policy=config)['entries'] if r['kind'] == 'candidate')
        self.assertTrue(row['eligible'])
        self.assertEqual(row['sha256'], candidate)

    def test_recovery_point_inside_open_task_source_is_protected(self):
        path = self.repo / 'checkpoint-in-source'
        self.assertEqual(self.cli('checkpoint', '--actor', 'C', '--to', str(path))[0], 0)
        preview = policy.plan(self.store, policy=dict(checkpoints=0, saves=0, hours=0))
        self.assertFalse(next(r for r in preview['entries'] if r['path'] == str(path))['eligible'])

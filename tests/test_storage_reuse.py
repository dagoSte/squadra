"""Sharing preserves content, mode classes, isolation and failure boundaries."""
import errno
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import frozen_candidate
import storage_reuse as reuse
from runtime import writable_copy


class StorageReuse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.write('source', mode=0o444)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def write(self, name, body=b'verified payload\n' * 200, mode=0o600):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        path.chmod(mode)
        return path

    def no_clone(self):
        return patch.object(reuse, '_clone', side_effect=OSError(errno.ENOTSUP, 'No clone filesystem'))

    def test_capabilities_are_actual_filesystem_probes(self):
        result = reuse.capabilities(self.root)
        self.assertEqual(result['device'], self.root.stat().st_dev)
        self.assertIs(type(result['clone']), bool)
        self.assertTrue(result['hardlink'])
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'source'})

    def test_clone_or_copy_is_independent_when_writable(self):
        target = self.root / 'writable'
        method = reuse.place(self.source, target, sha256=self.sha, readonly=False)
        self.assertIn(method, ('clone', 'copy'))
        self.assertNotEqual(self.source.stat().st_ino, target.stat().st_ino)
        target.chmod(0o600)
        target.write_bytes(b'local change')
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.sha)

    def test_no_clone_links_only_exact_readonly_mode_classes(self):
        with self.no_clone():
            target = self.root / 'linked'
            self.assertEqual(reuse.place(self.source, target, sha256=self.sha, readonly=True), 'hardlink')
            self.assertEqual(target.stat().st_ino, self.source.stat().st_ino)
            executable = self.write('executable', mode=0o555)
            run = self.root / 'run'
            self.assertEqual(reuse.place(executable, run, sha256=self.sha, readonly=True), 'hardlink')
            self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o555)
            writable = self.write('mutable', mode=0o644)
            copied = self.root / 'copied'
            self.assertEqual(reuse.place(writable, copied, sha256=self.sha, readonly=True), 'copy')
            self.assertNotEqual(writable.stat().st_ino, copied.stat().st_ino)
            independent = self.root / 'independent'
            self.assertEqual(reuse.place(target, independent, sha256=self.sha, readonly=False), 'copy')
            self.assertEqual(independent.stat().st_nlink, 1)

    def test_locks_and_lease_json_never_use_hardlinks_even_if_readonly(self):
        with self.no_clone():
            for number, name in enumerate(('registry.sqlite3', 'something.lock', 'run-leases/ended.json', 'runs/ended/stdout.log')):
                with self.subTest(name=name):
                    source = self.write(name, mode=0o444)
                    target = self.root / ('target-' + str(number))
                    self.assertEqual(reuse.place(source, target, sha256=self.sha, readonly=True), 'copy')
                    self.assertEqual(target.stat().st_nlink, 1)

    def test_hash_mismatch_and_symlink_source_leave_no_output(self):
        target = self.root / 'target'
        with self.assertRaises(reuse.ReuseError):
            reuse.place(self.source, target, sha256='0' * 64, readonly=True)
        self.assertFalse(target.exists())
        link = self.root / 'link'; link.symlink_to(self.source)
        with self.assertRaises(OSError):
            reuse.place(link, target, sha256=self.sha, readonly=True)
        self.assertFalse(target.exists())

    def test_existing_file_or_symlink_is_not_overwritten(self):
        target = self.write('target', b'keep')
        with self.assertRaises(FileExistsError):
            reuse.place(self.source, target, sha256=self.sha, readonly=True)
        self.assertEqual(target.read_bytes(), b'keep')
        target.unlink(); target.symlink_to(self.source)
        with self.assertRaises(FileExistsError):
            reuse.place(self.source, target, sha256=self.sha, readonly=True)
        self.assertTrue(target.is_symlink())

    def test_mid_copy_source_change_is_refused_and_partial_output_removed(self):
        source = self.write('mutable')
        original = reuse._copy_bytes

        def changed(src, dst):
            original(src, dst)
            source.write_bytes(b'changed while copying')

        with patch.object(reuse, '_copy_bytes', side_effect=changed), self.assertRaises(reuse.ReuseError):
            reuse.place(source, self.root / 'target', sha256=self.sha, readonly=False, prefer=('copy',))
        self.assertFalse((self.root / 'target').exists())
        self.assertFalse(list(self.root.glob('.reuse-*')))

    def test_clone_claiming_a_hardlink_is_refused_before_chmod(self):
        before = stat.S_IMODE(self.source.stat().st_mode)

        def unsafe(src, parent, name):
            os.link(self.source, name, dst_dir_fd=parent)
            return True

        with patch.object(reuse, '_clone', side_effect=unsafe), self.assertRaises(reuse.ReuseError):
            reuse.place(self.source, self.root / 'target', sha256=self.sha, readonly=False)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), before)
        self.assertEqual(self.source.stat().st_nlink, 1)

    def test_partial_native_clone_is_discarded_before_plain_fallback(self):
        def partial(src, parent, name):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            os.write(fd, b'partial content'); os.close(fd)
            raise OSError(errno.EIO, 'interrupted clone')

        target = self.root / 'target'
        with patch.object(reuse, '_clone', side_effect=partial):
            method = reuse.place(self.source, target, sha256=self.sha, readonly=False)
        self.assertEqual(method, 'copy')
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_dedupe_checks_stale_index_and_preserves_mode_class(self):
        tree = self.root / 'tree'
        target = self.write('tree/copy', mode=0o555)
        stale = self.write('stale', b'incorrect hint', mode=0o555)
        index = reuse.reuse_index({self.sha: [self.source, stale]})
        before = target.read_bytes(), stat.S_IMODE(target.stat().st_mode)
        with self.no_clone():
            result = reuse.dedupe_tree(tree, index)
        self.assertEqual(result['reused'], 0)
        self.assertEqual((target.read_bytes(), stat.S_IMODE(target.stat().st_mode)), before)

    def test_dedupe_publication_is_atomic_and_remove_never_chmods_shared_files(self):
        tree = self.root / 'tree'
        target = self.write('tree/copy', mode=0o444)
        before = target.read_bytes()
        original_place = reuse.place

        def place(*args, **kwargs):
            self.assertEqual(target.read_bytes(), before)
            result = original_place(*args, **kwargs)
            self.assertEqual(target.read_bytes(), before)
            return result

        tree.chmod(0o555)
        with self.no_clone(), patch.object(reuse, 'place', side_effect=place):
            result = reuse.dedupe_tree(tree, reuse.reuse_index({self.sha: [self.source]}))
        self.assertEqual(result['hardlink'], 1)
        self.assertEqual(self.source.stat().st_ino, target.stat().st_ino)
        self.assertEqual(stat.S_IMODE(tree.stat().st_mode), 0o555)
        frozen_candidate.remove(tree)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o444)
        self.assertEqual(self.source.read_bytes(), before)

    def test_writable_copy_from_hardlinked_frozen_input_is_always_private(self):
        tree = self.root / 'frozen'
        payload = self.write('frozen/file', mode=0o444)
        os.link(payload, tree / 'alias')
        frozen_candidate.readonly(tree)
        copied = self.root / 'writable'
        with self.no_clone():
            writable_copy(tree, copied)
        self.assertEqual((copied / 'file').stat().st_nlink, 1)
        self.assertNotEqual((copied / 'file').stat().st_ino, payload.stat().st_ino)
        (copied / 'file').write_bytes(b'mutated')
        self.assertEqual((copied / 'alias').read_bytes(), payload.read_bytes())
        self.assertEqual(payload.read_bytes(), self.source.read_bytes())
        frozen_candidate.remove(tree)

    def test_mode_override_gives_an_independent_copy_and_refuses_a_mismatched_link(self):
        for prefer in (('clone', 'copy'), ('copy',)):
            target = self.root / ('override-' + prefer[0])
            method = reuse.place(self.source, target, sha256=self.sha, readonly=False, prefer=prefer, mode=0o640)
            self.assertIn(method, prefer)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertNotEqual(target.stat().st_ino, self.source.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o444)
        with self.no_clone(), self.assertRaises(OSError):
            reuse.place(self.source, self.root / 'mismatch', sha256=self.sha, readonly=True,
                        prefer=('hardlink',), mode=0o555)
        self.assertFalse((self.root / 'mismatch').exists())
        with self.assertRaises(reuse.ReuseError):
            reuse.place(self.source, self.root / 'bad-mode', sha256=self.sha, readonly=False, mode=0o4755)

    def test_index_roots_offer_the_same_relative_path_filtered_by_mode_and_size(self):
        previous = self.write('previous/sub/file', mode=0o444)
        other = self.write('other/sub/file', body=b'different size', mode=0o444)
        self.write('writable/sub/file', mode=0o644)
        index = reuse.reuse_index({}, roots=[self.root / 'previous', self.root / 'other', self.root / 'writable'])
        size = previous.stat().st_size
        self.assertEqual(list(index.candidates(self.sha, 0o444, Path('sub/file'), size)), [previous])
        self.assertEqual(list(index.candidates(self.sha, 0o444, Path('sub/missing'), size)), [])
        self.assertEqual(len(list(index.candidates(self.sha, None, Path('sub/file'), size))), 2)
        self.assertNotIn(other, list(index.candidates(self.sha, None, Path('sub/file'), size)))
        self.assertIn(other, list(index.candidates(self.sha, None, Path('sub/file'))))

    def test_dedupe_clones_a_writable_file_from_a_hint_with_another_mode(self):
        hint = self.write('hint', mode=0o600)
        target = self.write('tree/log', mode=0o644)
        index = reuse.reuse_index({self.sha: [hint]})
        clone = reuse.capabilities(self.root)['clone']
        result = reuse.dedupe_tree(self.root / 'tree', index, readonly=False)
        self.assertEqual(result['clone'], 1 if clone else 0)
        self.assertEqual(result['hardlink'], 0)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), self.sha)
        self.assertEqual(stat.S_IMODE(hint.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()

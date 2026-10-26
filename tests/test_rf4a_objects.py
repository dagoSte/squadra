"""Git object IDs and batched blob reads preserve both supported object formats."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_rf4a_git as support


class GitObjects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='squadra-rf4a-objects-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        home = self.base / 'home'; home.mkdir()
        self.env = support.isolated_environment(home)
        context = patch.dict(os.environ, self.env, clear=True)
        context.start(); self.addCleanup(context.stop)

    def repo(self, fmt):
        path = self.base / fmt; path.mkdir()
        self.raw(path, 'init', '--quiet', '--object-format=' + fmt)
        return path

    def raw(self, repo, *args, input=None):
        return subprocess.run(['git', '-C', str(repo), *args], input=input, env=self.env,
                              capture_output=True, check=True, timeout=15).stdout

    def test_python_blob_ids_match_git_in_sha1_and_sha256_repositories(self):
        for fmt in ('sha1', 'sha256'):
            with self.subTest(format=fmt):
                repo = self.repo(fmt)
                self.assertEqual(support.registry.git_object_format(repo), fmt)
                for body in (b'', b'plain\n', b'zero\0and\nnewlines\n', b'x' * 1048576):
                    expected = self.raw(repo, 'hash-object', '--stdin', input=body).decode().strip()
                    self.assertEqual(support.registry.git_blob_id(body, fmt), expected)

    def test_binary_blobs_are_read_once_in_unique_input_order(self):
        for fmt in ('sha1', 'sha256'):
            with self.subTest(format=fmt):
                repo = self.repo(fmt)
                bodies = (b'first\0\nbody\n', b'', b'last\nbody\0')
                oids = [self.raw(repo, 'hash-object', '-w', '--stdin', input=body).decode().strip()
                        for body in bodies]
                filename = repo / 'newline\nfilename.txt'; filename.write_bytes(bodies[0])
                self.assertEqual(self.raw(repo, 'hash-object', str(filename)).decode().strip(), oids[0])
                with patch.object(support.registry, 'git_process', wraps=support.registry.git_process) as calls:
                    result = list(support.registry.git_blobs(repo, [oids[2], oids[0], oids[2], oids[1]]))
                self.assertEqual(result, [(oids[2], bodies[2]), (oids[0], bodies[0]), (oids[1], bodies[1])])
                self.assertEqual(calls.call_count, 1)
                self.assertEqual(calls.call_args.args[1:], ('cat-file', '--batch'))
                self.assertIsNotNone(calls.call_args.kwargs['stdout'])

    def test_stdout_redirection_keeps_stderr_captured_and_default_stdout_unchanged(self):
        repo = self.repo('sha1')
        expected = self.raw(repo, 'hash-object', '--stdin', input=b'blob body')
        with tempfile.TemporaryFile() as output:
            result = support.registry.git_process(repo, 'hash-object', '--stdin', input=b'blob body', stdout=output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(result.stdout)
            self.assertEqual(result.stderr, b'')
            output.seek(0)
            self.assertEqual(output.read(), expected)
        result = support.registry.git_process(repo, 'hash-object', '--stdin', input=b'blob body')
        self.assertEqual(result.stdout, expected)

    def test_missing_and_nonblob_objects_are_plain_refusals(self):
        repo = self.repo('sha1')
        with self.assertRaisesRegex(support.registry.Error, 'missing'):
            list(support.registry.git_blobs(repo, ['0' * 40]))
        tree = self.raw(repo, 'mktree', input=b'').decode().strip()
        with self.assertRaisesRegex(support.registry.Error, 'not a blob'):
            list(support.registry.git_blobs(repo, [tree]))

    def test_invalid_object_names_refuse_before_launch_and_empty_requests_do_not_launch(self):
        with patch.object(support.registry, 'git_process') as command:
            self.assertEqual(list(support.registry.git_blobs(self.base, [])), [])
            for oid in ('HEAD:path', '0' * 39, '0' * 65, 'g' * 40, '0' * 40 + '\n'):
                with self.subTest(oid=oid), self.assertRaises(support.registry.Error):
                    list(support.registry.git_blobs(self.base, [oid]))
        command.assert_not_called()

    def test_unknown_object_formats_refuse_plainly(self):
        with self.assertRaises(support.registry.Error):
            support.registry.git_blob_id(b'body', 'sha512')
        with patch.object(support.registry, 'git', return_value=b'unknown\n'):
            with self.assertRaises(support.registry.Error):
                support.registry.git_object_format(self.base)

    def test_malformed_truncated_or_mismatched_batch_output_refuses(self):
        body = b'body\0\n'
        oid = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        header = oid.encode() + b' blob ' + str(len(body)).encode() + b'\n'
        valid = header + body + b'\n'
        cases = {
            'missing header newline': header.rstrip(b'\n'),
            'oversized header': b'x' * 256 + b'\n',
            'wrong object': b'0' * 40 + header[40:] + body + b'\n',
            'wrong type': oid.encode() + b' tree 0\n\n',
            'invalid size': oid.encode() + b' blob -1\n',
            'truncated body': header + body[:-1],
            'missing body newline': header + body,
            'wrong body newline': header + body + b'x',
            'wrong content': header + b'x' * len(body) + b'\n',
            'trailing output': valid + b'extra',
        }
        for name, data in cases.items():
            def reply(repo, *args, stdout=None, **kwargs):
                self.assertIsNotNone(stdout)
                stdout.write(data)
                return subprocess.CompletedProcess(['git', *args], 0, None, b'')
            with self.subTest(case=name), patch.object(support.registry, 'git_process', side_effect=reply):
                with self.assertRaises(support.registry.Error):
                    list(support.registry.git_blobs(self.base, [oid]))


if __name__ == '__main__':
    unittest.main()

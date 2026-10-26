"""Secret headers and names apply uniformly across Git versions and save paths."""
import json
from pathlib import Path
import unittest

import test_ops_seams as ops
from recovery import private_key, secret

MARK = b'-' * 5

def key(kind=b'RSA PRIVATE KEY'):
    return MARK + b'BEGIN ' + kind + MARK + b'\nsynthetic\n'


class ContentAndNames(unittest.TestCase):
    def test_private_headers_anywhere_json_escaped_and_pgp(self):
        for kind in (b'PRIVATE KEY', b'RSA PRIVATE KEY', b'EC PRIVATE KEY',
                     b'OPENSSH PRIVATE KEY', b'PGP PRIVATE KEY BLOCK'):
            body = key(kind)
            for value in (body, b'KEY = """' + body, json.dumps({'private_key': body.decode()}).encode()):
                with self.subTest(kind=kind, value=value[:12]):
                    self.assertTrue(private_key(value))
        self.assertFalse(private_key(MARK + b'BEGIN PUBLIC KEY' + MARK + b'\npublic\n'))
        self.assertFalse(private_key(b'The words PRIVATE KEY are not a key header.'))

    def test_added_secret_names_and_template_controls(self):
        for name in ('client.pfx', 'client.ppk', '.git-credentials', 'store.jks', 'store.keystore',
                     'id_ecdsa', 'id_ecdsa_backup', 'id_dsa', 'id_dsa.old', '.npmrc', '.pypirc'):
            with self.subTest(name=name):
                self.assertTrue(secret(Path('sub') / name, b'placeholder'))
        self.assertTrue(secret(Path('.env.example'), b'placeholder'))
        self.assertFalse(secret(Path('.env.example'), b'placeholder', tracked=True))
        self.assertTrue(secret(Path('.env.example'), key(), tracked=True))


class SaveFlows(ops.Squad):
    def args(self):
        return ['save', '--actor', 'C', '--repo', str(self.repo), '--to', str(self.base / 'saved'), '--temporary']

    def test_untracked_embedded_key_is_excluded(self):
        (self.repo / 'service.json').write_text(json.dumps({'private_key': key().decode()}))
        rc, _, err = self.cli(*self.args()); self.assertEqual(rc, 0, err)
        manifest = json.loads((self.base / 'saved/MANIFEST.json').read_text())
        self.assertIn('service.json', manifest['repositories'][0]['excluded'])
        self.assertFalse((self.base / 'saved/repo-0/files/service.json').exists())

    def test_tracked_embedded_pgp_key_is_refused(self):
        (self.repo / 'service.json').write_text(json.dumps({'private_key': key(b'PGP PRIVATE KEY BLOCK').decode()}))
        self.git('add', 'service.json'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic key')
        rc, _, err = self.cli(*self.args())
        self.assertEqual(rc, 1, err); self.assertIn('service.json', err)

    def test_staged_embedded_key_is_refused_with_benign_head_and_worktree(self):
        path = self.repo / 'code.py'; old = path.read_bytes()
        path.write_bytes(b'KEY = """' + key()); self.git('add', 'code.py'); path.write_bytes(old)
        rc, _, err = self.cli(*self.args())
        self.assertEqual(rc, 1, err); self.assertIn('code.py', err)
        self.assertFalse((self.base / 'saved').exists())

    def test_name_allowance_cannot_override_an_embedded_key(self):
        path = self.repo / 'credentials.py'
        path.write_text(json.dumps({'private_key': key().decode()})); self.git('add', 'credentials.py')
        rc, _, err = self.cli(*self.args(), '--allow-name', 'credentials.py', '--reason', 'Synthetic control')
        self.assertEqual(rc, 1, err); self.assertIn('credentials.py', err)
        self.assertFalse((self.base / 'saved').exists())

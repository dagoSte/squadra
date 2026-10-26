"""A malformed inbox cursor file is refused in one plain line, never with a traceback."""
import subprocess
import unittest

import test_inbox_follow as support


class MalformedCursor(unittest.TestCase):
    setUp = support.InboxFollow.setUp
    argv = support.InboxFollow.argv
    inventory = support.InboxFollow.inventory

    def test_every_malformed_cursor_gets_the_same_plain_refusal(self):
        cursor = self.f.base / 'cursor.json'
        for body in (b'[]', b'"text"', b'1', b'null', b'{', b'\xff\xfe', b'{"root": 1, "agents": "A"}'):
            with self.subTest(body=body):
                cursor.write_bytes(body)
                before = self.inventory()
                p = subprocess.run(self.argv('--agent', 'A', '--follow', '--timeout', '1', '--cursor-file', str(cursor)),
                                   env=self.env, capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertNotIn('Traceback', p.stderr)
                self.assertEqual(p.stderr.strip().splitlines()[-1:], ['Squadra: Invalid inbox cursor file'], p.stderr)
                self.assertEqual(p.stdout, '')
                self.assertEqual(self.inventory(), before)
                self.assertEqual(cursor.read_bytes(), body)


if __name__ == '__main__':
    unittest.main()

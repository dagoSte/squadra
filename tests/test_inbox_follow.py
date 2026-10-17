"""A real follower streams verified mail, resumes gaps and never acknowledges it."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_squadra as support
from registry import Store, attention
from squadra import dispatch, parser

CLI = Path(__file__).resolve().parents[1] / 'scripts/squadra.py'


class InboxFollow(unittest.TestCase):
    def setUp(self):
        self.f = support.Workflow()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.ack()
        self.store = self.f.store
        self.env = self.f.env | {'CODEX_HOME': str(self.f.home / 'codex'),
                                 'CLAUDE_CONFIG_DIR': str(self.f.home / 'claude')}

    def send(self, name, to='A', **extra):
        ident = self.store.append('message', 'C', dict(id=name, to=to, body='First line\nSecond', **extra))
        return self.store.db.execute('SELECT seq FROM events WHERE id=?', (ident,)).fetchone()[0]

    def argv(self, *args):
        return [sys.executable, '-B', str(CLI), '--root', str(self.f.root), 'inbox', *args]

    def invoke(self, *args):
        p = subprocess.run(self.argv(*args), env=self.env, capture_output=True, text=True, timeout=8)
        self.assertEqual(p.returncode, 0, p.stderr)
        return [json.loads(line) for line in p.stdout.splitlines()]

    def start(self, *args):
        p = subprocess.Popen(self.argv(*args), env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1)
        lines, arrivals = [], queue.Queue()
        def read():
            for line in p.stdout:
                lines.append(line)
                arrivals.put((time.monotonic(), line))
            arrivals.put((time.monotonic(), None))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def cleanup():
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=4)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=4)
            reader.join(timeout=2)
            p.stdout.close(); p.stderr.close()
        self.addCleanup(cleanup)
        return p, reader, lines, arrivals

    def next_line(self, p, arrivals, kind=None):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            try:
                stamp, line = arrivals.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                self.fail('follower did not flush a line while running')
            if line is None:
                self.fail('follower exited before mail: ' + p.stderr.read())
            value = json.loads(line)
            if kind is None or value['kind'] == kind:
                return stamp, value
        self.fail('expected line kind was not observed')

    def inventory(self):
        # SQLite read marks live in -shm; persistent registry data must not change.
        return {str(p.relative_to(self.f.root)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                                 p.stat().st_mode & 0o777, p.stat().st_mtime_ns)
                for p in self.f.root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}

    def test_pending_union_is_oldest_first_bounded_and_shared_with_attention(self):
        self.send('already'); self.store.append('ack', 'A', {'message': 'already'})
        self.send('old-info', info=True); self.send('other', to='R')
        expected = []
        for n in range(12):
            expected.append('z' + str(12 - n))
            self.send(expected[-1], to='A' if n % 2 else 'A-alias', related_task='T')
        waiting = {w['id'] for w in attention(self.store.view())['waiting']
                   if w['kind'] == 'ack' and set(w['who']) & {'A', 'A-alias'}}
        self.assertEqual(waiting, set(expected))
        self.store.release_views()
        rows = self.invoke('--agent', 'A', '--agent', 'A-alias', '--agent', 'A', '--follow', '--timeout', '2')
        self.assertEqual(rows[0]['kind'], 'pending'); self.assertEqual(rows[0]['count'], 12)
        messages = [r for r in rows if r['kind'] == 'message']
        self.assertEqual([r['id'] for r in messages], expected[:10])
        self.assertEqual([r['event'] for r in messages], sorted(r['event'] for r in messages))
        more = next(r for r in rows if r['kind'] == 'more')
        self.assertEqual(more['count'], 2)
        command = shlex.split(more['inbox_command'])
        self.assertEqual(command, ['squadra', '--root', str(self.f.root), 'inbox', '--agent', 'A', '--agent', 'A-alias'])
        for r in messages:
            self.assertEqual(r['task'], 'T'); self.assertTrue(r['ack_needed'])
            self.assertEqual(shlex.split(r['ack_command']), ['squadra', '--root', str(self.f.root),
                             'ack', '--actor', r['to'], '--message', r['id']])

    def test_second_process_mail_is_flushed_and_information_is_marked(self):
        p, reader, lines, arrivals = self.start('--agent', 'A', '--agent', 'A-alias', '--follow', '--timeout', '7')
        self.next_line(p, arrivals, 'cursor')
        self.assertIsNone(p.poll())
        code = """import sys,json,time
from registry import Store
s=Store(sys.argv[1])
s.batch([('message','C',{'id':'new','to':'A','body':'arrived','related_task':'T'},None),
         ('message','C',{'id':'info','to':'A-alias','body':'notice','info':True},None),
         ('message','C',{'id':'other','to':'R','body':'hidden'},None)])
print(json.dumps({'committed':time.monotonic()}),flush=True)
s.close()
"""
        child = subprocess.run([sys.executable, '-B', '-c', code, str(self.f.root)],
                               env=self.env | {'PYTHONPATH': str(CLI.parent)}, capture_output=True,
                               text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        commit = json.loads(child.stdout)['committed']
        observed, new = self.next_line(p, arrivals, 'message')
        _, info = self.next_line(p, arrivals, 'message')
        self.assertEqual(new['id'], 'new'); self.assertTrue(new['ack_needed'])
        self.assertEqual(info['id'], 'info'); self.assertTrue(info['info'])
        self.assertFalse(info['ack_needed']); self.assertIsNone(info['ack_command'])
        self.assertLess(observed - commit, 4, '2 s poll plus scheduling/verification allowance')
        p.send_signal(signal.SIGINT); self.assertEqual(p.wait(timeout=4), 0)
        reader.join(timeout=2)
        self.assertEqual([json.loads(l)['id'] for l in lines if json.loads(l)['kind'] == 'message'], ['new', 'info'])
        self.assertIsNone(self.store.view()['messages']['new']['ack'])

    def test_since_drains_the_prearm_gap_then_rearm_is_silent(self):
        self.send('old')
        since = self.store.events()[-1]['seq']
        new = self.send('gap')
        self.send('gap-info', info=True)
        self.send('gap-acked'); self.store.append('ack', 'A', {'message': 'gap-acked'})
        self.send('not-for-me', to='R')
        rows = self.invoke('--agent', 'A', '--follow', '--since', str(since), '--timeout', '2')
        self.assertEqual(rows[0]['kind'], 'message'); self.assertEqual(rows[0]['id'], 'gap')
        self.assertEqual(rows[0]['event'], new)
        messages = [r for r in rows if r['kind'] == 'message']
        self.assertEqual([r['id'] for r in messages], ['gap', 'gap-info', 'gap-acked'])
        self.assertFalse(messages[-1]['ack_needed'])
        self.assertFalse(any(r['kind'] in {'pending', 'more'} for r in rows))
        last = max(r['event'] for r in rows)
        self.assertEqual(self.invoke('--agent', 'A', '--follow', '--since', str(last), '--timeout', '2'), [])

    def test_timeout_and_signals_preserve_registry_inventory(self):
        self.send('pending')
        before = self.inventory()
        rows = self.invoke('--agent', 'A', '--follow', '--timeout', '2')
        self.assertTrue(any(r['kind'] == 'message' for r in rows))
        self.assertEqual(self.inventory(), before)
        for sig in (signal.SIGINT, signal.SIGTERM):
            p, reader, _, arrivals = self.start('--agent', 'A', '--follow')
            self.next_line(p, arrivals, 'cursor')
            p.send_signal(sig); self.assertEqual(p.wait(timeout=4), 0)
            reader.join(timeout=2)
            self.assertEqual(self.inventory(), before)

    def test_quiet_since_arm_has_no_lines_and_no_writes(self):
        since = self.store.events()[-1]['seq']; before = self.inventory()
        self.assertEqual(self.invoke('--agent', 'A', '--follow', '--since', str(since), '--timeout', '2'), [])
        self.assertEqual(self.inventory(), before)

    def test_idle_poll_uses_only_the_head_and_changed_poll_releases_its_view(self):
        from inbox_follow import Follower
        s = Store(self.f.root, read_only=True); self.addCleanup(s.close)
        follower = Follower(s, ['A'], self.store.events()[-1]['seq'])
        with patch.object(s, 'view', wraps=s.view) as view, contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll()); self.assertEqual(view.call_count, 1)
            self.assertEqual(s._views, [])
            for _ in range(5):
                self.assertIsNone(follower.poll())
            self.assertEqual(view.call_count, 1)
            self.send('changed')
            batch = follower.poll(); self.assertEqual(view.call_count, 2)
            self.assertEqual(s._views, [])
            follower.emit(batch)
            self.assertIsNone(follower.poll()); self.assertEqual(view.call_count, 2)

    def test_snapshot_race_and_partial_flush_keep_a_safe_resume_cursor(self):
        from inbox_follow import Follower
        s = Store(self.f.root, read_only=True); self.addCleanup(s.close)
        start = self.store.events()[-1]['seq']; follower = Follower(s, ['A'], start)
        view = s.view
        def during_view():
            state = view()
            self.send('after-snapshot')
            return state
        with patch.object(s, 'view', side_effect=during_view), contextlib.redirect_stdout(io.StringIO()):
            follower.emit(follower.poll())
        batch = follower.poll()
        self.assertEqual([r['id'] for r in batch[2] if r['kind'] == 'message'], ['after-snapshot'])
        self.send('second')
        batch = follower.poll()
        out = io.StringIO(); written = 0
        def interrupt(text, **kwargs):
            nonlocal written
            if written == 1:
                raise KeyboardInterrupt
            out.write(text + '\n'); written += 1
        with patch('builtins.print', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            follower.emit(batch)
        last = json.loads(out.getvalue())['event']
        rows = self.invoke('--agent', 'A', '--follow', '--since', str(last), '--timeout', '2')
        self.assertEqual([r['id'] for r in rows if r['kind'] == 'message'], ['second'])

    def test_verified_fallback_and_plain_inbox_keep_their_semantics(self):
        self.send('z-first'); self.send('a-info', info=True)
        old = dispatch(parser().parse_args(['inbox', '--agent', 'A']), self.store)
        self.assertEqual([r['id'] for r in old], ['assign-T', 'z-first', 'a-info'])
        self.store.release_views()
        self.store.db.execute('DELETE FROM derived_state_meta')
        before = self.inventory()
        rows = self.invoke('--agent', 'A', '--follow', '--since', '0', '--timeout', '2')
        self.assertEqual([r['id'] for r in rows if r['kind'] == 'message'], ['assign-T', 'z-first', 'a-info'])
        self.assertEqual(self.inventory(), before)

    def test_preview_is_one_bounded_unicode_line_and_ack_command_handles_spaces(self):
        body = 'é' * 170 + '\nSECOND-BODY-CONTENT-CANARY\x1b[0m'
        self.store.append('message', 'C', {'id': 'preview', 'to': 'A', 'body': body})
        rows = self.invoke('--agent', 'A', '--follow', '--timeout', '2')
        row = next(r for r in rows if r['kind'] == 'message')
        self.assertEqual(row['preview'], 'é' * 160)
        self.assertNotIn('SECOND-BODY-CONTENT-CANARY', json.dumps(rows))
        command = shlex.split(row['ack_command'])
        self.assertEqual(command[2], str(self.f.root))
        self.assertIsNone(self.store.view()['messages']['preview']['ack'])
        from inbox_follow import message_line
        spaced = self.f.base / 'registry with spaces'
        quoted = message_line(spaced, {'id': 'quoted', 'to': 'A', 'from': 'C',
                              'body': 'quoted "text"\t\x1b end\nSECOND-BODY-CONTENT-CANARY'}, 123)
        self.assertEqual(shlex.split(quoted['ack_command'])[2], str(spaced))
        self.assertEqual(json.loads(json.dumps(quoted))['preview'], 'quoted "text"\t\x1b end')
        self.assertNotIn('SECOND-BODY-CONTENT-CANARY', json.dumps(quoted))

    def test_unavailable_registry_refuses_without_creating_a_workspace(self):
        missing = self.f.base / 'missing-registry'
        argv = self.argv('--agent', 'A', '--follow', '--timeout', '2')
        argv[argv.index('--root') + 1] = str(missing)
        p = subprocess.run(argv, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Unknown workspace', p.stderr)
        self.assertFalse(missing.exists())

    def test_pool_handoff_backlog_uses_shared_attention_for_all_named_readers(self):
        from squadra import deliver
        self.store.append('agent', 'R2', {'id': 'R2', 'role': 'reviewer', 'identity': 'reader2',
                          'client': 'claude', 'repo': str(self.f.repo), 'skills': []})
        current = self.store.state()['tasks']['T']
        task = {k: current[k] for k in ('id', 'title', 'owner', 'repo', 'base', 'scope', 'proof', 'depends', 'full_gate')}
        task.update(id='POOL', reviewer=None, pool=['R', 'R2'])
        self.store.batch([('task', 'C', task, None), ('message', 'C',
                         {'id': 'assign-POOL', 'to': 'A', 'task': 'POOL', 'body': 'Assignment'}, None)])
        self.f.ack('POOL'); self.assertEqual(self.f.proof(task='POOL')['native_rc'], 0)
        handoffs = deliver(self.store, 'A', 'POOL')['handoffs']
        rows = self.invoke('--agent', 'R', '--agent', 'R2', '--follow', '--timeout', '2')
        self.assertEqual(rows[0]['count'], 2)
        self.assertEqual({r['id'] for r in rows if r['kind'] == 'message'}, set(handoffs))
        self.store.append('ack', 'R', {'message': handoffs[0]})
        rows = self.invoke('--agent', 'R2', '--follow', '--timeout', '2')
        self.assertEqual(rows[0]['count'], 0)

    def test_invalid_arguments_and_corrupt_registry_refuse_without_writes(self):
        for flags in [('--timeout', '1'), ('--since', '0'), ('--follow', '--timeout', 'nan'),
                      ('--follow', '--timeout', '-1'), ('--follow', '--since', '-1'),
                      ('--follow', '--since', '999999')]:
            before = self.inventory()
            p = subprocess.run(self.argv('--agent', 'A', *flags), env=self.env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(p.returncode, 0, flags); self.assertEqual(self.inventory(), before)
        self.send('corrupt')
        self.store.release_views()
        self.store.db.execute('DROP TRIGGER no_update')
        self.store.db.execute("UPDATE events SET data='{}' WHERE seq=(SELECT max(seq) FROM events)")
        before = self.inventory()
        p = subprocess.run(self.argv('--agent', 'A', '--follow', '--timeout', '2'), env=self.env,
                           capture_output=True, text=True, timeout=5)
        self.assertNotEqual(p.returncode, 0); self.assertIn('integrity', p.stderr.lower())
        self.assertEqual(p.stdout, ''); self.assertEqual(self.inventory(), before)


if __name__ == '__main__':
    unittest.main()

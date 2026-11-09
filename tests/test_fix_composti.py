"""R315-FIX.COMPOSTI: assign never leaves lane assignments untransported; the seat note copies recorded fields only."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_composite as composite_support  # also puts scripts/ on sys.path
from context_brief import seat_context
from registry import Store, plain


class FixComposti(composite_support.Composite):
    """Reuses the synthetic squad of the composite tests: coordinator C, executor A, reader R."""


for _name in [n for n in dir(composite_support.Composite) if n.startswith('test_')]:
    setattr(FixComposti, _name, None)


class FixComposti(FixComposti):
    def test_assign_refuses_paths_before_recording_any_task(self):
        (self.repo / 'a.py').write_text('A = 1\n'); (self.repo / 'b.py').write_text('B = 1\n')
        rc, out, err = self.invoke('assign', *self.task_args(), '--paths', 'a.py', '--paths', 'b.py',
                                   '--brief-file', str(self.brief), '--via', 'manual')
        self.assertNotEqual(rc, 0, 'assign --paths recorded lanes whose assignments it does not transport')
        self.assertIn('task --paths', out + err)
        state = self.view()
        self.assertEqual(state['tasks'], {})
        self.assertFalse(any(m['id'].startswith('assign-') for m in state['messages'].values()))

    def test_assign_without_paths_still_works(self):
        out = self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        self.assertIn('"ready": true', out)

    def realistic_state(self):
        """Assignment transported, a ping, a reply and a plain message: the fields the journal records."""
        self.assert_ok('assign', *self.task_args(), '--brief-file', str(self.brief), '--via', 'manual')
        self.assert_ok('send', '--actor', 'C', '--to', 'A', '--id', 'NOTE', '--body-file', str(self.body))
        self.assert_ok('send', '--actor', 'A', '--to', 'C', '--id', 'REPLY', '--reply-to', 'NOTE', '--body-file', str(self.body))
        self.assert_ok('transport', '--actor', 'C', '--message', 'NOTE', '--via', 'manual')
        store = Store(self.root, read_only=True)
        return store, store.resolved()

    def note_messages(self, store, state, seat):
        packet = seat_context(store, seat, max_chars=None, state=state)
        packet = json.loads(packet) if isinstance(packet, str) else packet
        return {m['id']: m for m in packet['pending_messages']}

    def test_seat_note_keeps_every_recorded_field_of_pending_mail(self):
        store, state = self.realistic_state()
        try:
            note = self.note_messages(store, state, 'C')
            self.assertTrue({'assign-Q', 'NOTE', 'REPLY'} <= set(note))
            for ident, message in note.items():
                recorded = {k: v for k, v in plain(state['messages'][ident]).items() if not k.startswith('transport_')}
                with self.subTest(message=ident):
                    # A field the journal records but the note's list forgets would be dropped here.
                    self.assertEqual(message, recorded)
            self.assertEqual(note['NOTE']['transport']['via'], 'manual')
        finally:
            store.close()

    def test_seat_note_drops_any_read_time_field_not_only_transport_ones(self):
        store, state = self.realistic_state()
        try:
            for ident in ('assign-Q', 'NOTE'):
                state['messages'][ident]['transport_first_utc'] = '2026-11-09T10:00:00+00:00'
                state['messages'][ident]['wait_seconds_so_far'] = 42  # a later read-time derivation
            for ident, message in self.note_messages(store, state, 'C').items():
                with self.subTest(message=ident):
                    self.assertNotIn('transport_first_utc', message)
                    self.assertNotIn('wait_seconds_so_far', message)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()

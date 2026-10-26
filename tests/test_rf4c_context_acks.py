"""A resume packet lists as pending ACKs exactly the messages the registry waits on."""
import unittest

import test_context_brief as support


class PendingAcks(unittest.TestCase):
    state = support.ContextBrief.state
    packet = support.ContextBrief.packet

    def test_information_and_replaced_messages_are_not_pending_acks(self):
        state = self.state()
        state['messages'].update({
            'needs-ack': {'id': 'needs-ack', 'to': 'A', 'task': 'T', 'ack': None},
            'info': {'id': 'info', 'to': 'A', 'task': 'T', 'ack': None, 'info': True},
            'old': {'id': 'old', 'to': 'A', 'task': 'T', 'ack': None, 'replaced_by': 'needs-ack'},
            'done': {'id': 'done', 'to': 'A', 'related_task': 'T', 'ack': {'agent': 'A'}},
        })
        self.assertEqual([m['id'] for m in self.packet(state)['pending_acks']], ['needs-ack'])


if __name__ == '__main__':
    unittest.main()

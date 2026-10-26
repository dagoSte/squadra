"""The precheck lease-inheritance scenario holds on a loaded machine, where the child reports long after it starts."""
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_precheck_ownership as ownership

STARTUP_SECONDS = 6


class LoadedMachine(unittest.TestCase):
    setUp = ownership.PrecheckOwnership.setUp
    git = ownership.PrecheckOwnership.git
    ack = ownership.PrecheckOwnership.ack
    task = ownership.PrecheckOwnership.task
    declare = ownership.PrecheckOwnership.declare
    get = ownership.PrecheckOwnership.get
    runs = ownership.PrecheckOwnership.runs
    held = ownership.PrecheckOwnership.held

    def test_a_child_slower_than_any_fixed_wait_still_reports_its_inherited_lease(self):
        real = subprocess.Popen

        def slow(argv, *args, **kwargs):
            # A loaded machine: the reporting child starts STARTUP_SECONDS late.
            if argv[:2] == [sys.executable, '-c'] and 'child-ready' in argv[2]:
                argv = [argv[0], '-c', 'import time\ntime.sleep(%d)\n' % STARTUP_SECONDS + argv[2]]
            return real(argv, *args, **kwargs)

        with patch('subprocess.Popen', slow):
            ownership.PrecheckOwnership.test_child_inherits_its_lease_and_cleanup_keeps_the_whole_batch_owned(self)


if __name__ == '__main__':
    unittest.main()

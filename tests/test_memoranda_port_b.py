"""Second time-balanced group of the memoranda port; GROUPS in test_memoranda_port lists it."""
from test_memoranda_port import port_suite


def load_tests(loader, tests, pattern):
    return port_suite(loader, __name__)

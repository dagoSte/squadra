"""Carry the declaration, privacy, connector and action oracles into the public bundle.

The case files are split into loader modules balanced by measured run time, so the
parallel runner gives each group its own process. This module holds the shared helper
and the first group; the siblings test_memoranda_port_b and _c load the others.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

CASES = Path(__file__).with_name('memoranda_cases')
# Every case file exactly once, in sorted order, so discovery keeps the same ordered IDs.
GROUPS = {
    'test_memoranda_port': ('test_about', 'test_actions', 'test_auto'),
    'test_memoranda_port_b': ('test_collect', 'test_connector'),
    'test_memoranda_port_c': ('test_link', 'test_logo', 'test_panel', 'test_source'),
}
sys.path.insert(0, str(CASES))
sys.path.insert(0, str(CASES.parents[1] / 'plugins/memoranda/bridge'))


def port_suite(loader, group):
    """Load one group's case files as modules named memoranda_port_<stem>."""
    listed = [stem for stems in GROUPS.values() for stem in stems]
    if listed != sorted(path.stem for path in CASES.glob('test_*.py')):
        raise RuntimeError('GROUPS must list every memoranda case file once, in sorted order')
    suite = unittest.TestSuite()
    for stem in GROUPS[group]:
        name = 'memoranda_port_' + stem
        spec = importlib.util.spec_from_file_location(name, CASES / f'{stem}.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


def load_tests(loader, tests, pattern):
    return port_suite(loader, __name__)

"""Carry the declaration, privacy, connector and action oracles into the public bundle."""
import importlib.util
from pathlib import Path
import sys
import unittest


def load_tests(loader, tests, pattern):
    cases = Path(__file__).with_name('memoranda_cases')
    bridge = cases.parents[1] / 'plugins/memoranda/bridge'
    sys.path.insert(0, str(cases))
    sys.path.insert(0, str(bridge))
    suite = unittest.TestSuite()
    for path in sorted(cases.glob('test_*.py')):
        name = 'memoranda_port_' + path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite

"""Run a registered oracle and report assertions separately from setup failures."""
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import traceback
import unittest


def run(config):
    if config['runner'] == 'python-check-v1':
        spec = importlib.util.spec_from_file_location('fix_oracle', config['oracle'])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        check = module.check
        try:
            check(Path.cwd())
        except AssertionError:
            traceback.print_exc()
            return {'outcome': 'assertion'}, 1
        return {'outcome': 'passed'}, 0

    # Only explicitly named methods qualify; discovery and missing names are errors.
    sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(config['ids'])
    def cases(value):
        for item in value:
            if isinstance(item, unittest.TestSuite):
                yield from cases(item)
            else:
                yield item
    selected = list(cases(suite))
    ids = [case.id() for case in selected]
    if loader.errors or ids != config['ids']:
        raise ValueError('Requested test IDs did not load exactly')
    allowed = {(Path.cwd() / name).resolve() for name in config['files']}
    for case in selected:
        method = inspect.unwrap(getattr(case, case.id().rsplit('.', 1)[1]))
        for value in (type(case), method):
            if Path(inspect.getsourcefile(value)).resolve() not in allowed:
                raise ValueError('Test definition is outside the agreed files')

    class Result(unittest.TextTestResult):
        def __init__(self, *args):
            super().__init__(*args)
            self.executed = []
            self.asserted = set()
            self.invalid = False

        def startTest(self, test):
            self.executed.append(test.id())
            super().startTest(test)

        def assertion(self, test, err):
            frames = traceback.extract_tb(err[2]) + traceback.extract_stack()
            in_method = any(f.name == '_callTestMethod' and
                            Path(f.filename).resolve() == Path(unittest.case.__file__).resolve()
                            for f in frames)
            if issubclass(err[0], AssertionError) and in_method:
                self.asserted.add(test.id())
            else:
                self.invalid = True

        def addFailure(self, test, err):
            self.assertion(test, err)
            super().addFailure(test, err)

        def addSubTest(self, test, subtest, err):
            if err is not None:
                self.assertion(test, err)
            super().addSubTest(test, subtest, err)

    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    invalid = (result.invalid or result.errors or result.skipped or result.expectedFailures or
               result.unexpectedSuccesses or result.executed != ids or result.testsRun != len(ids))
    outcome = ('instrument-error' if invalid else 'assertion' if result.failures else 'passed')
    return {'outcome': outcome, 'ids': ids, 'executed': result.executed,
            'assertions': sorted(result.asserted), 'tests': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'skips': len(result.skipped), 'xfail': len(result.expectedFailures),
            'xpass': len(result.unexpectedSuccesses)}, {'passed': 0, 'assertion': 1, 'instrument-error': 2}[outcome]


if __name__ == '__main__':
    try:
        result, rc = run(json.loads(Path(sys.argv[1]).read_text()))
    except BaseException as exc:
        traceback.print_exc()
        result, rc = {'outcome': 'instrument-error', 'error': type(exc).__name__}, 2
    Path(sys.argv[2]).write_text(json.dumps(result) + '\n')
    raise SystemExit(rc)

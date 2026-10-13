"""Isolated installed-bridge probe: one helper instance and observable write traps."""
import contextlib
import io
import json
from pathlib import Path
import sys

folder = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(folder))
import playwright_plugin as bridge
import plugins
import plugin_actions
import plugin_settings
import registry

assert bridge.Store is registry.Store
assert bridge.p is plugins and bridge.files is plugin_settings and bridge.actions is plugin_actions
modules = {m.__name__: str(Path(m.__file__).resolve())
           for m in (bridge, plugins, plugin_actions, plugin_settings, registry)}
assert all(Path(file).parent == folder for file in modules.values()), modules
assert not any(Path(p).name == 'scripts' for p in sys.path)
calls, opens = [], []
original = registry.Store.__init__

def opened(self, *args, **kwargs):
    opens.append(kwargs.get('read_only'))
    assert kwargs.get('read_only') is True, 'read_only trap'
    return original(self, *args, **kwargs)

def trap(name):
    def forbidden(*args, **kwargs):
        calls.append(name)
        raise AssertionError('write trap: ' + name)
    return forbidden

registry.Store.__init__ = opened
for name in ('append', 'batch', 'project'):
    setattr(registry.Store, name, trap(name))
out, err = io.StringIO(), io.StringIO()
error = None
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = bridge.main(json.loads(sys.argv[2]))
except AssertionError as exc:
    error, rc = str(exc), 1
print(json.dumps({'rc': rc, 'stdout': out.getvalue(), 'stderr': err.getvalue(),
                  'error': error, 'trap_calls': calls, 'readonly_calls': opens, 'modules': modules}))

"""Local write probes: an observation in the calling window, never a permission grant."""
from __future__ import annotations

from contextlib import closing
import errno
import os
from pathlib import Path
import tempfile

from registry import Store, access_view, now, plain, require
from registry_projections import ProjectionError


def write_probe(path):
    result = {'path': str(Path(path).resolve()), 'outcome': 'unknown', 'error': ''}
    name = None
    try:
        fd, name = tempfile.mkstemp(prefix='.squadra-access-', dir=result['path'])
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'Squadra access probe\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.unlink(name)
        name = None
        result['outcome'] = 'writable'
    except OSError as exc:
        result['outcome'] = 'denied' if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS} else 'unknown'
        result['error'] = errno.errorcode.get(exc.errno, type(exc).__name__)
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except OSError:
                result['outcome'] = 'unknown'
                result['error'] = 'cleanup-failed'
    return result


def run(root, actor, action, repo=None, session=None, context=None):
    root = Path(root).resolve()
    with closing(Store(root, read_only=True)) as store:
        state = {'agents': plain(store.view()['agents'])}
    require(actor in state['agents'], 'Registered actor required')
    if action == 'status':
        return access_view(state, actor, repo=repo, root=str(root))
    require(repo and session and context, 'Access probe requires --repo, --session and --context')
    agent = state['agents'][actor]
    require(session == agent.get('session'), 'Access probe session must match the registered recipient')
    environment = {'codex': 'CODEX_THREAD_ID', 'claude': 'CLAUDE_SESSION_ID'}.get(agent['client'])
    observed = os.environ.get(environment, '') if environment else ''
    require(not observed or observed == session, 'Access probe must run in the recipient session, not another window')
    receipt = {'identity': agent['identity'], 'session': session, 'observed_session': observed,
               'registered_repo': agent['repo'], 'context': context,
               'registry': write_probe(root), 'clone': write_probe(repo)}
    # The JSON receipt remains usable even when the registry cannot be written here.
    state['agents'][actor]['access_probe'] = {**receipt, 'utc': now()}
    result = {'recorded': False, 'receipt': receipt, 'access': access_view(state, actor, repo=repo, root=str(root))}
    if receipt['registry']['outcome'] == 'writable':
        try:
            with closing(Store(root)) as store:
                store.append('access.probe', actor, receipt)
                result['recorded'] = True
                result['access'] = access_view(store.view(), actor, repo=repo, root=str(root))
                try:
                    store.publish()
                except (OSError, ProjectionError) as exc:
                    result['projection_error'] = type(exc).__name__ + ': command committed; projections unavailable; do not retry'
        except (PermissionError, OSError) as exc:
            result['record_error'] = type(exc).__name__
    return result

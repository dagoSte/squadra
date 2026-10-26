"""Release-local plugin catalogue and explicit, resumable private installation."""
from contextlib import contextmanager, ExitStack
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import unicodedata

import plugins as p
import plugin_settings as settings

RELEASE_ROOT = Path(__file__).resolve().parents[1]
ENTRIES, FILES, DEPTH = 128, 256, 8
FILE_BYTES, BUNDLE_BYTES, RECEIPT_BYTES = 4 * 1024**2, 32 * 1024**2, 2 * 1024**2
RECEIPT, LOCK = '.store-receipt.json', '.store.lock'
RESERVED = {'settings.json', 'secrets', 'actions', 'panels', '.settings.lock', RECEIPT, LOCK}
DISCOVERY = {'skill.md', '.claude-plugin', '.codex-plugin', '.agents', '.mcp.json'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(body):
    return hashlib.sha256(body).hexdigest()


def component(name):
    p.require(type(name) is str and re.fullmatch(r'[A-Za-z0-9._-]{1,128}', name)
              and name not in {'.', '..'}, 'invalid_bundle')


def relative(name):
    p.require(type(name) is str and 1 <= len(name.split('/')) <= DEPTH, 'invalid_bundle')
    parts = name.split('/')
    for part in parts:
        component(part)
        p.require(part.casefold() not in DISCOVERY, 'invalid_bundle')
    p.require(parts[0].casefold() not in RESERVED, 'invalid_bundle')
    return parts


class Named(p.Refused):
    """A refusal that names its folder and, when chmod can repair it, the mode argument; str() stays the code."""
    def __init__(self, code, path, repair=None):
        super().__init__(code)
        self.path, self.repair = path, repair


def where(fd, name=None):
    """Absolute path of an open folder, joined with name, for messages only; None where the system cannot tell."""
    try:
        if hasattr(fcntl, 'F_GETPATH'):
            path = os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b'\0', 1)[0])
        else:
            path = os.readlink('/proc/self/fd/' + str(fd))
    except OSError:
        return None
    return path if name is None else os.path.join(path, name)


def opened(parent, name):
    try:
        return p._open(parent, name, directory=True)
    except p.Refused as exc:
        raise Named(exc.code, name if parent is None else where(parent, name)) from None


def checked(fd, private=False):
    try:
        p.check_directory(fd, private=private)
    except p.Refused:
        own = os.fstat(fd).st_uid == os.getuid()
        raise Named('unsafe_permissions', where(fd), ('700' if private else 'go-w') if own else None) from None


def explain(exc, root=None):
    """CLI text for a refusal: its code, the path it names and, for an unsafe folder, the exact repair."""
    path = getattr(exc, 'path', None)
    if not path:
        return exc.code
    if exc.code == 'symlink':
        return 'symlink: ' + path + ' is a symbolic link, which the Store does not follow'
    if exc.code == 'unmanaged_installation':
        return ('unmanaged_installation: ' + path + '. Save settings.json and secrets/ separately; '
                'move this folder aside, run squadra plugin install <name>, then restore the saved '
                'settings and secrets only after checking compatibility. See references/plugins.md.')
    if exc.code != 'unsafe_permissions':
        return exc.code + ': ' + path
    if exc.repair is None:
        return 'unsafe_permissions: ' + path + ' belongs to another user'
    root = os.path.realpath(RELEASE_ROOT if root is None else root)
    # One recursive repair covers a release tree copied or cloned under a group-writable umask.
    inside = os.path.commonpath([root, os.path.realpath(path)]) == root
    command = ['chmod', '-R', 'go-w', root] if inside else ['chmod', exc.repair, path]
    return 'unsafe_permissions: ' + path + ' is open to other users; repair: ' + shlex.join(command)


@contextmanager
def directory(parent, name, *, private=False, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    fd = opened(parent, name)
    try:
        checked(fd, private=private)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def home_root(home, create=False):
    # HOME itself may be a link (network or managed homes); every level below it is opened without following.
    with directory(None, os.path.realpath(home)) as home_fd:
        with directory(home_fd, '.squadra', create=create) as squad:
            with directory(squad, 'plugins', create=create) as root:
                yield root


@contextmanager
def parent_for(root, name, create=False):
    parts = relative(name)
    with ExitStack() as stack:
        fd = root
        for part in parts[:-1]:
            fd = stack.enter_context(directory(fd, part, private=True, create=create))
        yield fd, parts[-1]


def release_info(root):
    # This code fingerprint works in client caches without Git or SOURCE_COMMIT.
    with directory(None, os.fspath(root)) as fd:
        with directory(fd, '.codex-plugin') as meta:
            manifest = p._read(meta, 'plugin.json', p.MANIFEST_BYTES)
        p.require(type(manifest) is dict, 'invalid_release')
        version = manifest.get('version')
        p.require(type(version) is str and len(version) <= 32 and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version), 'invalid_release')
        with directory(fd, 'scripts') as scripts:
            hashes = {name: digest(p._read_bytes(scripts, name, FILE_BYTES))
                      for name in ('squadra.py', 'registry.py', 'plugins.py', 'plugin_store.py')}
    return {'version': version, 'id': digest(canonical(hashes))}


def bundle(root, name):
    p.require(type(name) is str and p.NAME.fullmatch(name), 'bad_name')
    bodies = {}
    size = 0
    nodes = 0

    def walk(fd, prefix=''):
        nonlocal size, nodes
        # scandir is stopped before accumulating an unbounded directory listing.
        seen = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                p.require(entry.name.casefold() not in seen, 'invalid_bundle')
                seen.add(entry.name.casefold())
                nodes += 1
                p.require(nodes <= FILES * 2, 'too_large')
                path = prefix + entry.name
                relative(path)
                if entry.is_dir(follow_symlinks=False):
                    with directory(fd, entry.name) as child:
                        walk(child, path + '/')
                else:
                    p.require(len(bodies) < FILES, 'too_large')
                    bodies[path] = p._read_bytes(fd, entry.name, FILE_BYTES)
                    size += len(bodies[path])
                    p.require(size <= BUNDLE_BYTES, 'too_large')

    with directory(None, os.fspath(root)) as root_fd:
        with directory(root_fd, 'plugins') as catalogue:
            with directory(catalogue, name) as fd:
                walk(fd)
                manifest = p._manifest(fd, name)
                p.require(all(text.strip() for field in ('title', 'description') for text in manifest[field].values()), 'invalid_bundle')
                p.require('about' in manifest and 'homepage' in manifest['about'], 'invalid_bundle')
                # Validate and copy the same manifest bytes, even if a writer intervened.
                p.require(bodies['plugin.json'] == p._read_bytes(fd, 'plugin.json', p.MANIFEST_BYTES), 'invalid_bundle')
                store = p._read(fd, 'store.json', 16 * 1024)
                p.require(bodies['store.json'] == p._read_bytes(fd, 'store.json', 16 * 1024), 'invalid_bundle')
    p._fields(store, ('schema', 'licence', 'price', 'mcp', 'source', 'accepted_version', 'review_date'))
    p._schema(store)
    p._about_text(store['licence'], 1, 128)
    p.require(not any(unicodedata.category(c).startswith('C') for c in store['licence']), 'invalid_bundle')
    p.require(type(store['price']) is str and store['price'] in {'free', 'paid', 'trial'}, 'invalid_bundle')
    p.require(type(store['mcp']) is bool and store['accepted_version'] == manifest['about']['version'], 'invalid_bundle')
    p._homepage(store['source'])
    p.require(type(store['review_date']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', store['review_date']), 'invalid_bundle')
    try:
        date.fromisoformat(store['review_date'])
        for filename in ('AGENTS.md', 'README.md', *(['README.it.md'] if 'README.it.md' in bodies else [])):
            body = bodies[filename]
            p.require(0 < len(body) <= 64 * 1024 and bool(body.decode('utf-8').strip()), 'invalid_bundle')
    except (ValueError, KeyError):
        raise p.Refused('invalid_bundle') from None
    hashes = {key: digest(value) for key, value in sorted(bodies.items())}
    hash_map(hashes)
    return {'manifest': manifest, 'store': store, 'bodies': bodies, 'files': hashes, 'digest': digest(canonical(hashes))}


def hash_map(value):
    p.require(type(value) is dict and len(value) <= FILES, 'unmanaged_installation')
    folded = set()
    all_folded = {name.casefold() for name in value if type(name) is str}
    for name, sha in value.items():
        parts = relative(name)
        folded_name = name.casefold()
        p.require(folded_name not in folded and type(sha) is str and p.KEY.fullmatch(sha), 'unmanaged_installation')
        folded.add(folded_name)
        p.require(not any('/'.join(parts[:n]).casefold() in all_folded for n in range(1, len(parts))), 'unmanaged_installation')


def valid_release(value):
    p._fields(value, ('version', 'id'))
    p.require(type(value['version']) is str and len(value['version']) <= 32 and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', value['version']))
    p.require(type(value['id']) is str and p.KEY.fullmatch(value['id']))


def receipt(fd, name):
    try:
        value = p._read(fd, RECEIPT, RECEIPT_BYTES, settings.private)
        p._fields(value, ('schema', 'name', 'state', 'release', 'version', 'bundle', 'files', 'old', 'previous'))
        p._schema(value)
        p.require(value['name'] == name and value['state'] in ('upgrading', 'complete'))
        valid_release(value['release'])
        p.require(type(value['version']) is str and re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+-]{0,31}', value['version']))
        hash_map(value['files'])
        hash_map(value['old'])
        p.require(value['bundle'] == digest(canonical(value['files'])))
        p.require({'plugin.json', 'store.json', 'AGENTS.md', 'README.md'} <= set(value['files']))
        if value['previous'] is not None:
            p._fields(value['previous'], ('release', 'version'))
            valid_release(value['previous']['release'])
            p.require(type(value['previous']['version']) is str and re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+-]{0,31}', value['previous']['version']))
        if value['state'] == 'complete':
            p.require(value['old'] == {} and value['previous'] is None)
        return value
    except (p.Refused, ValueError, TypeError):
        raise Named('unmanaged_installation', where(fd)) from None


def installed(root, name):
    try:
        with directory(root, name, private=True) as fd:
            try:
                record = receipt(fd, name)
            except p.Refused as exc:
                return {'installed': True, 'managed': False, 'installation_error': exc.code}
            return {'installed': True, 'managed': True, 'receipt': {k: record[k] for k in ('state', 'release', 'version')}}
    except p.Refused as exc:
        if exc.code == 'not_found':
            return {'installed': False, 'managed': False}
        return {'installed': exc.code == 'unsafe_permissions', 'managed': False, 'installation_error': exc.code}


def commands(root, name=None):
    path = Path(root).resolve() / 'scripts/squadra.py'
    launcher = shutil.which('squadra')
    prefix = ['python3', str(path)]
    if launcher:
        try:
            # Match our standalone launcher, without running it or trusting arbitrary shell.
            body = p._read_bytes(None, launcher, 4096).decode('utf-8')
            words = shlex.split(body.splitlines()[1]) if body.startswith('#!/bin/sh\n') else []
            if (len(words) == 5 and words[0] == 'exec' and words[2] == '-B'
                    and Path(words[3]) == path and words[4] == '$@'):
                prefix = ['squadra']
        except (p.Refused, OSError, ValueError, IndexError, UnicodeError):
            pass
    return shlex.join(prefix + ['plugin', 'install', name] if name is not None else prefix + ['plugin', 'list'])


def catalogue(home, root=None, lang='en'):
    root = RELEASE_ROOT if root is None else Path(root)
    result = {'release': release_info(root), 'plugins': [], 'refused': [], 'list_command': commands(root)}
    try:
        with directory(None, os.fspath(root)) as fd, directory(fd, 'plugins') as source:
            with os.scandir(source) as entries:
                names = []
                for entry in entries:
                    p.require(len(names) < ENTRIES, 'too_large')
                    names.append(entry.name)
    except p.Refused as exc:
        if exc.code == 'not_found':
            return result
        raise
    for name in sorted(names):
        try:
            value = bundle(root, name)
            manifest = value['manifest']
            row = {k: manifest[k] for k in ('name', 'title', 'description', 'about')}
            row.update(store=value['store'], install_command=commands(root, name))
            try:
                with home_root(home) as target:
                    row.update(installed(target, name))
            except p.Refused as exc:
                row.update(installed=False, managed=False)
                if exc.code != 'not_found':
                    row['installation_error'] = exc.code
            result['plugins'].append(row)
        except (p.Refused, OSError, ValueError, KeyError) as exc:
            result['refused'].append({'name': name if p.NAME.fullmatch(name) else '',
                                      'code': exc.code if isinstance(exc, p.Refused) else 'invalid_bundle'})
    result['plugins'].sort(key=lambda row: (not row['store']['mcp'], row['title'][lang].casefold(), row['name']))
    return result


@contextmanager
def install_lock(root):
    fd = settings.optional(root, LOCK)
    if fd is None:
        try:
            fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            os.fsync(root)
        except FileExistsError:
            fd = settings.optional(root, LOCK)
    try:
        settings.private(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise p.Refused('busy') from None
        yield
    finally:
        os.close(fd)


def file_hash(fd, path):
    try:
        with parent_for(fd, path) as (parent, name):
            return digest(p._read_bytes(parent, name, FILE_BYTES, settings.private))
    except p.Refused as exc:
        if exc.code == 'not_found':
            return None
        raise


def direction(old, new):
    if old is None:
        return 'install'
    if old == new:
        return 'same'
    if re.fullmatch(r'\d+(?:\.\d+)*', old) and re.fullmatch(r'\d+(?:\.\d+)*', new):
        before, after = [int(n) for n in old.split('.')], [int(n) for n in new.split('.')]
        width = max(len(before), len(after))
        before += [0] * (width - len(before))
        after += [0] * (width - len(after))
        return 'downgrade' if after < before else 'upgrade' if after > before else 'same'
    return 'unordered'


def install(home, name, root=None):
    root = RELEASE_ROOT if root is None else Path(root)
    value = bundle(root, name)
    release = release_info(root)
    with home_root(home, create=True) as target, install_lock(target):
        try:
            fd = opened(target, name)
        except p.Refused as exc:
            if exc.code != 'not_found':
                raise
            os.mkdir(name, 0o700, dir_fd=target)
            os.fsync(target)
            fd = opened(target, name)
            fresh = True
        else:
            fresh = False
        try:
            checked(fd, private=True)
            previous = None if fresh else receipt(fd, name)
            version = value['manifest']['about']['version']
            if previous and previous['state'] == 'upgrading':
                p.require(previous['bundle'] == value['digest'] and previous['release'] == release
                          and previous['version'] == version, 'install_incomplete')
                pending = previous
            else:
                pending = {'schema': 1, 'name': name, 'state': 'upgrading', 'release': release,
                           'version': version, 'bundle': value['digest'], 'files': value['files'],
                           'old': previous['files'] if previous else {},
                           'previous': {'release': previous['release'], 'version': previous['version']} if previous else None}
            paths = sorted(set(pending['old']) | set(pending['files']))
            for path in paths:
                actual = file_hash(fd, path)
                allowed = {pending['old'].get(path)}
                if previous and previous['state'] == 'upgrading':
                    allowed.add(pending['files'].get(path))
                p.require(actual in allowed, 'modified_installation')
            try:
                settings.atomic(fd, RECEIPT, canonical(pending))
                for path in paths:
                    expected = pending['files'].get(path)
                    actual = file_hash(fd, path)
                    p.require(actual in {pending['old'].get(path), expected}, 'modified_installation')
                    if actual == expected:
                        continue
                    with parent_for(fd, path, create=expected is not None) as (parent, filename):
                        if expected is None:
                            os.unlink(filename, dir_fd=parent)
                            os.fsync(parent)
                        else:
                            settings.atomic(parent, filename, value['bodies'][path])
                complete = {**pending, 'state': 'complete', 'old': {}, 'previous': None}
                settings.atomic(fd, RECEIPT, canonical(complete))
            except (OSError, p.Refused):
                raise p.Refused('install_incomplete') from None
            before = pending['previous']
            old_version = before['version'] if before else None
            old_release = before['release']['version'] if before else None
            return {'name': name, 'installed': True, 'state': 'complete', 'release': release,
                    'plugin': {'from': old_version, 'to': version, 'direction': direction(old_version, version)},
                    'squadra': {'from': old_release, 'to': release['version'], 'direction': direction(old_release, release['version'])},
                    'automatic_execution': False}
        finally:
            os.close(fd)

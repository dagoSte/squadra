"""Read-only, deterministic work lanes from imports and Git co-changes."""
import ast
from bisect import bisect_right
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from itertools import combinations
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re

from registry import Error, git

CODE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'}
JS_IMPORT = re.compile(r'''(?:\b(?:import|export)\s+(?:[^;'"]*?\bfrom\s*)?|\b(?:require|import)\s*\(\s*)['"](\.[^'"\n]+)['"]''')
JS_TOKENS = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/''')


def excluded(path):
    return path == 'tests/fixtures' or path.startswith('tests/fixtures/')


def relative(root, value):
    path = Path(os.path.abspath(root / value))
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise Error('Lane paths must stay inside the repository') from None


def imports(path, body, available, modules):
    """Resolve local imports without importing or executing project code."""
    suffix = PurePosixPath(path).suffix
    folder = PurePosixPath(path).parent

    def python_module(name, level=0):
        parts = name.split('.') if name else []
        if level:
            parent = folder
            for _ in range(level - 1):
                parent = parent.parent
            candidates = [str(parent.joinpath(*parts))]
        else:
            candidates = [str(folder.joinpath(*parts)), '/'.join(parts)]
        for stem in candidates:
            for candidate in (stem + '.py', stem + '/__init__.py'):
                if candidate in available:
                    return candidate
        matches = modules.get(name, [])
        return min(matches, key=lambda p: (len(PurePosixPath(p).parts), p)) if matches and not level else None

    found = set()
    if suffix == '.py':
        try:
            tree = ast.parse(body, filename=path)
        except (SyntaxError, ValueError):
            return found
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(filter(None, (python_module(alias.name) for alias in node.names)))
            elif isinstance(node, ast.ImportFrom):
                module = python_module(node.module or '', node.level)
                if module:
                    found.add(module)
                for alias in node.names:
                    name = '.'.join(filter(None, (node.module, alias.name)))
                    target = python_module(name, node.level)
                    if target:
                        found.add(target)
    elif suffix in CODE_SUFFIXES:
        text = body.decode('utf-8', errors='replace')
        tokens = list(JS_TOKENS.finditer(text))
        starts = [token.start() for token in tokens]
        for match in JS_IMPORT.finditer(text):
            token = bisect_right(starts, match.start()) - 1
            if token >= 0 and match.start() < tokens[token].end():
                continue
            stem = os.path.normpath(str(folder / match.group(1))).replace(os.sep, '/')
            typed = [str(PurePosixPath(stem).with_suffix(s)) for s in ('.ts', '.tsx')
                     if PurePosixPath(stem).suffix in {'.js', '.mjs', '.cjs'}]
            for candidate in [stem, *typed, *(stem + s for s in sorted(CODE_SUFFIXES - {'.py'})),
                              *(stem + '/index' + s for s in sorted(CODE_SUFFIXES - {'.py'}))]:
                if candidate in available:
                    found.add(candidate)
                    break
    found.discard(path)
    return found


def analyze(repo, paths, *, history=400):
    """Inspect tracked glob matches and explicitly named new files; never write the repo."""
    if type(history) is not int or history < 0:
        raise Error('Lane history must be a nonnegative integer')
    root = Path(os.fsdecode(git(repo, 'rev-parse', '--show-toplevel')).strip()).resolve()
    base = git(root, 'rev-parse', 'HEAD').decode().strip()
    tracked = sorted(os.fsdecode(p) for p in git(root, 'ls-files', '-z').split(b'\0') if p)
    selected, reasons = set(), []
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    for value in sorted(set(os.fspath(p) for p in paths)):
        pattern = relative(root, value)
        matches = [p for p in tracked if pattern == '.' or fnmatchcase(p, pattern) or
                   (pattern.startswith('**/') and fnmatchcase(p, pattern[3:])) or p.startswith(pattern + '/')]
        if not matches and not any(c in pattern for c in '*?['):
            matches = [pattern]
        selected.update(matches)
    selected = {p for p in selected if not excluded(p)}
    bodies = {}
    for name in sorted(set(tracked) | selected):
        if excluded(name):
            continue
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            selected.discard(name)
            continue
        if path.is_file():
            bodies[name] = path.read_bytes()
        elif path.is_dir():
            selected.discard(name)
        elif name in selected:
            reasons.append('New or absent file: ' + name)
    selected = sorted(selected)
    wanted = set(selected)
    modules = defaultdict(list)
    for name in bodies:
        if name.endswith('.py'):
            parts = name[:-3].split('/')
            if parts[-1] == '__init__':
                parts.pop()
            for start in range(len(parts)):
                modules['.'.join(parts[start:])].append(name)
    edges = sorted({(name, target) for name, body in bodies.items()
                    if PurePosixPath(name).suffix in CODE_SUFFIXES
                    for target in imports(name, body, bodies, modules)})
    hashes = defaultdict(list)
    for name, body in bodies.items():
        hashes[hashlib.sha256(body).digest()].append(name)
    unit = {name: name for name in selected}
    unit.update({name: min(group) for group in hashes.values() for name in group})
    fan_in = defaultdict(set)
    for source, target in edges:
        if unit[source] != unit[target]:
            fan_in[unit[target]].add(unit[source])
    threshold = max(3, math.ceil(max(map(len, fan_in.values()), default=0) / 4))
    hubs = sorted(p for p in selected if len(fan_in[unit[p]]) >= threshold)
    counts, co_changes = Counter(), Counter()
    if history:
        log = git(root, 'log', '-' + str(history), '--pretty=format:', '--name-only', '--no-renames', '-z', base, '--')
        for commit in log.split(b'\0\0'):
            changed = sorted({os.fsdecode(p) for p in commit.split(b'\0') if p} & wanted)
            counts.update(changed)
            co_changes.update(combinations(changed, 2))
    return {'repo': str(root), 'base': base, 'files': selected,
            'lines': {p: len(bodies.get(p, b'').splitlines()) for p in selected},
            'imports': [list(edge) for edge in edges],
            'co_changes': [[a, b, n] for (a, b), n in sorted(co_changes.items())],
            'commits': dict(sorted(counts.items())), 'hubs': hubs,
            'fan_in': {p: len(fan_in[unit[p]]) for p in selected},
            'twins': sorted(sorted(p for p in group if p in wanted)
                            for group in hashes.values() if len(set(group) & wanted) > 1),
            'reasons': reasons + ['Hub fan-in threshold: ' + str(threshold) + ' (one quarter of maximum, at least 3)']}


def is_test(path):
    name = PurePosixPath(path).name
    return ('tests' in PurePosixPath(path).parts or name.startswith('test_') or
            '.test.' in name or '.spec.' in name)


def partition(analysis, *, max_lanes=4):
    """Keep twins and primary tests indivisible; cluster history more strongly than imports."""
    if type(max_lanes) is not int or max_lanes < 1:
        raise Error('Maximum lanes must be a positive integer')
    files = sorted(set(analysis['files']))
    parent = {p: p for p in files}
    reasons = list(analysis.get('reasons', []))

    def find(path):
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def join(a, b):
        a, b = sorted((find(a), find(b)))
        parent[b] = a

    for group in analysis.get('twins', []):
        twins = sorted(set(group) & parent.keys())
        for twin in twins[1:]:
            join(twins[0], twin)
        if len(twins) > 1:
            reasons.append('Byte-identical files stay together: ' + ', '.join(twins))
    changes = {(a, b): count for a, b, count in analysis.get('co_changes', [])}
    imported = defaultdict(set)
    for source, target in analysis.get('imports', []):
        if source in parent and target in parent:
            imported[source].add(target)
    for test in files:
        if not is_test(test):
            continue
        targets = sorted(p for p in imported[test] if not is_test(p))
        if not targets:
            continue
        test_name = PurePosixPath(test).stem.removeprefix('test_')

        def priority(target):
            name = PurePosixPath(target).stem
            affinity = len(name) if test_name == name or test_name.startswith(name + '_') else 0
            return (-affinity, -changes.get(tuple(sorted((test, target))), 0), target)

        primary = min(targets, key=priority)
        join(test, primary)
        affinity, history_weight, _ = priority(primary)
        why = 'name match' if affinity else 'co-changes: ' + str(-history_weight) if history_weight else 'stable path order'
        reasons.append('Primary imported module: ' + test + ' -> ' + primary + ' (' + why + ')')
    units = defaultdict(list)
    for path in files:
        units[find(path)].append(path)
    shared_units = {find(p) for p in analysis.get('hubs', []) if p in parent}
    shared = sorted(p for unit in shared_units for p in units[unit])
    if shared:
        reasons.append('High fan-in units belong to shared/L0, outside clustering.')
    weights = Counter()
    for (a, b), count in changes.items():
        if a in parent and b in parent and a != b:
            weights[tuple(sorted((a, b)))] += 3 * count
    for source, targets in imported.items():
        for target in targets:
            if source != target:
                weights[tuple(sorted((source, target)))] += 1
    groups = {u: list(paths) for u, paths in sorted(units.items()) if u not in shared_units}
    peripheral = {u: paths for u, paths in groups.items()
                  if not any(PurePosixPath(p).suffix in CODE_SUFFIXES for p in paths)}
    if len(peripheral) < len(groups):
        groups = {u: paths for u, paths in groups.items() if u not in peripheral}
    else:
        peripheral = {}
    adjacency = {u: Counter() for u in groups}
    for (a, b), weight in weights.items():
        a, b = find(a), find(b)
        if a != b and a in groups and b in groups:
            adjacency[a][b] += weight
            adjacency[b][a] += weight
    degree = {u: sum(row.values()) for u, row in adjacency.items()}
    total = sum(degree.values())
    # Greedy modularity uses integer gains and stable ties; no random initialization.
    while total:
        candidates = [(total * weight - degree[a] * degree[b], a, b)
                      for a in sorted(adjacency) for b, weight in sorted(adjacency[a].items()) if a < b]
        if not candidates:
            break
        gain, a, b = min(candidates, key=lambda row: (-row[0], row[1], row[2]))
        if gain <= 0:
            break
        groups[a].extend(groups.pop(b))
        degree[a] += degree.pop(b)
        adjacency[a].pop(b, None)
        for neighbor, weight in adjacency.pop(b).items():
            if neighbor != a:
                adjacency[a][neighbor] += weight
                adjacency[neighbor][a] += weight
            adjacency.get(neighbor, {}).pop(b, None)
    lines = analysis['lines']
    # Documentation follows source communities; broad release-note edits cannot fuse them.
    core = {u: set(paths) for u, paths in groups.items()}
    for unit, paths in sorted(peripheral.items()):
        def attachment(group):
            weight = sum(weights.get(tuple(sorted((a, b))), 0) for a in paths for b in core[group])
            return (-weight, sum(lines[p] for p in groups[group]), group)

        target = min(groups, key=attachment)
        groups[target].extend(paths)
        reasons.append('Non-code files follow a source community: ' + ', '.join(paths) + ' -> ' + target)
    groups = [sorted(paths) for paths in groups.values()]
    while len(groups) > max_lanes:
        groups.sort(key=lambda paths: (sum(lines[p] for p in paths), len(paths), paths))
        first, second = groups[:2]
        reasons.append('Merge smallest groups to respect max_lanes: ' + first[0] + ', ' + second[0])
        groups[:2] = [sorted(first + second)]
    lanes = [{'id': 'L' + str(i), 'files': paths, 'tests': [p for p in paths if is_test(p)],
              'lines': sum(lines[p] for p in paths)} for i, paths in enumerate(sorted(groups), 1)]
    owner = {p: 'L0' for p in shared}
    owner.update({p: lane['id'] for lane in lanes for p in lane['files']})
    return {'form': 'squadra-lanes/1', 'repo': analysis['repo'], 'base': analysis['base'],
            'files': files, 'shared': shared, 'lanes': lanes,
            'depends': {lane['id']: ['L0'] if shared else [] for lane in lanes},
            'cross_edges': [[a, b, weight] for (a, b), weight in sorted(weights.items()) if owner[a] != owner[b]],
            'reasons': reasons + ['Co-change weight 3; import weight 1; deterministic greedy modularity.']}

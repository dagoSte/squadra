#!/usr/bin/env python3
"""Squadra's own release checks, declared in .squadra-precheck.json and run before proof and delivery.

Usage: precheck_checks.py CHECK [LOCATION...] [--root ROOT]
Exit 0 prints "ok CHECK"; exit 1 prints one line per defect naming its file; exit 2 is a malformed invocation.
"""
import ast
import json
from pathlib import Path
import re
import sys

PIN = "plugins/memoranda/bridge/squadra_memories/pin.json"
BADGE = re.compile(r"^\*\*Versione? ([^\s*]+)\*\*", re.M)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^(#{1,6})(?:[ \t]|$)")


class Usage(Exception):
    pass


def module_version(path):
    """The single top-level VERSION string of a Python file, or None."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)]
    if len(values) == 1 and isinstance(values[0], ast.Constant) and isinstance(values[0].value, str):
        return values[0].value
    return None


def json_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(str(path) + ": " + str(exc)) from None


def location_version(path):
    if path.suffix == ".py":
        return module_version(path)
    if path.suffix == ".json":
        value = json_file(path)
        return value.get("version") if isinstance(value, dict) and isinstance(value.get("version"), str) else None
    if path.suffix == ".md":
        found = BADGE.findall(path.read_text(encoding="utf-8"))
        return found[0] if len(found) == 1 else None
    raise Usage("unsupported version location " + str(path))


def reference(root):
    version = module_version(root / "scripts/registry.py")
    if version is None:
        raise ValueError("scripts/registry.py has no single VERSION")
    return version


def bridge_copies(root, locations):
    if locations:
        raise Usage("bridge-copies takes no locations")
    source = (root / "scripts/registry.py").read_bytes()
    copies = sorted(root.glob("plugins/*/bridge/registry.py"))
    problems = [] if copies else ["plugins/*/bridge/registry.py: no bridge copies found"]
    for copy in copies:
        if copy.read_bytes() != source:
            problems.append(copy.relative_to(root).as_posix() + ": differs from scripts/registry.py")
    return problems


def versions(root, locations):
    if not locations:
        raise Usage("versions needs the version locations")
    expected, problems = reference(root), []
    for location in locations:
        path = root / location
        if not path.is_file():
            problems.append(location + ": missing")
            continue
        value = location_version(path)
        if value is None:
            problems.append(location + ": no single version")
        elif value != expected:
            problems.append(location + ": " + value + ", expected " + expected + " (scripts/registry.py VERSION)")
    return problems


def headings(path):
    levels, fence = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = FENCE.match(line)
        if marker:
            mark = marker.group(1)
            if fence is None:
                fence = (mark[0], len(mark))
            elif mark[0] == fence[0] and len(mark) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if fence is None:
            match = HEADING.match(line)
            if match:
                levels.append((len(match.group(1)), line.strip()))
    return levels


def readme_headings(root, locations):
    if locations:
        raise Usage("readme-headings takes no locations")
    english, italian = headings(root / "README.md"), headings(root / "README.it.md")
    for index, (en, it) in enumerate(zip(english, italian), 1):
        if en[0] != it[0]:
            return ["README.it.md: heading " + str(index) + " " + json.dumps(it[1], ensure_ascii=False) + " has level " +
                    str(it[0]) + ", README.md " + json.dumps(en[1], ensure_ascii=False) + " has level " + str(en[0])]
    if len(english) != len(italian):
        return ["README.it.md: " + str(len(italian)) + " headings, README.md has " + str(len(english))]
    return []


def pin_label(root, locations):
    if locations:
        raise Usage("pin-label takes no locations")
    label = "squadra " + reference(root)
    pin = json_file(root / PIN)
    accepted = pin.get("accepted") if isinstance(pin, dict) else None
    labels = [entry.get("label") for entry in accepted or [] if isinstance(entry, dict)]
    return [] if label in labels else [PIN + ": no accepted entry labelled " + json.dumps(label)]


def plugin_versions(root, locations):
    """Each bundled plugin's version agrees in plugin.json, store.json and the listed package files."""
    problems, own = [], {}
    manifests = sorted(root.glob("plugins/*/plugin.json"))
    if not manifests:
        problems.append("plugins/*/plugin.json: no bundled plugins found")
    for manifest in manifests:
        name, folder = manifest.parent.name, "plugins/" + manifest.parent.name
        value = json_file(manifest)
        about = value.get("about") if isinstance(value, dict) else None
        version = about.get("version") if isinstance(about, dict) else None
        if not isinstance(version, str):
            problems.append(folder + "/plugin.json: no about.version")
            continue
        own[name] = version
        store = manifest.parent / "store.json"
        if not store.is_file():
            problems.append(folder + "/store.json: missing")
            continue
        listing = json_file(store)
        accepted = listing.get("accepted_version") if isinstance(listing, dict) else None
        if accepted != version:
            problems.append(folder + "/store.json: accepted_version " + str(accepted) + ", expected " + version +
                            " (" + folder + "/plugin.json about.version)")
    for location in locations:
        parts = Path(location).parts
        if len(parts) < 3 or parts[0] != "plugins" or not location.endswith(".py"):
            raise Usage("plugin-versions locations are plugins/<name>/...py files")
        path = root / location
        if parts[1] not in own:
            problems.append(location + ": plugins/" + parts[1] + " has no plugin version")
        elif not path.is_file():
            problems.append(location + ": missing")
        else:
            value = module_version(path)
            if value != own[parts[1]]:
                problems.append(location + ": " + str(value) + ", expected " + own[parts[1]] +
                                " (plugins/" + parts[1] + "/plugin.json about.version)")
    return problems


CHECKS = {"bridge-copies": bridge_copies, "versions": versions, "readme-headings": readme_headings,
          "pin-label": pin_label, "plugin-versions": plugin_versions}


def main(argv):
    args = list(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if "--root" in args:
            index = args.index("--root")
            if index + 1 >= len(args):
                raise Usage("--root needs a directory")
            root = Path(args[index + 1]).resolve()
            del args[index:index + 2]
        if not args or args[0] not in CHECKS:
            raise Usage("first argument must be one of " + ", ".join(CHECKS))
        name = args[0]
        try:
            problems = CHECKS[name](root, args[1:])
        except (OSError, ValueError, SyntaxError) as exc:
            problems = [type(exc).__name__ + ": " + str(exc)]
    except Usage as exc:
        print("usage: precheck_checks.py CHECK [LOCATION...] [--root ROOT]: " + str(exc), file=sys.stderr)
        return 2
    for problem in problems:
        print(name + ": " + problem, file=sys.stderr)
    if problems:
        return 1
    print("ok " + name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""No rule is lost when SKILL.md shrinks to a core (R315-S.ISTR).

references/regole.md holds the rule inventory and the index of the 127 rule-bearing units of the 3.14.0
SKILL.md (commit 0e56ba3). Each unit maps to rules; each rule has one line in the core and a home in
references/ that holds every clause of the unit, word for word (whitespace and link targets aside): the
index keeps a short hash of each clause, so a home that drops one clause fails even when the command
tokens are still there. A pointer unit only needs the page it names. The index bytes are pinned below;
when the Git object is reachable the index is also rebuilt from the 3.14.0 text itself.
"""
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'references' / 'regole.md'
BASE = '0e56ba3107d80275bd5460d29dfcc52bed28d979'
UNITS = 127
INDEX_SHA256 = '76c8043de9bbd09b109e373f159978e15097ae79649dc72016b7d89d09a33a16'
CORE_LIMIT = 22000


def raw_units(text, front_matter=True):
    """Rule-bearing units of a SKILL.md (or of a guide): each paragraph, bullet and table, with its section."""
    body = text.split('\n---\n', 1)[1] if front_matter else text
    out, section, cur, table = [], '', [], False

    def flush():
        if cur:
            out.append((section, '\n'.join(cur)))
            cur.clear()
    for line in body.splitlines():
        if line.startswith('#'):
            flush()
            section = line.lstrip('#').strip()
            continue
        if not line.strip():
            flush()
            table = False
            continue
        if line.startswith('|'):
            if not table:
                flush()
                table = True
            cur.append(line)
            continue
        if re.match(r'^ {0,4}(- |\d+\. )', line):
            flush()
        cur.append(line)
    flush()
    return out


def norm(text):
    return ' '.join(text.split())


def unlink(text):
    """Link targets differ once a text moves under references/; compare without them."""
    return re.sub(r'\]\([^)]*\)', ']', text)


def pointer(text):
    """The pages a pure pointer unit («Follow the [guide](references/…)») names, or None."""
    n = norm(text)
    if n.startswith(('Follow the [', 'Read [')) and len(n) < 400 and '](references/' in n and n.count('. ') <= 1:
        return re.findall(r'\]\((references/[^)#]+)', n)
    if n.startswith('Read `references/') and len(n) < 400:
        return re.findall(r'`(references/[^`]+\.md)`', n)
    return None


def clauses(text):
    """The unit's clauses: sentences of its text (of each table cell for a table), without list markers."""
    n = unlink(norm(text))
    if n.startswith('|'):
        cells = [c.strip() for c in n.split('|') if c.strip() and not set(c.strip()) <= set('- ')]
        n = ' '.join(c if c.endswith(('.', ';', ':')) else c + '.' for c in cells)
    parts = re.split(r'(?<=[.;:!?])\s+', n)
    return [q for q in (re.sub(r'^(?:- |\d+\. )+', '', p).strip() for p in parts) if len(q) > 2]


def clause_hash(clause):
    return hashlib.sha256(clause.encode()).hexdigest()[:10]


def evidence(text):
    """What a unit's homes must hold: '→page' for a pointer, else the hash of every clause."""
    pages = pointer(text)
    if pages:
        return ['→' + p for p in pages]
    return [clause_hash(c) for c in clauses(text)]


def home_clauses(paths):
    found = set()
    for path in paths:
        for _, unit in raw_units(path.read_text(), front_matter=False):
            found.update(clause_hash(c) for c in clauses(unit))
    return found


def index_lines(skill_text):
    units = raw_units(skill_text)
    return ['U%03d %s %s' % (i, hashlib.sha256(norm(t).encode()).hexdigest()[:12],
                             json.dumps(evidence(t), ensure_ascii=False))
            for i, (_, t) in enumerate(units, 1)]


def inventory():
    text = INVENTORY.read_text()
    rules = {}
    for line in text.splitlines():
        m = re.match(r'^\| (R\d\d) \| (.+?) \| (.+?) \| (.+?) \| (.+?) \| (.*?) \|$', line)
        if m:
            rid, rule, exit_, cli, homes, units = m.groups()
            rules[rid] = {'rule': rule, 'exit': exit_, 'cli': cli,
                          'homes': re.findall(r'\]\(([^)]+)\)', homes),
                          'units': [int(u) for u in re.findall(r'U(\d{3})', units)]}
    block = text.split('<!-- units:begin -->\n```\n', 1)[1].split('```\n<!-- units:end -->', 1)[0]
    return rules, block.splitlines()


def anchors(path):
    slugs = set()
    for line in path.read_text().splitlines():
        if line.startswith('#'):
            title = line.lstrip('#').strip().lower()
            slugs.add(re.sub(r'[^\w\- ]', '', title).replace(' ', '-'))
    return slugs


def resolve(base, target):
    path, _, anchor = target.partition('#')
    file = (base.parent / path).resolve() if path else base
    return file, anchor


class RuleIndex(unittest.TestCase):
    def setUp(self):
        self.rules, self.index = inventory()
        self.core = (ROOT / 'SKILL.md').read_text()

    def test_core_is_short(self):
        self.assertLessEqual(len(self.core.encode()), CORE_LIMIT)

    def test_unit_index_is_the_pinned_3_14_0_index(self):
        self.assertEqual(len(self.index), UNITS)
        self.assertEqual(hashlib.sha256(('\n'.join(self.index) + '\n').encode()).hexdigest(), INDEX_SHA256)

    def test_unit_index_rebuilds_from_the_3_14_0_skill_when_git_has_it(self):
        try:
            old = subprocess.run(['git', 'show', BASE + ':SKILL.md'], cwd=ROOT, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            old = None
        if old is None or old.returncode != 0:
            # A frozen copy without history: the pinned digest above still binds the index bytes.
            return
        self.assertEqual(index_lines(old.stdout.decode()), self.index)

    def test_every_unit_belongs_to_a_rule(self):
        covered = {u for r in self.rules.values() for u in r['units']}
        self.assertEqual(sorted(set(range(1, UNITS + 1)) - covered), [])
        self.assertEqual(sorted(covered - set(range(1, UNITS + 1))), [])

    def test_every_rule_is_in_the_core_with_a_home_that_exists(self):
        self.assertGreaterEqual(len(self.rules), 82)
        for rid, rule in self.rules.items():
            with self.subTest(rule=rid):
                self.assertTrue(re.search(r'\*\*%s\b' % rid, self.core), '%s has no line in SKILL.md' % rid)
                self.assertTrue(rule['rule'].strip() and rule['exit'].strip())
                self.assertTrue(rule['homes'])
                for home in rule['homes']:
                    file, anchor = resolve(INVENTORY, home)
                    self.assertTrue(file.is_file(), home)
                    if anchor:
                        self.assertIn(anchor, anchors(file), home)

    def test_every_clause_of_every_unit_is_in_its_rules_homes(self):
        owners = {}
        for rid, rule in self.rules.items():
            for u in rule['units']:
                owners.setdefault(u, set()).update(resolve(INVENTORY, h)[0] for h in rule['homes'])
        for line in self.index:
            number, _, ev = line.split(' ', 2)
            homes = sorted(owners[int(number[1:])])
            found = home_clauses(homes)
            for item in json.loads(ev):
                with self.subTest(unit=number, evidence=item):
                    if item.startswith('→'):
                        self.assertTrue((ROOT / item[1:]).is_file())
                    else:
                        self.assertTrue(item in found, 'clause %s of %s is missing from %s' % (
                            item, number, ', '.join(p.name for p in homes)))

    def test_every_link_of_the_core_and_inventory_resolves(self):
        for doc in (ROOT / 'SKILL.md', INVENTORY):
            text = re.sub(r'<!-- units:begin -->.*?<!-- units:end -->', '', doc.read_text(), flags=re.S)  # data, not links
            for target in re.findall(r'\]\(([^)\s]+)\)', text):
                if target.startswith(('http://', 'https://', 'mailto:')):
                    continue
                with self.subTest(doc=doc.name, target=target):
                    file, anchor = resolve(doc, target)
                    self.assertTrue(file.exists(), target)
                    if anchor and file.suffix == '.md':
                        self.assertIn(anchor, anchors(file), target)


    def test_links_from_the_references_into_the_core_resolve(self):
        core = anchors(ROOT / 'SKILL.md')
        for doc in sorted((ROOT / 'references').rglob('*.md')):
            for target in re.findall(r'\]\(([^)\s]*SKILL\.md#[^)\s]+)\)', doc.read_text()):
                with self.subTest(doc=str(doc.relative_to(ROOT)), target=target):
                    file, anchor = resolve(doc, target)
                    self.assertEqual(file, (ROOT / 'SKILL.md').resolve())
                    self.assertIn(anchor, core)

if __name__ == '__main__':
    unittest.main()

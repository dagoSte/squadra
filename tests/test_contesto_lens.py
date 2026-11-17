"""Context observations coexist with historical rasoio reviews, without new gates."""
from pathlib import Path
import unittest

from test_errata import Fixture, run
from registry import Error, Store
from squadra import bindings

ROOT = Path(__file__).resolve().parents[1]


class Lens(Fixture, unittest.TestCase):
    def review(self, **extra):
        return self.store.append('review', 'R', {
            'task': 'T', 'stage': 'code', 'verdict': 'accepted', 'covers': [],
            'binding': bindings(self.store, 'T', require_review=False)['sha256'],
            'report': self.store.artifact(self.report), **extra})

    def arguments(self, *extra, verdict='accepted'):
        return ('review', '--actor', 'R', '--task', 'T', '--verdict', verdict,
                '--report', str(self.report), *extra)

    def test_both_lenses_share_one_review_and_one_round_in_either_cli_order(self):
        self.delivered()
        for order in (('rasoio', 'contesto'), ('contesto', 'rasoio')):
            with self.subTest(order=order):
                rc, _, err = run(self.root, *self.arguments(
                    '--lens', order[0], '--lens', order[1], '--net-lines', '7',
                    '--constants-checked', '3', '--constants-suspect', '1'))
                self.assertEqual(rc, 0, err)
                review = self.store.state()['tasks']['T']['reviews'][-1]
                self.assertEqual((review['lens'], review['net_lines']), ('rasoio', 7))
                self.assertEqual(review['contesto'], {'constants_checked': 3, 'constants_suspect': 1})
        task = self.store.state()['tasks']['T']
        self.assertEqual((len(task['reviews']), task['rounds']['code']), (1, 1))
        self.review(contesto={'constants_checked': 0, 'constants_suspect': 0})
        self.assertEqual(self.store.state()['tasks']['T']['rounds']['code'], 2)
        with self.assertRaisesRegex(Error, 'Review round budget exhausted'):
            self.review(contesto={'constants_checked': 0, 'constants_suspect': 0})

    def test_registry_refuses_malformed_and_impossible_declarations_without_recording_them(self):
        self.delivered()
        cases = [(None, 'contesto_counts_required'), ([], 'contesto_counts_required'),
                 ({}, 'contesto_counts_required'), ({'constants_checked': 1}, 'contesto_counts_required'),
                 ({'constants_checked': 1, 'constants_suspect': 0, 'extra': 0}, 'contesto_counts_required'),
                 ({'constants_checked': 1, 'constants_suspect': 2}, 'contesto_suspect_exceeds_checked')]
        for key in ('constants_checked', 'constants_suspect'):
            for invalid in (None, True, False, -1, 1.5, '1', 1000001, [], {}):
                cases.append(({'constants_checked': 1, 'constants_suspect': 0, key: invalid},
                              'contesto_counts_invalid'))
        before = self.last_seq()
        for value, code in cases:
            with self.subTest(value=value), self.assertRaisesRegex(Error, code):
                self.review(contesto=value)
        self.assertEqual(self.last_seq(), before)
        self.assertEqual(self.store.state()['tasks']['T']['rounds']['code'], 0)

    def test_cli_refuses_missing_counts_wrong_lens_duplicate_and_non_integer(self):
        self.delivered()
        cases = [(('--constants-checked', '1'), 'contesto_counts_require_lens'),
                 (('--lens', 'contesto'), 'contesto_counts_invalid'),
                 (('--lens', 'contesto', '--constants-checked', '1'), 'contesto_counts_invalid'),
                 (('--lens', 'rasoio', '--lens', 'rasoio'), 'lens_duplicate'),
                 (('--lens', 'contesto', '--lens', 'contesto'), 'lens_duplicate'),
                 (('--lens', 'contesto', '--constants-checked', '1', '--constants-suspect', '2'),
                  'contesto_suspect_exceeds_checked'),
                 (('--lens', 'contesto', '--constants-checked', '-1', '--constants-suspect', '0'),
                  'contesto_counts_invalid'),
                 (('--lens', 'contesto', '--constants-checked', '1.5', '--constants-suspect', '0'),
                  'invalid int value'),
                 (('--lens', 'contesto', '--constants-checked', '0', '--constants-suspect', '0',
                   '--net-lines', '1'), 'net_lines_requires_lens')]
        for extra, code in cases:
            with self.subTest(extra=extra):
                rc, _, err = run(self.root, *self.arguments(*extra))
                self.assertNotEqual(rc, 0, err)
                self.assertIn(code, err)
        self.assertEqual(self.store.state()['tasks']['T']['reviews'], [])

    def test_zero_upper_bound_and_standalone_contesto_preserve_legacy_listing(self):
        self.delivered()
        for count in (0, 1000000):
            rc, _, err = run(self.root, *self.arguments('--lens', 'contesto',
                '--constants-checked', str(count), '--constants-suspect', str(count)))
            self.assertEqual(rc, 0, err)
            review = self.store.state()['tasks']['T']['reviews'][-1]
            self.assertNotIn('lens', review)
            self.assertNotIn('net_lines', review)
        _, listing, err = run(self.root, 'lesson', '--list')
        self.assertEqual((listing['rasoio_reads'], listing['net_lines_declared']), (0, 0), err)

    def test_suspect_constants_do_not_change_acceptance_check_or_close_and_replay(self):
        self.delivered()
        self.review(lens='rasoio', net_lines=0, contesto={'constants_checked': 1, 'constants_suspect': 1})
        self.assertEqual(self.cli('check', '--actor', 'C', '--task', 'T'), 0)
        self.assertEqual(self.cli('close', '--actor', 'C', '--task', 'T'), 0)
        reopened = Store(self.root)
        self.addCleanup(reopened.close)
        task = reopened.state()['tasks']['T']
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['reviews'][0]['contesto']['constants_suspect'], 1)
        self.assertTrue(reopened.events())

    def test_the_lens_keeps_changes_required_and_legacy_rasoio_fields(self):
        self.delivered()
        rc, data, err = run(self.root, *self.arguments('--lens', 'rasoio', '--net-lines', '4',
            '--lens', 'contesto', '--constants-checked', '2', '--constants-suspect', '0',
            verdict='changes-required'))
        self.assertEqual(rc, 0, err)
        self.assertIn('errata', data)
        self.assertEqual(self.store.state()['tasks']['T']['status'], 'changes-required')
        _, listing, err = run(self.root, 'lesson', '--list')
        self.assertEqual((listing['rasoio_reads'], listing['net_lines_declared']), (1, 4), err)


class Guide(unittest.TestCase):
    def test_guide_source_signature_and_protocol_are_bundled(self):
        guide = (ROOT / 'references/skills/contesto.md').read_text()
        for term in ('Orders of magnitude', '30 days ago', 'git log -p -S', 'git log --grep=revert',
                     'rg -n -F', 'Requirement match', 'Verified:', 'Not verified:', 'Signed:',
                     '--lens rasoio', '--lens contesto', '--constants-checked', '--constants-suspect',
                     'declared counts', '91a1c77ceab95c1c3f45680debcc29de2d684206', 'MIT'):
            self.assertIn(term, guide)
        notice = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text()
        for term in ('laulauraluo', 'Copyright (c) 2026', 'Permission is hereby granted',
                     'THE SOFTWARE IS PROVIDED "AS IS"'):
            self.assertIn(term, notice)
        self.assertIn('(references/skills/contesto.md)', (ROOT / 'SKILL.md').read_text())


if __name__ == '__main__':
    unittest.main()

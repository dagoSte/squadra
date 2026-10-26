"""The memoranda bridge follows the Store's HOME rule: HOME itself may be a link, nothing below it may."""
import os
from pathlib import Path
import tempfile
import unittest

from test_memoranda_bundle import migration_module
import fx
import plugins
from squadra_memories import actions
from squadra_memories.source import Refusal


class LinkedHome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.real = self.base / 'real-home'; self.real.mkdir(mode=0o700)
        self.home = self.base / 'home-link'; self.home.symlink_to(self.real, target_is_directory=True)
        fx.install(self.home)
        self.key = 'a' * 64
        self.reg = type('Registry', (), {'event': lambda _, seq: {'hash': 'a' * 64}})()

    def test_selection_opens_under_a_linked_home_like_the_store(self):
        self.assertTrue((self.real / '.squadra/plugins/memoranda/.store-receipt.json').is_file())
        with actions.Selection(self.home, self.key) as selection:
            self.assertIsNone(selection.data)
        fx.request(self.reg, self.home, ['20:0'])
        with actions.Selection(self.home, self.key) as selection:
            self.assertEqual(selection.data['ids'], ['20:0'])

    def test_a_link_below_a_linked_home_still_refuses(self):
        squad = self.real / '.squadra'
        moved = self.base / 'moved-squadra'; squad.rename(moved)
        squad.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(Refusal):
            with actions.Selection(self.home, self.key):
                pass

    def test_migration_sources_under_a_linked_home_are_not_refused_as_links(self):
        module = migration_module()
        for name in ('old-plugin', 'old-data'):
            (self.real / name).mkdir(mode=0o700)
        try:
            module.migrate(self.home, self.home / 'old-plugin', self.home / 'old-data')
        except module.LinkedComponent as exc:
            self.fail('linked component ' + exc.component)
        except plugins.Refused as exc:
            self.assertEqual(exc.code, 'migration_missing_ledger')

    def test_a_link_inside_a_linked_home_still_refuses_migration(self):
        module = migration_module()
        (self.real / 'old-data').mkdir(mode=0o700)
        (self.real / 'alias').symlink_to(self.real / 'old-data', target_is_directory=True)
        with self.assertRaises(module.LinkedComponent) as caught:
            module.migrate(self.home, self.home / 'alias', self.home / 'old-data')
        self.assertEqual(caught.exception.component, 'alias')


if __name__ == '__main__':
    unittest.main()

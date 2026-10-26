"""A plugin's panel folder and file pass the plugin store's owner, mode and link checks before the dashboard reads them."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plugins  # noqa: E402
from test_plugins import Fixtures, KEY, panel  # noqa: E402


class PanelChecks(Fixtures, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.panels = self.base / 'panels'
        self.file = self.panels / (KEY + '.json')

    def through_panel_at(self):
        fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return plugins.panel_at(fd, KEY)
        finally:
            os.close(fd)

    def check_refused(self, code='unsafe_permissions'):
        self.refused(code)
        with self.assertRaises(plugins.Refused) as caught:
            self.through_panel_at()
        self.assertEqual(caught.exception.code, code)

    def test_private_and_owner_only_panels_still_load(self):
        for folder, file in ((0o700, 0o600), (0o755, 0o644), (0o555, 0o444)):
            with self.subTest(folder=oct(folder), file=oct(file)):
                self.file.chmod(file); self.panels.chmod(folder)
                try:
                    self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())
                    self.assertEqual(self.through_panel_at(), panel())
                finally:
                    self.panels.chmod(0o755)

    def test_a_folder_others_can_write_is_refused(self):
        for mode in (0o777, 0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.panels.chmod(mode)
                try:
                    self.check_refused()
                finally:
                    self.panels.chmod(0o755)

    def test_a_file_others_can_write_is_refused(self):
        for mode in (0o666, 0o664, 0o646):
            with self.subTest(mode=oct(mode)):
                self.file.chmod(mode)
                self.check_refused()

    def test_a_file_with_a_second_link_is_refused(self):
        os.link(self.file, self.home / 'second-link.json')
        self.check_refused()
        (self.home / 'second-link.json').unlink()
        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), panel())

    def test_linked_folder_and_file_stay_refused(self):
        outside = self.home / 'outside.json'; outside.write_text(json.dumps(panel()))
        self.file.unlink(); self.file.symlink_to(outside)
        self.check_refused('symlink')
        self.file.unlink(); self.panels.rmdir()
        elsewhere = self.home / 'elsewhere'; elsewhere.mkdir()
        self.panels.symlink_to(elsewhere, target_is_directory=True)
        self.check_refused('symlink')


if __name__ == '__main__':
    unittest.main()

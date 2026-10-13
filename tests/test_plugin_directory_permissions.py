"""Exercise the shared folder policy across discovery, panels and plugin writes."""
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plugin_actions as actions
import plugin_settings as settings
import plugins
from test_plugin_actions import declaration, selectable
from test_plugin_settings import tree
from test_plugins import Fixtures, KEY


class DirectoryPermissions(Fixtures, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.levels = [self.home, self.home / '.squadra', self.base.parent, self.base]
        for folder in self.levels:
            folder.chmod(0o700)
        manifest = declaration()
        manifest['settings'] = [{'id': 'label', 'kind': 'text',
                                 'label': {'it': 'Nome', 'en': 'Name'}}]
        self.write('plugin.json', manifest)
        self.write('panels/' + KEY + '.json', selectable())
        settings.save(self.home, 'example', {'label': 'Before'})
        actions.save(self.home, 'example', 'send', KEY, ['one'])

    def test_each_unsafe_directory_is_refused_before_any_read_or_write(self):
        for folder in self.levels:
            for fault in (0o770, 0o702, 'foreign-owner'):
                with self.subTest(level=str(folder.relative_to(self.home)), fault=fault):
                    original_fstat = os.fstat
                    target = folder.stat()

                    def foreign(fd):
                        info = original_fstat(fd)
                        if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
                            fields = list(info)
                            fields[4] = os.getuid() + 1
                            return os.stat_result(fields)
                        return info

                    if isinstance(fault, int):
                        folder.chmod(fault)
                    before = tree(self.home)
                    try:
                        guard = patch.object(plugins.os, 'fstat', side_effect=foreign) if fault == 'foreign-owner' else nullcontext()
                        with guard:
                            expected = {'plugins': [], 'refused': [
                                {'name': 'example' if folder == self.base else None,
                                 'code': 'unsafe_permissions'}]}
                            for discover in (plugins.discover, settings.discover,
                                             lambda home: actions.discover(home, KEY)):
                                with self.subTest(operation=discover):
                                    self.assertEqual(discover(self.home), expected)
                            calls = [lambda: plugins.load_panel(self.home, 'example', KEY),
                                     lambda: settings.read(self.home, 'example'),
                                     lambda: settings.save(self.home, 'example', {'label': 'After'}),
                                     lambda: actions.read(self.home, 'example', 'send', KEY),
                                     lambda: actions.save(self.home, 'example', 'send', KEY, ['two']),
                                     lambda: actions.save(self.home, 'example', 'send', KEY, [])]
                            for index, call in enumerate(calls):
                                with self.subTest(operation=index):
                                    with self.assertRaises(plugins.Refused) as caught:
                                        call()
                                    self.assertEqual(caught.exception.code, 'unsafe_permissions')
                        self.assertEqual(tree(self.home), before)
                    finally:
                        folder.chmod(0o700)

    def test_owned_0700_and_0755_directories_remain_usable(self):
        for folder in self.levels:
            for mode in (0o700, 0o755):
                with self.subTest(level=str(folder.relative_to(self.home)), mode=oct(mode)):
                    folder.chmod(mode)
                    try:
                        for discover in (plugins.discover, settings.discover,
                                         lambda home: actions.discover(home, KEY)):
                            result = discover(self.home)
                            self.assertEqual(result['refused'], [])
                            self.assertEqual([p['name'] for p in result['plugins']], ['example'])
                        self.assertEqual(plugins.load_panel(self.home, 'example', KEY), selectable())
                        self.assertEqual(settings.save(self.home, 'example', {'label': 'Safe'}),
                                         settings.read(self.home, 'example'))
                        self.assertEqual(actions.save(self.home, 'example', 'send', KEY, ['two']),
                                         actions.read(self.home, 'example', 'send', KEY))
                    finally:
                        folder.chmod(0o700)


if __name__ == '__main__':
    unittest.main()

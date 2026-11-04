"""Growth measurements keep an explicit physical method and a real restore receipt."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / 'tools/storage_growth.py'
spec = importlib.util.spec_from_file_location('storage_growth', TOOL)
growth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(growth)


class StorageGrowth(unittest.TestCase):
    def test_df_counts_used_blocks_and_preserves_a_signed_delta(self):
        with patch.object(growth, 'command', return_value=type('Result', (), {'stdout':
                'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk9 10000 4096 5904 41% /volume\n'})()):
            self.assertEqual(growth.df_used('/volume'), 4096 * 1024)

    def test_one_round_uses_concurrent_real_delivery_and_restores_sample(self):
        with tempfile.TemporaryDirectory(prefix='squadra-growth-test-') as folder:
            run = subprocess.run([sys.executable, '-B', str(TOOL), '--dir', folder, '--count', '1'],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            output = json.loads((Path(folder) / 'comparison.json').read_text())['results']['candidate']
            self.assertEqual(output['restore_check'], 'passed')
            self.assertEqual(output['delivered'], ['growth-0'])
            self.assertEqual(len(output['rows']), 1)
            row = output['rows'][0]
            self.assertEqual(row['physical_delta'], row['df_after'] - row['df_before'])
            self.assertGreater(row['logical_bytes'], 0)
            self.assertFalse(output['dedicated_volume'])
            self.assertTrue((Path(folder) / 'candidate/growth.csv').is_file())

    def test_cli_rejects_zero_iterations_before_creating_measurement_data(self):
        with tempfile.TemporaryDirectory() as folder:
            run = subprocess.run([sys.executable, '-B', str(TOOL), '--dir', folder, '--count', '0'],
                                 capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((Path(folder) / 'candidate').exists())

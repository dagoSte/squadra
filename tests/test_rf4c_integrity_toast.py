"""An integrity failure clears the dashboard's notices, as it clears every other registry view."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_panel as panel

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'tests/dashboard_audit_transfer_contract.js'
SEAM = 'const tests={\n'
SCENARIO = r'''const tests={
 async rf4c_integrity_toast(){
  const id=Object.keys(base.tasks)[0],open=fixture('complete');open.tasks[id].status='working';
  const app=await boot({fixture:open});
  const closed=clone(open);closed.change_token='c'.repeat(64);closed.tasks[id].status='done';
  app.http=()=>reply(closed);await app.refresh();
  const toasts=app.doc.getElementById('notify-toasts');
  assert.equal(toasts.children.length,1,'a closed step shows its notice');
  app.http=()=>Promise.resolve({ok:false,status:409,text:async()=>'{"error":"registry_invalid"}'});await app.refresh();
  assert.equal(app.run('loadUI.integrity'),true);
  assert.equal(toasts.children.length,0,'no registry-derived notice survives the integrity failure');
  assert.equal(app.run('notifyToasts.length'),0);
  assert.equal(app.timers.filter(t=>String(t.fn).includes('notifyDismiss')).length,0,'its dismissal timer is cleared');
 },
'''


class IntegrityToast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='sq-integrity-toast-')
        cls.folder = Path(cls.temp.name)
        cls.fixture = cls.folder / 'state.json'
        cls.fixture.write_text(json.dumps(panel.PanelJavaScript.build(cls.folder)))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_step_notice_is_cleared_when_the_registry_fails_verification(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required; JS contracts cannot be skipped')
        source = HARNESS.read_text()
        self.assertEqual(source.count(SEAM), 1, 'harness seam changed')
        # The harness reads its sibling contracts relative to its own folder.
        contract = self.folder / 'rf4c_integrity_toast_contract.js'
        contract.write_text('__dirname=' + json.dumps(str(HARNESS.parent)) + ';\n' + source.replace(SEAM, SCENARIO))
        result = subprocess.run([node, str(contract), str(ROOT / 'assets/dashboard.html'), str(self.fixture),
                                 'rf4c_integrity_toast'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('audit transfer contract passed: rf4c_integrity_toast', result.stdout)


if __name__ == '__main__':
    unittest.main()

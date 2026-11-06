"""The Cisco gate fails at HIGH or CRITICAL and excepts only the loopback bind probe of runtime.py."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import cisco_gate


def finding(rule, severity='CRITICAL', network=None):
    evidence = {} if network is None else {'network_files': network}
    return {'rule_id': rule, 'severity': severity, 'metadata': {'evidence': evidence}}


class CiscoGate(unittest.TestCase):
    def test_probe_only_chain_is_excepted(self):
        blocking, allowed = cisco_gate.gate({'findings': [
            finding('BEHAVIOR_CROSSFILE_ENV_VAR_EXFILTRATION', network=['scripts/runtime.py']),
            finding('BEHAVIOR_CROSSFILE_EXFILTRATION_CHAIN', network=['scripts/runtime.py']),
            finding('DATA_EXFIL_ENV_VARS', severity='MEDIUM')]})
        self.assertEqual(blocking, [])
        self.assertEqual(len(allowed), 2)

    def test_another_network_file_blocks_again(self):
        blocking, _ = cisco_gate.gate({'findings': [
            finding('BEHAVIOR_CROSSFILE_ENV_VAR_EXFILTRATION', network=['scripts/runtime.py', 'scripts/upload.py'])]})
        self.assertEqual(len(blocking), 1)

    def test_other_high_rules_and_missing_evidence_block(self):
        blocking, _ = cisco_gate.gate({'findings': [
            finding('BEHAVIOR_CROSSFILE_CREDENTIAL_NETWORK_SEPARATION', severity='HIGH', network=['scripts/runtime.py']),
            finding('BEHAVIOR_CROSSFILE_EXFILTRATION_CHAIN')]})
        self.assertEqual(len(blocking), 2)


if __name__ == '__main__':
    unittest.main()

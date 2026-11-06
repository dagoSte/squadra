"""Fail a Cisco skill-scanner report at HIGH or CRITICAL, with one declared exception.

The exception: since 3.14.0 scripts/runtime.py has local_bind_available(), which binds a socket to
127.0.0.1 port 0 and closes it, so a proof is refused at once where a sandbox forbids local bind. It opens no
connection and sends nothing. The scanner's cross-file rules call any Python socket use in a repository that
also reads environment variables a CRITICAL exfiltration, and its policy cannot re-rate them (they fail its own
rule contract, so suppressions do not apply). This gate lets those two rules pass only while scripts/runtime.py
is the single network file in their evidence; any other network file makes the check fail again. The finding
stays in every report unchanged.

Usage: python tools/cisco_gate.py results.json
"""
import json
import sys

BLOCKING = ('HIGH', 'CRITICAL')
LOOPBACK_PROBE = ['scripts/runtime.py']
EXCEPTED_RULES = ('BEHAVIOR_CROSSFILE_ENV_VAR_EXFILTRATION', 'BEHAVIOR_CROSSFILE_EXFILTRATION_CHAIN')


def excepted(finding):
    """Whether a blocking finding is the declared loopback-probe false positive."""
    evidence = (finding.get('metadata') or {}).get('evidence') or {}
    return finding.get('rule_id') in EXCEPTED_RULES and evidence.get('network_files') == LOOPBACK_PROBE


def gate(result):
    """Return (blocking findings, excepted findings) of a scanner JSON result."""
    blocking, allowed = [], []
    for finding in result['findings']:
        if finding.get('severity') not in BLOCKING:
            continue
        (allowed if excepted(finding) else blocking).append(finding)
    return blocking, allowed


def main(argv):
    with open(argv[1], encoding='utf-8') as handle:
        blocking, allowed = gate(json.load(handle))
    for finding in allowed:
        print('EXCEPTED %s %s: loopback bind probe only (%s)' % (finding['severity'], finding['rule_id'],
                                                                    ', '.join(LOOPBACK_PROBE)))
    for finding in blocking:
        print('BLOCKING %s %s: %s' % (finding['severity'], finding['rule_id'], finding.get('description', '')[:300]))
    print('blocking=%d excepted=%d' % (len(blocking), len(allowed)))
    return 1 if blocking else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

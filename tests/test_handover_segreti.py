"""Recognizable secrets are refused before handover notes enter the registry."""
import unittest

import test_squadra as support
import handover
from registry import Error


# The fourteen scenarios from R315-HO.L3-read-secret-probe.log.
REPORT_CASES = (
    ('password=', 'password=synthetic-value', True),
    ('token=', 'token=synthetic-value', True),
    ('sk-', 'sk-synthetic-key', True),
    ('ghp_', 'ghp_synthetic_key', True),
    ('AKIA', 'AKIASYNTHETICKEY', True),
    ('PRIVATE KEY', '-----BEGIN PRIVATE KEY-----', True),
    ('export TOKEN=', 'export TOKEN=synthetic-value', True),
    ('DB_PASSWORD=', 'DB_PASSWORD=synthetic-value', True),
    ('api_token=', 'api_token=synthetic-value', True),
    ('password YAML', 'password: synthetic-value', True),
    ('indented token=', '  token=synthetic-value', True),
    ('word sk- inside', 'the task-sk-1 notes', False),
    ('prose token', 'Keep the token outside the registry.', False),
    ('location', 'Sessione nella cartella /tmp/squadra-note', False),
)
EXTRA_CASES = (
    ('quoted password', '"password" = "synthetic-value"', True),
    ('quoted YAML token', "  'api_token': 'synthetic-value'", True),
    ('export lowercase', '  export api_token = synthetic-value', True),
    ('passwd', 'APP_PASSWD: synthetic-value', True),
    ('secret', 'client.secret = synthetic-value', True),
    ('multiline', 'nessuno\nexport TOKEN=synthetic-value\nnessuno', True),
    ('key inside prose', 'Credenziale: sk-synthetic-key', True),
    ('RSA private key', '-----BEGIN RSA PRIVATE KEY-----', True),
    ('long task id', 'task-sk-1234567890123456', False),
    ('github task id', 'task-ghp_synthetic_key', False),
    ('aws task id', 'task-AKIASYNTHETICKEY', False),
    ('short key marker', 'sk-1', False),
    ('token id', 'task-token-123', False),
    ('assignment to unrelated key', 'task_id=task-sk-1234567890123456', False),
    ('multiline ids', '## Altro\nI task-sk-1 e task-sk-1234567890123456 sono aperti.', False),
    ('empty value', 'password=\n', False),
    ('empty before prose', 'password=\nserver fermo', False),
)


class SecretFilter(unittest.TestCase):
    def test_report_cases(self):
        self.check_cases(REPORT_CASES)

    def test_additional_cases(self):
        self.check_cases(EXTRA_CASES)

    def check_cases(self, cases):
        for label, text, secret in cases:
            with self.subTest(case=label):
                if secret:
                    with self.assertRaisesRegex(Error, 'recognizable secret'):
                        handover.safe_text(text)
                else:
                    try:
                        handover.safe_text(text)
                    except Error as exc:
                        self.fail('Unexpected refusal: ' + str(exc))


class SecretPersistence(unittest.TestCase):
    setUp = support.Workflow.setUp
    git = support.Workflow.git
    task = support.Workflow.task

    def test_file_and_unknown_reason_refuse_secrets_without_persisting_them(self):
        outside = self.base / 'outside.md'
        sections = '\n'.join(h + '\nnessuno\n' for h in handover.HEADINGS)
        for label, text, secret in REPORT_CASES + EXTRA_CASES:
            if not secret:
                continue
            outside.write_text(sections + '\n' + text, encoding='utf-8')
            before = {p.relative_to(self.root): p.read_bytes()
                      for p in (self.root / 'artifacts').iterdir()}
            events = self.store.events()
            with self.assertRaisesRegex(Error, 'recognizable secret', msg=label):
                handover.prepare(self.store, 'C', 'C', 'New', outside_file=outside,
                                 owner_words='Procedi', channel='typed-here')
            with self.assertRaisesRegex(Error, 'recognizable secret', msg=label):
                handover.prepare(self.store, 'C', 'C', 'New', outside_unknown=text,
                                 owner_words='Procedi', channel='typed-here')
            self.assertEqual(self.store.events(), events)
            self.assertEqual(before, {p.relative_to(self.root): p.read_bytes()
                                     for p in (self.root / 'artifacts').iterdir()})

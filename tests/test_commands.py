import json
import subprocess
import unittest
from unittest.mock import patch
from fleet import commands


class OrcaErrorTests(unittest.TestCase):
    def test_nonzero_json_preserves_only_safe_error_code(self):
        for status in (0, 1):
            with self.subTest(status=status):
                response = {'ok': False, 'error': {'code': 'recipient_run_mismatch',
                                                  'message': 'private body and argv'}}
                process = subprocess.CompletedProcess([], status, json.dumps(response), 'private stderr')
                with patch.object(commands.subprocess, 'run', return_value=process):
                    with self.assertRaises(commands.CommandError) as caught:
                        commands.orca({'orca': '/orca', 'root': '/'}, 'orchestration', 'send')
                self.assertEqual(str(caught.exception), 'recipient_run_mismatch')
                self.assertEqual(caught.exception.code, 'recipient_run_mismatch')

    def test_rejected_payloads_never_expose_arbitrary_errors(self):
        for code in ('private detail', 'secret\nvalue', 'x' * 81, None, {'secret': True}):
            with self.subTest(code=code):
                process = subprocess.CompletedProcess([], 1, json.dumps(
                    {'ok': False, 'error': {'code': code, 'message': 'private'}}), 'private')
                with patch.object(commands.subprocess, 'run', return_value=process):
                    with self.assertRaises(commands.CommandError) as caught:
                        commands.orca({'orca': '/orca', 'root': '/'}, 'send')
                self.assertEqual(str(caught.exception), 'orca_rejected')

    def test_nonzero_success_payload_is_not_accepted(self):
        process = subprocess.CompletedProcess([], 1, '{"ok":true,"result":{}}', 'private')
        with patch.object(commands.subprocess, 'run', return_value=process):
            with self.assertRaisesRegex(commands.CommandError, 'invalid Orca response'):
                commands.orca({'orca': '/orca', 'root': '/'}, 'send')


if __name__ == '__main__':
    unittest.main()

import json
import subprocess
import unittest
from unittest.mock import patch
from fleet import commands


class OrcaErrorTests(unittest.TestCase):
    def setUp(self):
        capability = patch.object(commands, '_orchestration_full', return_value=False)
        capability.start()
        self.addCleanup(capability.stop)

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


class CapabilityMutationTests(unittest.TestCase):
    def test_uncertain_help_never_sends_or_replies(self):
        for verb in ('send', 'reply'):
            for failure in ('nonzero', 'timeout', 'empty', 'ambiguous'):
                with self.subTest(verb=verb, failure=failure):
                    commands._ORCHESTRATION_CAPABILITIES.clear()
                    self.addCleanup(commands._ORCHESTRATION_CAPABILITIES.clear)

                    def execute(argv, **kwargs):
                        if argv[-1] != '--help':
                            self.fail('uncertain capabilities emitted a mutation')
                        if failure == 'timeout':
                            raise subprocess.TimeoutExpired(argv, 60)
                        text = (f'Usage: orca orchestration {verb}\nOptions:\n'
                                '  --full first\n  --full duplicate\n'
                                if failure == 'ambiguous' else '')
                        return subprocess.CompletedProcess(
                            argv, 1 if failure == 'nonzero' else 0, text, '')

                    with patch.object(commands.subprocess, 'run', side_effect=execute):
                        with self.assertRaises(commands.CommandError):
                            commands.orca({'orca': '/orca', 'root': '/'},
                                          'orchestration', verb)


class GitDiagnosticTests(unittest.TestCase):
    def test_git_failure_retains_bounded_redacted_command_and_stderr(self):
        process = subprocess.CompletedProcess([], 128, '', (
            'fatal: cannot checkout task-2\nAuthorization: Bearer hidden-header\n'
            'private-secret https://user:pass@example.test/repo?token=hidden ' + 'x' * 400))
        with patch.dict('os.environ', {'PRIVATE_TOKEN': 'private-secret'}), \
                patch.object(commands.subprocess, 'run', return_value=process):
            with self.assertRaises(commands.CommandError) as caught:
                commands.run(['git', '-c', 'http.extraHeader=hidden-config',
                              'checkout', 'task-2'], '/repo')
        error = caught.exception
        self.assertIn('checkout task-2', error.command)
        self.assertIn('cannot checkout task-2', error.stderr)
        for secret in ('hidden-config', 'hidden-header', 'private-secret', 'user:pass', 'token=hidden'):
            self.assertNotIn(secret, error.command + error.stderr)
        self.assertLessEqual(len(error.command), 300)
        self.assertLessEqual(len(error.stderr), 300)

    def test_other_command_failure_keeps_private_output_unexposed(self):
        process = subprocess.CompletedProcess([], 1, '', 'private non-git output')
        with patch.object(commands.subprocess, 'run', return_value=process):
            with self.assertRaises(commands.CommandError) as caught:
                commands.run(['tool', 'secret'])
        self.assertFalse(hasattr(caught.exception, 'stderr'))
        self.assertNotIn('private', str(caught.exception))


if __name__ == '__main__':
    unittest.main()

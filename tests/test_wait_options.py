import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import select
import subprocess
import sys
from unittest.mock import patch

from fleet import cli, mailbox


class WaitOptionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, XDG_STATE_HOME=temp.name, ORCA_TERMINAL_HANDLE='main')
        env.start()
        self.addCleanup(env.stop)
        self.config = {'coordinator': {'run': 'test-run'}}

    def invoke(self, events, *options):
        output = io.StringIO()
        events = iter(events)
        def receive(*args, **kwargs):
            if args[2]:
                kwargs['before_check']()
            return next(events)
        with patch('sys.argv', ['fleet', 'wait', *options]), \
                patch.object(cli, 'load', return_value=self.config), \
                patch.object(mailbox, 'wait', side_effect=receive) as wait, \
                contextlib.redirect_stdout(output):
            code = cli.main()
        return code, output.getvalue(), wait

    def test_loop_rearms_without_repeating_ack_and_retains_unknown_receipt(self):
        rearm = {'event': 'rearm', 'batches': [], 'counts': {},
                 'mutation': {'requestId': 'unknown-request'}, 'ackRunRequested': 'prior'}
        events = [{'event': 'timeout'}, rearm, {'event': 'messages', 'batches': []}]
        code, text, wait = self.invoke(events, '--loop', '--ack-run', 'prior')
        self.assertEqual(code, 0)
        self.assertEqual([call.args[2] for call in wait.call_args_list], ['prior', None, None])
        self.assertEqual(json.loads(text)['rearmEvents'], [rearm])

    def test_single_shot_and_real_errors_do_not_loop(self):
        for event, code in [('timeout', 3), ('rearm', 4), ('error', 2), ('runtime_unavailable', 2)]:
            with self.subTest(event=event):
                options = ['--loop'] if code == 2 else []
                actual, text, wait = self.invoke([{'event': event}], *options)
                self.assertEqual(actual, code)
                self.assertEqual(json.loads(text)['event'], event)
                self.assertEqual(wait.call_count, 1)

    def test_summary_without_save_never_consumes_mail(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _, wait = self.invoke([], '--summary')
        self.assertEqual(code, 2)
        wait.assert_not_called()

    def test_save_failure_returns_complete_auto_ack_batch_even_with_summary(self):
        result = {'event': 'messages', 'nativeAutoAck': True,
                  'batches': [{'deliveryId': 'handled', 'nativeRequestId': 'request',
                               'messages': [{'body': 'must not disappear'}]}]}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(mailbox, 'save_result', side_effect=OSError('disk full')):
            code, text, wait = self.invoke([result], '--save', str(Path(temp) / 'batch'),
                                          '--summary', '--loop')
        returned = json.loads(text)
        self.assertEqual(code, 2)
        self.assertEqual(returned['batches'], result['batches'])
        self.assertTrue(returned['nativeAutoAck'])
        self.assertEqual(returned['saveError'], 'disk full')
        self.assertEqual(wait.call_count, 1)

    def test_unusable_save_destination_never_consumes_mail(self):
        with tempfile.TemporaryDirectory() as temp:
            for path in [Path(temp), Path(temp) / 'missing' / 'batch']:
                with self.subTest(path=path), contextlib.redirect_stderr(io.StringIO()):
                    code, _, wait = self.invoke([], '--save', str(path))
                    self.assertEqual(code, 2)
                    wait.assert_not_called()

    def test_save_replaces_only_complete_private_json_and_cleans_up_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'batch.json'
            path.write_text('previous')
            path.chmod(0o644)
            replace = os.replace
            result = {'event': 'messages', 'batches': [{'messages': [{'body': 'full body'}]}]}

            def observe(source, destination):
                self.assertEqual(path.read_text(), 'previous')
                self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(Path(source).read_text()), result)
                replace(source, destination)

            with patch.object(mailbox.os, 'replace', side_effect=observe):
                mailbox.save_result(path, result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), result)
            with patch.object(mailbox.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    mailbox.save_result(path, {'event': 'error'})
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(list(Path(temp).iterdir()), [path])


    def batch(self, body='full body', **extra):
        rows, counts = mailbox.classify([
            {'id': 'report', 'type': 'status', 'from_handle': 'main',
             'subject': 'relay', 'body': '[fleet x] ' + body}], 'main')
        return {'event': 'messages', 'counts': counts, 'batches': [
            {'deliveryId': 'previous', 'source': 'run', 'messages': rows, **extra}]}

    def test_legacy_history_is_opt_in_and_saved_unchanged(self):
        body = 'first\n' + 'x' * 20000 + '\nlast'
        result = self.batch(body)
        result['legacyTerminalInbox'] = {'messages': result['batches'][0]['messages'],
                                         'counts': result['counts']}
        for flags, expected in (([], 1), (['--legacy-history'], 2)):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as temp:
                code, text, _ = self.invoke(
                    [result], '--summary', '--save', temp + '/saved', *flags)
                self.assertEqual(json.loads(Path(temp + '/saved').read_text()), result)
            self.assertEqual(code, 0)
            self.assertEqual(text.count('[fleet x] ' + body), expected)
            self.assertEqual(text.count('--- END MESSAGE ---'), expected)
            self.assertEqual('LEGACY TERMINAL INBOX' in text, bool(flags))

    def test_auto_ack_previous_once_and_explicit_override(self):
        self.invoke([self.batch()], '--auto-ack')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack', '--ack-run', 'override')
        self.assertEqual(wait.call_args.args[2], 'override')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertIsNone(wait.call_args.args[2])
        self.invoke([self.batch()], '--auto-ack')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertEqual(wait.call_args.args[2], 'previous')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertIsNone(wait.call_args.args[2])
        paths = list((Path(os.environ['XDG_STATE_HOME']) / 'fleet/waits').iterdir())
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in paths))

    def test_crash_before_print_never_arms_ack(self):
        with patch('builtins.print', side_effect=SystemExit('crash')):
            with self.assertRaises(SystemExit):
                self.invoke([self.batch()], '--auto-ack')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertIsNone(wait.call_args.args[2])

    def test_save_failure_after_delivery_never_arms_ack(self):
        save = mailbox.save_result
        def fail_batch(path, result):
            if result.get('batches'):
                raise OSError('disk full')
            save(path, result)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(mailbox, 'save_result', side_effect=fail_batch):
            code, text, wait = self.invoke([self.batch()], '--auto-ack', '--save', temp + '/saved')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(text)['batches'], self.batch()['batches'])
        self.assertEqual(wait.call_count, 1)
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertIsNone(wait.call_args.args[2])

    def test_native_auto_handled_batch_is_not_acked_again(self):
        self.invoke([self.batch(nativeAutoAck=True)], '--auto-ack')
        _, _, wait = self.invoke([{'event': 'timeout'}], '--auto-ack')
        self.assertIsNone(wait.call_args.args[2])

    def test_concurrent_consumer_refuses_and_releases_on_exit(self):
        with mailbox.consumer(self.config):
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                code, _, wait = self.invoke([])
            self.assertEqual(code, 2)
            wait.assert_not_called()
            self.assertIn(f'another fleet wait is active for run test-run (pid {os.getpid()})',
                          errors.getvalue())
        code, _, _ = self.invoke([{'event': 'timeout'}])
        self.assertEqual(code, 3)

    def test_preflight_failures_preserve_previous_ack_until_native_check(self):
        self.invoke([self.batch()], '--auto-ack')
        def native(c, verb, *args, **kwargs):
            if verb == 'run-current':
                return {'run': {'id': 'test-run', 'coordinator_handle': 'main'}}
            if verb == 'inbox':
                return {'messages': [], 'count': 0}
            self.assertEqual(verb, 'check')
            return {'runId': 'test-run', 'messages': [], 'count': 0,
                    'deliveryId': None, 'timedOut': True}
        with mailbox.consumer(self.config) as receipt, \
                patch.object(mailbox, 'request', side_effect=native):
            for options in ({'timeout': 0}, {'wake_on': 'invalid'},
                            {'terminal': 'wrong'}, {'interval': 0}):
                result = mailbox.wait(self.config, ack_run=receipt.pending(auto=True),
                                      before_check=receipt.consume, **options)
                self.assertEqual(result['event'], 'error')
                self.assertEqual(receipt.pending(auto=True), 'previous')
            with patch.object(mailbox, '_binding', side_effect=mailbox.MailboxError('wrong run')):
                mailbox.wait(self.config, ack_run='previous', before_check=receipt.consume)
            self.assertEqual(receipt.pending(auto=True), 'previous')
            result = mailbox.wait(self.config, ack_run='previous', before_check=receipt.consume)
            self.assertEqual(result['event'], 'timeout')
            self.assertIsNone(receipt.pending(auto=True))

    def test_full_body_escapes_terminal_controls_without_altering_saved_mail(self):
        body = 'visible\n\x1b[2J\rhidden\x9b2K\u202ereversed\tend'
        result = self.batch(body)
        with tempfile.TemporaryDirectory() as temp:
            path = temp + '/mail'
            _, text, _ = self.invoke([result], '--summary', '--save', path)
            self.assertEqual(json.loads(Path(path).read_text()), result)
        self.assertIn('visible\n\\u001b[2J\\rhidden\\u009b2K\\u202ereversed\tend', text)
        for control in ('\x1b', '\r', '\x9b', '\u202e'):
            self.assertNotIn(control, text)

    def test_second_cli_process_refuses_while_first_holds_consumer(self):
        script = """import sys
from unittest.mock import patch
from fleet import cli, mailbox
def wait(*args, **kwargs):
    print('READY', file=sys.stderr, flush=True)
    sys.stdin.readline()
    return {'event':'timeout','batches':[],'counts':{}}
with patch.object(cli,'load',return_value={'coordinator':{'run':'test-run'}}), patch.object(mailbox,'wait',side_effect=wait):
    raise SystemExit(cli.main())
"""
        command = [sys.executable, '-c', script, 'wait']
        first = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([first.stderr], [], [], 10)
            self.assertTrue(ready, 'first CLI did not reach locked wait')
            self.assertEqual(first.stderr.readline(), 'READY\n')
            second = subprocess.run(command, input='', capture_output=True, text=True, timeout=10)
            self.assertEqual(second.returncode, 2)
            self.assertIn(f'another fleet wait is active for run test-run (pid {first.pid})',
                          second.stderr)
            self.assertNotIn('READY', second.stderr)
            out, err = first.communicate('\n', timeout=10)
            self.assertEqual(first.returncode, 3)
            self.assertEqual(json.loads(out)['event'], 'timeout')
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()

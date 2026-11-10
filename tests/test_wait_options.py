import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fleet import cli, mailbox


class WaitOptionsTests(unittest.TestCase):
    def invoke(self, events, *options):
        output = io.StringIO()
        with patch('sys.argv', ['fleet', 'wait', *options]), \
                patch.object(cli, 'load', return_value={}), \
                patch.object(mailbox, 'wait', side_effect=events) as wait, \
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

    def test_summary_includes_needs_next_lane_free_and_urgent_but_save_keeps_all(self):
        rows = [
            {'id': 'status', 'type': 'status', 'subject': 'report', 'body': 'keep full payload'},
            {'id': 'ready', 'type': 'status', 'from_handle': 'builder', 'subject': 'TASK-87: FRESH-READY TASK-90', 'body': 'historical readiness'},
            {'id': 'next', 'type': 'question', 'from_handle': 'builder', 'subject': 'NEEDS-NEXT-REF TASK-87'},
            {'id': 'free', 'type': 'question', 'from_handle': 'main', 'subject': 'Lane free: TASK-87 needs next task', 'body': '[fleet x] free'},
            {'id': 'urgent', 'type': 'question', 'from_handle': 'main', 'subject': '[urgent] stalled\nsecond line', 'body': '[fleet x] stalled'}]
        classified, counts = mailbox.classify(rows, 'main')
        result = {'event': 'messages', 'counts': counts, 'batches': [
            {'deliveryId': 'delivery1', 'source': 'run', 'messages': classified}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'last.json'
            code, text, _ = self.invoke([result], '--summary', '--save', str(path))
            self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn('DELIVERY "delivery1" source=run n=5', lines)
        self.assertIn('"next" "builder" builder_decision_request "NEEDS-NEXT-REF TASK-87"', lines)
        self.assertIn('"free" "main" builder_decision_request "Lane free: TASK-87 needs next task"', lines)
        self.assertIn('"urgent" "main" supervisor_urgent "[urgent] stalled\\nsecond line"', lines)
        self.assertIn('"ready" "builder" builder_report "TASK-87: FRESH-READY TASK-90"', lines)
        self.assertIn('"status" "" builder_report "report"', lines)
        self.assertNotIn('keep full payload', text)

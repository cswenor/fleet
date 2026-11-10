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
        self.assertEqual(len(lines), 5)
        self.assertIn('DELIVERY "delivery1" source=run n=4', lines)
        self.assertIn('"next" "builder" builder_decision_request "NEEDS-NEXT-REF TASK-87"', lines)
        self.assertIn('"free" "main" builder_decision_request "Lane free: TASK-87 needs next task"', lines)
        self.assertIn('"urgent" "main" supervisor_urgent "[urgent] stalled\\nsecond line"', lines)
        self.assertNotIn('keep full payload', text)

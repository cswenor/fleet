import os
import subprocess
import unittest
from unittest.mock import patch
from fleet import mailbox


RUN = 'run_scratch'
CALLER = 'term_owner'
CONFIG = {'orca': '/orca', 'root': '/', 'coordinator': {'run': RUN}}


def response(**changes):
    return {'runId': RUN, 'deliveryId': 'delivery_one', 'messages': [
        {'id': 'status', 'type': 'status', 'body': 'build finished'},
        {'id': 'question', 'type': 'question', 'body': 'approve?'}],
        'count': 2, 'timedOut': False, 'replayed': False, **changes}


class DeliveryTests(unittest.TestCase):
    def test_mixed_types_and_payload_are_not_filtered(self):
        parsed = mailbox.delivery(response(replayed=True), RUN)
        self.assertEqual(parsed['source'], 'run')
        self.assertEqual(parsed['deliveryId'], 'delivery_one')
        self.assertEqual([(m['type'], m['body']) for m in parsed['messages']],
                         [('status', 'build finished'), ('question', 'approve?')])
        self.assertTrue(parsed['replayed'])

    def test_timeout_is_not_an_empty_success_or_disconnect(self):
        self.assertIsNone(mailbox.delivery(response(messages=[], count=0,
                          deliveryId=None, timedOut=True), RUN))
        for changes, error in [({'connectionLost': True}, 'connection_lost'),
                               ({'cancelled': True}, 'wait_cancelled')]:
            with self.subTest(error=error), self.assertRaisesRegex(mailbox.MailboxError, error):
                mailbox.delivery(response(**changes), RUN)

    def test_ambiguous_or_nonreplayable_deliveries_fail_closed(self):
        for changes in [{'runId': 'other'}, {'deliveryId': None}, {'count': 1},
                        {'timedOut': True}, {'timedOut': None},
                        {'messages': [None], 'count': 1},
                        {'messages': [], 'count': 0, 'deliveryId': None}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(mailbox.MailboxError, 'invalid_delivery'):
                mailbox.delivery(response(**changes), RUN)

    def test_wake_selection_rejects_empty_or_unknown_kinds(self):
        for value in ('', 'question,', 'heartbeat', 'ALL', None):
            with self.subTest(value=value), self.assertRaises(mailbox.MailboxError):
                mailbox.wake_types(value)

    def test_urgent_alias_and_all_precedence(self):
        self.assertEqual(mailbox.wake_types('question, urgent,escalation,status'),
                         'question,escalation,status')
        self.assertIsNone(mailbox.wake_types('question,all'))


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': CALLER})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_binding_failure_never_checks_or_acknowledges_mail(self):
        for bound in [None, {'id': 'other', 'coordinator_handle': CALLER},
                      {'id': RUN, 'coordinator_handle': 'another_terminal'},
                      {'id': RUN, 'coordinator_handle': CALLER, 'legacy': 1}]:
            calls = []
            def native(c, verb, *args, **kwargs):
                calls.append(verb)
                if verb == 'run-current':
                    return {'run': bound}
                if verb == 'inbox':
                    return {'messages': [{'id': 'old', 'type': 'status', 'read': 0}], 'count': 1}
                self.fail('unbound caller must not consume or ack')
            with self.subTest(bound=bound), patch.object(mailbox, 'request', side_effect=native):
                result = mailbox.wait(CONFIG, ack_run='unprocessed')
            self.assertEqual(result['event'], 'error')
            self.assertEqual(result['error'], 'caller_not_bound_to_configured_run')
            self.assertTrue(result['legacyTerminalInbox']['readOnly'])
            self.assertEqual(result['legacyTerminalInbox']['messages'][0]['id'], 'old')
            self.assertNotIn('check', calls)

    def test_cannot_override_live_caller_identity(self):
        with patch.object(mailbox, 'request', return_value={'messages': [], 'count': 0}) as native:
            result = mailbox.wait(CONFIG, terminal='someone_else')
        self.assertEqual(result['error'], 'caller_identity_mismatch')
        self.assertEqual([call.args[1] for call in native.call_args_list], ['inbox'])
        self.assertEqual(result['terminal'], CALLER)

    def test_diagnostic_failure_preserves_unacked_run_delivery(self):
        with patch.object(mailbox, 'request', side_effect=[
                {'run': {'id': RUN, 'coordinator_handle': CALLER}}, response(),
                mailbox.MailboxError('orca_unavailable')]):
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'error')
        self.assertEqual(result['batches'][0]['deliveryId'], 'delivery_one')
        self.assertEqual(result['legacyTerminalInbox']['error'], 'orca_unavailable')

    def test_invalid_timeout_does_not_start_wait(self):
        for timeout in [0, -1, 1801, float('inf'), float('nan')]:
            with self.subTest(timeout=timeout), patch.object(mailbox, 'request', return_value={'messages': [], 'count': 0}) as native:
                result = mailbox.wait(CONFIG, timeout)
            self.assertEqual(result['event'], 'error')
            self.assertEqual([call.args[1] for call in native.call_args_list], ['inbox'])


    def test_classifies_all_mail_decisions_first_without_changing_ack(self):
        messages = [
            {'id': 'report', 'type': 'status', 'from_handle': 'builder', 'body': '[fleet x] report'},
            {'id': 'relay', 'type': 'status', 'from_handle': 'coordinator', 'body': '[fleet x] alert'},
            {'id': 'decision', 'type': 'question', 'from_handle': 'coordinator', 'body': '[fleet x] approve?'},
            {'id': 'escalation', 'type': 'escalation', 'body': 'blocked'},
            {'id': 'other', 'type': 'unknown', 'from_handle': 'coordinator', 'body': 'ordinary'},
            {'id': 'missing', 'body': None}]
        config = {**CONFIG, 'coordinator': {'run': RUN, 'terminal': 'coordinator'}}
        with patch.object(mailbox, 'request', side_effect=[
                {'run': {'id': RUN, 'coordinator_handle': CALLER}},
                response(messages=messages, count=len(messages), acknowledged='prior'),
                {'messages': messages, 'count': len(messages)}]) as native:
            result = mailbox.wait(config, ack_run='prior')
        batch = result['batches'][0]
        self.assertEqual(batch['deliveryId'], 'delivery_one')
        self.assertEqual(result['acknowledged'], 'prior')
        self.assertIn('--ack', native.call_args_list[1].args)
        self.assertEqual([m['id'] for m in batch['messages']],
                         ['decision', 'escalation', 'report', 'relay', 'other', 'missing'])
        self.assertEqual(batch['counts'], {'builder_decision_request': 2,
                                         'supervisor_urgent': 0, 'supervisor_relay': 1, 'builder_report': 3})
        self.assertEqual(result['legacyTerminalInbox']['counts'], batch['counts'])
        self.assertEqual(result['legacyTerminalInbox']['messages'], batch['messages'])
        self.assertEqual(batch['messages'][0]['body'], '[fleet x] approve?')

    def test_missing_coordinator_cannot_mark_unknown_sender_as_relay(self):
        messages, counts = mailbox.classify([{'id': 'one', 'body': '[fleet x] text'}], None)
        self.assertEqual(messages[0]['classification'], 'builder_report')
        self.assertEqual(counts['supervisor_relay'], 0)

    def test_urgent_markers_are_advisory_and_do_not_hide_builder_questions(self):
        rows = [
            {'id': 'relay', 'type': 'question', 'from_handle': 'main',
             'subject': '[urgent] wrong model', 'body': '[fleet x] model mismatch'},
            {'id': 'builder', 'type': 'question', 'from_handle': 'builder',
             'subject': '[urgent] decision', 'body': '[fleet x] approve?'},
            {'id': 'ordinary', 'type': 'question', 'from_handle': 'main',
             'subject': 'decision', 'body': '[fleet x] approve?'}]
        classified, counts = mailbox.classify(rows, 'main')
        self.assertEqual([m['id'] for m in classified], ['builder', 'ordinary', 'relay'])
        self.assertEqual(counts['builder_decision_request'], 2)
        self.assertEqual(counts['supervisor_urgent'], 1)
        self.assertFalse(classified[-1]['replyExpected'])
        self.assertEqual(classified[-1]['triageNote'], 'no reply expected')
        self.assertNotIn('replyExpected', classified[0])

class NativeErrorTests(unittest.TestCase):
    def test_runtime_errors_cannot_look_like_timeout(self):
        for stdout, code, expected in [
                ('not JSON', 0, 'invalid_orca_json'),
                ('[]', 0, 'invalid_orca_response'),
                ('{"ok":false,"error":{"code":"consumer_fenced","message":"private detail"}}', 1, 'consumer_fenced'),
                ('{"ok":true,"result":{}}', 1, 'invalid_orca_response')]:
            with self.subTest(stdout=stdout), patch.object(mailbox.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout)), self.assertRaisesRegex(mailbox.MailboxError, expected):
                mailbox.request(CONFIG, 'check')

    def test_transport_timeout_is_failure_not_checkpoint(self):
        with patch.object(mailbox.subprocess, 'run', side_effect=subprocess.TimeoutExpired('orca', 1)), self.assertRaisesRegex(mailbox.MailboxError, 'orca_unavailable'):
            mailbox.request(CONFIG, 'check')


if __name__ == '__main__':
    unittest.main()

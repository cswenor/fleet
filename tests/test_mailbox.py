import os
import json
import subprocess
import unittest
from unittest.mock import patch
from fleet import commands, mailbox


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
                        {'deliveryAcknowledged': 'true'},
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
                mailbox.MailboxError('invalid_orca_json')]):
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'error')
        self.assertEqual(result['batches'][0]['deliveryId'], 'delivery_one')
        self.assertEqual(result['legacyTerminalInbox']['error'], 'invalid_orca_json')

    def test_auto_ack_survives_diagnostic_failure_with_native_recovery(self):
        with patch.object(mailbox, 'request', side_effect=[
                {'run': {'id': RUN, 'coordinator_handle': CALLER}},
                response(deliveryAcknowledged=True, mutation={'requestId': 'request_one'}),
                mailbox.MailboxError('invalid_orca_json')]):
            result = mailbox.wait(CONFIG, ack_run='prior')
        self.assertEqual(result['event'], 'error')
        self.assertTrue(result['nativeAutoAck'])
        self.assertEqual(result['acknowledgmentStatus'], 'already acknowledged natively')
        self.assertEqual(result['batches'][0]['nativeRequestId'], 'request_one')
        self.assertTrue(result['batches'][0]['deliveryAcknowledged'])

    def test_legacy_delivery_output_remains_unchanged(self):
        self.assertEqual(mailbox.delivery(response(replayed=True), RUN),
                         {'source': 'run', 'deliveryId': 'delivery_one',
                          'messages': response()['messages'], 'replayed': True})

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

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        patches = [
            patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': CALLER}),
            patch.object(mailbox.time, 'monotonic', side_effect=lambda: self.now),
            patch.object(mailbox.time, 'sleep', side_effect=self.advance)]
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def advance(self, seconds):
        self.now += seconds

    def bound(self):
        return {'run': {'id': RUN, 'coordinator_handle': CALLER}}

    def test_read_outage_recovers_before_single_consuming_check(self):
        with patch.object(mailbox, 'request', side_effect=[
                mailbox.MailboxError('orca_unavailable'), self.bound(), response(),
                {'messages': [], 'count': 0}]) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'messages')
        self.assertTrue(result['runtimeRecovered'])
        self.assertEqual(self.now, 1)
        self.assertEqual([call.args[1] for call in native.call_args_list],
                         ['run-current', 'run-current', 'check', 'inbox'])

    def test_ambiguous_check_never_repeats_check_or_ack(self):
        metadata = {'mutation': {'requestId': 'request_one'}, 'deliveryId': 'delivery_one'}
        with patch.object(mailbox, 'request', side_effect=[
                self.bound(), mailbox.MailboxError('connection_lost', metadata=metadata),
                mailbox.MailboxError('runtime_unavailable'), self.bound()]) as native:
            result = mailbox.wait(CONFIG, ack_run='prior')
        self.assertEqual(result['event'], 'rearm')
        self.assertEqual(result['reason'], 'consuming_wait_interrupted_outcome_unknown')
        self.assertEqual(result['mutation']['requestId'], 'request_one')
        self.assertEqual(result['deliveryId'], 'delivery_one')
        self.assertEqual(result['ackRunRequested'], 'prior')
        self.assertIn('do not repeat acknowledgment', result['acknowledgmentStatus'])
        self.assertEqual([call.args[1] for call in native.call_args_list].count('check'), 1)
        self.assertEqual(sum('--ack' in call.args for call in native.call_args_list), 1)

    def test_persistent_outage_is_bounded_and_distinct(self):
        with patch.object(mailbox, 'request', side_effect=mailbox.MailboxError('orca_unavailable')) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'runtime_unavailable')
        self.assertEqual(result['error'], 'runtime_unavailable')
        self.assertEqual(self.now, 30)
        self.assertEqual(len(native.call_args_list), 30)
        self.assertTrue(all(call.kwargs['timeout'] <= 30 for call in native.call_args_list))

    def test_first_read_timeout_counts_toward_recovery_deadline(self):
        def blocked(c, *args, timeout):
            self.advance(timeout)
            raise mailbox.MailboxError('orca_unavailable')
        with patch.object(mailbox, 'request', side_effect=blocked) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'runtime_unavailable')
        self.assertEqual(self.now, 30)
        self.assertEqual(native.call_count, 1)

    def test_interrupted_wait_persistent_outage_never_repeats_mutation(self):
        def native(c, verb, *args, **kwargs):
            if verb == 'run-current' and self.now == 0:
                return self.bound()
            raise mailbox.MailboxError('orca_unavailable')
        with patch.object(mailbox, 'request', side_effect=native) as request:
            result = mailbox.wait(CONFIG, ack_run='prior')
        self.assertEqual(result['event'], 'runtime_unavailable')
        self.assertEqual(self.now, 30)
        self.assertEqual([call.args[1] for call in request.call_args_list].count('check'), 1)

    def test_recovered_diagnostics_revalidate_binding_and_keep_batch(self):
        with patch.object(mailbox, 'request', side_effect=[
                self.bound(), response(deliveryAcknowledged=True, mutation={'requestId': 'native'}),
                mailbox.MailboxError('orca_unavailable'), self.bound(),
                {'messages': [], 'count': 0}]) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'messages')
        self.assertTrue(result['runtimeRecovered'])
        self.assertTrue(result['nativeAutoAck'])
        self.assertEqual(result['batches'][0]['nativeRequestId'], 'native')
        self.assertEqual([call.args[1] for call in native.call_args_list],
                         ['run-current', 'check', 'inbox', 'run-current', 'inbox'])


    def test_malformed_permission_and_binding_errors_do_not_retry(self):
        for error in ('invalid_orca_json', 'invalid_orca_response', 'permission_denied',
                      'consumer_fenced', 'orca_capabilities_unavailable'):
            with self.subTest(error=error), patch.object(mailbox, 'request', side_effect=[
                    mailbox.MailboxError(error), {'messages': [], 'count': 0}]) as native:
                result = mailbox.wait(CONFIG)
            self.assertEqual(result['event'], 'error')
            self.assertEqual(result['error'], error)
            self.assertEqual([call.args[1] for call in native.call_args_list],
                             ['run-current', 'inbox'])
        self.assertEqual(self.now, 0)

    def test_recovery_rechecks_identity_before_diagnostic_retry(self):
        with patch.object(mailbox, 'request', side_effect=[
                self.bound(), response(deliveryAcknowledged=True, mutation={'requestId': 'native'}),
                mailbox.MailboxError('orca_unavailable'),
                {'run': {'id': 'wrong', 'coordinator_handle': CALLER}}]) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'error')
        self.assertEqual(result['legacyTerminalInbox']['error'], 'caller_not_bound_to_configured_run')
        self.assertEqual(result['batches'][0]['nativeRequestId'], 'native')
        self.assertTrue(result['nativeAutoAck'])
        self.assertEqual([call.args[1] for call in native.call_args_list].count('inbox'), 1)

    def test_fetched_autoack_batch_survives_persistent_diagnostic_outage(self):
        def native(c, verb, *args, **kwargs):
            if verb == 'run-current':
                return self.bound()
            if verb == 'check':
                return response(deliveryAcknowledged=True, mutation={'requestId': 'native'})
            raise mailbox.MailboxError('orca_unavailable')
        with patch.object(mailbox, 'request', side_effect=native) as request:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'runtime_unavailable')
        self.assertEqual(self.now, 30)
        self.assertEqual(result['batches'][0]['deliveryId'], 'delivery_one')
        self.assertEqual(result['batches'][0]['nativeRequestId'], 'native')
        self.assertTrue(result['nativeAutoAck'])
        self.assertEqual(result['acknowledgmentStatus'], 'already acknowledged natively')
        self.assertEqual([call.args[1] for call in request.call_args_list].count('check'), 1)

    def test_disconnect_retains_native_response_identifiers(self):
        received = response(connectionLost=True, mutation={'requestId': 'native'})
        with patch.object(mailbox, 'request', side_effect=[
                self.bound(), received, self.bound()]) as native:
            result = mailbox.wait(CONFIG)
        self.assertEqual(result['event'], 'rearm')
        self.assertEqual(result['nativeResult'], received)
        self.assertEqual(result['mutation']['requestId'], 'native')
        self.assertEqual([call.args[1] for call in native.call_args_list].count('check'), 1)


class CapabilitySafetyTests(unittest.TestCase):
    def test_failed_or_ambiguous_check_help_never_consumes_mail(self):
        for failure in (commands.CommandError('unavailable'), '',
                        'Usage: orca orchestration check\nOptions:\n  --full\n  --full\n'):
            with self.subTest(failure=failure):
                commands._ORCHESTRATION_CAPABILITIES.clear()
                self.addCleanup(commands._ORCHESTRATION_CAPABILITIES.clear)
                def help_text(argv, *args, **kwargs):
                    if argv[2] == 'check':
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    return f'Usage: orca orchestration {argv[2]}\nOptions:\n  --json\n'
                def native(argv, **kwargs):
                    if argv[2] == 'run-current':
                        result = {'run': {'id': RUN, 'coordinator_handle': CALLER}}
                    elif argv[2] == 'inbox':
                        result = {'messages': [], 'count': 0}
                    else:
                        self.fail('capability uncertainty must not consume or acknowledge')
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'ok': True, 'result': result}))
                with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': CALLER}), \
                        patch.object(commands, 'run', side_effect=help_text), \
                        patch.object(mailbox.subprocess, 'run', side_effect=native):
                    result = mailbox.wait(CONFIG, ack_run='unprocessed')
                self.assertEqual(result['event'], 'error')
                self.assertEqual(result['error'], 'orca_capabilities_unavailable')
                self.assertEqual(result['batches'], [])

    def test_cold_capabilities_and_native_read_share_recovery_deadline(self):
        for phase in ('capability', 'native'):
            with self.subTest(phase=phase):
                commands._ORCHESTRATION_CAPABILITIES.clear()
                self.addCleanup(commands._ORCHESTRATION_CAPABILITIES.clear)
                clock = [0.0]
                def execute(argv, **kwargs):
                    if '--help' in argv and phase == 'native':
                        clock[0] += 20
                        return subprocess.CompletedProcess(argv, 0,
                            'Usage: orca orchestration run-current\nOptions:\n  --json\n', '')
                    clock[0] += kwargs['timeout']
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': CALLER}), \
                        patch.object(mailbox.time, 'monotonic', side_effect=lambda: clock[0]), \
                        patch.object(mailbox.subprocess, 'run', side_effect=execute) as native:
                    result = mailbox.wait(CONFIG, ack_run='unprocessed')
                self.assertEqual(result['event'], 'runtime_unavailable')
                self.assertEqual(clock[0], 30)
                self.assertTrue(all('check' not in call.args[0] for call in native.call_args_list))


class NativeErrorTests(unittest.TestCase):
    def setUp(self):
        capability = patch.object(mailbox, 'orchestration_args',
                                  side_effect=lambda executable, args, **kwargs: args)
        capability.start()
        self.addCleanup(capability.stop)

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

    def test_structured_transport_error_retains_native_identifiers(self):
        payload = {'ok': False, 'error': {'code': 'runtime_unavailable',
                   'requestId': 'native'}, 'deliveryId': 'delivery_one'}
        with patch.object(mailbox.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, json.dumps(payload))), self.assertRaises(mailbox.MailboxError) as raised:
            mailbox.request(CONFIG, 'check')
        self.assertEqual(str(raised.exception), 'runtime_unavailable')
        self.assertEqual(raised.exception.metadata,
                         {'requestId': 'native', 'deliveryId': 'delivery_one'})

    def test_executable_permission_failure_is_not_a_transport_outage(self):
        with patch.object(mailbox.subprocess, 'run', side_effect=PermissionError()), \
                self.assertRaisesRegex(mailbox.MailboxError, 'orca_executable_unavailable'):
            mailbox.request(CONFIG, 'check')


if __name__ == '__main__':
    unittest.main()

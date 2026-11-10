import contextlib
import io
import tempfile
import json
import os
import unittest
from unittest.mock import patch

from fleet import cli, mailbox


class BuilderWakeTests(unittest.TestCase):
    def setUp(self):
        self.c = {'coordinator': {'run': 'run_test', 'terminal': 'main'}}
        self.now = 0
        self.calls = []
        self.rows = [{'id': 'relay', 'from_handle': 'main', 'type': 'status',
                      'body': '[fleet test] checks changed'}]
        self.arrival = None
        self.env = patch.dict(os.environ, ORCA_TERMINAL_HANDLE='main')
        self.env.start()
        self.addCleanup(self.env.stop)

    def sleep(self, seconds):
        self.now += seconds
        if self.arrival:
            self.rows.append(self.arrival)
            self.arrival = None

    def native(self, c, verb, *args, **kwargs):
        self.calls.append((verb, args))
        if verb == 'run-current':
            return {'run': {'id': 'run_test', 'coordinator_handle': 'main'}}
        if verb == 'inbox':
            return {'messages': [], 'count': 0}
        if '--peek' in args:
            return {'runId': 'run_test', 'messages': list(self.rows), 'count': len(self.rows)}
        rows, self.rows = self.rows, []
        return {'runId': 'run_test', 'messages': rows, 'count': len(rows),
                'deliveryId': f'delivery-{len(self.calls)}' if rows else None,
                'timedOut': not bool(rows), 'deliveryAcknowledged': True}

    def wait(self, **options):
        with patch.object(mailbox, 'request', side_effect=self.native), \
                patch.object(mailbox.time, 'monotonic', side_effect=lambda: self.now), \
                patch.object(mailbox.time, 'sleep', side_effect=self.sleep):
            options.setdefault('wake_on', 'question,escalation,urgent,builder')
            return mailbox.wait(self.c, timeout=6, **options)

    def test_builder_status_wakes_and_preserves_whole_relay_batch(self):
        self.arrival = {'id': 'builder', 'from_handle': 'lane', 'type': 'status', 'body': 'merged'}
        result = self.wait()
        self.assertEqual(result['event'], 'messages')
        self.assertEqual(self.now, 3)
        self.assertEqual([row['id'] for row in result['batches'][0]['messages']], ['relay', 'builder'])
        self.assertTrue(result['nativeAutoAck'])
        consumes = [args for verb, args in self.calls if verb == 'check' and '--peek' not in args]
        self.assertEqual(len(consumes), 1)
        self.assertNotIn('--types', consumes[0])

    def test_relay_only_defers_consumption_until_timeout_checkpoint(self):
        result = self.wait()
        self.assertEqual(result['event'], 'messages')
        self.assertEqual(self.now, 6)
        self.assertEqual(result['batches'][0]['messages'][0]['id'], 'relay')
        self.assertEqual(sum(verb == 'check' and '--peek' not in args
                             for verb, args in self.calls), 1)
        self.assertIn('messages', result['legacyTerminalInbox'])

    def test_empty_timeout_does_not_consume(self):
        self.rows = []
        result = self.wait()
        self.assertEqual(result['event'], 'timeout')
        self.assertEqual(self.now, 6)
        self.assertTrue(all('--peek' in args for verb, args in self.calls if verb == 'check'))

    def test_arrival_during_final_sleep_is_observed_at_checkpoint(self):
        self.rows = []
        def arrive_at_deadline(seconds):
            self.now += seconds
            if self.now == 6:
                self.rows.append({'id': 'late-relay', 'from_handle': 'main',
                                  'type': 'status', 'body': '[fleet test] checks'})
        with patch.object(self, 'sleep', side_effect=arrive_at_deadline):
            result = self.wait()
        self.assertEqual(result['event'], 'messages')
        self.assertEqual(self.now, 6)
        self.assertEqual(result['batches'][0]['messages'][0]['id'], 'late-relay')

    def test_builder_and_status_union_still_wakes_on_builder_question(self):
        self.rows = []
        self.arrival = {'id': 'ask', 'from_handle': 'lane', 'type': 'question'}
        result = self.wait(wake_on='builder,status')
        self.assertEqual(result['event'], 'messages')
        self.assertEqual(result['batches'][0]['messages'][0]['id'], 'ask')


    def test_cap_fails_explicitly_without_consuming(self):
        self.rows = [{**self.rows[0], 'id': str(i)} for i in range(100)]
        result = self.wait()
        self.assertEqual(result['event'], 'error')
        self.assertEqual(result['error'], 'builder_peek_incomplete')
        self.assertTrue(all('--peek' in args for verb, args in self.calls if verb == 'check'))

    def test_ack_time_relay_waits_until_checkpoint(self):
        result = self.wait(ack_run='previous')
        self.assertEqual(self.now, 6)
        self.assertEqual(result['event'], 'messages')
        self.assertEqual([r['id'] for b in result['batches'] for r in b['messages']], ['relay'])
        checks = [args for verb, args in self.calls if verb == 'check']
        self.assertEqual(sum('--ack' in args for args in checks), 1)
        self.assertEqual(sum('--peek' not in args for args in checks), 1)

    def test_ack_time_relay_combines_with_selected_arrival(self):
        self.arrival = {'id': 'builder', 'from_handle': 'lane', 'type': 'status'}
        result = self.wait(ack_run='previous')
        self.assertEqual(self.now, 3)
        self.assertEqual([r['id'] for b in result['batches'] for r in b['messages']],
                         ['relay', 'builder'])
        self.assertEqual(result['counts']['supervisor_relay'], 1)
        self.assertEqual(result['counts']['builder_report'], 1)
        self.assertTrue(all(b['nativeAutoAck'] for b in result['batches']))

    def test_explicit_ack_runtime_keeps_selected_arrival_queued(self):
        original = self.native
        held = list(self.rows)
        def explicit(c, verb, *args, **kwargs):
            response = original(c, verb, *args, **kwargs)
            if verb == 'check' and '--peek' not in args:
                response.pop('deliveryAcknowledged')
                self.rows = list(held)
            return response
        self.arrival = {'id': 'builder', 'from_handle': 'lane', 'type': 'status'}
        with patch.object(self, 'native', side_effect=explicit):
            result = self.wait(ack_run='previous')
        self.assertEqual(self.now, 3)
        self.assertEqual([r['id'] for b in result['batches'] for r in b['messages']], ['relay'])
        self.assertEqual([r['id'] for r in self.rows], ['relay', 'builder'])
        with tempfile.TemporaryDirectory() as temp:
            receipt = mailbox._AckReceipt(mailbox.Path(temp) / 'ack.json')
            receipt.printed(result)
            self.assertEqual(receipt.pending(auto=True), result['batches'][0]['deliveryId'])

    def test_held_delivery_survives_later_peek_failure(self):
        original = self.native
        def failed(c, verb, *args, **kwargs):
            if '--peek' in args:
                raise mailbox.MailboxError('invalid_builder_peek')
            return original(c, verb, *args, **kwargs)
        with patch.object(self, 'native', side_effect=failed):
            result = self.wait(ack_run='previous')
        self.assertEqual(result['event'], 'error')
        self.assertEqual(result['batches'][0]['messages'][0]['id'], 'relay')

    def test_cli_holds_saves_and_arms_only_explicit_ack_batches(self):
        for native_auto_ack in (False, True):
            for arrival in (False, True):
                with self.subTest(native_auto_ack=native_auto_ack, arrival=arrival), \
                        tempfile.TemporaryDirectory() as temp, \
                        patch.dict(os.environ, XDG_STATE_HOME=temp):
                    self.now, self.calls = 0, []
                    self.rows = [{'id': 'relay', 'from_handle': 'main', 'type': 'status',
                                  'body': '[fleet test] relay'}]
                    self.arrival = ({'id': 'builder', 'from_handle': 'lane', 'type': 'status'}
                                    if arrival else None)
                    original = self.native
                    held = list(self.rows)
                    def transport(c, verb, *args, **kwargs):
                        result = original(c, verb, *args, **kwargs)
                        if not native_auto_ack and verb == 'check' and '--peek' not in args:
                            result.pop('deliveryAcknowledged')
                            self.rows = list(held)
                        return result
                    with mailbox.consumer(self.c) as receipt:
                        receipt.printed({'batches': [{'deliveryId': 'previous'}]})
                    path = temp + '/saved.json'
                    with patch('sys.argv', ['fleet', 'wait', '--timeout', '6', '--loop',
                                           '--wake-on', 'builder', '--auto-ack', '--summary',
                                           '--save', path]), \
                            patch.object(cli, 'load', return_value=self.c), \
                            patch.object(mailbox, 'request', side_effect=transport), \
                            patch.object(mailbox.time, 'monotonic', side_effect=lambda: self.now), \
                            patch.object(mailbox.time, 'sleep', side_effect=self.sleep), \
                            contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(cli.main(), 0)
                    self.assertEqual(self.now, 3 if arrival else 6)
                    saved = json.loads(mailbox.Path(path).read_text())
                    ids = [r['id'] for b in saved['batches'] for r in b['messages']]
                    self.assertEqual(ids, ['relay', 'builder'] if arrival and native_auto_ack
                                     else ['relay'])
                    self.assertEqual(output.getvalue().count('[fleet test] relay'), 1)
                    with mailbox.consumer(self.c) as receipt:
                        self.assertEqual(receipt.pending(auto=True), None if native_auto_ack
                                         else saved['batches'][0]['deliveryId'])
                    checks = [args for verb, args in self.calls if verb == 'check']
                    self.assertEqual(sum('--ack' in args for args in checks), 1)

    def test_consuming_outage_returns_rearm_without_second_consume(self):
        original = self.native
        self.rows.append({'id': 'builder', 'type': 'status', 'from_handle': 'lane'})
        def interrupted(c, verb, *args, **kwargs):
            if verb == 'check' and '--peek' not in args:
                self.calls.append((verb, args))
                raise mailbox.MailboxError('connection_lost', metadata={'requestId': 'unknown'})
            return original(c, verb, *args, **kwargs)
        with patch.object(self, 'native', side_effect=interrupted):
            result = self.wait()
        self.assertEqual(result['event'], 'rearm')
        self.assertEqual(result['requestId'], 'unknown')
        self.assertEqual(sum(verb == 'check' and '--peek' not in args for verb, args in self.calls), 1)

    def test_invalid_interval_refuses_before_peek(self):
        for interval in (0, 31, float('nan')):
            with self.subTest(interval=interval):
                self.calls.clear()
                result = self.wait(interval=interval)
                self.assertEqual(result['error'], 'interval_must_be_1_to_30_seconds')
                self.assertFalse(any(verb == 'check' for verb, _ in self.calls))

    def test_explicit_and_auto_ack_empty_inbox_wait_full_requested_timeout(self):
        self.rows = []
        for options in (['--ack-run', 'previous'], ['--auto-ack']):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, XDG_STATE_HOME=temp):
                self.now = 0
                self.calls = []
                with mailbox.consumer(self.c) as receipt:
                    receipt.printed({'batches': [{'deliveryId': 'previous'}]})
                argv = ['fleet', 'wait', '--timeout', '6', '--wake-on',
                        'question,escalation,urgent,builder', *options]
                with patch('sys.argv', argv), patch.object(cli, 'load', return_value=self.c), \
                        patch.object(mailbox, 'request', side_effect=self.native), \
                        patch.object(mailbox.time, 'monotonic', side_effect=lambda: self.now), \
                        patch.object(mailbox.time, 'sleep', side_effect=self.sleep), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.main(), 3)
                self.assertEqual(self.now, 6)
                self.assertEqual(json.loads(output.getvalue())['event'], 'timeout')
                checks = [args for verb, args in self.calls if verb == 'check']
                self.assertEqual(sum('--ack' in args for args in checks), 1)
                self.assertTrue(all('--peek' in args for args in checks[1:]))

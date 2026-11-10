import json
import os
import unittest
from unittest.mock import patch

from fleet import mailbox


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
        return {'runId': 'run_test', 'messages': list(self.rows), 'count': len(self.rows),
                'deliveryId': 'delivery', 'timedOut': False, 'deliveryAcknowledged': True}

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

    def test_explicit_ack_retains_native_whole_batch_precedence(self):
        result = self.wait(ack_run='previous')
        self.assertEqual(result['batches'][0]['messages'][0]['id'], 'relay')
        checks = [args for verb, args in self.calls if verb == 'check']
        self.assertEqual(len(checks), 1)
        self.assertIn('--ack', checks[0])
        self.assertNotIn('--peek', checks[0])

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

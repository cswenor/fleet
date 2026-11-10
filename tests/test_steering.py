import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fleet import cli, state, steering, threads
from fleet.mailbox import MailboxError


class SteeringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.slot = self.temp.name
        self.c = {'_state': self.slot, 'root': self.slot, 'pad_workspace': 'scratch',
                  'builder': {'slots': [self.slot]},
                  'coordinator': {'run': 'run_main', 'terminal': 'term_main'}}
        self.launch = {'id': 'one', 'ref': 'TASK-1', 'terminal': 'term_builder',
                       'terminal_incarnation_id': 'builder-incarnation',
                       'worktree': {'id': 'repo::' + self.slot, 'path': self.slot}}
        state.write(self.c, 'launches', {self.slot: self.launch})
        self.terminal = {'handle': 'term_builder', 'incarnationId': 'builder-incarnation',
                         'worktreeId': self.launch['worktree']['id'],
                         'connected': True, 'writable': True, 'orphaned': False}
        self.bindings = {'term_main': {'id': 'run_main', 'coordinator_handle': 'term_main'},
                         'term_builder': None}
        self.native_calls = []
        self.terminal_calls = []
        self.sent = []
        self.send_error = None
        self.before_send = None
        self.env = patch.dict(os.environ, ORCA_TERMINAL_HANDLE='term_builder')
        self.env.start()
        self.addCleanup(self.env.stop)
        for module in (threads, steering):
            native = patch.object(module, 'request', side_effect=self.request)
            native.start()
            self.addCleanup(native.stop)
            terminal = patch.object(module, 'orca', side_effect=self.orca, create=True)
            terminal.start()
            self.addCleanup(terminal.stop)
        pad = patch.object(threads, 'pad')
        pad.start()
        self.addCleanup(pad.stop)
        git = patch.object(threads, 'run', return_value='task-1\n')
        git.start()
        self.addCleanup(git.stop)
        self.opened = threads.open_thread(self.c, 'TASK-1', self.slot)
        self.native_calls.clear()
        self.terminal_calls.clear()

    def request(self, c, verb, *args, **kwargs):
        self.native_calls.append((verb, args))
        flags = dict(zip(args[::2], args[1::2]))
        if verb == 'run-current':
            return {'run': self.bindings.get(flags['--from'])}
        if verb == 'send':
            self.sent.append(flags)
            if self.before_send:
                self.before_send()
            if self.send_error:
                raise self.send_error
            return {'message': {'id': 'm' + str(len(self.sent))}}
        self.fail('steering must not inspect or consume native mail: ' + verb)

    def orca(self, c, *args):
        self.terminal_calls.append(args)
        self.assertEqual(args, ('terminal', 'show', '--terminal', 'term_builder'))
        return {'terminal': dict(self.terminal)}

    def as_main(self):
        return patch.dict(os.environ, ORCA_TERMINAL_HANDLE='term_main')

    def send(self, body='Stop after the current edit and report the new constraint.'):
        with self.as_main():
            return steering.send(self.c, 'TASK-1', body)

    def pending(self):
        with self.as_main():
            return steering.pending(self.c)

    def attempt(self):
        return state.read(self.c, 'threads-TASK-1')['TASK-1']['attempts'][self.launch['id']]

    def test_send_check_ack_round_trip_preserves_until_processed(self):
        body = 'Preserve the user edits; do not publish.'
        with patch.object(steering.time, 'time', return_value=1000):
            receipt = self.send(body)
        uuid.UUID(receipt['token'])
        self.assertEqual(receipt['status'], 'sent')
        self.assertEqual(receipt['message_id'], 'm1')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['--from'], 'term_main')
        self.assertEqual(self.sent[0]['--to'], 'term_builder')
        self.assertEqual(self.sent[0]['--type'], 'status')
        self.assertEqual(self.sent[0]['--thread-id'], self.opened['thread_id'])
        self.assertIn(body, self.sent[0]['--body'])
        first = steering.check(self.c, 'TASK-1')['messages']
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['token'], receipt['token'])
        self.assertEqual(first[0]['body'], body)
        self.assertEqual(first[0]['message_id'], 'm1')
        self.assertEqual(first[0]['status'], 'sent')
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], first)
        with patch.object(steering.time, 'time', return_value=1042):
            lane, = self.pending()['lanes']
        self.assertEqual((lane['ref'], lane['slot'], lane['launch_id'], lane['attempt']),
                         ('TASK-1', self.slot, 'one', self.opened['attempt']))
        self.assertEqual(lane['messages'], [{**first[0], 'age_seconds': 42}])
        self.assertNotIn('age_seconds', self.attempt()['steering'][receipt['token']])
        steering.check(self.c, 'TASK-1', acknowledge=receipt['token'])
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], [])
        self.assertEqual(self.pending(), {'lanes': []})
        self.assertTrue(self.attempt()['steering'][receipt['token']]['acknowledged_at'])
        self.assertEqual(len(self.sent), 1)

    def test_check_keeps_instruction_order_after_state_round_trip(self):
        with patch.object(steering.uuid, 'uuid4', side_effect=['z-first', 'a-second']):
            with patch.object(steering.time, 'time', return_value=1000):
                first = self.send('Start with this constraint.')
            with patch.object(steering.time, 'time', return_value=1001):
                second = self.send('Then apply this constraint.')
        self.assertEqual([row['token'] for row in steering.check(self.c, 'TASK-1')['messages']],
                         [first['token'], second['token']])

    def test_send_is_journaled_before_native_delivery(self):
        observed = []
        def before_send():
            records = self.attempt()['steering']
            self.assertEqual(len(records), 1)
            token, record = next(iter(records.items()))
            uuid.UUID(token)
            self.assertEqual(record['status'], 'sending')
            self.assertEqual(record['body'], 'Do not publish.')
            self.assertFalse(record.get('message_id'))
            observed.append(token)
        self.before_send = before_send
        receipt = self.send('Do not publish.')
        self.assertEqual(observed, [receipt['token']])
        self.assertEqual(self.attempt()['steering'][receipt['token']]['status'], 'sent')

    def test_acknowledging_one_message_keeps_other_messages_pending(self):
        first = self.send('First constraint.')
        second = self.send('Second constraint.')
        self.assertNotEqual(first['token'], second['token'])
        steering.check(self.c, 'TASK-1', acknowledge=first['token'])
        remaining = steering.check(self.c, 'TASK-1')['messages']
        self.assertEqual([m['token'] for m in remaining], [second['token']])
        pending, = self.pending()['lanes'][0]['messages']
        self.assertGreaterEqual(pending.pop('age_seconds'), 0)
        self.assertEqual(pending, remaining[0])

    def test_send_and_pending_require_configured_main_terminal(self):
        for caller in ('term_builder', 'impostor'):
            with self.subTest(caller=caller), patch.dict(os.environ, ORCA_TERMINAL_HANDLE=caller):
                with self.assertRaises(ValueError):
                    steering.send(self.c, 'TASK-1', 'Do not publish.')
                with self.assertRaises(ValueError):
                    steering.pending(self.c)
        self.assertEqual(self.sent, [])
        self.assertNotIn('steering', self.attempt())

    def test_send_and_pending_require_main_bound_to_configured_run(self):
        for binding in (None, {'id': 'other_run', 'coordinator_handle': 'term_main'},
                        {'id': 'run_main', 'coordinator_handle': 'impostor'},
                        {'id': 'run_main', 'coordinator_handle': 'term_main', 'legacy': True}):
            with self.subTest(binding=binding):
                self.bindings['term_main'] = binding
                with self.as_main():
                    with self.assertRaises(MailboxError):
                        steering.send(self.c, 'TASK-1', 'Do not publish.')
                    with self.assertRaises(MailboxError):
                        steering.pending(self.c)
        self.assertEqual(self.sent, [])
        self.assertNotIn('steering', self.attempt())

    def test_changed_target_ownership_refuses_send_and_check(self):
        for field, value in (('incarnationId', 'recreated'), ('worktreeId', 'other-worktree'),
                             ('connected', False), ('writable', False), ('orphaned', True)):
            with self.subTest(field=field), patch.dict(self.terminal, {field: value}):
                with self.assertRaises(ValueError):
                    self.send()
                with self.assertRaises(ValueError):
                    steering.check(self.c, 'TASK-1')
        self.assertEqual(self.sent, [])
        self.assertNotIn('steering', self.attempt())

    def test_run_bound_target_refuses_send_and_check(self):
        self.bindings['term_builder'] = {'id': 'unexpected_run'}
        with self.assertRaises(ValueError):
            self.send()
        with self.assertRaises(ValueError):
            steering.check(self.c, 'TASK-1')
        self.assertEqual(self.sent, [])
        self.assertNotIn('steering', self.attempt())

    def test_only_current_builder_can_check_or_ack(self):
        receipt = self.send()
        for caller in ('term_main', 'impostor'):
            with self.subTest(caller=caller), patch.dict(os.environ, ORCA_TERMINAL_HANDLE=caller):
                with self.assertRaises(ValueError):
                    steering.check(self.c, 'TASK-1', acknowledge=receipt['token'])
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'][0]['token'], receipt['token'])

    def test_unknown_ack_does_not_clear_pending_message(self):
        receipt = self.send()
        with self.assertRaises(ValueError):
            steering.check(self.c, 'TASK-1', acknowledge=str(uuid.uuid4()))
        self.assertEqual([m['token'] for m in steering.check(self.c, 'TASK-1')['messages']],
                         [receipt['token']])

    def test_new_launch_hides_old_messages_and_rejects_stale_ack(self):
        old = self.send('Old launch constraint.')
        self.launch = {**self.launch, 'id': 'two'}
        state.write(self.c, 'launches', {self.slot: self.launch})
        threads.open_thread(self.c, 'TASK-1', self.slot)
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], [])
        self.assertEqual(self.pending(), {'lanes': []})
        new = self.send('New launch constraint.')
        with self.assertRaises(ValueError):
            steering.check(self.c, 'TASK-1', acknowledge=old['token'])
        messages = steering.check(self.c, 'TASK-1')['messages']
        self.assertEqual([m['token'] for m in messages], [new['token']])
        lane, = self.pending()['lanes']
        self.assertEqual(lane['launch_id'], 'two')
        pending, = lane['messages']
        self.assertGreaterEqual(pending.pop('age_seconds'), 0)
        self.assertEqual(pending, messages[0])

    def test_settled_receipt_in_other_lane_does_not_block_active_attempt(self):
        old_slot = self.slot + '/retired'
        self.c['builder']['slots'].append(old_slot)
        state.write(self.c, 'launches', {
            self.slot: self.launch,
            old_slot: {**self.launch, 'id': 'retired', 'terminal': 'term_old'}})
        ledger = state.read(self.c, 'threads-TASK-1')
        ledger['TASK-1']['attempts']['retired'] = {
            'terminal': 'term_old', 'slot': old_slot, 'settled': True, 'recorded': True}
        state.write(self.c, 'threads-TASK-1', ledger)
        receipt = self.send('Only the new active attempt should receive this.')
        self.assertEqual(self.sent[-1]['--to'], 'term_builder')
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'][0]['token'], receipt['token'])

    def test_reopened_attempt_rejects_old_ack_without_clearing_new_message(self):
        old = self.send('Old attempt constraint.')
        threads.result(self.c, 'TASK-1', 'Settled proof.')
        reopened = threads.reopen(self.c, 'TASK-1', 'main-authorization', 'new requirement')
        self.assertNotEqual(reopened['attempt'], self.opened['attempt'])
        self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], [])
        self.assertEqual(self.pending(), {'lanes': []})
        new = self.send('New attempt constraint.')
        with self.assertRaises(ValueError):
            steering.check(self.c, 'TASK-1', acknowledge=old['token'])
        messages = steering.check(self.c, 'TASK-1')['messages']
        self.assertEqual([m['token'] for m in messages], [new['token']])
        lane, = self.pending()['lanes']
        self.assertEqual(lane['attempt'], reopened['attempt'])
        self.assertEqual(self.sent[-1]['--thread-id'], reopened['thread_id'])

    def test_ambiguous_send_is_retained_pending_and_not_delivered_to_consumer(self):
        self.send_error = ValueError('transport interrupted')
        with self.assertRaisesRegex(ValueError, 'transport interrupted'):
            self.send('Delivery may already have happened.')
        token, record = next(iter(self.attempt()['steering'].items()))
        self.assertEqual(record['status'], 'sending')
        self.assertFalse(record.get('message_id'))
        for _ in range(2):
            lane, = self.pending()['lanes']
            self.assertEqual(lane['messages'][0]['token'], token)
            self.assertEqual(lane['messages'][0]['status'], 'sending')
            self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], [])
        with self.assertRaises(ValueError):
            steering.check(self.c, 'TASK-1', acknowledge=token)
        self.assertEqual(len(self.sent), 1)
        self.send_error = None
        with self.assertRaisesRegex(ValueError, 'ambiguous steering send'):
            self.send('Delivery may already have happened.')
        with self.assertRaisesRegex(ValueError, 'ambiguous steering send'):
            self.send('A different instruction must not bypass reconciliation.')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.attempt()['steering']), 1)
        self.assertEqual(self.attempt()['steering'][token]['status'], 'sending')

    def test_priority_refuses_before_any_native_or_journal_side_effect(self):
        before = state.read(self.c, 'threads-TASK-1')
        with self.as_main(), self.assertRaises(ValueError):
            steering.send(self.c, 'TASK-1', 'Interrupt now.', priority=True)
        self.assertEqual(self.native_calls, [])
        self.assertEqual(self.terminal_calls, [])
        self.assertEqual(state.read(self.c, 'threads-TASK-1'), before)
        self.assertEqual(self.sent, [])

    def test_pending_ignores_launches_outside_configured_slots(self):
        self.send()
        self.c['builder']['slots'] = []
        self.assertEqual(self.pending(), {'lanes': []})

    def test_check_is_local_and_does_not_read_or_consume_unrelated_native_mail(self):
        receipt = self.send()
        self.native_calls.clear()
        for _ in range(2):
            self.assertEqual(steering.check(self.c, 'TASK-1')['messages'][0]['token'], receipt['token'])
        steering.check(self.c, 'TASK-1', acknowledge=receipt['token'])
        self.assertTrue(all(verb == 'run-current' for verb, _ in self.native_calls))
        self.assertEqual(len(self.sent), 1)

    def test_check_cli_emits_one_json_object_per_message_and_empty_after_ack(self):
        first = self.send('First constraint.')
        second = self.send('Second constraint.')
        argv = ['fleet', 'thread', 'check', 'TASK-1']
        with patch.object(cli, 'load', return_value=self.c), patch('sys.argv', argv):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual({record['token'] for record in records}, {first['token'], second['token']})
            steering.check(self.c, 'TASK-1', acknowledge=first['token'])
            steering.check(self.c, 'TASK-1', acknowledge=second['token'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(output.getvalue(), '')

    def test_send_requires_opened_unsettled_attempt(self):
        with self.as_main(), self.assertRaises(ValueError):
            steering.send(self.c, 'TASK-2', 'No attempt was opened.')
        threads.result(self.c, 'TASK-1', 'Settled proof.')
        sends_before = len(self.sent)
        with self.assertRaises(ValueError):
            self.send('This attempt has already settled.')
        self.assertEqual(len(self.sent), sends_before)
        self.assertNotIn('steering', self.attempt())

    def test_send_refuses_ambiguous_authoritative_launch(self):
        other_slot = os.path.join(self.slot, 'other-slot')
        os.mkdir(other_slot)
        self.c['builder']['slots'].append(other_slot)
        other = {**self.launch, 'id': 'two', 'terminal': 'term_other',
                 'worktree': {'id': 'repo::' + other_slot, 'path': other_slot}}
        state.write(self.c, 'launches', {self.slot: self.launch, other_slot: other})
        ledger = state.read(self.c, 'threads-TASK-1')
        ledger['TASK-1']['attempts']['two'] = {
            'terminal': 'term_other', 'slot': other_slot,
            'settled': False, 'recorded': True}
        state.write(self.c, 'threads-TASK-1', ledger)
        with self.assertRaises(ValueError):
            self.send()
        self.assertEqual(self.sent, [])
        self.assertNotIn('steering', self.attempt())

    def test_cli_send_pending_and_explicit_ack(self):
        def invoke(*args):
            output = io.StringIO()
            with patch.object(cli, 'load', return_value=self.c), \
                    patch('sys.argv', ['fleet', 'thread', *args]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            return output.getvalue()
        with self.as_main():
            receipt = json.loads(invoke('send', 'TASK-1', '--body', 'Keep user edits.'))
            pending = json.loads(invoke('pending'))
        self.assertEqual(receipt['status'], 'sent')
        self.assertEqual(pending['lanes'][0]['messages'][0]['token'], receipt['token'])
        invoke('check', 'TASK-1', '--ack', receipt['token'])
        self.assertEqual(invoke('check', 'TASK-1'), '')
        self.assertTrue(self.attempt()['steering'][receipt['token']]['acknowledged_at'])

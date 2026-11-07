import contextlib
import io
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from fleet import cli, state, threads


class ThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.c = {'_state': self.temp.name, 'root': self.temp.name, 'pad_workspace': 'scratch',
                  'builder': {'slots': [self.temp.name]},
                  'coordinator': {'run': 'run_main', 'terminal': 'term_main'}}
        state.write(self.c, 'launches', {self.temp.name: {'id': 'one', 'ref': 'TASK-1', 'terminal': 'term_builder'}})
        self.env = patch.dict(os.environ, ORCA_TERMINAL_HANDLE='term_builder')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.pads = []
        self.native = patch.object(threads, 'request', side_effect=self.request)
        self.native.start()
        self.addCleanup(self.native.stop)
        self.pad = patch.object(threads, 'pad', side_effect=lambda c, r, b: self.pads.append((r, b)))
        self.pad.start()
        self.addCleanup(self.pad.stop)
        self.git = patch.object(threads, 'run', return_value='task-1\n')
        self.git.start()
        self.addCleanup(self.git.stop)
        self.messages = []
        self.sent = []

    def request(self, c, verb, *args, **kwargs):
        if verb == 'run-current':
            return {'run': None}
        if verb == 'send':
            self.sent.append(args)
            return {'message': {'id': 'q' + str(len(self.sent))}}
        if verb == 'inbox':
            return {'messages': self.messages, 'count': len(self.messages)}
        self.fail('thread must never consume native mail')

    def open(self, ref='TASK-1'):
        return threads.open_thread(self.c, ref, self.temp.name)

    def answer(self, q, ident='reply', **changes):
        return {'id': ident, 'thread_id': q['thread_id'], 'from_handle': 'term_main',
                'to_handle': 'term_builder', 'type': 'status', 'body': 'approved', **changes}

    def test_late_previous_task_reply_does_not_resolve_new_task(self):
        first = self.open()
        old = threads.question(self.c, 'TASK-1', 'old question')
        state.write(self.c, 'launches', {self.temp.name: {'id': 'two', 'ref': 'TASK-2', 'terminal': 'term_builder'}})
        second = self.open('TASK-2')
        new = threads.question(self.c, 'TASK-2', 'new question')
        self.assertNotEqual(first['thread_id'], second['thread_id'])
        self.messages = [self.answer(old), self.answer(new, 'new-reply')]
        result = threads.wait(self.c, 'TASK-2', 1)
        self.assertEqual(result['reply']['id'], 'new-reply')
        self.assertEqual({m['id'] for m in result['messages']}, {'reply', 'new-reply'})
        with self.assertRaisesRegex(ValueError, 'no unique current'):
            threads.wait(self.c, 'TASK-1', 1)

    def test_reply_is_durable_before_pad_retry_and_never_consumed(self):
        self.open()
        q = threads.question(self.c, 'TASK-1', 'approve?')
        self.messages = [self.answer(q), {'id': 'report', 'type': 'status', 'body': 'build done'}]
        with patch.object(threads, 'pad', side_effect=ValueError('pad unavailable')):
            with self.assertRaisesRegex(ValueError, 'pad unavailable'):
                threads.wait(self.c, 'TASK-1', 1)
        self.messages = []  # Native terminal history is no longer needed for this reply.
        result = threads.wait(self.c, 'TASK-1', 1)
        self.assertEqual(result['reply']['body'], 'approved')
        self.assertEqual({m['id'] for m in result['messages']}, {'reply', 'report'})
        self.assertTrue(any('Decision: approved' in body for _, body in self.pads))

    def test_wait_retries_failed_question_pad_write_before_decision(self):
        self.open()
        with patch.object(threads, 'pad', side_effect=ValueError('pad unavailable')):
            with self.assertRaisesRegex(ValueError, 'pad unavailable'):
                threads.question(self.c, 'TASK-1', 'original proposal and risk')
        _, task, _, attempt = threads.current(self.c, 'TASK-1', 'term_builder')
        q = attempt['questions'][attempt['latest_question']]
        self.messages = [self.answer(q)]
        self.pads.clear()
        threads.wait(self.c, 'TASK-1', 1)
        self.assertEqual(len(self.pads), 2)
        self.assertIn('original proposal and risk', self.pads[0][1])
        self.assertIn('Decision: approved', self.pads[1][1])
        self.assertEqual(len(self.sent), 1)

    def test_only_latest_question_and_expected_sender_authorize_work(self):
        self.open()
        old = threads.question(self.c, 'TASK-1', 'first?')
        self.messages = [self.answer(old)]
        threads.wait(self.c, 'TASK-1', 1)
        new = threads.question(self.c, 'TASK-1', 'second?')
        self.messages = [self.answer(old, 'late'), self.answer(new, 'spoof', from_handle='other'),
                         self.answer(new, 'real')]
        self.assertEqual(threads.wait(self.c, 'TASK-1', 1)['reply']['id'], 'real')

    def test_two_lane_waits_do_not_abort_each_other_during_network_io(self):
        second_slot = os.path.join(self.temp.name, 'second-slot')
        os.mkdir(second_slot)
        self.c['builder']['slots'].append(second_slot)
        launches = state.read(self.c, 'launches')
        launches[second_slot] = {'id': 'two', 'ref': 'TASK-2', 'terminal': 'term_second'}
        state.write(self.c, 'launches', launches)
        caller = threading.local()
        with patch.object(threads, 'identity', side_effect=lambda c: caller.value):
            caller.value = 'term_builder'
            self.open()
            first = threads.question(self.c, 'TASK-1', 'first lane decision')
            caller.value = 'term_second'
            threads.open_thread(self.c, 'TASK-2', second_slot)
            second = threads.question(self.c, 'TASK-2', 'second lane decision')
            messages = [self.answer(first, 'first-answer'),
                        self.answer(second, 'second-answer', to_handle='term_second')]
            in_flight = threading.Barrier(2)
            def native(c, verb, *args, **kwargs):
                self.assertEqual(verb, 'inbox')
                in_flight.wait(timeout=5)
                return {'messages': messages, 'count': len(messages)}
            def lane(ref, terminal):
                caller.value = terminal
                return threads.wait(self.c, ref, 1)
            with patch.object(threads, 'request', side_effect=native), ThreadPoolExecutor(2) as pool:
                one = pool.submit(lane, 'TASK-1', 'term_builder')
                two = pool.submit(lane, 'TASK-2', 'term_second')
                self.assertEqual(one.result(timeout=10)['reply']['id'], 'first-answer')
                self.assertEqual(two.result(timeout=10)['reply']['id'], 'second-answer')

    def test_settled_attempt_cannot_accept_old_reply_or_reopen(self):
        opened = self.open()
        threads.result(self.c, 'TASK-1', 'published proof')
        with self.assertRaisesRegex(ValueError, 'settled'):
            self.open()
        with self.assertRaisesRegex(ValueError, 'no unique current'):
            threads.wait(self.c, 'TASK-1', 1)
        state.write(self.c, 'launches', {self.temp.name: {'id': 'two', 'ref': 'TASK-1', 'terminal': 'term_builder'}})
        self.assertEqual(self.open()['thread_id'], opened['thread_id'])

    def test_question_cli_confirms_send_and_refuses_pending_id_without_resending(self):
        self.open()
        argv = ['fleet', 'thread', 'question', 'TASK-1', '--body', 'approve?']
        output = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), patch('sys.argv', argv):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            receipt = json.loads(output.getvalue())
            self.assertTrue(receipt['sent'])
            self.assertEqual(receipt['message_id'], 'q1')
            self.assertIn(receipt['thread_id'], self.sent[0])
            self.assertIn('no separate direct send', receipt['notice'])
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(cli.main(), 2)
            self.assertIn(receipt['message_id'], error.getvalue())
            self.assertIn(receipt['thread_id'], error.getvalue())
        self.assertEqual(len(self.sent), 1)

    def test_pending_or_ambiguous_question_is_never_resent(self):
        self.open()
        with patch.object(threads, 'request', side_effect=[{'run': None}, ValueError('transport')]):
            with self.assertRaisesRegex(ValueError, 'transport'):
                threads.question(self.c, 'TASK-1', 'approve?')
        with self.assertRaisesRegex(ValueError, 'pending question'):
            threads.question(self.c, 'TASK-1', 'approve?')
        with self.assertRaisesRegex(ValueError, 'ambiguous question'):
            threads.wait(self.c, 'TASK-1', 1)
        with self.assertRaisesRegex(ValueError, 'pending decision'):
            threads.result(self.c, 'TASK-1', 'not actually done')

    def test_rebinding_builder_refuses_thread_access(self):
        self.open()
        with patch.object(threads, 'request', return_value={'run': {'id': 'scratch'}}):
            with self.assertRaisesRegex(ValueError, 'must not be Run-bound'):
                threads.question(self.c, 'TASK-1', 'approve?')

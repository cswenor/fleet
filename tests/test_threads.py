import contextlib
import io
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from fleet import cli, mailbox, state, threads


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


class ReadyTests(unittest.TestCase):
    open = ThreadTests.open
    request = ThreadTests.request

    def setUp(self):
        ThreadTests.setUp(self)
        from fleet import restart
        self.launch = state.read(self.c, 'launches')[self.temp.name]
        self.launch['at'] = 1767225599
        self.launch['terminal_incarnation_id'] = 'builder-incarnation'
        self.launch['worktree'] = {'id': 'repo::' + self.temp.name, 'path': self.temp.name}
        self.terminal = {'handle': 'term_builder', 'incarnationId': 'builder-incarnation',
                         'worktreeId': self.launch['worktree']['id'],
                         'connected': True, 'writable': True, 'orphaned': False}
        self.live = patch.object(threads, 'orca', create=True,
                                 side_effect=lambda *args: {'terminal': self.terminal})
        self.live.start()
        self.addCleanup(self.live.stop)
        state.write(self.c, 'launches', {self.temp.name: self.launch})
        self.prs = [{'title': 'TASK-1 completed', 'body': '', 'url': 'https://example/pr/1',
                     'mergedAt': '2026-01-01T00:00:00Z'}]
        self.comments = [{'id': 'handoff-1', 'body': 'TASK-1 handoff: implement the next CLI piece',
                          'created_at': '2026-01-01T00:00:01Z'}]
        self.proof = patch.object(restart, '_merged_prs', side_effect=lambda *a: self.prs)
        self.proof.start()
        self.addCleanup(self.proof.stop)
        self.pad_reads = patch.object(restart, 'json_run',
                                     side_effect=lambda argv, cwd: self.comments if 'comments' in argv
                                     else {'fields': {'status': 'done'}})
        self.pad_reads.start()
        self.addCleanup(self.pad_reads.stop)
        self.open()
        threads.result(self.c, 'TASK-1', 'published')
        self.sent.clear()
        self.pads.clear()

    def test_ready_cli_after_settle_is_idempotent_and_records_message_on_pad(self):
        argv = ['fleet', 'thread', 'ready', 'TASK-2', '--body', 'Ready to retire.']
        receipts = []
        with patch.object(cli, 'load', return_value=self.c), patch('sys.argv', argv):
            for _ in range(2):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 0)
                receipts.append(json.loads(output.getvalue()))
        self.assertEqual(receipts[0]['message_id'], receipts[1]['message_id'])
        self.assertEqual(len(self.sent), 1)
        flags = dict(zip(self.sent[0][::2], self.sent[0][1::2]))
        self.assertEqual(flags['--type'], 'question')
        self.assertEqual(flags['--to'], 'run:run_main')
        self.assertEqual(flags['--subject'], 'TASK-1: FRESH-READY TASK-2')
        self.assertIn('TASK-2 comment handoff-1', flags['--body'])
        self.assertEqual(len(self.pads), 1)
        self.assertEqual(self.pads[0][0], 'TASK-1')
        self.assertIn('Ready report: ' + receipts[0]['message_id'], self.pads[0][1])
        classified, _ = mailbox.classify(
            [{'id': receipts[0]['message_id'], 'type': flags['--type'],
              'subject': flags['--subject'], 'body': flags['--body'],
              'from_handle': 'term_builder'}], self.c['coordinator'])
        self.assertEqual(classified[0]['classification'], 'builder_decision_request')

    def test_existing_status_ready_receipt_remains_valid_without_resend(self):
        ledger = state.read(self.c, 'threads-TASK-1')
        ledger['TASK-1']['attempts']['one']['ready'] = {
            'next_ref': 'TASK-2', 'message_id': 'old-status', 'recorded': True,
            'status': 'sent'}
        state.write(self.c, 'threads-TASK-1', ledger)
        self.assertEqual(threads.ready(self.c, 'TASK-2')['message_id'], 'old-status')
        self.assertEqual(self.sent, [])
        self.assertEqual(self.pads, [])

    def test_reopen_continuation_is_idempotent_and_isolates_old_decisions(self):
        first = state.read(self.c, 'threads-TASK-1')['TASK-1']['attempts']['one']
        reopened = threads.reopen(self.c, 'TASK-1', 'msg_approval', 'Continue implementation')
        self.assertNotEqual(reopened['attempt'], 'one')
        self.assertEqual(reopened['previous_attempt'], 'one')
        self.assertFalse(reopened['settled'])
        repeated = threads.reopen(self.c, 'TASK-1', 'msg_approval')
        self.assertEqual(reopened['attempt'], repeated['attempt'])
        self.assertEqual(len(self.sent), 1)
        self.assertIn('authorized by msg_approval', self.pads[0][1])
        q = threads.question(self.c, 'TASK-1', 'new decision')
        self.messages = [ThreadTests.answer(self, q)]
        self.assertEqual(threads.wait(self.c, 'TASK-1', 1)['attempt'], reopened['attempt'])
        threads.result(self.c, 'TASK-1', 'continuation complete')
        ledger = state.read(self.c, 'threads-TASK-1')['TASK-1']
        self.assertEqual(ledger['history']['one'], first)
        next_attempt = threads.reopen(self.c, 'TASK-1', 'msg_second')
        self.assertEqual(next_attempt['previous_attempt'], reopened['attempt'])
        # An old authorization cannot reopen again or select its historical attempt.
        with self.assertRaisesRegex(ValueError, 'already used'):
            threads.reopen(self.c, 'TASK-1', 'msg_approval')

    def test_reopen_refuses_unsettled_or_missing_authorization(self):
        with self.assertRaisesRegex(ValueError, 'authorization'):
            threads.reopen(self.c, 'TASK-1', ' ')
        threads.reopen(self.c, 'TASK-1', 'msg_approval')
        with self.assertRaisesRegex(ValueError, 'unsettled'):
            threads.reopen(self.c, 'TASK-1', 'msg_different')

    def test_reopen_refuses_changed_launch_terminal_incarnation_or_binding(self):
        for changes in ({'incarnationId': 'replacement'}, {'worktreeId': 'other'},
                        {'connected': False}, {'writable': False}, {'orphaned': True}):
            with self.subTest(changes=changes), patch.dict(self.terminal, changes):
                with self.assertRaisesRegex(ValueError, 'terminal ownership'):
                    threads.reopen(self.c, 'TASK-1', 'msg_approval')
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='other'):
            with self.assertRaisesRegex(ValueError, 'authoritative'):
                threads.reopen(self.c, 'TASK-1', 'msg_approval')
        with patch.object(threads, 'request', return_value={'run': {'id': 'bound'}}):
            with self.assertRaisesRegex(ValueError, 'Run-bound'):
                threads.reopen(self.c, 'TASK-1', 'msg_approval')
        state.write(self.c, 'launches', {self.temp.name: dict(self.launch, id='replacement')})
        with self.assertRaisesRegex(ValueError, 'settled'):
            threads.reopen(self.c, 'TASK-1', 'msg_approval')
        self.assertEqual(self.sent, [])

    def test_reopen_pad_retry_never_resends_and_ambiguous_send_blocks_questions(self):
        with patch.object(threads, 'pad', side_effect=ValueError('pad unavailable')):
            with self.assertRaisesRegex(ValueError, 'pad unavailable'):
                threads.reopen(self.c, 'TASK-1', 'msg_approval')
        threads.reopen(self.c, 'TASK-1', 'msg_approval')
        self.assertEqual(len(self.sent), 1)
        threads.result(self.c, 'TASK-1', 'second boundary')
        with patch.object(threads, 'request', side_effect=[{'run': None}, ValueError('transport')]):
            with self.assertRaisesRegex(ValueError, 'transport'):
                threads.reopen(self.c, 'TASK-1', 'msg_second')
        with self.assertRaisesRegex(ValueError, 'ambiguous reopen'):
            threads.reopen(self.c, 'TASK-1', 'msg_second')
        with self.assertRaisesRegex(ValueError, 'reopen'):
            threads.question(self.c, 'TASK-1', 'not yet notified')

    def test_ready_after_reopen_requires_settlement_and_post_launch_merge(self):
        continuation = threads.reopen(self.c, 'TASK-1', 'msg_approval')
        with self.assertRaisesRegex(ValueError, 'settled'):
            threads.ready(self.c, 'TASK-1')
        threads.result(self.c, 'TASK-1', 'continued')
        self.prs[0]['mergedAt'] = '2025-12-31T23:59:58Z'
        with self.assertRaisesRegex(ValueError, 'merged after this launch'):
            threads.ready(self.c, 'TASK-1')
        self.prs[0]['mergedAt'] = '2026-01-01T00:00:00Z'
        self.assertEqual(threads.ready(self.c, 'TASK-1')['handoff'], 'handoff-1')
        self.assertIn('Attempt: ' + continuation['attempt'] + '\n', self.pads[-1][1])
        self.assertIn('Fleet task thread: ' + continuation['thread_id'] + '\n', self.pads[-1][1])

    def test_pad_failure_retries_comment_not_native_send(self):
        with patch.object(threads, 'pad', side_effect=ValueError('pad unavailable')):
            with self.assertRaisesRegex(ValueError, 'pad unavailable'):
                threads.ready(self.c, 'TASK-2')
        report = threads.ready(self.c, 'TASK-2')
        self.assertEqual(len(self.sent), 1)
        self.assertIn(report['message_id'], self.pads[0][1])

    def test_ambiguous_native_send_never_resends(self):
        with patch.object(threads, 'request', side_effect=[{'run': None}, ValueError('transport')]):
            with self.assertRaisesRegex(ValueError, 'transport'):
                threads.ready(self.c, 'TASK-2')
        with self.assertRaisesRegex(ValueError, 'ambiguous ready send'):
            threads.ready(self.c, 'TASK-2')
        self.assertEqual(self.sent, [])
        self.assertEqual(self.pads, [])

    def test_same_ref_requires_new_merge_and_later_concrete_handoff(self):
        self.assertEqual(threads.ready(self.c, 'TASK-1')['handoff'], 'handoff-1')

    def test_old_publication_and_missing_or_marker_handoff_refuse_for_any_next_ref(self):
        for next_ref in ('TASK-1', 'TASK-2'):
            with self.subTest(next_ref=next_ref, boundary='old merge'):
                self.prs[0]['mergedAt'] = '2025-12-31T23:59:58Z'
                with self.assertRaisesRegex(ValueError, 'merged after this launch'):
                    threads.ready(self.c, next_ref)
            self.prs[0]['mergedAt'] = '2026-01-01T00:00:00Z'
            for body, at in (('TASK-1 handoff:', '2026-01-01T00:00:01Z'),
                             ('TASK-1 handoff: next piece', '2026-01-01T00:00:00Z')):
                with self.subTest(next_ref=next_ref, body=body, at=at):
                    self.comments[0].update(body=body, created_at=at)
                    with self.assertRaisesRegex(ValueError, 'concrete handoff on ' + next_ref):
                        threads.ready(self.c, next_ref)
        self.assertEqual(self.sent, [])

    def test_ready_requires_settlement_and_current_primary_launch(self):
        ledger = state.read(self.c, 'threads-TASK-1')
        ledger['TASK-1']['attempts']['one']['settled'] = False
        state.write(self.c, 'threads-TASK-1', ledger)
        with self.assertRaisesRegex(ValueError, 'settled'):
            threads.ready(self.c, 'TASK-2')
        state.write(self.c, 'launches', {self.temp.name: dict(self.launch, id='replacement')})
        with self.assertRaisesRegex(ValueError, 'settled'):
            threads.ready(self.c, 'TASK-2')
        self.assertEqual(self.sent, [])

    def test_report_cannot_change_next_ref_after_delivery(self):
        threads.ready(self.c, 'TASK-2')
        with self.assertRaisesRegex(ValueError, 'different next ref'):
            threads.ready(self.c, 'TASK-3')
        self.assertEqual(len(self.sent), 1)

    def test_changed_or_unknown_terminal_ownership_refuses_ready(self):
        original = dict(self.terminal)
        for change in ({'incarnationId': 'replacement'}, {'incarnationId': None},
                       {'worktreeId': 'another-worktree'}, {'connected': False},
                       {'writable': False}, {'orphaned': True}):
            with self.subTest(change=change):
                self.terminal = {**original, **change}
                with self.assertRaisesRegex(ValueError, 'terminal ownership'):
                    threads.ready(self.c, 'TASK-2')
                self.assertEqual(self.sent, [])
                self.assertEqual(self.pads, [])

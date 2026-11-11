import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fleet import health, launcher, restart, state, supervisor, threads


class BuilderWakeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.c = {'_state': tmp.name, '_path': '/private/fleet.yml', 'builder': {'slots': ['/lane']},
                  'coordinator': {'terminal': 'main', 'run': 'run_test'}}
        self.launch = {'id': 'launch', 'ref': 'TASK-1', 'terminal': 'builder',
                       'terminal_incarnation_id': 'one',
                       'worktree': {'id': 'tree', 'path': '/lane'}}
        state.write(self.c, 'launches', {'/lane': self.launch})
        self.token = 'fleet-task/launch/question'
        self.q = {'thread_id': self.token, 'message_id': 'msg_question',
                  'subject': 'TASK-1: decision', 'status': 'pending'}
        self.attempt = {'slot': '/lane', 'terminal': 'builder', 'settled': False,
                        'recorded': True, 'latest_question': self.token,
                        'questions': {self.token: self.q}}
        self.save_attempt()
        self.terminal = {'handle': 'builder', 'incarnationId': 'one', 'worktreeId': 'tree',
                         'tabId': 'tab', 'leafId': 'leaf', 'connected': True,
                         'writable': True, 'agentIdentity': 'omp'}
        captured = json.loads((Path(__file__).parent / 'fixtures/orca-main-ui.json').read_text())
        self.screen = {**captured['email'], 'handle': 'builder', 'source': 'screen'}
        self.agent = {'paneKey': 'tab:leaf', 'agentType': 'omp', 'state': 'done',
                      'programStatus': None}
        self.reply = {'id': 'msg_reply', 'run_id': 'run_test', 'from_handle': 'main',
                      'to_handle': 'builder', 'type': 'status', 'thread_id': self.token,
                      'subject': 'Re: TASK-1: decision', 'created_at': '2026-11-11T00:00:00Z'}
        self.messages = [self.reply]
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp()
        self.ledger = {}
        self.sends = []
        self.binding = {'run': None}
        self.fail_send = False
        for module in (health, supervisor):
            p = patch.object(module, 'orca', side_effect=self.native)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(threads, 'request', side_effect=self.native)
        p.start()
        self.addCleanup(p.stop)

    def save_attempt(self):
        state.write(self.c, 'threads-TASK-1', {
            'TASK-1': {'id': 'fleet-task', 'attempts': {'launch': self.attempt}}})

    def native(self, c, *args):
        # Real flock probe on every native operation: a supervisor cycle must not
        # keep unrelated launches/checkpoints blocked while waiting on Orca.
        with state.lock(c, 'launcher', operation='test native checkpoint'):
            pass
        if args[0] == 'inbox':
            self.assertEqual(args, ('inbox', '--terminal', 'builder', '--limit', '1000'))
            return {'messages': copy.deepcopy(self.messages), 'count': len(self.messages)}
        if args[:2] == ('orchestration', 'run-current'):
            return self.binding
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.terminal}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': self.screen}
        if args[:2] == ('worktree', 'ps'):
            return {'worktrees': [{'worktreeId': 'tree', 'agents': [self.agent]}]}
        if args[:2] == ('terminal', 'send'):
            attempts = state.read(c, 'supervisor')['builder_wakes']['/lane']['attempts']
            attempt = attempts[supervisor.digest([self.q['message_id'], self.reply['id']])]
            self.assertEqual(attempt['status'], 'attempted')
            self.sends.append(args)
            if self.fail_send:
                raise RuntimeError('ambiguous')
            return {'send': {'accepted': True}}
        self.fail(args)

    def wake(self, seconds=0, deliver=True):
        return supervisor.wake_builders(self.c, self.ledger, self.now + seconds, deliver)

    def test_idle_reply_wakes_once_with_exact_fixed_line_even_after_reload(self):
        self.assertEqual(self.wake(), [])
        self.assertEqual(self.sends, [(
            'terminal', 'send', '--terminal', 'builder', '--text',
            'fleet: Main replied to your pending decision msg_question on TASK-1; '
            'run fleet --config /private/fleet.yml thread check TASK-1, then '
            'fleet --config /private/fleet.yml thread wait TASK-1 --timeout 600 '
            'to read and record the decision before continuing.', '--enter')])
        self.ledger = state.read(self.c, 'supervisor')
        self.wake(10000)
        self.assertEqual(len(self.sends), 1)
        attempt, = self.ledger['builder_wakes']['/lane']['attempts'].values()
        self.assertEqual(attempt['reply_id'], 'msg_reply')
        self.assertEqual(attempt['proof']['composer'], 'empty')
        self.assertEqual(attempt['status'], 'sent')

    def test_no_reply_or_unrelated_native_mail_never_wakes(self):
        self.messages = []
        self.wake(10000)
        for field in ('thread_id', 'from_handle', 'to_handle', 'run_id', 'type', 'subject', 'id'):
            with self.subTest(field=field):
                self.messages = [{**self.reply, field: 'unrelated'}]
                self.assertEqual(self.wake(10000), [])
        self.assertEqual(self.sends, [])
        self.assertNotIn('builder_wakes', self.ledger)

    def test_nonidle_proof_relays_once_after_reply_age_but_can_wake_when_safe(self):
        for tail in ([' π > ◒ Sol>', '╰─ draft'], [' ⠋ Working > ◒ Sol>', '╰─'],
                     [' π > ◒ Sol> ⚙', '╰─'], [],
                     ['╭─ Ask ─╮', '│ Question │', '╰──────╯']):
            with self.subTest(tail=tail), patch.dict(self.screen, tail=tail):
                self.ledger = {}
                self.assertEqual(self.wake(899), [])
                event, = self.wake(900)
                self.assertEqual(event['type'], 'status')
                self.assertEqual(event['recipients'], ['run:run_test'])
                self.assertEqual(event['subject'], 'TASK-1 builder_reply_unwakeable')
                self.assertEqual(self.wake(1000), [])
        self.assertEqual(self.sends, [])
        self.wake(1001)
        self.assertEqual(len(self.sends), 1)

    def test_identity_binding_and_agent_uncertainty_never_permits_input(self):
        for field, value in [('incarnationId', 'replacement'), ('worktreeId', 'other'),
                             ('connected', False), ('writable', False), ('agentIdentity', 'bash')]:
            with self.subTest(field=field), patch.dict(self.terminal, {field: value}):
                self.wake(1000)
        for binding in ({}, {'run': {'id': 'run_test'}}):
            self.binding = binding
            self.wake(1000)
        self.binding = {'run': None}
        for field, value in [('state', 'working'), ('interrupted', True), ('parentPaneKey', 'parent')]:
            with self.subTest(field=field), patch.dict(self.agent, {field: value}):
                self.wake(1000)
        self.assertEqual(self.sends, [])

    def test_disabled_delivery_and_optout_never_input(self):
        self.wake(deliver=False)
        self.c['coordinator']['builder_wake'] = False
        self.wake(1000)
        self.assertEqual(self.sends, [])

    def test_ambiguous_send_never_retried(self):
        self.fail_send = True
        self.wake()
        self.ledger = state.read(self.c, 'supervisor')
        self.wake(10000)
        self.assertEqual(len(self.sends), 1)
        attempt, = self.ledger['builder_wakes']['/lane']['attempts'].values()
        self.assertEqual(attempt['status'], 'unknown')

    def test_settled_recorded_ambiguous_or_replaced_attempts_do_not_wake(self):
        for change in ({'settled': True}, {'recorded': False}, {'terminal': 'other'}):
            with patch.dict(self.attempt, change):
                self.save_attempt()
                self.wake(1000)
        for change in ({'reply_recorded': True}, {'status': 'sending'}):
            with patch.dict(self.q, change):
                self.save_attempt()
                self.wake(1000)
        self.save_attempt()
        state.write(self.c, 'launches', {'/lane': {**self.launch, 'id': 'new-launch'}})
        self.wake(1000)
        self.assertEqual(self.sends, [])

    def test_new_decision_obeys_lane_cooldown_then_wakes(self):
        self.wake()
        token = 'fleet-task/launch/second'
        self.q.update(thread_id=token, message_id='msg_second')
        self.attempt.update(latest_question=token, questions={token: self.q})
        self.save_attempt()
        self.reply.update(thread_id=token, id='msg_second_reply')
        self.wake(599)
        self.assertEqual(len(self.sends), 1)
        self.wake(600)
        self.assertEqual(len(self.sends), 2)

    def test_launcher_or_thread_contention_withholds_input(self):
        with state.lock(self.c, 'launcher'):
            self.wake(1000)
        with state.lock(self.c, 'threads-TASK-1'):
            self.wake(1000)
        self.assertEqual(self.sends, [])

    def test_busy_slot_skips_without_native_io_then_retries(self):
        with state.slot_lock(self.c, '/lane'), patch.object(
                threads, 'request', side_effect=AssertionError('busy slot native I/O')):
            self.wake()
        self.assertEqual(self.sends, [])
        self.wake(1)
        self.assertEqual(len(self.sends), 1)

    def test_wake_lease_prevents_retirement_and_reuse_even_at_send(self):
        original = self.native
        def native(c, *args):
            if args[:2] == ('terminal', 'send'):
                with patch.object(restart.sys, 'platform', 'linux'), self.assertRaisesRegex(ValueError, 'slot-'):
                    restart.restart(c, 'TASK-2', '/lane', 'TASK-1')
                with patch.object(launcher.snapshot, 'collect', return_value={
                        'errors': [], 'inventory': {}}), patch.object(
                        launcher.snapshot, 'may_start', return_value={'may_start': True, 'reasons': []}), patch.object(
                        launcher, 'orca', return_value={}), patch.object(
                        launcher.dispatcher, 'check_launch', return_value={}):
                    with self.assertRaisesRegex(ValueError, 'slot-'):
                        launcher.launch(c, 'TASK-2', '/lane')
            return original(c, *args)
        with patch.object(supervisor, 'orca', side_effect=native):
            self.wake()
        self.assertEqual(len(self.sends), 1)

    def test_current_carried_question_is_woken_but_unapproved_ref_is_not(self):
        self.launch['ref'] = 'TASK-2'
        state.write(self.c, 'launches', {'/lane': self.launch})
        primary = {'TASK-2': {'id': 'fleet-primary', 'attempts': {
            'launch': {'settled': False}}}}
        state.write(self.c, 'threads-TASK-2', primary)
        self.wake()
        self.assertEqual(self.sends, [])
        state.write(self.c, 'carries', {'launch': {'TASK-1': {
            'ref': 'TASK-1', 'slot': '/lane', 'launch_id': 'launch'}}})
        self.wake()
        self.assertEqual(len(self.sends), 1)

    def test_continuation_matches_its_own_unique_question_not_archived_attempt(self):
        token = 'fleet-task/launch/continued/question'
        self.attempt.update(attempt_id='launch/continued', thread_id='fleet-task/launch/continued',
                            latest_question=token, questions={token: {**self.q, 'thread_id': token}})
        self.save_attempt()
        self.wake()
        self.assertEqual(self.sends, [])
        self.reply['thread_id'] = token
        self.wake()
        self.assertEqual(len(self.sends), 1)

    def test_retirement_or_reconciliation_blocks_even_if_pane_looks_idle(self):
        state.write(self.c, 'retirements', {'/lane': {'launch_id': 'launch'}})
        self.wake()
        state.write(self.c, 'retirements', {})
        state.write(self.c, 'reconciliations', {'launch': {'launch_id': 'launch'}})
        self.wake()
        self.assertEqual(self.sends, [])

    def test_identity_change_during_screen_read_withholds_input(self):
        original = self.native

        def replacing(c, *args):
            result = original(c, *args)
            if args[:2] == ('terminal', 'read'):
                self.terminal = {**self.terminal, 'incarnationId': 'replacement'}
            return copy.deepcopy(result)

        with patch.object(health, 'orca', side_effect=replacing):
            self.wake(1000)
        self.assertEqual(self.sends, [])

    def test_launch_replaced_during_native_proof_never_wakes_old_builder(self):
        original = self.native
        for field, value in (('id', 'replacement'), ('terminal', 'other'),
                             ('terminal_incarnation_id', 'replacement')):
            with self.subTest(field=field):
                state.write(self.c, 'launches', {'/lane': self.launch})
                self.ledger = {}
                def replacing(c, *args):
                    result = original(c, *args)
                    if args[:2] == ('terminal', 'read'):
                        state.write(c, 'launches', {'/lane': {**self.launch, field: value}})
                    return result
                with patch.object(health, 'orca', side_effect=replacing):
                    self.wake(1000)
                self.assertEqual(self.sends, [])

    def test_unproven_config_path_never_inputs(self):
        for path in (None, '', 'relative.yml', '/private/fleet\nmalicious.yml'):
            with self.subTest(path=path):
                self.c['_path'] = path
                self.ledger = {}
                event, = self.wake(1000)
                self.assertIn('config-path proof unavailable', event['body'])
                self.assertEqual(self.sends, [])

    def test_config_path_is_shell_quoted_in_both_required_commands(self):
        self.c['_path'] = "/private/project's fleet.yml"
        self.wake()
        line = self.sends[0][self.sends[0].index('--text') + 1]
        self.assertEqual(line,
            'fleet: Main replied to your pending decision msg_question on TASK-1; run '
            """fleet --config '/private/project'"'"'s fleet.yml' thread check TASK-1, then """
            """fleet --config '/private/project'"'"'s fleet.yml' thread wait TASK-1 --timeout 600 """
            'to read and record the decision before continuing.')

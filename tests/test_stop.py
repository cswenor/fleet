import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from fleet import cli, config, state, steering
from fleet import stop as emergency


class StopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Config loading resolves macOS /var -> /private/var; receipts must agree.
        self.slot = str(Path(self.temp.name).resolve())
        self.c = {'_state': self.slot, 'root': self.slot, 'builder': {'slots': [self.slot]},
                  'coordinator': {'run': 'main', 'terminal': 'main'}}
        self.launch = {'id': 'launch', 'ref': 'TASK-1', 'terminal': 'builder',
                       'terminal_incarnation_id': 'incarnation', 'session_dir': self.slot,
                       'worktree': {'id': 'tree', 'path': self.slot}}
        self.attempt = {'settled': False, 'recorded': True, 'slot': self.slot, 'terminal': 'builder'}
        state.write(self.c, 'launches', {self.slot: self.launch})
        state.write(self.c, 'threads-TASK-1', {'TASK-1': {'id': 'thread', 'attempts': {'launch': self.attempt}}})
        self.log = Path(self.slot) / 'session.jsonl'
        self.log.write_text(json.dumps({'type': 'session', 'id': 'session', 'cwd': self.slot}) + '\n')
        self.terminal = {'handle': 'builder', 'connected': True, 'writable': True, 'orphaned': False,
                         'incarnationId': 'incarnation', 'worktreeId': 'tree', 'agentIdentity': 'omp',
                         'tabId': 'tab', 'leafId': 'leaf'}
        self.agent = {'agentType': 'omp', 'paneKey': 'tab:leaf', 'state': 'working'}
        self.ui = 'working'
        self.inputs = []
        self.native = []
        self.escape_error = False
        self.stuck = False
        self.clear_stuck = False
        self.process = {'pid': 12, 'start': '15'}
        self.lease = {'held': ['session'], 'unknown': []}
        self.inventory_extra = {}
        self.env = patch.dict(os.environ, ORCA_TERMINAL_HANDLE='main')
        self.env.start()
        self.addCleanup(self.env.stop)
        for module in (emergency, steering):
            p = patch.object(module, 'orca', side_effect=self.orca, create=True)
            p.start()
            self.addCleanup(p.stop)
        from fleet import threads
        p = patch.object(threads, 'orca', side_effect=self.orca)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(steering, 'request', side_effect=self.request)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(threads, 'request', side_effect=self.request)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(emergency.restart, '_foreground', side_effect=lambda *a: dict(self.process))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(emergency, 'json_run', side_effect=lambda *a: dict(self.lease))
        p.start()
        self.addCleanup(p.stop)

    def request(self, c, verb, *args):
        if verb == 'run-current':
            return {'run': {'id': 'main', 'coordinator_handle': 'main'} if args[-1] == 'main' else None}
        self.native.append(args)
        return {'message': {'id': 'message'}}

    def orca(self, c, *args):
        if args[:2] == ('terminal', 'show'):
            return {'terminal': dict(self.terminal)}
        if args[:2] == ('worktree', 'ps'):
            return {'worktrees': [{'worktreeId': 'tree', 'agents': [dict(self.agent)]}], **self.inventory_extra}
        if args[:2] == ('terminal', 'read'):
            tails = {'working': [' ⠋ > ◑ Sol > working', '╰─'],
                     'idle': [' π > ◑ Sol > idle', '╰─'],
                     'draft': [' π > ◑ Sol > idle', '╰─ queued one', '   queued two'],
                     # Bottom rows captured from real OMP 18.18.6 mid-tool.
                     'console': ['│⎋ force-kill · input forwarded to PTY                                                                                 │',
                                 '╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯'],
                     'unknown': ['clipped screen']}
            return {'terminal': {'source': 'screen', 'tail': tails[self.ui]}}
        self.assertEqual(args[:2], ('terminal', 'send'))
        text = args[args.index('--text') + 1]
        self.inputs.append((text, '--enter' in args))
        if text == '\x1b':
            if self.escape_error:
                raise RuntimeError('transport lost')
            if not self.stuck:
                self.agent['state'] = 'idle'
                self.ui = 'draft'
        elif text == '\x03':
            if not self.clear_stuck:
                self.ui = 'idle'
        return {'send': {'accepted': True, 'bytesWritten': len(text.encode())}}

    def run_stop(self, **kwargs):
        return emergency.stop(self.c, 'TASK-1', 'Wrong task', **kwargs)

    def receipt(self):
        records = state.read(self.c, 'threads-TASK-1')['TASK-1']['attempts']['launch']['steering']
        return next(iter(records.values()))

    def test_stop_clears_multiline_draft_and_persists_unacked_instruction(self):
        receipt = self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False), ('\x03', False), (receipt['body'], True)])
        self.assertEqual(receipt['stop_phase'], 'submitted')
        self.assertEqual(receipt['status'], 'sent')
        self.assertEqual(receipt['message_id'], 'message')
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            messages = steering.check(self.c, 'TASK-1')['messages']
            self.assertEqual(messages[0]['body'], receipt['body'])
            steering.check(self.c, 'TASK-1', receipt['token'])
            self.assertEqual(steering.check(self.c, 'TASK-1')['messages'], [])
        self.assertEqual((Path(self.slot) / 'threads-TASK-1.json').stat().st_mode & 0o777, 0o600)

    def test_ambiguous_escape_and_repeat_never_send_again(self):
        self.escape_error = True
        with self.assertRaises(RuntimeError):
            self.run_stop()
        with self.assertRaises(ValueError):
            self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False)])
        self.assertEqual(self.native, [])
        self.assertEqual(self.receipt()['stop_phase'], 'refused')

    def test_no_idle_after_escape_withholds_stop(self):
        self.stuck = True
        with patch.object(emergency, 'time', wraps=emergency.time) as clock:
            clock.monotonic.side_effect = [0, 2]
            with self.assertRaisesRegex(ValueError, 'turn did not stop'):
                self.run_stop(timeout=1)
        self.assertEqual(self.inputs, [('\x1b', False)])
        self.assertEqual(self.native, [])

    def test_uncleared_composer_gets_no_second_clear_or_stop(self):
        self.clear_stuck = True
        with self.assertRaisesRegex(ValueError, 'did not clear'):
            self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False), ('\x03', False)])
        self.assertEqual(self.native, [])

    def test_non_main_and_invalid_input_have_no_effect(self):
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            with self.assertRaises(ValueError):
                self.run_stop()
        for reason in ('', 'two\nlines', '\x1b', 'x' * 2001):
            with self.assertRaises(ValueError):
                emergency.stop(self.c, 'TASK-1', reason)
        for timeout in (0, 601, True):
            with self.assertRaises(ValueError):
                self.run_stop(timeout=timeout)
        self.assertEqual(self.inputs, [])

    def test_mismatched_terminal_pane_lease_session_or_process_refuses(self):
        cases = [patch.dict(self.terminal, {'incarnationId': 'other'}),
                 patch.dict(self.terminal, {'agentIdentity': 'bash'}),
                 patch.dict(self.terminal, {'connected': False}),
                 patch.dict(self.terminal, {'worktreeId': 'elsewhere'}),
                 patch.dict(self.terminal, {'writable': False}),
                 patch.dict(self.terminal, {'orphaned': True}),
                 patch.dict(self.terminal, {'leafId': ''}),
                 patch.dict(self.agent, {'paneKey': 'other'}),
                 patch.dict(self.agent, {'agentType': 'bash'}),
                 patch.dict(self.inventory_extra, {'truncated': True}),
                 patch.dict(self.inventory_extra, {'hostScope': {'omittedHostIds': ['remote']}}),
                 patch.dict(self.lease, {'held': []}),
                 patch.dict(self.lease, {'unknown': ['session']}),
                 patch.object(emergency.restart, '_foreground', side_effect=ValueError('no exact process'))]
        for change in cases:
            with self.subTest(change=change), change:
                with self.assertRaises(ValueError):
                    emergency._proof(self.c, self.launch)
        self.log.write_text(json.dumps({'type': 'session', 'id': 'session', 'cwd': 'elsewhere'}))
        with self.assertRaises(ValueError):
            emergency._proof(self.c, self.launch)
        self.assertEqual(self.inputs, [])

    def test_process_replacement_after_escape_prevents_clear(self):
        original = self.orca
        def replace(c, *args):
            result = original(c, *args)
            if args[:2] == ('terminal', 'send'):
                self.process['start'] = 'new'
            return result
        with patch.object(emergency, 'orca', side_effect=replace):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False)])

    def test_unknown_input_surface_gets_no_escape(self):
        self.ui = 'unknown'
        with self.assertRaisesRegex(ValueError, 'surface unknown'):
            self.run_stop()
        self.assertEqual(self.inputs, [])

    def test_real_console_escape_surface_reaches_stop(self):
        self.ui = 'console'
        receipt = self.run_stop()
        self.assertEqual(receipt['stop_phase'], 'submitted')
        self.assertEqual(self.inputs[0], ('\x1b', False))

    def test_native_send_ambiguity_never_submits_stop_or_retries(self):
        original = self.request
        def lost(c, verb, *args):
            if verb == 'send':
                raise RuntimeError('transport lost')
            return original(c, verb, *args)
        with patch.object(steering, 'request', side_effect=lost):
            with self.assertRaises(RuntimeError):
                self.run_stop()
        with self.assertRaises(ValueError):
            self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False), ('\x03', False)])
        self.assertEqual(self.receipt()['status'], 'sending')

    def test_draft_arriving_during_native_delivery_withholds_submission(self):
        original = self.request
        def draft(c, verb, *args):
            result = original(c, verb, *args)
            if verb == 'send':
                self.ui = 'draft'
            return result
        with patch.object(steering, 'request', side_effect=draft):
            with self.assertRaisesRegex(ValueError, 'no longer empty'):
                self.run_stop()
        self.assertFalse(any(enter for text, enter in self.inputs))
        self.assertEqual(self.receipt()['status'], 'sent')

    def test_submission_transport_loss_preserves_message_without_resubmit(self):
        original = self.orca
        def lost(c, *args):
            result = original(c, *args)
            if '--enter' in args:
                raise RuntimeError('submission lost')
            return result
        with patch.object(emergency, 'orca', side_effect=lost):
            with self.assertRaises(RuntimeError):
                self.run_stop()
        with self.assertRaises(ValueError):
            self.run_stop()
        self.assertEqual(sum(enter for text, enter in self.inputs), 1)
        self.assertEqual(self.receipt()['status'], 'sent')

    def test_session_log_absence_ambiguity_and_partial_headers_refuse(self):
        for content in ('', '{', '{}', '{"type":"session","cwd":"wrong","id":"session"}'):
            with self.subTest(content=content):
                self.log.write_text(content)
                with self.assertRaises(ValueError):
                    emergency._proof(self.c, self.launch)
        self.log.unlink()
        with self.assertRaises(ValueError):
            emergency._proof(self.c, self.launch)
        self.log.write_text('{}')
        (Path(self.slot) / 'other.jsonl').write_text('{}')
        with self.assertRaises(ValueError):
            emergency._proof(self.c, self.launch)

    def test_unavailable_rendered_screen_refuses_before_escape(self):
        original = self.orca
        def unavailable(c, *args):
            if args[:2] == ('terminal', 'read'):
                return {'terminal': {'source': 'screen-unavailable', 'tail': []}}
            return original(c, *args)
        with patch.object(emergency, 'orca', side_effect=unavailable):
            with self.assertRaisesRegex(ValueError, 'rendered'):
                self.run_stop()
        self.assertEqual(self.inputs, [])

    def test_incomplete_raw_input_receipt_stays_ambiguous_without_more_input(self):
        original = self.orca
        def incomplete(c, *args):
            result = original(c, *args)
            if args[:2] == ('terminal', 'send'):
                return {'send': {'accepted': True, 'bytesWritten': 0}}
            return result
        with patch.object(emergency, 'orca', side_effect=incomplete):
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False)])
        self.assertEqual(self.native, [])

    def test_idle_agent_with_running_program_does_not_receive_clear_or_stop(self):
        self.agent['programStatus'] = {'state': 'running'}
        with patch.object(emergency, 'time', wraps=emergency.time) as clock:
            clock.monotonic.side_effect = [0, 2]
            with self.assertRaisesRegex(ValueError, 'turn did not stop'):
                self.run_stop(timeout=1)
        self.assertEqual(self.inputs, [('\x1b', False)])

    def test_carried_ref_cannot_bypass_ambiguous_launch_stop(self):
        state.write(self.c, 'carries', {'launch': {'TASK-2': {
            'ref': 'TASK-2', 'slot': self.slot, 'launch_id': 'launch'}}})
        state.write(self.c, 'threads-TASK-2', {'TASK-2': {
            'id': 'carried-thread', 'attempts': {'launch': dict(self.attempt)}}})
        self.escape_error = True
        with self.assertRaises(RuntimeError):
            self.run_stop()
        with self.assertRaisesRegex(ValueError, 'this launch'):
            emergency.stop(self.c, 'TASK-2', 'Second ref must not repeat input')
        self.assertEqual(self.inputs, [('\x1b', False)])
        guard = state.read(self.c, 'stops')['launch']
        self.assertEqual(guard['ref'], 'TASK-1')
        self.assertEqual(guard['token'], self.receipt()['token'])

    def test_launch_guard_survives_partial_task_journal_write(self):
        original = state.write
        def fail(c, name, value):
            if name == 'threads-TASK-1':
                raise OSError('task journal write failed')
            return original(c, name, value)
        with patch.object(state, 'write', side_effect=fail):
            with self.assertRaises(OSError):
                self.run_stop()
        with self.assertRaisesRegex(ValueError, 'this launch'):
            self.run_stop()
        self.assertEqual(self.inputs, [])

    def test_resume_after_idle_proof_never_receives_raw_clear(self):
        original = self.orca
        def resumed(c, *args):
            result = original(c, *args)
            if (args[:2] == ('terminal', 'read') and
                    self.receipt()['stop_phase'] == 'idle-proven'):
                # The model resumes after the initial idle/draft observation.
                # The final input proof can see it and must withhold Ctrl-C.
                self.agent['state'] = 'working'
            return result
        with patch.object(emergency, 'orca', side_effect=resumed):
            with self.assertRaisesRegex(ValueError, 'fresh input proof'):
                self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False)])
        self.assertEqual(self.native, [])

    def test_resume_after_empty_proof_never_receives_stop_text(self):
        original = self.orca
        def resumed(c, *args):
            result = original(c, *args)
            if (args[:2] == ('terminal', 'read') and
                    self.receipt()['stop_phase'] == 'message-sent'):
                self.agent['state'] = 'working'
            return result
        with patch.object(emergency, 'orca', side_effect=resumed):
            with self.assertRaisesRegex(ValueError, 'fresh input proof'):
                self.run_stop()
        self.assertEqual(self.inputs, [('\x1b', False), ('\x03', False)])
        self.assertEqual(self.receipt()['status'], 'sent')

    def test_stop_check_uses_private_config_from_another_cwd(self):
        path = Path(self.slot) / "private project's config.yml"
        path.write_text(json.dumps({
            'version': 1, 'repo': 'test/stop', 'root': self.slot,
            'orca_repo_id': 'tree', 'pad_workspace': 'test', 'orca': '/bin/true',
            'builder': {'model': 'test/model', 'resolved_model': 'test/model',
                        'rules_file': 'rules', 'slots': [self.slot]},
            'coordinator': self.c['coordinator']}))
        ledgers = {name: state.read(self.c, name)
                   for name in ('launches', 'threads-TASK-1')}
        with patch.dict(os.environ, XDG_STATE_HOME=self.slot):
            self.c = config.load(path)
            for name, value in ledgers.items():
                state.write(self.c, name, value)
            result = self.run_stop()
            command = shlex.split(result['body'].split('`')[1])
            other = Path(self.slot) / 'another checkout'
            other.mkdir()
            output = io.StringIO()
            with (contextlib.chdir(other), contextlib.redirect_stdout(output),
                  patch.object(sys, 'argv', command),
                  patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder')):
                self.assertEqual(cli.main(), 0)
            consumed = json.loads(output.getvalue())
            self.assertEqual(consumed['token'], result['token'])

    def test_multiline_config_path_refuses_before_input(self):
        self.c['_path'] = self.slot + '/config\ninjection.yml'
        with self.assertRaisesRegex(ValueError, 'config path'):
            self.run_stop()
        self.assertEqual(self.inputs, [])

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import restart, state
from fleet.commands import CommandError


class RetirementTests(unittest.TestCase):
    def setUp(self):
        platform_patch = patch.object(restart.sys, 'platform', 'linux')
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.slot = str(root)
        self.c = {'root': self.slot, '_state': str(root / 'state'), 'repo': 'owner/repo',
                  'pad_workspace': 'project', 'builder': {'slots': [self.slot], 'max': 1,
                                                        'model': 'provider/model', 'thinking': 'medium'}}
        self.tree = {'id': 'tree', 'path': self.slot}
        self.builder = {'terminal': 'target', 'path': self.slot, 'connected': True, 'shell_ready': False}
        self.inventory = {'errors': [], 'worktrees': [self.tree], 'builders': [self.builder]}
        self.terminal = {'connected': True, 'writable': True, 'worktreeId': 'tree', 'agentIdentity': 'omp',
                         'incarnationId': 'incarnation', 'tabId': 'tab', 'leafId': 'leaf'}
        self.agent = {'paneKey': 'tab:leaf', 'agentType': 'omp', 'state': 'done', 'programStatus': {'state': 'done'}}
        self.screen = ['finished', ' π > ◑ GPT-6.1 Sol >', '╰─']
        self.events = [{'type': 'session', 'id': 'session', 'cwd': self.slot},
                       {'type': 'message', 'message': {'role': 'assistant', 'stopReason': 'stop', 'content': []}}]
        self.log = root / 'probe.jsonl'
        self.write_log()
        self.record = {'id': 'old', 'at': 1, 'ref': 'TASK-1', 'worktree': self.tree, 'terminal': 'target',
                       'terminal_incarnation_id': 'incarnation', 'session_dir': self.slot,
                       'overlay': str(root / 'overlay'), 'prompt': str(root / 'prompt')}
        state.write(self.c, 'launches', {self.slot: self.record})
        self.proc = root / 'proc'
        self.process = self.proc / '123'
        self.process.mkdir(parents=True)
        self.argv = ['omp', '--model', 'provider/model', '--thinking', 'medium', '--config', self.record['overlay'],
                     '--session-dir', self.slot, '@' + self.record['prompt']]
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in self.argv) + b'\0')
        (self.process / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=target\0')
        (self.process / 'cwd').symlink_to(root)
        (self.process / 'exe').symlink_to(root / 'omp')
        self.write_stat()
        proc_patch = patch.object(restart, 'PROC', self.proc)
        proc_patch.start()
        self.addCleanup(proc_patch.stop)
        self.item = {'fields': '{"status":"done"}'}
        self.prs = [{'title': 'TASK-1 fix', 'body': '', 'mergedAt': '2026-01-01T00:00:00Z', 'url': 'https://example/pr/1'}]
        self.comments = []
        self.sent = []
        self.exited = False
        self.next_status = 'in-progress'
        self.next_parent_status = 'active'
        self.next_edges = []
        self.send_error = False
        self.patches = [patch.object(restart.health, 'collect', side_effect=lambda c: copy.deepcopy(self.inventory)),
                        patch.object(restart, 'orca', side_effect=self.orca),
                        patch.object(restart, 'json_run', side_effect=self.pad),
                        patch.object(restart, 'gh', side_effect=lambda *a: self.prs),
                        patch.object(restart.snapshot, 'collect', side_effect=lambda c: {
                            'errors': [], 'inventory': copy.deepcopy(self.inventory), 'quota': {'may_start': True}, 'disk': {'low': False}}),
                        patch.object(restart.dispatcher, 'pad', side_effect=self.dispatcher_pad),
                        patch.object(restart.dispatcher, 'run', return_value='task-1'),
                        patch.object(restart.launcher, 'launch', return_value={'id': 'new'})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.launch = restart.launcher.launch

    def write_log(self):
        self.log.write_text('\n'.join(json.dumps(e) for e in self.events) + '\n')

    def dispatcher_pad(self, c, *args):
        if args == ('collection', 'list'):
            raise CommandError('schema unavailable')
        if args[1] == 'deps':
            return self.next_edges
        if args[2] == 'PLAN-1':
            return {'ref': args[2], 'fields': {'status': self.next_parent_status}}
        return {'ref': args[2], 'fields': {'status': self.next_status}, 'parent_ref': 'PLAN-1'}

    def pad(self, argv, cwd):
        if argv[0] == 'omp':
            return {'held': ['session'], 'unknown': []}
        return self.comments if 'comments' in argv else self.item

    def orca(self, c, *args):
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.terminal}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': {'tail': ['dev@host:~/slot$'] if self.exited else self.screen}}
        if args[:2] == ('worktree', 'ps'):
            return {'worktrees': [{'worktreeId': 'tree', 'agents': [self.agent]}]}
        if args[:2] == ('terminal', 'send'):
            self.sent.append(args)
            # Intent must already be durable even if the transport fails.
            self.assertEqual(state.read(c, 'retirements')[self.slot]['stage'], 'exit-attempted')
            if self.send_error:
                raise RuntimeError('lost transport')
            self.exited = True
            return {'accepted': True}
        self.fail(args)

    def run_restart(self):
        return restart.restart(self.c, 'TASK-2', self.slot, 'TASK-1')

    def refused(self, reason):
        with self.assertRaisesRegex(ValueError, reason):
            self.run_restart()
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()

    def test_finished_idle_session_exits_once_then_launches(self):
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(result['retirement']['session']['id'], 'session')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][-3:], ('--text', '/exit', '--enter'))
        self.launch.assert_called_once_with(self.c, 'TASK-2', self.slot, _locked=True)
        self.sent.clear()
        self.launch.reset_mock()
        self.refused('already attempted')

    def test_ready_bound_shell_does_not_block_exact_builder_retirement(self):
        self.inventory['builders'].append({
            'terminal': 'retired-bound-shell', 'path': self.slot,
            'connected': True, 'shell_ready': True,
            'binding': {'state': 'bound', 'run_id': 'run_scratch'}})
        result = self.run_restart()
        self.assertEqual(result['retirement']['terminal'], 'target')
        self.assertEqual([args[args.index('--terminal') + 1] for args in self.sent], ['target'])

    def test_unbound_or_unknown_extra_shell_still_blocks_retirement(self):
        for binding in ({'state': 'none'}, {'state': 'unknown'}, {}):
            with self.subTest(binding=binding):
                self.inventory['builders'] = [self.builder, {
                    'terminal': 'extra', 'path': self.slot, 'connected': True,
                    'shell_ready': True, 'binding': binding}]
                self.refused('multiple terminals')

    def test_working_dialog_background_and_unknown_screens_refuse(self):
        for screen in (['⠹ working > ◑ GPT >', '╰─'], ['Select permission', 'Approve / Deny'],
                       ['Waiting for background job', ' π > ◑ GPT >', '╰─'], [], ['old working footer']):
            with self.subTest(screen=screen):
                self.screen = screen
                self.refused('idle|dialog|background')

    def test_running_unknown_and_interrupted_exact_panes_refuse(self):
        for agent in ({}, {**self.agent, 'state': 'working'}, {**self.agent, 'programStatus': {}},
                      {**self.agent, 'interrupted': True}, {**self.agent, 'paneKey': 'other:leaf'}):
            with self.subTest(agent=agent):
                self.agent = agent
                self.refused('agent is running|unknown')

    def test_disconnected_ambiguous_and_changed_identity_refuse(self):
        self.builder['connected'] = False
        self.refused('disconnected')
        self.builder['connected'] = True
        self.inventory['builders'].append(dict(self.builder, terminal='second'))
        self.refused('exactly one')
        self.inventory['builders'].pop()
        self.terminal['incarnationId'] = 'replacement'
        self.refused('identity')

    def test_unfinished_or_unpublished_task_refuses(self):
        self.item = {'fields': {'status': 'in-progress'}}
        self.refused('neither Pad done nor handed off')
        self.item = {'fields': {'status': 'done'}}
        self.prs[0]['title'] = 'TASK-10 unrelated'
        self.refused('no merged PR')

    def test_post_publication_handoff_allows_open_old_task(self):
        self.item = {'fields': {'status': 'in-progress'}}
        self.comments = [{'id': 'handoff', 'body': 'TASK-1 post-publication handoff', 'created_at': '2026-01-02T00:00:00Z'}]
        self.assertEqual(self.run_restart()['retirement']['completion']['handoff'], 'handoff')

    def test_old_handoff_is_not_completion(self):
        self.item = {'fields': {'status': 'in-progress'}}
        self.comments = [{'id': 'handoff', 'body': 'TASK-1 handoff', 'created_at': '2025-01-02T00:00:00Z'}]
        self.refused('neither Pad done nor handed off')

    def test_handoff_fractional_second_after_merge_is_accepted(self):
        self.item = {'fields': {'status': 'in-progress'}}
        self.comments = [{'id': 'handoff', 'body': 'TASK-1 handoff', 'created_at': '2026-01-01T00:00:00.500Z'}]
        self.assertEqual(self.run_restart()['retirement']['completion']['handoff'], 'handoff')

    def test_lexically_later_handoff_with_earlier_timezone_is_not_completion(self):
        self.item = {'fields': {'status': 'in-progress'}}
        self.comments = [{'id': 'handoff', 'body': 'TASK-1 handoff', 'created_at': '2026-01-01T00:00:01+01:00'}]
        self.refused('neither Pad done nor handed off')

    def test_unknown_handoff_timestamp_refuses(self):
        self.item = {'fields': {'status': 'in-progress'}}
        for value in ('invalid', '2026-01-02T00:00:00', None):
            with self.subTest(value=value):
                self.comments = [{'id': 'handoff', 'body': 'TASK-1 handoff', 'created_at': value}]
                self.refused('timestamp')

    def test_session_must_end_at_stop_except_known_metadata(self):
        for event in ({'type': 'message', 'message': {'role': 'user'}}, {'type': 'custom'}, {'type': 'future_event'}):
            with self.subTest(event=event):
                self.events = self.events[:2] + [event]
                self.write_log()
                self.refused('final assistant|later work')
        self.events = self.events[:2] + [{'type': 'credential_pin'}, {'type': 'title_change'}]
        self.write_log()
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    def test_real_shaped_post_turn_todo_snapshot_allows_retirement(self):
        # Sanitized omp 18.17.0 shapes from fleet sessions 01a1b1aa / 01a1b1ae.
        self.events += [
            {'type': 'credential_pin', 'id': 'pin', 'parentId': 'stop',
             'timestamp': '2026-11-06T16:39:19.000Z'},
            {'type': 'title_change', 'id': 'title', 'parentId': 'pin',
             'timestamp': '2026-11-06T16:39:20.000Z'},
            {'type': 'custom', 'customType': 'todo_hud_state',
             'data': {'sourceEntryId': 'stop', 'fingerprint': 'sanitized', 'visibility': {}},
             'id': 'todo', 'parentId': 'title', 'timestamp': '2026-11-06T16:40:02.272Z'},
        ]
        self.write_log()
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_called_once_with(self.c, 'TASK-2', self.slot, _locked=True)

    def test_unknown_or_misencoded_metadata_never_retires(self):
        for event in (
                {'type': 'custom', 'customType': 'tool_execution_start'},
                {'type': 'custom', 'customType': 'session_exit'},
                {'type': 'custom', 'customType': 'future_metadata'},
                {'type': 'custom', 'customType': 'credential_pin'},
                {'type': 'todo_hud_state'},
                {'type': 'custom_message', 'customType': 'todo_hud_state'},
                {'type': 'future_event', 'customType': 'todo_hud_state'}):
            with self.subTest(event=event):
                self.events = self.events[:2] + [event]
                self.write_log()
                self.refused('later work')

    def test_todo_snapshot_does_not_make_mid_turn_session_finished(self):
        self.events[-1]['message']['stopReason'] = 'toolUse'
        self.events.append({'type': 'custom', 'customType': 'todo_hud_state', 'data': {}})
        self.write_log()
        self.refused('no final assistant stop')

    def test_missing_launch_binding_refuses(self):
        for key in ('terminal', 'ref', 'session_dir', 'terminal_incarnation_id'):
            with self.subTest(key=key):
                record = dict(self.record)
                record.pop(key)
                state.write(self.c, 'launches', {self.slot: record})
                self.refused('record|identity|session directory')

    def test_lost_send_receipt_never_sends_exit_twice(self):
        self.send_error = True
        with self.assertRaisesRegex(RuntimeError, 'lost transport'):
            self.run_restart()
        self.assertEqual(len(self.sent), 1)
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertEqual(receipt['failure'], 'RuntimeError')
        self.assertIn('never resend /exit', receipt['retry_guidance'])
        self.sent.clear()
        self.refused('already attempted')

    def test_shell_timeout_and_launch_failure_preserve_receipts(self):
        self.orca_original = self.orca
        def no_shell(c, *args):
            result = self.orca_original(c, *args)
            self.exited = False
            return result
        with patch.object(restart, 'orca', side_effect=no_shell), patch.object(restart.time, 'monotonic', side_effect=[0, 16]):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                self.run_restart()
        self.assertEqual(state.read(self.c, 'retirements')[self.slot]['stage'], 'exit-sent')
        self.launch.assert_not_called()

    def test_launch_failure_cannot_repeat_retirement(self):
        self.launch.side_effect = ValueError('quota changed')
        with self.assertRaisesRegex(ValueError, 'quota changed.*retry fleet launch.*not fleet restart'):
            self.run_restart()
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertEqual(receipt['stage'], 'launch-attempted')
        self.assertEqual(receipt['failure'], 'ValueError')
        self.assertIn('confirming no replacement session started', receipt['retry_guidance'])
        self.sent.clear()
        self.launch.reset_mock()
        self.refused('already attempted')

    def test_reserve_failure_does_not_retire_finished_session(self):
        with patch.object(restart.snapshot, 'may_start', return_value={'may_start': False, 'reasons': ['quota reserve']}):
            self.refused('quota reserve')

    def test_next_task_admission_refuses_before_exit(self):
        self.next_status = 'done'
        self.refused('task status')
        self.next_status = 'in-progress'
        self.next_parent_status = 'draft'
        with self.assertRaises(ValueError):
            self.run_restart()
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()
        self.next_parent_status = 'active'
        self.next_edges = [{'link_type': 'blocks', 'target_ref': 'TASK-2',
                            'source_ref': 'TASK-9', 'source_status': 'open'}]
        self.refused('blocked by TASK-9')

    def test_competing_receipt_refuses_before_exit(self):
        state.write(self.c, 'last-launch', dict(self.record, id='competing', ref='TASK-9'))
        self.refused('unresolved launch receipt competing')

    def during_admission(self, change):
        def collect(c):
            change()
            return {'errors': [], 'inventory': copy.deepcopy(self.inventory),
                    'quota': {'may_start': True}, 'disk': {'low': False}}
        return patch.object(restart.snapshot, 'collect', side_effect=collect)

    def test_late_allowlisted_metadata_reverified_before_single_exit(self):
        def append():
            self.events.extend([{'type': 'custom', 'customType': 'todo_hud_state', 'data': {}},
                                {'type': 'credential_pin'}, {'type': 'title_change'}])
            self.write_log()
        with self.during_admission(append):
            result = self.run_restart()
        self.assertEqual(result['retirement']['session']['size'], self.log.stat().st_size)
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)

    def test_changed_prefix_and_completed_new_turn_refuse(self):
        original = copy.deepcopy(self.events)
        for events in (
                [dict(original[0], extra='rewritten'), original[1]],
                [dict(original[0], cwd=self.slot, id='other'), original[1]],
                original + [{'type': 'message', 'message': {'role': 'user'}}, original[1]],
                original + [{'type': 'custom', 'customType': 'future_metadata'}]):
            with self.subTest(events=events):
                self.events = copy.deepcopy(original)
                self.write_log()
                def change():
                    self.events = events
                    self.write_log()
                with self.during_admission(change):
                    self.refused('evidence changed|later work')
                self.assertIsNone(state.read(self.c, 'retirements'))

    def test_truncated_metadata_and_partial_append_refuse(self):
        self.events.append({'type': 'custom', 'customType': 'todo_hud_state'})
        self.write_log()
        baseline = self.log.read_bytes()
        for data in (baseline[:baseline.rfind(b'\n', 0, -1) + 1],
                     baseline + b'{\"type\":\"custom\"'):
            with self.subTest(data=data):
                self.log.write_bytes(baseline)
                with self.during_admission(lambda: self.log.write_bytes(data)):
                    self.refused('evidence changed|incomplete')
                self.assertIsNone(state.read(self.c, 'retirements'))


    def test_prepared_next_branch_exempts_only_exact_retiring_builder(self):
        self.builder.update(task='TASK-2', worktree='tree', incarnation_id='incarnation')
        with patch.object(restart.dispatcher, 'run', return_value='task-2-prepared'):
            result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)

    def test_competing_next_builder_still_refuses(self):
        self.builder.update(task='TASK-2', worktree='tree', incarnation_id='incarnation')
        self.inventory['builders'].append(dict(self.builder, path='/other', worktree='other', terminal='other'))
        self.refused('TASK-2 already assigned to /other')

    def test_changed_retiring_inventory_identity_is_not_exempt(self):
        self.builder.update(task='TASK-2', worktree='tree', incarnation_id='incarnation')
        for key, value in (('incarnation_id', 'new'), ('worktree', 'other')):
            with self.subTest(key=key):
                original = self.builder[key]
                self.builder[key] = value
                self.refused('TASK-2 already assigned')
                self.builder[key] = original

    def test_resumed_work_during_admission_never_receives_exit(self):
        def collect(c):
            self.events.append({'type': 'message', 'message': {'role': 'user'}})
            self.write_log()
            return {'errors': [], 'inventory': copy.deepcopy(self.inventory),
                    'quota': {'may_start': True}, 'disk': {'low': False}}
        with patch.object(restart.snapshot, 'collect', side_effect=collect):
            self.refused('final assistant|later work')
        self.assertFalse(self.exited)
        self.assertIsNone(state.read(self.c, 'retirements'))

    def test_replacement_cap_excludes_only_its_own_active_terminal(self):
        # Own active target fits max=1, as the successful restart test proves.
        self.inventory['builders'].append({'terminal': 'other', 'path': '/other',
                                           'connected': True, 'shell_ready': False})
        self.refused('builder cap reached')
        self.assertFalse(self.exited)
        self.assertIsNone(state.read(self.c, 'retirements'))

    def test_connection_loss_after_exit_never_launches(self):
        original = self.orca
        def disconnected(c, *args):
            result = original(c, *args)
            if self.exited and args[:2] == ('terminal', 'show'):
                result = {'terminal': dict(self.terminal, connected=False)}
            return result
        with patch.object(restart, 'orca', side_effect=disconnected):
            with self.assertRaisesRegex(ValueError, 'identity lost'):
                self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()

    def test_exited_old_session_log_cannot_authorize_retiring_a_new_process(self):
        original = self.pad
        def unheld(argv, cwd):
            return {'held': [], 'unknown': []} if argv[0] == 'omp' else original(argv, cwd)
        with patch.object(restart, 'json_run', side_effect=unheld):
            self.refused('no longer positively owned')

    def test_unsent_single_and_multiline_drafts_never_receive_exit(self):
        for composer in (['╰─ draft'], ['╰─ first', '   second'],
                         ['╰─ first', '   ╰─'], ['╰─ first', '   π > ◑ model >', '   ╰─']):
            with self.subTest(composer=composer):
                self.screen = ['finished', ' π > ◑ GPT >', *composer]
                self.refused('draft|ambiguous')

    def write_stat(self, status='S', foreground='123'):
        fields = [status, '1', '123', '1', '456', foreground] + ['0'] * 13 + ['1000']
        (self.process / 'stat').write_text('123 (omp) ' + ' '.join(fields))

    def test_held_old_lease_with_different_foreground_process_never_receives_exit(self):
        # Lease, old completed log, and exact pane idle remain positive.
        self.write_stat(foreground='999')
        self.refused('no unique matching foreground')

    def test_stopped_old_process_and_wrong_session_argv_refuse(self):
        self.write_stat(status='T')
        self.refused('no unique matching foreground')
        self.write_stat()
        replacement = [*self.argv]
        replacement[replacement.index('--session-dir') + 1] = '/replacement'
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in replacement))
        self.refused('no unique matching foreground')

    def test_configured_leading_orca_extension_allows_exact_retirement(self):
        self.c['builder']['orca_status_extension'] = '/trusted/orca-agent-status.ts'
        argv = [self.argv[0], '--extension', '/trusted/orca-agent-status.ts', *self.argv[1:]]
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)

    def test_other_extra_or_unconfigured_extensions_never_receive_exit(self):
        self.c['builder']['orca_status_extension'] = '/trusted/orca-agent-status.ts'
        for extra in (
                ['--extension', '/other/status.ts'],
                ['--extension', '/trusted/orca-agent-status.ts', '--extension', '/trusted/orca-agent-status.ts'],
                ['--extension', '/trusted/orca-agent-status.ts', '--no-tools'],
                ['--no-tools']):
            with self.subTest(extra=extra):
                argv = [self.argv[0], *extra, *self.argv[1:]]
                (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
                self.refused('no unique matching foreground')
        self.c['builder'].pop('orca_status_extension')
        (self.process / 'environ').write_bytes(
            b'ORCA_TERMINAL_HANDLE=target\0ORCA_OMP_STATUS_EXTENSION=/trusted/orca-agent-status.ts\0')
        argv = [self.argv[0], '--extension', '/trusted/orca-agent-status.ts', *self.argv[1:]]
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        self.refused('no unique matching foreground')

    def test_foreground_requires_exact_terminal_routing_and_accessible_process_evidence(self):
        (self.process / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=replacement\0')
        self.refused('no unique matching foreground')
        (self.process / 'environ').unlink()
        self.refused('no unique matching foreground')

    def test_missing_proc_never_retires(self):
        with patch.object(restart, 'PROC', Path(self.slot) / 'missing'):
            with self.assertRaises(ValueError):
                self.run_restart()
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()

    def test_unrelated_environment_secret_never_reaches_output_or_receipts(self):
        secret = 'fake-secret-do-not-emit'
        (self.process / 'environ').write_bytes(('UNRELATED_SECRET=' + secret + '\0ORCA_TERMINAL_HANDLE=target\0').encode())
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        serialized = json.dumps(result) + json.dumps(state.read(self.c, 'retirements')) + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(secret, serialized)
        self.assertNotIn('UNRELATED_SECRET', serialized)


if __name__ == '__main__':
    unittest.main()

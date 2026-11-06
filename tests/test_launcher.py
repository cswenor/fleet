from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import dispatcher, health, launcher, snapshot, state
from fleet.commands import CommandError


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.slot = str(root / 'slot')
        Path(self.slot).mkdir()
        (Path(self.slot) / 'RULES.md').write_text('rules')
        self.c = {'root': self.directory.name, '_state': str(root / 'state'), 'repo': 'owner/project',
                  'orca': '/orca', 'orca_repo_id': 'repo', 'pad_workspace': 'workspace',
                  'coordinator': {'terminal': 'main', 'run': 'run'}, 'hooks': {},
                  'quota': {'reserve_percent': 10},
                  'builder': {'slots': [self.slot], 'max': 8, 'model': 'provider/model',
                              'resolved_model': 'provider/model', 'thinking': 'medium',
                              'rules_file': 'RULES.md', 'footer': '', 'idle_seconds': 2700}}
        self.s = {'errors': [], 'quota': {'may_start': True}, 'disk': {'low': False},
                  'inventory': {'errors': [], 'worktrees': [{'id': 'tree', 'path': self.slot, 'branch': 'work'}],
                                'builders': [dict(terminal='old-shell', incarnation_id='inc', path=self.slot, worktree='tree', connected=True, shell_ready=True,
                                                  binding={'state': 'none', 'run_id': None})] +
                                            [dict(terminal=f'live-{i}', path=f'/other-{i}', worktree=f'other-{i}', connected=True) for i in range(7)]}}
        self.created = 0
        self.sent = []
        self.prompt = 'user@host:~/slot$ '
        self.binding = {'run': None}
        self.calls = []
        self.incarnation = 'inc'

    def orca(self, config, *args):
        self.calls.append(args)
        if args[:2] == ('orchestration', 'run-current'):
            if isinstance(self.binding, Exception):
                raise self.binding
            return self.binding
        if args[:2] == ('terminal', 'show'):
            return {'terminal': {'connected': True, 'writable': True, 'worktreeId': 'tree', 'incarnationId': self.incarnation}}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': {'tail': ['old omp footer', self.prompt]}}
        if args[:2] == ('terminal', 'create'):
            self.created += 1
            return {'terminal': {'handle': 'new-terminal'}}
        if args[:2] == ('terminal', 'send'):
            handle = args[args.index('--terminal') + 1]
            self.sent.append(handle)
            self.prompt = '◑ model >'
            return {'send': {'handle': handle, 'accepted': True}}
        return {}

    def launch(self, snapshots=None, automatic=False):
        with patch.object(launcher.snapshot, 'collect', side_effect=snapshots or [self.s, self.s]), patch.object(launcher, 'orca', side_effect=self.orca), patch.object(health, 'orca', side_effect=self.orca), patch.object(launcher, 'run'), patch.object(dispatcher, 'status_policy', return_value=dispatcher.config.STATUS_DEFAULTS), patch.object(dispatcher, 'pad', side_effect=lambda c, *args: {'ref': args[2], 'fields': {'status': 'active' if args[2] == 'PLAN-1' else 'open'}, 'parent_ref': 'PLAN-1'} if args[1] == 'show' else []), patch.object(dispatcher, 'run', return_value='task-123'):
            return launcher.launch(self.c, 'TASK-123', None if automatic else self.slot)

    def test_eight_connected_terminals_with_one_exited_session_reuse_its_shell(self):
        self.assertTrue(snapshot.may_start(self.c, self.s)['may_start'])
        receipt = self.launch()
        self.assertEqual(receipt['receipt']['send']['handle'], 'old-shell')
        self.assertEqual(self.sent, ['old-shell'])
        self.assertEqual(self.created, 0)
        self.assertEqual(health.owner(self.s['inventory'], 'work'), None)
        self.assertEqual(receipt['terminal_selection']['reason'], 'ready_unbound_shell')
        self.assertEqual(self.calls[-2][:2], ('terminal', 'send'))
        send_index = next(i for i, args in enumerate(self.calls) if args[:2] == ('terminal', 'send'))
        self.assertEqual(self.calls[send_index - 1], ('orchestration', 'run-current', '--from', 'old-shell'))

    def test_bound_ready_shell_creates_in_same_worktree_without_sending_or_binding(self):
        self.s['inventory']['builders'][0]['binding'] = {'state': 'bound', 'run_id': 'run_old'}
        receipt = self.launch()
        self.assertEqual((self.sent, self.created), ([], 1))
        self.assertEqual(receipt['terminal'], 'new-terminal')
        self.assertEqual(receipt['worktree']['path'], self.slot)
        self.assertEqual(receipt['terminal_selection'], {
            'action': 'create', 'reason': 'run_bound',
            'skipped_terminals': [{'terminal': 'old-shell', 'binding': {'state': 'bound', 'run_id': 'run_old'}}]})
        create = next(args for args in self.calls if args[:2] == ('terminal', 'create'))
        self.assertEqual(create[create.index('--worktree') + 1], 'id:tree')
        self.assertFalse(any(args[:2] == ('worktree', 'create') for args in self.calls))
        self.assertTrue(all(args[1] in ('run-show', 'run-current') for args in self.calls if args[0] == 'orchestration'))

    def test_unknown_or_missing_binding_is_never_permission_to_reuse(self):
        for value in ({'state': 'unknown', 'run_id': None}, None):
            with self.subTest(binding=value):
                self.c['_state'] = str(Path(self.directory.name) / f'state-case-{self.created}')
                shell = self.s['inventory']['builders'][0]
                if value is None:
                    shell.pop('binding', None)
                else:
                    shell['binding'] = value
                receipt = self.launch()
                self.assertEqual(receipt['terminal_selection']['reason'], 'binding_unknown')
                self.assertEqual(receipt['terminal'], 'new-terminal')
        self.assertEqual((self.sent, self.created), ([], 2))

    def test_binding_race_and_failed_recheck_create_fresh_instead_of_send(self):
        for value, reason in (({'run': {'id': 'run_new'}}, 'run_bound'),
                              (RuntimeError('unavailable'), 'binding_unknown'),
                              ({}, 'binding_unknown')):
            with self.subTest(binding=value):
                self.c['_state'] = str(Path(self.directory.name) / f'state-case-{self.created}')
                self.binding = value
                receipt = self.launch()
                self.assertEqual(receipt['terminal_selection']['reason'], reason)
                self.assertEqual(receipt['terminal'], 'new-terminal')
        self.assertEqual((self.sent, self.created), ([], 3))

    def test_retired_bound_shell_does_not_prevent_reusing_one_unbound_shell(self):
        self.s['inventory']['builders'].append(dict(self.s['inventory']['builders'][0],
            terminal='retired-bound', binding={'state': 'bound', 'run_id': 'run_old'}))
        receipt = self.launch()
        self.assertEqual(receipt['terminal'], 'old-shell')
        self.assertEqual((self.sent, self.created), (['old-shell'], 0))

    def test_bound_live_session_is_still_occupied(self):
        self.s['inventory']['builders'].pop()
        self.s['inventory']['builders'][0].update(shell_ready=False, binding={'state': 'bound', 'run_id': 'run_old'})
        with self.assertRaisesRegex(ValueError, 'live or unconfirmed'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_automatic_slot_selection_accepts_its_prepared_task_branch(self):
        receipt = self.launch(automatic=True)
        self.assertEqual(receipt['worktree']['path'], self.slot)
        self.assertEqual(self.sent, ['old-shell'])

    def test_full_live_cap_and_live_target_are_still_withheld(self):
        self.s['inventory']['builders'][0]['shell_ready'] = False
        with self.assertRaisesRegex(ValueError, 'builder cap reached'):
            self.launch()
        self.s['inventory']['builders'].pop()
        with self.assertRaisesRegex(ValueError, 'live or unconfirmed'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_disconnected_session_never_frees_a_slot(self):
        self.s['inventory']['builders'].pop()
        self.s['inventory']['builders'][0].update(connected=False, shell_ready=False)
        with self.assertRaisesRegex(ValueError, 'live or unconfirmed'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))
        self.assertIsNone(health.owner(self.s['inventory'], 'work'))

    def test_raced_or_ambiguous_shell_never_receives_input(self):
        self.prompt = '◑ model >'
        with self.assertRaisesRegex(ValueError, 'no longer a ready shell'):
            self.launch()
        self.s['inventory']['builders'].append(dict(self.s['inventory']['builders'][0], terminal='second-shell'))
        with self.assertRaisesRegex(ValueError, 'multiple ready shells'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_reserve_gate_cannot_be_bypassed_by_replacing_a_session(self):
        self.s['quota'] = {'may_start': False, 'reason': 'quota reserve reached'}
        with self.assertRaisesRegex(ValueError, 'quota reserve reached'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))
    def test_preparation_rechecks_quota_and_terminal_identity(self):
        self.c['hooks']['before_launch'] = 'prepare-slot'
        fresh = deepcopy(self.s)
        fresh['quota'] = {'may_start': False, 'reason': 'reserve reached'}
        with self.assertRaisesRegex(ValueError, 'reserve reached'):
            self.launch([self.s, fresh])
        receipt = state.read(self.c, 'launch-preparations')[self.slot]
        self.assertEqual(receipt['safe_state'], 'no-builder-input-sent')
        self.assertEqual(receipt['admission']['reasons'], ['reserve reached'])
        self.assertIn('retry fleet launch', receipt['retry_guidance'])
        self.assertIsNone(state.read(self.c, 'launches'))
        fresh = deepcopy(self.s)
        fresh['inventory']['builders'][0]['terminal'] = 'replacement'
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.launch([self.s, fresh])
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_unknown_initial_admission_does_not_run_preparation_hook(self):
        self.c['hooks']['before_launch'] = 'prepare-slot'
        self.s['quota'] = {'may_start': False, 'reason': 'quota unknown'}
        with patch.object(launcher.snapshot, 'collect', return_value=self.s), patch.object(launcher, 'run') as hook, patch.object(launcher, 'orca') as orca, patch.object(dispatcher, 'run', return_value='task-123'):
            with self.assertRaisesRegex(ValueError, 'quota unknown'):
                launcher.launch(self.c, 'TASK-123', self.slot)
        hook.assert_not_called()
        orca.assert_not_called()
        self.assertIsNone(state.read(self.c, 'launch-preparations'))

    def test_hook_failure_records_prepared_slot_without_authorizing_launch(self):
        self.c['hooks']['before_launch'] = 'prepare-slot'
        with patch.object(launcher.snapshot, 'collect', return_value=self.s), patch.object(launcher, 'orca', side_effect=self.orca), patch.object(dispatcher, 'check_launch'), patch.object(dispatcher, 'run', return_value='task-123'), patch.object(launcher, 'run', side_effect=CommandError('hook failed')):
            with self.assertRaisesRegex(CommandError, 'hook failed; no builder input sent.*retry fleet launch'):
                launcher.launch(self.c, 'TASK-123', self.slot)
        receipt = state.read(self.c, 'launch-preparations')[self.slot]
        self.assertEqual(receipt['stage'], 'preparation-attempted')
        self.assertEqual(receipt['action'], 'before-launch-hook')
        self.assertEqual(receipt['failure'], 'CommandError')
        self.assertIn('inspect prepared slot and hooks', receipt['retry_guidance'])
        self.assertEqual((self.sent, self.created), ([], 0))
        self.assertIsNone(state.read(self.c, 'launches'))

    def test_created_worktree_is_retained_on_post_preparation_refusal(self):
        missing = str(Path(self.directory.name) / 'new-slot')
        self.c['builder']['slots'] = [missing]
        initial = deepcopy(self.s)
        initial['inventory']['builders'] = []
        initial['inventory']['worktrees'] = []
        fresh = deepcopy(initial)
        fresh['quota'] = {'may_start': False, 'reason': 'quota unknown'}
        with patch.object(launcher.snapshot, 'collect', side_effect=[initial, fresh]), patch.object(launcher, 'orca', return_value={'worktree': {'id': 'new-tree', 'path': missing}}) as orca, patch.object(dispatcher, 'check_launch'), patch.object(launcher, 'run'):
            with self.assertRaisesRegex(ValueError, 'no builder input sent.*retry fleet launch'):
                launcher.launch(self.c, 'TASK-123', missing)
        receipt = state.read(self.c, 'launch-preparations')[missing]
        self.assertEqual(receipt['worktree'], {'id': 'new-tree', 'path': missing})
        self.assertEqual(receipt['safe_state'], 'no-builder-input-sent')
        self.assertEqual(receipt['admission']['reasons'], ['quota unknown'])
        self.assertEqual([call.args[1:3] for call in orca.call_args_list],
                         [('terminal', 'show'), ('orchestration', 'run-show'), ('worktree', 'create')])
        self.assertIsNone(state.read(self.c, 'launches'))

    def test_missing_or_replaced_final_incarnation_never_receives_input(self):
        self.s['inventory']['builders'][0]['incarnation_id'] = None
        with self.assertRaisesRegex(ValueError, 'incarnation is unknown'):
            self.launch()
        self.s['inventory']['builders'][0]['incarnation_id'] = 'inc'
        self.incarnation = 'replacement'
        with self.assertRaisesRegex(ValueError, 'no longer a ready shell'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))


    def test_shell_prompt_requires_current_positive_evidence(self):
        for prompt in ('user@host:~/slot$ ', '\x1b[32muser@host:~/slot$\x1b[0m ', 'bash-5.2$ '):
            self.assertTrue(health.shell_ready('old session\n' + prompt))
        for prompt in ('user@host:~/slot$\n◑ GPT >', 'user@host:~/slot$ sleep 600 #', 'user@host:~/slot$ echo %', 'approval required', 'waiting for input', '', '$', 'price is $'):
            self.assertFalse(health.shell_ready(prompt))


if __name__ == '__main__':
    unittest.main()

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
import subprocess
from unittest.mock import patch
import yaml
from fleet import dispatcher, health, launcher, snapshot, state
from fleet.commands import CommandError


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.slot = str(root / 'slot')
        Path(self.slot).mkdir()
        (Path(self.slot) / 'RULES.md').write_text('rules')
        subprocess.run(['git', 'init', self.slot], check=True, capture_output=True)
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

    def command(self, argv, cwd, **kwargs):
        if argv[:2] == ['git', 'ls-files']:
            return subprocess.run(argv, cwd=cwd, check=True, capture_output=True).stdout
        return ''

    def launch(self, snapshots=None, automatic=False, preparation=None):
        with patch.object(launcher, '_prepare_branch', return_value='task-123-test', side_effect=preparation), patch.object(launcher.snapshot, 'collect', side_effect=snapshots or [self.s, self.s]), patch.object(launcher, 'orca', side_effect=self.orca), patch.object(health, 'orca', side_effect=self.orca), patch.object(launcher, 'run', side_effect=self.command), patch.object(dispatcher, 'status_policy', return_value=dispatcher.config.STATUS_DEFAULTS), patch.object(dispatcher, 'pad', side_effect=lambda c, *args: {'ref': args[2], 'title': 'Test', 'fields': {'status': 'active' if args[2] == 'PLAN-1' else 'open'}, 'parent_ref': 'PLAN-1'} if args[1] == 'show' else []), patch.object(dispatcher, 'run', return_value='task-123'):
            return launcher.launch(self.c, 'TASK-123', None if automatic else self.slot)

    def test_launch_preserves_and_reports_user_files(self):
        user = Path(self.slot) / 'user\nnotes'
        user.write_bytes(b'user\x00content')
        receipt = self.launch()
        self.assertIn(user.name, receipt['untracked_files'])
        self.assertEqual(receipt['untracked_count'], 2)
        prompt = Path(receipt['prompt']).read_text()
        self.assertIn('never stage, commit, move or delete them: ', prompt)
        self.assertIn(json.dumps(user.name), prompt)
        self.assertEqual(user.read_bytes(), b'user\x00content')

    def test_user_file_notice_survives_checkout_ignore_changes(self):
        user = Path(self.slot) / 'user-file'
        user.write_text('user content')
        def prepare(*args):
            (Path(self.slot) / '.git/info/exclude').write_text('user-file\n')
            return 'task-123-test'
        receipt = self.launch(preparation=prepare)
        self.assertIn(user.name, receipt['untracked_files'])
        self.assertIn(json.dumps(user.name), Path(receipt['prompt']).read_text())
        self.assertEqual(user.read_text(), 'user content')

    def test_launch_recovers_after_retirement_then_withheld_launch(self):
        old = {'id': 'old', 'ref': 'TASK-122', 'terminal': 'old-shell',
               'terminal_incarnation_id': 'inc', 'worktree': {'path': self.slot}}
        state.write(self.c, 'launches', {self.slot: old})
        state.write(self.c, 'retirements', {self.slot: {
            'launch_id': 'old', 'terminal': 'old-shell', 'terminal_incarnation_id': 'inc',
            'next_ref': 'TASK-123', 'stage': 'launch-attempted', 'failure': 'ValueError'}})
        self.assertEqual(self.launch()['terminal'], 'old-shell')
        self.assertEqual(self.sent, ['old-shell'])

    def test_builder_cannot_automatically_spend_saved_resets(self):
        self.launch()
        overlay, = Path(self.c['_state']).glob('omp-*.yml')
        settings = yaml.safe_load(overlay.read_text())
        for key in ('codexResets', 'claudeResets'):
            self.assertEqual(settings[key]['autoRedeem'], 'no')

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
        missing = str(Path(self.directory.name).resolve() / 'new-slot')
        self.c['builder']['slots'] = [missing]
        initial = deepcopy(self.s)
        initial['inventory']['builders'] = []
        initial['inventory']['worktrees'] = []
        fresh = deepcopy(initial)
        fresh['quota'] = {'may_start': False, 'reason': 'quota unknown'}
        with patch.object(launcher, '_prepare_branch', return_value='task-123-test'), patch.object(launcher.snapshot, 'collect', side_effect=[initial, fresh]), patch.object(launcher, 'orca', return_value={'worktree': {'id': 'new-tree', 'path': missing}}) as orca, patch.object(dispatcher, 'check_launch'), patch.object(launcher, 'run'):
            with self.assertRaisesRegex(ValueError, 'no builder input sent.*retry fleet launch'):
                launcher.launch(self.c, 'TASK-123', missing)
        receipt = state.read(self.c, 'launch-preparations')[missing]
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

    def test_wrapped_shell_prompt_requires_bounded_complete_prompt(self):
        for prompt in (
                'dev@devhost:~/orca/workspaces/project-beta/task-17-fle\net-lane-2$',
                'dev@devhost:~/orca/\nworkspaces/project-beta/\ntask-17-fleet-lane-2$',
                'dev@devhost:~/orca/\nworkspaces/\nproject-beta/\ntask-17-fleet-lane-2$',
                'ba\nsh-5.2$'):
            with self.subTest(prompt=prompt):
                self.assertTrue(health.shell_ready(prompt))
        for prompt in (
                'output ending\nin $',
                'dev@host:~/slot$\necho hello\n$',
                'dev@host:~/slot\n\ncontinuation$',
                'dev@host:~/\none/\ntwo/\nthree/\nfour$',
                'dev@host:~/slot\ncontinuation',
                ' π > ◑ GPT-6.1 Sol >\n╰─',
                'dev@host:~/slot\ncontinuation$ echo command'):
            with self.subTest(prompt=prompt):
                self.assertFalse(health.shell_ready(prompt))

    def test_captured_screen_wrap_metadata(self):
        # Orca --screen captures, 2026-11-08: scratch terminal at 120 columns
        # and the live Email lane-2 omp UI. wrapped marks the continuation row.
        captures = json.loads((Path(__file__).parent / 'fixtures' /
                               'orca-wrapped-screens.json').read_text())
        for name, ready in (
                ('wrapped_prompt', True),
                ('clean_prompt_after_wrapped_command', True),
                ('wrapped_output', False),
                ('omp_ui', False)):
            with self.subTest(capture=name):
                self.assertEqual(health.shell_ready(captures[name]), ready)

    def test_clipped_wrapped_prompt_is_not_complete_shell_evidence(self):
        for tail in (['dev@host:~$'], ['dev@host:~/path/', 'continuation$']):
            with self.subTest(tail=tail):
                self.assertFalse(health.shell_ready({
                    'tail': tail, 'tailRows': [{'wrapped': True} for _ in tail]}))

    def test_screen_wrap_metadata_is_authoritative(self):
        # Email's original 51-column prompt and the command reported on TASK-78.
        tail = ['dev@devhost:~/orca/workspaces/project-beta/task-17-fle', 'et-lane-2$']
        rows = [{'wrapped': False}, {'wrapped': True}]
        self.assertTrue(health.shell_ready({'tail': tail, 'tailRows': rows}))
        self.assertTrue(health.shell_ready({
            'tail': [tail[0], tail[1] + ' cd ~', 'dev@devhost:~$'],
            'tailRows': rows + [{'wrapped': False}]}))
        self.assertFalse(health.shell_ready({
            'tail': tail, 'tailRows': [{'wrapped': False}, {'wrapped': False}]}))
        self.assertTrue(health.shell_ready({'tail': tail}))
        self.assertTrue(health.shell_ready({
            'tail': tail + [''], 'tailRows': rows + [{'wrapped': False}]}))
        self.assertFalse(health.shell_ready({
            'tail': ['dev@host:~$', '16;8t'],
            'tailRows': [{'wrapped': False}, {'wrapped': True}]}))
        for rows in ([], [{'wrapped': True}], [{'wrapped': 'false'}, {'wrapped': False}]):
            with self.subTest(rows=rows):
                self.assertFalse(health.shell_ready({'tail': tail, 'tailRows': rows}))


class BranchPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / 'origin.git'
        self.slot = self.root / 'lane'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.slot)], check=True, capture_output=True)
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('switch', '-c', 'main')
        (self.slot / 'file').write_text('base')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.git('push', '-u', 'origin', 'main')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.c = {'repo': 'owner/repo', '_state': str(self.root / 'state')}
        self.github = patch.object(launcher, 'gh', return_value=[])
        self.github.start()
        self.addCleanup(self.github.stop)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.slot, check=True,
                              capture_output=True, text=True).stdout

    def prepare(self):
        return launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'}, str(self.slot))

    def commit(self):
        (self.slot / 'file').write_text('changed')
        self.git('add', '.')
        self.git('commit', '-m', 'change')

    def test_untracked_file_survives_preflight_and_switch(self):
        user = self.slot / 'user notes'
        user.write_bytes(b'private\x00bytes')
        before = self.git('rev-parse', 'HEAD')
        branch = launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                          str(self.slot), dry_run=True)
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.prepare(), branch)
        self.assertEqual(user.read_bytes(), b'private\x00bytes')

    def test_conflicting_untracked_file_refuses_without_switching(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        (self.slot / 'conflict').write_text('target content')
        self.git('add', 'conflict')
        self.git('commit', '-m', 'target')
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        (self.slot / 'conflict').write_text('user content')
        before = self.git('write-tree')
        with self.assertRaises(CommandError):
            launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                     str(self.slot), dry_run=True)
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')
        self.assertEqual(self.git('write-tree'), before)
        self.assertEqual((self.slot / 'conflict').read_text(), 'user content')

    def test_rules_preflight_preserves_untracked_ignored_and_absolute_files(self):
        (self.slot / '.git/info/exclude').write_text('ignored-rules\n')
        paths = [self.slot / 'user-rules', self.slot / 'ignored-rules', self.root / 'private-rules']
        for path in paths:
            path.write_text('user instructions')
            self.c['builder'] = {'rules_file': str(path) if path.parent == self.root else path.name}
            branch = launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                              str(self.slot), dry_run=True)
            self.assertEqual(branch, 'task-123-fix-branch')
            self.assertEqual(path.read_text(), 'user instructions')
            self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_rules_added_on_target_are_available_but_removed_rules_refuse(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        self.git('rm', 'file')
        (self.slot / 'new-rules').write_text('new instructions')
        self.git('add', 'new-rules')
        self.git('commit', '-m', 'replace rules')
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        self.c['builder'] = {'rules_file': 'new-rules'}
        self.assertEqual(launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                                 str(self.slot), dry_run=True), 'task-123-fix-branch')
        self.assertFalse((self.slot / 'new-rules').exists())
        self.c['builder']['rules_file'] = 'file'
        with self.assertRaisesRegex(ValueError, 'rules_file would be absent'):
            launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                     str(self.slot), dry_run=True)
        self.assertEqual((self.slot / 'file').read_text(), 'base')
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_rules_symlinks_resolve_against_target_not_current_files(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        (self.slot / 'rules-link').symlink_to('file')
        (self.slot / 'bad-link').symlink_to('missing')
        (self.slot / 'loop-link').symlink_to('loop-link')
        self.git('add', 'rules-link', 'bad-link', 'loop-link')
        self.git('commit', '-m', 'rules links')
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        self.c['builder'] = {'rules_file': 'rules-link'}
        self.assertEqual(launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                                 str(self.slot), dry_run=True), 'task-123-fix-branch')
        for name in ('bad-link', 'loop-link'):
            self.c['builder']['rules_file'] = name
            with self.assertRaisesRegex(ValueError, 'rules_file would be absent'):
                launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                         str(self.slot), dry_run=True)
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_preserved_rules_symlinks_cannot_hide_deleted_target_files(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        self.git('rm', 'file')
        self.git('commit', '-m', 'remove instructions')
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        links = [self.slot / 'user-rules', self.root / 'external-rules']
        for path in links:
            path.symlink_to(self.slot / 'file')
            self.c['builder'] = {'rules_file': str(path)}
            self.assertTrue(path.is_file())
            with self.assertRaisesRegex(ValueError, 'rules_file would be absent'):
                launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                         str(self.slot), dry_run=True)
            self.assertTrue(path.is_symlink())
        self.assertEqual((self.slot / 'file').read_text(), 'base')
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_rules_parent_components_require_real_target_directories(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        (self.slot / 'bad-rules').symlink_to('missing/../file')
        (self.slot / 'good-rules').symlink_to('present/../file')
        (self.slot / 'present').mkdir()
        (self.slot / 'present/kept').write_text('kept')
        self.git('add', 'bad-rules', 'good-rules', 'present')
        self.git('commit', '-m', 'rules paths with parent components')
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        self.c['builder'] = {'rules_file': 'bad-rules'}
        with self.assertRaisesRegex(ValueError, 'rules_file would be absent'):
            launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                     str(self.slot), dry_run=True)
        self.c['builder']['rules_file'] = 'good-rules'
        self.assertEqual(launcher._prepare_branch(self.c, 'TASK-123', {'title': 'Fix branch!'},
                                                 str(self.slot), dry_run=True), 'task-123-fix-branch')
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_same_ref_dirty_unpushed_recovery_preserves_everything(self):
        for branch, previous_ref in (('task-123-original-title', 'TASK-122'),
                                     ('custom-existing-branch', 'TASK-123')):
            with self.subTest(branch=branch):
                self.git('switch', '-c', branch)
                self.git('commit', '--allow-empty', '-m', 'private WIP')
                state.write(self.c, 'launches', {str(self.slot): {'ref': previous_ref}})
                (self.slot / 'file').write_text('staged WIP')
                self.git('add', 'file')
                (self.slot / 'file').write_text('unstaged WIP')
                (self.slot / 'untracked').write_bytes(b'private\\x00bytes')
                before = {name: self.git(*args) for name, args in (
                    ('head', ('rev-parse', 'HEAD')),
                    ('index', ('diff', '--cached')),
                    ('worktree', ('diff',)),
                    ('status', ('status', '--porcelain')))}
                # Fetch would fail; same-ref recovery must not need the network.
                self.git('remote', 'set-url', 'origin', str(self.root / 'absent-origin'))
                with self.assertRaisesRegex(ValueError, 'uncommitted tracked'):
                    self.prepare()
                for name, args in (('head', ('rev-parse', 'HEAD')),
                                   ('index', ('diff', '--cached')),
                                   ('worktree', ('diff',)),
                                   ('status', ('status', '--porcelain'))):
                    self.assertEqual(self.git(*args), before[name])
                self.assertEqual((self.slot / 'untracked').read_bytes(), b'private\\x00bytes')
                self.assertEqual(self.git('branch', '--show-current').strip(), branch)
                self.git('add', '.')
                self.git('commit', '--allow-empty', '-m', 'retain fixture WIP')

    def test_merged_previous_branch_starts_at_fetched_main(self):
        self.git('switch', '-c', 'task-122-old')
        self.commit()
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        self.git('merge', '--ff-only', 'task-122-old')
        self.git('push', 'origin', 'main')
        self.git('switch', 'task-122-old')
        expected = self.git('rev-parse', 'origin/main').strip()
        self.assertEqual(self.prepare(), 'task-123-fix-branch')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), expected)
        self.assertEqual(self.git('branch', '--show-current').strip(), 'task-123-fix-branch')

    def test_squash_merged_previous_branch_with_deleted_remote_is_safe(self):
        self.git('switch', '-c', 'task-122-old')
        self.commit()
        self.git('push', 'origin', 'HEAD')
        published = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', 'main')
        self.git('merge', '--squash', 'task-122-old')
        self.git('commit', '-m', 'squash publication')
        self.git('push', 'origin', 'main')
        self.git('push', 'origin', '--delete', 'task-122-old')
        self.git('switch', 'task-122-old')
        def history(c, *args):
            return [{'headRefOid': published}] if args[args.index('--head') + 1] == 'task-122-old' else []
        with patch.object(launcher, 'gh', side_effect=history):
            self.assertEqual(self.prepare(), 'task-123-fix-branch')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('rev-parse', 'origin/main'))
        self.git('switch', 'task-122-old')
        self.git('commit', '--allow-empty', '-m', 'private after publication')
        with patch.object(launcher, 'gh', side_effect=history):
            with self.assertRaisesRegex(ValueError, 'unpushed'):
                self.prepare()
        self.assertEqual(self.git('branch', '--show-current').strip(), 'task-122-old')

    def test_retry_reuses_unused_prepared_branch(self):
        self.git('branch', 'task-123-fix-branch')
        self.assertEqual(self.prepare(), 'task-123-fix-branch')
        self.assertEqual(self.prepare(), 'task-123-fix-branch')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)


    def test_dirty_and_unpushed_work_refuse_without_switching(self):
        (self.slot / 'file').write_text('user work')
        with self.assertRaisesRegex(ValueError, 'uncommitted'):
            self.prepare()
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')
        self.git('add', '.')
        self.git('commit', '-m', 'private')
        with self.assertRaisesRegex(ValueError, 'unpushed'):
            self.prepare()
        self.assertEqual((self.slot / 'file').read_text(), 'user work')
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')

    def test_reuses_pushed_unmerged_task_branch_without_repointing(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        self.commit()
        expected = self.git('rev-parse', 'HEAD').strip()
        self.git('push', 'origin', 'HEAD')
        self.git('switch', 'main')
        self.assertEqual(self.prepare(), 'task-123-fix-branch')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), expected)

    def test_merged_exact_name_is_preserved_and_new_suffix_starts_at_main(self):
        self.git('branch', 'task-123-fix-branch')
        with patch.object(launcher, 'gh', side_effect=lambda c, *args:
                          [{'number': 1}] if args[args.index('--head') + 1] == 'task-123-fix-branch' else []):
            self.assertEqual(self.prepare(), 'task-123-fix-branch-2')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)
        self.assertEqual(self.git('rev-parse', 'task-123-fix-branch').strip(), self.base)

    def test_deleted_merged_name_is_not_recreated(self):
        def history(c, *args):
            return [{'number': 1}] if args[args.index('--head') + 1] == 'task-123-fix-branch' else []
        with patch.object(launcher, 'gh', side_effect=history):
            self.assertEqual(self.prepare(), 'task-123-fix-branch-2')
            self.assertEqual(self.prepare(), 'task-123-fix-branch-2')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)

    def test_unpushed_existing_task_branch_refuses(self):
        self.git('switch', '-c', 'task-123-fix-branch')
        self.commit()
        self.git('switch', 'main')
        with self.assertRaisesRegex(ValueError, 'existing task branch has unpushed'):
            self.prepare()
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')


if __name__ == '__main__':
    unittest.main()

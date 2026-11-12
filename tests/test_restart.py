import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import shlex
import subprocess
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
                                                        'model': 'provider/model', 'resolved_model': 'provider/model',
                                                        'thinking': 'medium'}, 'quota': {}}
        self.c['coordinator'] = {'terminal': 'main', 'run': 'run'}
        self.c['builder']['rules_file'] = 'RULES.md'
        (root / 'RULES.md').write_text('rules')
        self.prepare_branch = restart.launcher._prepare_branch
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
        self.record['argv'] = list(self.argv)
        state.write(self.c, 'launches', {self.slot: self.record})
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in self.argv) + b'\0')
        (self.process / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=target\0')
        (self.process / 'cwd').symlink_to(root)
        (self.process / 'exe').symlink_to(root / 'omp')
        self.write_stat()
        boot = self.proc / 'sys/kernel/random/boot_id'
        boot.parent.mkdir(parents=True)
        boot.write_text('test-boot')
        proc_patch = patch.object(restart, 'PROC', self.proc)
        proc_patch.start()
        self.addCleanup(proc_patch.stop)
        self.item = {'fields': '{"status":"done"}'}
        self.prs = [{'number': 1, 'title': 'TASK-1 fix', 'body': '', 'mergedAt': '2026-01-01T00:00:00Z', 'url': 'https://example/pr/1'}]
        self.comments = []
        self.sent = []
        self.exited = False
        self.residue = None
        self.cleanup_works = True
        self.keep_omp = False
        self.shell_exe = 'bash'
        self.shell_foreground = '321'
        self.next_status = 'in-progress'
        self.next_parent_status = 'active'
        self.next_edges = []
        self.send_error = False
        self.patches = [patch.object(restart.health, 'collect', side_effect=lambda c: copy.deepcopy(self.inventory)),
                        patch.object(restart, 'orca', side_effect=self.orca),
                        patch.object(restart, 'json_run', side_effect=self.pad),
                        patch.object(restart, 'gh', side_effect=lambda *a: self.search_page(self.prs)),
                        patch.object(restart.snapshot, 'collect', side_effect=lambda c: {
                            'errors': [], 'inventory': copy.deepcopy(self.inventory), 'quota': {'may_start': True}, 'disk': {'low': False}}),
                        patch.object(restart.dispatcher, 'pad', side_effect=self.dispatcher_pad),
                        patch.object(restart.dispatcher, 'run', return_value='task-1'),
                        patch.object(restart.launcher, '_prepare_branch', return_value='task-2'),
                        patch.object(restart.launcher, 'orca', side_effect=self.orca),
                        patch.object(restart.launcher, 'launch', return_value={'id': 'new'})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.launch = restart.launcher.launch

    @staticmethod
    def search_page(prs, total=None, more=False, cursor=None):
        return {'data': {'search': {
            'issueCount': len(prs) if total is None else total, 'nodes': prs,
            'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}}

    def test_many_unrelated_refs_paginate_to_body_only_completion(self):
        unrelated = [{**self.prs[0], 'number': n, 'title': f'TASK-{n} unrelated'}
                     for n in range(2, 102)]
        correct = {**self.prs[0], 'title': 'Fix restart', 'body': 'Pad-Ref: TASK-1'}
        with patch.object(restart, 'gh', side_effect=[
                self.search_page(unrelated, 101, True, 'next'),
                self.search_page([correct], 101)]):
            self.assertEqual(self.run_restart()['retirement']['completion']['pr'], correct['url'])

    def test_different_ref_historical_completion_survives_launch_date_bound(self):
        self.record['at'] = 1794010000
        state.write(self.c, 'launches', {self.slot: self.record})
        with patch.object(restart, 'gh', side_effect=[
                self.search_page([]), self.search_page(self.prs)]):
            self.assertEqual(self.run_restart()['retirement']['completion']['pr'], self.prs[0]['url'])

    def test_incomplete_search_never_sends_retirement_input(self):
        for response in (
                self.search_page(self.prs, 1000),
                self.search_page(self.prs, 2),
                self.search_page([], 2, True, 'next'),
                {'errors': [{'message': 'API failed'}], **self.search_page(self.prs)},
                {'data': None}):
            with self.subTest(response=response):
                with patch.object(restart, 'gh', return_value=response):
                    self.refused('completion PR search')

    def test_search_page_changes_and_repeated_rows_refuse(self):
        first = self.search_page(self.prs, 3, True, 'next')
        for second in (self.search_page(self.prs, 3, True, 'next'),
                       self.search_page([], 2)):
            with self.subTest(second=second):
                with patch.object(restart, 'gh', side_effect=[first, second]):
                    self.refused('incomplete or inconsistent')

    def test_search_api_error_refuses_without_historical_fallback(self):
        with patch.object(restart, 'gh', side_effect=CommandError('unavailable')):
            self.refused('search API failed')

    def write_log(self):
        self.log.write_text('\n'.join(json.dumps(e) for e in self.events) + '\n')

    def dispatcher_pad(self, c, *args):
        if args == ('collection', 'list'):
            raise CommandError('schema unavailable')
        if args[1] == 'deps':
            return self.next_edges
        if args[2] == 'PLAN-1':
            return {'ref': args[2], 'fields': {'status': self.next_parent_status}}
        return {'ref': args[2], 'title': 'Test', 'fields': {'status': self.next_status}, 'parent_ref': 'PLAN-1'}

    def pad(self, argv, cwd):
        if argv[0] == 'omp':
            return {'held': ['session'], 'unknown': []}
        return self.comments if 'comments' in argv else self.item

    def orca(self, c, *args):
        if args[:2] == ('orchestration', 'run-show'):
            return {}
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.terminal}
        if args[:2] == ('terminal', 'read'):
            prompt = 'dev@host:~/slot$' + (' ' + self.residue if self.residue else '')
            return {'terminal': {'tail': [prompt] if self.exited else self.screen}}
        if args[:2] == ('worktree', 'ps'):
            return {'worktrees': [{'worktreeId': 'tree', 'agents': [self.agent]}]}
        if args[:2] == ('terminal', 'send'):
            self.sent.append(args)
            if args[-2:] == ('--text', '\x15'):
                receipt = state.read(c, 'retirements')[self.slot]
                self.assertEqual(receipt['residue_cleanup']['stage'], 'input-attempted')
                if self.cleanup_works:
                    self.residue = None
                return {'accepted': True}
            # Intent must already be durable even if the transport fails.
            self.assertEqual(state.read(c, 'retirements')[self.slot]['stage'], 'exit-attempted')
            if self.send_error:
                raise RuntimeError('lost transport')
            self.exited = True
            if self.residue:
                if not self.keep_omp:
                    (self.process / 'stat').unlink()
                shell = self.proc / '321'
                shell.mkdir()
                fields = ['S', '1', '321', '1', '456', self.shell_foreground] + ['0'] * 13 + ['2000']
                (shell / 'stat').write_text('321 (bash) ' + ' '.join(fields))
                (shell / 'exe').symlink_to(shell / self.shell_exe)
                (shell / self.shell_exe).touch()
                (shell / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=target\0')
            return {'accepted': True}
        self.fail(args)

    def run_restart(self):
        return restart.restart(self.c, 'TASK-2', self.slot, 'TASK-1')

    def refused(self, reason):
        with self.assertRaisesRegex(ValueError, reason):
            self.run_restart()
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()

    def test_override_uses_selected_provider_before_retiring(self):
        self.c['builder']['allowed_models'] = {'other/model': ['high']}
        def admission(config):
            return {'errors': [], 'inventory': copy.deepcopy(self.inventory),
                    'quota': {'may_start': config['quota']['provider'] != 'other',
                              'reason': 'selected pool exhausted'}, 'disk': {'low': False}}
        with patch.object(restart.snapshot, 'collect', side_effect=admission):
            with self.assertRaisesRegex(ValueError, 'selected pool exhausted'):
                restart.restart(self.c, 'TASK-2', self.slot, 'TASK-1',
                                model='other/model', thinking='high')
        self.assertEqual(self.sent, [])
        self.assertEqual(self.c['builder']['resolved_model'], 'provider/model')
        self.assertFalse(state.read(self.c, 'retirements', {}))

    def test_relaunch_selection_preserves_old_identity_and_project_defaults(self):
        self.relaunch_repo()
        self.c['builder']['allowed_models'] = {'other/model': ['high']}
        self.c['builder']['plan_models'] = {'PLAN-1': {'model': 'other/model', 'thinking': 'high'}}
        before = copy.deepcopy(self.c)
        result = self.run_relaunch(model='provider/new-model', thinking='medium')
        evidence = result['retirement']['relaunch']
        self.assertEqual(evidence['old_model'], 'provider/old-model')
        self.assertEqual(evidence['old_thinking'], 'high')
        self.assertEqual(evidence['new_model'], 'provider/new-model')
        self.assertEqual(evidence['new_thinking'], 'medium')
        self.assertEqual(evidence['new_model_source'], 'override')
        self.assertEqual(evidence['plan_ref'], 'PLAN-1')
        self.assertEqual(self.c, before)

    def test_relaunch_mapped_quota_refuses_before_retirement(self):
        self.relaunch_repo()
        self.c['builder']['allowed_models'] = {'other/model': ['high']}
        self.c['builder']['plan_models'] = {'PLAN-1': {'model': 'other/model', 'thinking': 'high'}}
        def admission(config):
            return {'errors': [], 'inventory': copy.deepcopy(self.inventory),
                    'quota': {'may_start': config['quota']['provider'] != 'other',
                              'reason': 'selected pool exhausted'}, 'disk': {'low': False}}
        with patch.object(restart.snapshot, 'collect', side_effect=admission):
            with self.assertRaisesRegex(ValueError, 'selected pool exhausted'):
                self.run_relaunch()
        self.assertEqual(self.sent, [])
        self.assertFalse(state.read(self.c, 'retirements', {}))

    def test_relaunch_invalid_override_leaves_old_session_untouched(self):
        self.relaunch_repo()
        for pair in ({'model': 'other/model'}, {'thinking': 'high'},
                     {'model': 'other/model', 'thinking': 'high'}):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                self.run_relaunch(**pair)
        self.assertEqual(self.sent, [])
        self.assertFalse(state.read(self.c, 'retirements', {}))
    def relaunch_repo(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.slot, check=True,
                                  capture_output=True).stdout
        git('init')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        git('add', 'RULES.md')
        git('commit', '-m', 'base')
        git('switch', '-c', 'task-1-wip')
        self.c['builder']['resolved_model'] = 'provider/new-model'
        self.events[1:1] = [
            {'type': 'model_change', 'model': 'provider/old-model'},
            {'type': 'thinking_level_change', 'thinkingLevel': 'high'}]
        self.write_log()
        return git

    def run_relaunch(self, **kwargs):
        with patch.object(restart.steering, '_main', return_value='main'), \
                patch.object(restart.threads, 'pad') as pad, \
                patch.object(restart.launcher, '_prepare_branch', wraps=self.prepare_branch):
            result = restart.relaunch(self.c, 'TASK-1', self.slot, 'replace unfinished session', **kwargs)
            self.assertIn('replace unfinished session', pad.call_args.args[2])
            return result

    def test_relaunch_idle_requires_no_publication_and_records_identity(self):
        git = self.relaunch_repo()
        self.prs = []
        result = self.run_relaunch()
        evidence = result['retirement']['relaunch']
        self.assertEqual(evidence['old_launch_id'], 'old')
        self.assertEqual(evidence['old_session_id'], 'session')
        self.assertEqual(evidence['old_model'], 'provider/old-model')
        self.assertEqual(evidence['old_thinking'], 'high')
        self.assertEqual(evidence['new_model'], 'provider/new-model')
        self.assertEqual(git('branch', '--show-current').strip(), b'task-1-wip')
        self.assertEqual(result['retirement']['stage'], 'launched')

    def test_relaunch_running_refuses_with_stop_hint_without_input(self):
        self.relaunch_repo()
        self.agent['state'] = 'working'
        with self.assertRaisesRegex(ValueError, 'run `fleet thread stop TASK-1` first'):
            self.run_relaunch()
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()

    def test_relaunch_tracked_and_staged_work_requires_explicit_preservation(self):
        git = self.relaunch_repo()
        rules = Path(self.slot) / 'RULES.md'
        rules.write_text('staged work')
        git('add', 'RULES.md')
        rules.write_text('unstaged work')
        before_index = git('show', ':RULES.md')
        with self.assertRaisesRegex(ValueError, 'RULES.md.*--keep-uncommitted'):
            self.run_relaunch()
        self.assertEqual(self.sent, [])
        result = self.run_relaunch(keep_uncommitted=True)
        self.assertEqual(result['retirement']['relaunch']['tracked_files'], ['RULES.md'])
        self.assertEqual(rules.read_text(), 'unstaged work')
        self.assertEqual(git('show', ':RULES.md'), before_index)

    def test_relaunch_stopped_session_requires_matching_completed_stop_journal(self):
        self.relaunch_repo()
        process = restart._foreground(self.c, self.record, self.slot, 'target')
        self.events[-1]['message']['stopReason'] = 'aborted'
        self.write_log()
        with self.assertRaisesRegex(ValueError, 'final assistant stop'):
            self.run_relaunch()
        state.write(self.c, 'stops', {'old': {'ref': 'TASK-1', 'token': 'stop'}})
        name = restart.threads._name('TASK-1')
        attempt = {'settled': False, 'steering': {'stop': {'steps': [
            {'step': 'identity-proven', 'proof': {'session': 'wrong', 'process': process}},
            {'step': 'submitted'}]}}}
        state.write(self.c, name, {'TASK-1': {'attempts': {'old': attempt}}})
        with self.assertRaisesRegex(ValueError, 'different session'):
            self.run_relaunch()
        attempt['steering']['stop']['steps'][0]['proof']['session'] = 'session'
        state.write(self.c, name, {'TASK-1': {'attempts': {'old': attempt}}})
        self.run_relaunch()
        settled = state.read(self.c, name)['TASK-1']['attempts']['old']
        self.assertTrue(settled['settled'])
        self.assertEqual(settled['relaunch']['reason'], 'replace unfinished session')

    def test_relaunch_wrong_ref_and_non_main_refuse_without_input(self):
        self.relaunch_repo()
        with patch.object(restart.steering, '_main', return_value='main'):
            with self.assertRaisesRegex(ValueError, 'exact finished-task launch'):
                restart.relaunch(self.c, 'TASK-2', self.slot, 'replace')
        with patch.dict('os.environ', {'ORCA_TERMINAL_HANDLE': 'builder'}):
            with self.assertRaisesRegex(ValueError, 'Main terminal identity'):
                restart.relaunch(self.c, 'TASK-1', self.slot, 'replace')
        self.assertEqual(self.sent, [])

    def test_relaunch_pad_failure_keeps_old_session_alive(self):
        self.relaunch_repo()
        with patch.object(restart.steering, '_main', return_value='main'), \
                patch.object(restart.threads, 'pad', side_effect=CommandError('Pad unavailable')):
            with self.assertRaisesRegex(CommandError, 'Pad unavailable'):
                restart.relaunch(self.c, 'TASK-1', self.slot, 'replace')
        self.assertEqual(self.sent, [])
        self.assertEqual(state.read(self.c, 'retirements', {}), {})
    def test_relaunch_deleted_preserved_rules_refuses_before_retirement(self):
        self.relaunch_repo()
        (Path(self.slot) / 'RULES.md').unlink()
        with self.assertRaisesRegex(ValueError, 'rules_file is absent in preserved worktree.*old session untouched'):
            self.run_relaunch(keep_uncommitted=True)
        self.assertEqual(self.sent, [])
        self.launch.assert_not_called()
        self.assertEqual(state.read(self.c, 'retirements', {}), {})

    def test_tracked_work_refuses_before_exit(self):
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.slot, check=True,
                                  capture_output=True).stdout
        git('init')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        git('add', 'RULES.md')
        git('commit', '-m', 'base')
        (Path(self.slot) / 'RULES.md').write_text('user work')
        with patch.object(restart.launcher, '_prepare_branch', wraps=self.prepare_branch):
            self.refused('uncommitted tracked.*old session untouched')
        self.assertFalse(self.exited)
        self.assertEqual((Path(self.slot) / 'RULES.md').read_text(), 'user work')

    def test_checkout_conflict_refuses_before_exit(self):
        git = lambda *args: subprocess.run(['git', *args], cwd=self.slot, check=True,
                                           capture_output=True).stdout
        remote = Path(self.temp.name) / 'remote.git'
        git('init', '--bare', str(remote))
        git('init')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        git('switch', '-c', 'main')
        git('add', 'RULES.md')
        git('commit', '-m', 'base')
        git('remote', 'add', 'origin', str(remote))
        git('push', 'origin', 'main')
        git('switch', '-c', 'task-2-test')
        user = Path(self.slot) / 'conflict'
        user.write_text('target')
        git('add', 'conflict')
        git('commit', '-m', 'target')
        git('push', 'origin', 'HEAD')
        git('switch', 'main')
        user.write_text('user')
        with patch.object(restart.launcher, '_prepare_branch', wraps=self.prepare_branch), \
                patch.object(restart.launcher, 'gh', return_value=[]):
            self.refused('old session untouched')
        self.assertFalse(self.exited)
        self.assertEqual(user.read_text(), 'user')
        self.assertEqual(git('branch', '--show-current').strip(), b'main')

    def test_finished_idle_session_exits_once_then_launches(self):
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(result['retirement']['session']['id'], 'session')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][-3:], ('--text', '/exit', '--enter'))
        self.sent.clear()
        self.launch.reset_mock()
        self.refused('already attempted')

    def same_ref(self, launched=1767225599):
        self.record['at'] = launched
        state.write(self.c, 'launches', {self.slot: self.record})
        self.comments = [{'id': 'next-piece', 'body': 'TASK-1 handoff: implement the next CLI piece',
                          'created_at': '2026-01-01T00:00:00.500Z'}]
        self.run_restart = lambda: restart.restart(self.c, 'TASK-1', self.slot, 'TASK-1')

    def test_same_ref_new_publication_and_concrete_handoff_allow_restart(self):
        self.same_ref()
        self.item = {'fields': {'status': 'in-progress'}}
        result = self.run_restart()
        self.assertEqual(result['retirement']['completion']['handoff'], 'next-piece')
        self.assertEqual(self.sent[0][-3:], ('--text', '/exit', '--enter'))

    def test_same_ref_without_new_publication_refuses_even_when_pad_done(self):
        self.same_ref()
        self.prs = []
        self.refused('no merged PR')

    def test_same_ref_merge_before_or_at_launch_refuses(self):
        for launched in (1767225600, 1767225601):
            with self.subTest(launched=launched):
                self.same_ref(launched)
                self.refused('merged after this launch')

    def test_same_ref_old_or_marker_only_handoff_refuses(self):
        for body, created in (
                ('TASK-1 handoff: implement next piece', '2025-12-31T23:59:59Z'),
                ('TASK-1 handoff: implement next piece', '2026-01-01T00:00:00Z'),
                ('TASK-1 handoff:', '2026-01-01T00:00:01Z')):
            with self.subTest(body=body, created=created):
                self.same_ref()
                self.comments[0].update(body=body, created_at=created)
                self.refused('neither Pad done nor handed off')

    def test_same_ref_launch_timestamp_must_be_valid(self):
        for launched in (None, '2026-01-01', True, float('nan'), float('inf')):
            with self.subTest(launched=launched):
                self.same_ref(launched)
                self.refused('launch timestamp')

    def test_same_ref_merge_comparison_uses_timezone(self):
        self.same_ref(1767225600)
        self.prs[0]['mergedAt'] = '2026-01-01T01:00:00.250+01:00'
        self.assertEqual(self.run_restart()['retirement']['completion']['handoff'], 'next-piece')

    def test_same_ref_competing_receipt_still_blocks(self):
        self.same_ref()
        state.write(self.c, 'last-launch', {**self.record, 'id': 'competing'})
        self.refused('already assigned|unresolved launch receipt')

    def test_same_ref_active_builder_on_other_terminal_still_blocks(self):
        self.same_ref()
        self.inventory['builders'].append({
            **self.builder, 'path': '/other-slot', 'terminal': 'other',
            'task': 'TASK-1', 'worktree': 'other-tree', 'incarnation_id': 'other-incarnation'})
        self.refused('already assigned')

    def test_reply_residue_is_cleared_once_before_launch(self):
        self.residue = '16;8t'
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(result['retirement']['residue_cleanup']['stage'], 'clean-prompt-verified')
        self.assertEqual(self.sent[-1][-2:], ('--text', '\x15'))
        self.assertEqual(len(self.sent), 2)
        self.launch.assert_called_once()

    def test_failed_line_kill_never_retries_or_launches(self):
        self.residue, self.cleanup_works = '16;8t', False
        with self.assertRaisesRegex(ValueError, 'manually verify and clear'):
            self.run_restart()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(state.read(self.c, 'retirements')[self.slot]['stage'], 'shell-verified-with-residue')
        self.launch.assert_not_called()
        self.sent.clear()
        self.refused('already attempted')

    def test_ambiguous_cleanup_transport_never_retries_or_launches(self):
        self.residue = '16;8t'
        original = self.orca
        def lost(c, *args):
            result = original(c, *args)
            if args[-2:] == ('--text', '\x15'):
                raise RuntimeError('transport lost after input')
            return result
        with patch.object(restart, 'orca', side_effect=lost):
            with self.assertRaisesRegex(ValueError, 'never retry cleanup'):
                self.run_restart()
        self.assertEqual(len(self.sent), 2)
        self.launch.assert_not_called()
        self.sent.clear()
        self.refused('already attempted')

    def test_changed_residue_before_cleanup_preserves_input(self):
        self.residue = '16;8t'
        original = self.orca
        reads = 0
        def changed(c, *args):
            nonlocal reads
            if self.exited and args[:2] == ('terminal', 'read'):
                reads += 1
                if reads == 2:
                    self.residue = '16;8t echo draft'
            return original(c, *args)
        with patch.object(restart, 'orca', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'launch withheld'):
                self.run_restart()
        self.assertEqual(self.residue, '16;8t echo draft')
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()

    def test_live_omp_refuses_residue_cleanup(self):
        self.residue, self.keep_omp = '16;8t', True
        with self.assertRaisesRegex(ValueError, 'launch withheld'):
            self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()

    def test_non_shell_foreground_refuses_cleanup(self):
        self.residue, self.shell_exe = '16;8t', 'cat'
        with self.assertRaisesRegex(ValueError, 'launch withheld'):
            self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()

    def test_background_shell_refuses_cleanup(self):
        self.residue, self.shell_foreground = '16;8t', '999'
        with self.assertRaisesRegex(ValueError, 'launch withheld'):
            self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()
    def test_arbitrary_or_oversized_input_never_receives_line_kill(self):
        for text in ('echo hello', '1;' + '9' * 1000 + 'R'):
            with self.subTest(text=text):
                self.residue = text
                # Exercise one post-exit observation, then expire the bounded wait.
                with patch.object(restart.time, 'monotonic', side_effect=[0, 1, 16]), \
                        patch.object(restart.time, 'sleep'):
                    with self.assertRaisesRegex(ValueError, 'timed out'):
                        self.run_restart()
                self.assertEqual(len(self.sent), 1)
                self.launch.assert_not_called()
                state.write(self.c, 'retirements', {})
                self.sent.clear()
                self.exited = False
                self.write_stat()
                for path in (self.proc / '321').iterdir():
                    path.unlink()
                (self.proc / '321').rmdir()

    def test_changed_boot_refuses_cleanup(self):
        self.residue = '16;8t'
        original = self.orca
        def change_boot(c, *args):
            result = original(c, *args)
            if args[:2] == ('terminal', 'send'):
                (self.proc / 'sys/kernel/random/boot_id').write_text('new-boot')
            return result
        with patch.object(restart, 'orca', side_effect=change_boot):
            with self.assertRaisesRegex(ValueError, 'launch withheld'):
                self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()

    def test_extra_pts_process_refuses_cleanup(self):
        self.residue = '16;8t'
        extra = self.proc / '999'
        extra.mkdir()
        (extra / 'stat').write_text('999 (cat) ' + ' '.join(
            ['S', '321', '999', '1', '456', '321'] + ['0'] * 13 + ['3000']))
        with self.assertRaisesRegex(ValueError, 'launch withheld'):
            self.run_restart()
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_not_called()


    def test_reply_patterns_never_make_normal_launch_ready(self):
        from fleet import health
        for residue in ('16;8t', '12;80R', '4;600;800t', '8;24;80t'):
            with self.subTest(residue=residue):
                text = 'dev@host:~/slot$ ' + residue
                self.assertEqual(health.shell_reply_residue(text), residue)
                self.assertFalse(health.shell_ready(text))
        for residue in ('echo hello', '16;8t;pwd', '16;8t ', ' 16;8t', '1234567;8t',
                        '16;8x', '0;0;0t', '1;2R3;4R', '\x1b[31m16;8t', '1;' + '9' * 1000 + 'R'):
            with self.subTest(residue=residue):
                self.assertIsNone(health.shell_reply_residue('dev@host:~/slot$ ' + residue))

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

    def test_idle_approval_words_are_not_dialogs(self):
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        self.screen = screens['idle']
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(self.sent[0][-3:], ('--text', '/exit', '--enter'))

    def test_captured_interactive_and_background_screens_refuse(self):
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        for name, screen in screens.items():
            with self.subTest(surface=name):
                if name == 'idle':
                    continue
                self.screen = screen
                self.refused('idle|dialog|background')

    def test_background_job_marker_with_idle_pi_still_refuses(self):
        # Repaint boundary: the idle glyph alone cannot override the job segment.
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        footer = screens['background'][-2]
        self.screen = ['finished', ' π' + footer[footer.index(' >'):], '╰─']
        self.refused('background job status')

    def test_working_and_unknown_screens_refuse(self):
        for screen in (['⠹ working > ◑ GPT >', '╰─'], [], ['old working footer']):
            with self.subTest(screen=screen):
                self.screen = screen
                self.refused('idle|dialog|background')

    def test_real_post_restart_clipped_screen_never_proves_empty_composer(self):
        capture = json.loads((Path(__file__).parent / 'fixtures' /
                              'orca-stale-screen.json').read_text())
        self.screen = capture['terminal']['tail']
        for agent_state in ('done', 'idle', 'working'):
            with self.subTest(agent_state=agent_state):
                self.agent.update(state=agent_state, programStatus=None, interrupted=False)
                self.refused('no positive idle OMP composer')

    def chronology_processes(self):
        fields = (self.process / 'stat').read_text().rsplit(')', 1)[1].split()
        fields[3] = '321'
        (self.process / 'stat').write_text('123 (omp) ' + ' '.join(fields))
        shell = self.proc / '321'
        shell.mkdir()
        shell_fields = ['S', '1', '321', '321', '456', '123'] + ['0'] * 13 + ['500']
        (shell / 'stat').write_text('321 (bash) ' + ' '.join(shell_fields))
        executable = Path(self.slot) / 'bash'
        executable.touch()
        (shell / 'exe').symlink_to(executable)
        (shell / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=target\0')
        runtime = self.proc / '999'
        runtime.mkdir()
        return shell, runtime

    def test_terminal_chronology_uses_shell_not_fresh_builder_start(self):
        shell, runtime = self.chronology_processes()
        # The builder starts at tick 1000, but its terminal survives from tick 500.
        for started, expected in ((499, False), (500, False), (750, True)):
            with self.subTest(runtime_start=started):
                fields = ['S', '1', '999', '999', '0', '-1'] + ['0'] * 13 + [str(started)]
                (runtime / 'stat').write_text('999 (node) ' + ' '.join(fields))
                with patch.object(restart, 'orca', return_value={
                        'app': {'pid': 999}, 'runtime': {'reachable': True}}):
                    self.assertEqual(restart._terminal_predates_runtime(
                        self.c, self.record, self.tree, 'target'), expected)
        self.assertEqual(self.sent, [])

    def test_unavailable_or_unbound_chronology_never_claims_restart(self):
        shell, runtime = self.chronology_processes()
        fields = ['S', '1', '999', '999', '0', '-1'] + ['0'] * 13 + ['750']
        (runtime / 'stat').write_text('999 (node) ' + ' '.join(fields))
        for status in ({}, {'app': {'pid': True}, 'runtime': {'reachable': True}},
                       {'app': {'pid': 999}, 'runtime': {'reachable': False}},
                       {'app': {'pid': 998}, 'runtime': {'reachable': True}}):
            with self.subTest(status=status), patch.object(restart, 'orca', return_value=status):
                self.assertFalse(restart._terminal_predates_runtime(
                    self.c, self.record, self.tree, 'target'))
        with patch.object(restart, 'orca', side_effect=CommandError('unavailable')):
            self.assertFalse(restart._terminal_predates_runtime(
                self.c, self.record, self.tree, 'target'))
        (shell / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=another\0')
        self.assertFalse(restart._terminal_predates_runtime(
            self.c, self.record, self.tree, 'target'))
        self.assertEqual(self.sent, [])

    def test_post_restart_missing_program_status_preserves_guarded_retirement(self):
        for agent_state in ('done', 'idle'):
            for missing in (False, True):
                with self.subTest(state=agent_state, missing=missing):
                    self.agent.update(state=agent_state, programStatus=None, interrupted=False)
                    if missing:
                        self.agent.pop('programStatus')
                    proof = restart._idle(self.c, self.record, self.tree, 'target')
                    self.assertEqual(proof['id'], 'session')
                    self.assertEqual(self.sent, [])

    def test_running_unknown_and_interrupted_exact_panes_refuse(self):
        for agent in ({}, {**self.agent, 'state': 'working', 'programStatus': None},
                      {**self.agent, 'programStatus': {}},
                      {**self.agent, 'programStatus': {'state': 'running'}},
                      {**self.agent, 'programStatus': {'state': 'unknown'}},
                      {**self.agent, 'programStatus': 'idle'},
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

    @staticmethod
    def compaction_pair():
        return [{'type': 'compaction'},
                {'type': 'message', 'message': {'role': 'custom', 'customType': 'eval-state-context'}}]

    def test_post_turn_compaction_allows_retirement(self):
        self.events.extend(self.compaction_pair())
        self.write_log()
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    def test_compaction_does_not_hide_later_work_or_unknown_summary(self):
        original = copy.deepcopy(self.events)
        pair = self.compaction_pair()
        for tail in (
                pair + [{'type': 'message', 'message': {'role': 'user'}}],
                pair + [{'type': 'message', 'message': {'role': 'assistant', 'stopReason': 'toolUse'}}],
                [pair[0], {'type': 'message', 'message': {'role': 'custom', 'customType': 'unknown'}}],
                [pair[0]], [pair[1]],
                [pair[0], {'type': 'title_change'}, pair[1]]):
            with self.subTest(tail=tail):
                self.events = copy.deepcopy(original) + tail
                self.write_log()
                self.refused('final assistant|later work')

    def test_compaction_appended_during_admission_is_reverified(self):
        def append():
            self.events.extend(self.compaction_pair())
            self.write_log()
        with self.during_admission(append):
            self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    def test_session_must_end_at_stop_except_known_metadata(self):
        for event in ({'type': 'message', 'message': {'role': 'user'}}, {'type': 'custom'}, {'type': 'future_event'}):
            with self.subTest(event=event):
                self.events = self.events[:2] + [event]
                self.write_log()
                self.refused('final assistant|later work')
        self.events = self.events[:2] + [{'type': 'credential_pin'}, {'type': 'title_change'}]
        self.write_log()
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    @staticmethod
    def model_usage_tail():
        # TASK-99: unmodified final stop and four trailing events from fleet
        # session 01a1c718, 2026-11-10T20:18:25.127Z–20:19:17.359Z.
        return [json.loads(line) for line in (Path(__file__).parent / 'fixtures' /
                'omp-18.18.6-model-usage.jsonl').read_text().splitlines()]

    def test_captured_post_turn_model_usage_sequence_allows_retirement(self):
        self.events = self.events[:1] + self.model_usage_tail()
        self.write_log()
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    def test_model_usage_does_not_hide_later_work_or_unknown_events(self):
        for event in (
                {'type': 'message', 'message': {'role': 'user'}},
                {'type': 'message', 'message': {'role': 'toolResult'}},
                {'type': 'future_event'},
                {**self.model_usage_tail()[1], 'customType': 'unknown'}):
            with self.subTest(event=event):
                self.events = self.events[:1] + self.model_usage_tail() + [event]
                self.write_log()
                self.refused('final assistant|later work')

    @staticmethod
    def eval_state_context():
        # TASK-97: actual trailing event from project-gamma session 01a1c704,
        # 2026-11-10T20:11:32.153Z; only the host reminder body is redacted.
        return json.loads((Path(__file__).parent / 'fixtures' /
                           'omp-18.18.6-eval-state-context.json').read_text())

    def test_captured_post_turn_eval_state_context_allows_retirement(self):
        self.events.append(self.eval_state_context())
        self.write_log()
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')

    def test_eval_state_context_requires_exact_encoding_and_hidden_display(self):
        captured = self.eval_state_context()
        missing_display = dict(captured)
        del missing_display['display']
        for event in (
                {**captured, 'display': True},
                {**captured, 'display': 0},
                {**captured, 'display': None},
                missing_display,
                {**captured, 'customType': 'unknown'},
                {**captured, 'type': 'custom'}):
            with self.subTest(event=event):
                self.events = self.events[:2] + [event]
                self.write_log()
                self.refused('later work')

    def test_eval_state_context_does_not_hide_later_user_message(self):
        self.events.extend([self.eval_state_context(),
                            {'type': 'message', 'message': {'role': 'user'}}])
        self.write_log()
        self.refused('no final assistant stop')

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

    def test_branch_preflight_command_failure_never_retires(self):
        with patch.object(restart.launcher, '_prepare_branch',
                          side_effect=CommandError('git checkout preflight refused')):
            self.refused('restart preflight withheld.*git checkout preflight refused.*old session untouched')
        self.assertFalse(self.exited)
        self.assertEqual(state.read(self.c, 'retirements', {}), {})

    def test_cli_reports_all_post_retirement_launch_failures_and_persists_recovery(self):
        from fleet import cli
        config = str(Path(self.slot) / 'private config.yml')
        self.c['_path'] = config
        recovery = shlex.join(['fleet', '--config', config, 'launch', 'TASK-2', '--slot', self.slot])
        for error in (ValueError('quota changed'), CommandError('git checkout refused'),
                      RuntimeError('transport lost'), OSError('disk unavailable'), KeyError('id')):
            with self.subTest(error=type(error).__name__):
                state.write(self.c, 'retirements', {})
                self.exited = False
                self.sent.clear()
                self.launch.reset_mock()
                self.launch.side_effect = error
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(cli, 'load', return_value=self.c), \
                        patch.object(cli.sys, 'argv', ['fleet', '--config', config, 'restart',
                            'TASK-2', '--slot', self.slot, '--finished-ref', 'TASK-1']), \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main()
                self.assertNotEqual(code, 0)
                self.assertEqual(stdout.getvalue(), '')
                receipt = state.read(self.c, 'retirements')[self.slot]
                self.assertEqual(receipt['stage'], 'launch-attempted')
                self.assertEqual(receipt['failure'], type(error).__name__)
                self.assertEqual(receipt['failure_reason'], restart.snapshot._collection_reason(error))
                self.assertEqual(receipt['recovery_command'], recovery)
                self.assertIn('restart retired old but did not launch TASK-2:', receipt['error'])
                self.assertIn(receipt['error'], stderr.getvalue())
                self.assertIn('recover with: ' + recovery, stderr.getvalue())
                self.assertEqual(len(self.sent), 1)
                self.sent.clear()
                self.launch.reset_mock()
                self.refused('already attempted')

    def test_incomplete_launch_result_is_a_consumer_visible_partial_restart(self):
        from fleet import cli
        self.launch.return_value = {}
        stderr = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), \
                patch.object(cli.sys, 'argv', ['fleet', 'restart', 'TASK-2',
                    '--slot', self.slot, '--finished-ref', 'TASK-1']), redirect_stderr(stderr):
            self.assertNotEqual(cli.main(), 0)
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertEqual(receipt['stage'], 'launch-attempted')
        self.assertEqual(receipt['failure'], 'KeyError')
        self.assertIn(receipt['error'], stderr.getvalue())
        self.assertIn('confirming no replacement session started', stderr.getvalue())
        self.assertEqual(len(self.sent), 1)

    def test_post_retirement_failure_reason_is_sanitized_in_receipt_and_cli(self):
        from fleet import cli
        self.launch.side_effect = RuntimeError(
            '\x1b[31mcheckout refused TOKEN=do-not-persist\x1b[0m\nprivate second line')
        stderr = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), \
                patch.object(cli.sys, 'argv', ['fleet', 'restart', 'TASK-2',
                    '--slot', self.slot, '--finished-ref', 'TASK-1']), redirect_stderr(stderr):
            self.assertNotEqual(cli.main(), 0)
        receipt = state.read(self.c, 'retirements')[self.slot]
        serialized = json.dumps(receipt) + stderr.getvalue()
        self.assertIn('checkout refused', serialized)
        for private in ('do-not-persist', 'private second line', '\\u001b'):
            self.assertNotIn(private, serialized)

    def test_withheld_residue_launch_reports_retirement_and_recovery_to_cli(self):
        from fleet import cli
        self.residue = '16;8t'
        self.cleanup_works = False
        stderr = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), \
                patch.object(cli.sys, 'argv', ['fleet', 'restart', 'TASK-2',
                    '--slot', self.slot, '--finished-ref', 'TASK-1']), redirect_stderr(stderr):
            self.assertNotEqual(cli.main(), 0)
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertEqual(receipt['stage'], 'shell-verified-with-residue')
        self.assertIn('restart retired old but did not launch TASK-2:', stderr.getvalue())
        self.assertIn('manually verify and clear', stderr.getvalue())
        self.assertIn(receipt['recovery_command'], stderr.getvalue())
        self.launch.assert_not_called()

    def test_residue_without_exit_proof_does_not_claim_retirement(self):
        self.residue = '16;8t'
        self.keep_omp = True
        with self.assertRaises(ValueError) as raised:
            self.run_restart()
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertFalse(receipt.get('retired', False))
        self.assertNotIn('restart retired', str(raised.exception))
        self.launch.assert_not_called()

    def test_post_launch_hook_failure_preserves_replacement_outcome_warning(self):
        from fleet import cli
        def fail_after_publication(*args, **kwargs):
            state.write(self.c, 'last-launch', {
                'id': 'replacement', 'ref': 'TASK-2', 'worktree': {'path': self.slot}})
            raise CommandError('after-launch hook failed')
        self.launch.side_effect = fail_after_publication
        stderr = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), \
                patch.object(cli.sys, 'argv', ['fleet', 'restart', 'TASK-2',
                    '--slot', self.slot, '--finished-ref', 'TASK-1']), redirect_stderr(stderr):
            self.assertNotEqual(cli.main(), 0)
        receipt = state.read(self.c, 'retirements')[self.slot]
        self.assertEqual(receipt['launch_outcome'], 'replacement-receipt-present')
        self.assertIn('replacement may already be running', stderr.getvalue())
        self.assertIn('do not retry launch without reconciliation', stderr.getvalue())
        self.assertNotIn('did not launch TASK-2', stderr.getvalue())

    def test_git_diagnostics_survive_retirement_receipt_and_cli(self):
        from fleet import cli
        error = CommandError('git exited 128')
        error.command = 'git switch --no-track -c task-2-test origin/main'
        error.stderr = 'fatal: invalid reference: origin/main'
        self.launch.side_effect = error
        stderr = io.StringIO()
        with patch.object(cli, 'load', return_value=self.c), \
                patch.object(cli.sys, 'argv', ['fleet', 'restart', 'TASK-2',
                    '--slot', self.slot, '--finished-ref', 'TASK-1']), redirect_stderr(stderr):
            self.assertNotEqual(cli.main(), 0)
        receipt = state.read(self.c, 'retirements')[self.slot]
        for key in ('command', 'stderr'):
            self.assertEqual(receipt[key], getattr(error, key))
            self.assertIn(getattr(error, key), stderr.getvalue())

    def test_already_retired_shell_refuses_restart_with_launch_recovery(self):
        self.builder['shell_ready'] = True
        self.refused('exactly one non-shell session.*fleet launch NEXT --slot PATH')

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

    def test_deleted_updater_backup_allows_exact_retirement(self):
        link = self.process / 'exe'
        link.unlink()
        link.symlink_to('/opt/omp.1793967975370.308783.0.bak (deleted)')
        result = self.run_restart()
        self.assertEqual(result['retirement']['stage'], 'launched')
        self.assertEqual(result['retirement']['session']['process']['pid'], 123)
        self.assertEqual(self.sent[0][-3:], ('--text', '/exit', '--enter'))
        self.assertEqual(len(self.sent), 1)
        self.launch.assert_called_once()

    def test_backup_near_misses_and_wrong_argv_never_receive_exit(self):
        for executable, argv0 in (
                ('omp.1.2.3.bak', 'omp'),
                ('omp.1.2.bak (deleted)', 'omp'),
                ('omp.192.0.2.1.bak (deleted)', 'omp'),
                ('omp.1.x.3.bak (deleted)', 'omp'),
                ('omp.١.2.3.bak (deleted)', 'omp'),
                ('prefixomp.1.2.3.bak (deleted)', 'omp'),
                ('omp.1.2.3.bak (deleted).extra', 'omp'),
                ('other (deleted)', 'omp'),
                ('omp.1.2.3.bak (deleted)', 'python')):
            with self.subTest(executable=executable, argv0=argv0):
                link = self.process / 'exe'
                link.unlink()
                link.symlink_to('/opt/' + executable)
                argv = [argv0, *self.argv[1:]]
                (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
                self.refused('no unique matching foreground')

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

    def test_changed_config_retires_old_recorded_argv(self):
        self.c['builder'].update(model='anthropic/claude-fable-5-2', thinking='xhigh')
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)

    def test_legacy_artifacts_bind_old_model_after_config_change(self):
        self.record.pop('argv')
        self.record.update(id='1234', session_dir=str(Path(self.slot) / 'sessions-1234'),
                           overlay=str(Path(self.slot) / 'omp-1234.yml'))
        argv = list(self.argv)
        argv[argv.index('--session-dir') + 1] = self.record['session_dir']
        argv[argv.index('--config') + 1] = self.record['overlay']
        self.c['builder'].update(model='anthropic/claude-fable-5-2', thinking='xhigh')
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        with patch.object(restart, 'PROC', self.proc):
            self.assertEqual(restart._foreground(self.c, self.record, self.slot, 'target')['pid'], 123)
            for flag in ('--config', '--session-dir'):
                changed = list(argv)
                changed[changed.index(flag) + 1] += '-other'
                (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in changed) + b'\0')
                with self.assertRaisesRegex(ValueError, 'no unique matching foreground'):
                    restart._foreground(self.c, self.record, self.slot, 'target')

    def test_legacy_without_nonce_artifacts_withholds_with_recovery(self):
        self.record.pop('argv')
        state.write(self.c, 'launches', {self.slot: self.record})
        self.refused('/exit \\+ reconcile \\+ launch')

    def test_configured_leading_orca_extension_allows_exact_retirement(self):
        self.record['orca_status_extension'] = '/trusted/orca-agent-status.ts'
        state.write(self.c, 'launches', {self.slot: self.record})
        argv = [self.argv[0], '--extension', '/trusted/orca-agent-status.ts', *self.argv[1:]]
        (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
        self.assertEqual(self.run_restart()['retirement']['stage'], 'launched')
        self.assertEqual(len(self.sent), 1)

    def test_other_extra_or_unconfigured_extensions_never_receive_exit(self):
        self.record['orca_status_extension'] = '/trusted/orca-agent-status.ts'
        state.write(self.c, 'launches', {self.slot: self.record})
        for extra in (
                ['--extension', '/other/status.ts'],
                ['--extension', '/trusted/orca-agent-status.ts', '--extension', '/trusted/orca-agent-status.ts'],
                ['--extension', '/trusted/orca-agent-status.ts', '--no-tools'],
                ['--no-tools']):
            with self.subTest(extra=extra):
                argv = [self.argv[0], *extra, *self.argv[1:]]
                (self.process / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
                self.refused('no unique matching foreground')
        self.record.pop('orca_status_extension')
        state.write(self.c, 'launches', {self.slot: self.record})
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

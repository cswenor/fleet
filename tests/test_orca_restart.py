from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from fleet import orca_restart as restart
from fleet import orca_restart_preflight as preflight
from fleet.commands import CommandError


class ConductorTests(unittest.TestCase):
    def plan(self, root):
        return {'id': 'drill', 'state_dir': str(root / 'journal'), 'service': 'orca-serve.service',
                'timeout_seconds': 1, 'launch_attempts': 2,
                'execute_after': '2026-01-01T00:00:00+00:00',
                'expires_at': '2099-01-01T00:00:00+00:00',
                'projects': [],
                'target': {'pid': 987, 'start_ticks': 12, 'runtime_id': 'old'}}
    def test_expired_resume_is_read_only_even_with_completed_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self.plan(root)
            plan.update(execute_after='2019-01-01T00:00:00+00:00',
                        expires_at='2020-01-01T00:00:00+00:00')
            journal = restart.Journal(plan)
            journal.event('preflight', 'done', {'sha256': 'historical'})
            journal.event('restart', 'done', {'runtime_id': 'replacement'})
            before = journal.path.read_bytes()
            with patch.dict(os.environ, {'INVOCATION_ID': 'external-service'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=plan), \
                    patch.object(restart, 'orca') as native, patch.object(restart, 'run') as command:
                outcome = restart.conduct({}, 'private-plan')
            self.assertEqual(outcome['event'], 'blocked')
            self.assertEqual(outcome['blockers'], ['execution_window_closed'])
            self.assertEqual(journal.path.read_bytes(), before)
            native.assert_not_called()
            command.assert_not_called()

    def test_expiry_between_phases_blocks_each_new_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self.plan(root)
            plan.update(execute_after='2019-01-01T00:00:00+00:00',
                        expires_at='2020-01-01T00:00:00+00:00')
            journal = restart.Journal(plan)
            config = root / 'config.yml'
            config.write_text(yaml.safe_dump({'coordinator': {'terminal': 'old', 'run': 'run'}}))
            config.chmod(0o600)
            before = config.read_bytes()
            directory = root / 'new-main'
            project = {'name': 'test', 'mode': 'main', 'terminal': 'old', 'run': 'run',
                       'config': str(config), 'service': 'fleet-test.service',
                       'command': ['omp', '--session-dir', str(directory), 'Main prompt']}
            mutations = [
                lambda: restart._restart_service({}, plan, journal),
                lambda: restart._new_main({}, project, journal),
                lambda: restart._configure(project, 'new', journal),
                lambda: restart._install(project, journal),
                lambda: restart._notify({'coordinator': {'run': 'run'}}, [project], journal),
            ]
            with patch.object(restart, 'inspect', return_value={'blockers': [], 'target': {}}), \
                    patch.object(restart, 'orca') as native, patch.object(restart, 'run') as command, \
                    patch('fleet.service.install') as install:
                for mutate in mutations:
                    with self.assertRaises(restart._AuthorizationClosed):
                        mutate()
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse(directory.exists())
            native.assert_not_called()
            command.assert_not_called()
            install.assert_not_called()

    def test_ambiguous_notification_does_not_duplicate_either_owner_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            project, before = self.coordinator()
            project.update(name='project-alpha', run='project-run')
            terminal = {**before['terminals'][0], 'handle': 'new', 'incarnationId': 'new-inc'}
            journal.event('coordinator-project-alpha', 'done', {'terminal': 'new'})
            journal.event('main-project-alpha', 'done', {
                'terminal': 'new', 'incarnationId': 'new-inc', 'session_dir': '/private/project/sessions'})
            journal.data['status'] = 'complete'
            c = {'coordinator': {'run': 'fleet-run'}}
            receipts = iter([{'message': {'id': 'received'}}, CommandError('ambiguous enqueue')])
            def native_response(_c, *args):
                if args[:2] == ('terminal', 'list'):
                    return {'terminals': [terminal]}
                if args[:2] == ('orchestration', 'run-current'):
                    return {'run': {'id': 'project-run'}}
                if args[:2] == ('orchestration', 'send'):
                    result = next(receipts)
                    if isinstance(result, Exception):
                        raise result
                    return result
                self.fail('unexpected native operation')
            with patch.object(restart, 'orca', side_effect=native_response) as native:
                pending = restart._notify(c, [project], journal)
                journal.data['status'] = 'complete'
                pending_again = restart._notify(c, [project], journal)
            self.assertTrue(journal.done('notify-complete-project-alpha-project-run'))
            self.assertEqual(journal.step('notify-complete-project-alpha-fleet-run')['status'], 'intent')
            self.assertEqual(pending, ['notify-complete-project-alpha-fleet-run'])
            self.assertEqual(pending_again, pending)
            deliveries = [call.args for call in native.call_args_list
                          if call.args[1:3] == ('orchestration', 'send')]
            self.assertEqual(len(deliveries), 2)
            self.assertEqual({args[args.index('--to') + 1] for args in deliveries},
                             {'run:project-run', 'run:fleet-run'})

    def test_failure_notifications_never_borrow_an_unverified_sender(self):
        with tempfile.TemporaryDirectory() as tmp:
            for case in ('preflight-binding-failed', 'cached-incarnation-changed', 'unmapped-after-restart'):
                root = Path(tmp).resolve() / case
                root.mkdir()
                journal = restart.Journal(self.plan(root))
                project, snapshot = self.coordinator()
                terminal = dict(snapshot['terminals'][0])
                if case == 'cached-incarnation-changed':
                    journal.event('coordinator-test', 'done', {'terminal': 'old'})
                    terminal['incarnationId'] = 'reassigned'
                if case == 'unmapped-after-restart':
                    journal.event('restart', 'done', {'runtime_id': 'replacement'})
                journal.data['status'] = 'failed_manual_reconciliation'
                c = {'coordinator': {'run': 'run'}}
                def native_response(_c, *args):
                    if args[:2] == ('terminal', 'list'):
                        return {'terminals': [terminal]}
                    if args[:2] == ('orchestration', 'run-current'):
                        return {'run': {'id': 'wrong-run'}}
                    self.fail('unverified sender must not enqueue')
                with self.subTest(case=case), patch.object(restart, 'orca', side_effect=native_response) as native:
                    pending = restart._notify(c, [project], journal)
                    restart._notify(c, [project], journal)
                self.assertFalse(any(call.args[1:3] == ('orchestration', 'send')
                                     for call in native.call_args_list))
                self.assertEqual(journal.step(pending[0])['status'], 'blocked')


    def test_journal_private_and_changed_plan_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'attempts': 1})
            recovered = restart.Journal(plan)
            self.assertEqual(recovered.step('restart')['status'], 'intent')
            self.assertEqual(recovered.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(recovered.path.stat().st_mode & 0o777, 0o600)
            plan['target']['start_ticks'] = 13
            with self.assertRaisesRegex(ValueError, 'plan changed'):
                restart.Journal(plan)

    def test_ambiguous_restart_live_original_never_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'attempts': 1})
            with patch.object(restart, '_wait_ready', side_effect=ValueError), \
                    patch.object(restart, '_process_state', return_value='live'), \
                    patch.object(restart, '_service', return_value={'MainPID': '987', 'ActiveState': 'active'}), \
                    patch.object(restart, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'no retry'):
                    restart._restart_service({}, plan, journal)
                run.assert_not_called()
            self.assertEqual(journal.step('restart')['receipt']['attempts'], 1)

    def test_recovered_success_does_not_restart_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'attempts': 1})
            with patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                    patch.object(restart, 'run', return_value='') as run:
                restart._restart_service({}, plan, journal)
                self.assertFalse(any(call.args[0][:1] == ['sudo'] for call in run.call_args_list))
            self.assertTrue(journal.done('restart'))
            self.assertEqual(journal.step('restart')['receipt']['runtime_id'], 'new')

    def test_dead_failed_unit_recovery_ignores_foreign_processes_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            foreign = Path(tmp) / '1'
            foreign.mkdir()
            uid = os.getuid() + 1
            (foreign / 'status').write_text(f'Uid: {uid} {uid} {uid} {uid}\n')
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'attempts': 1})
            with patch.object(restart, '_wait_ready', side_effect=ValueError), \
                    patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart, '_runtime', return_value=None), \
                    patch.object(restart.orca_capture, 'PROC', Path(tmp)), \
                    patch.object(restart.orca_capture.sys, 'platform', 'linux'), \
                    patch.object(restart, '_service', return_value={'MainPID': '0', 'ActiveState': 'failed'}), \
                    patch.object(restart, 'run', side_effect=CommandError('failed')) as run:
                with self.assertRaisesRegex(ValueError, 'exhausted'):
                    restart._restart_service({}, plan, journal)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ['sudo', '-n', 'systemctl', 'restart', 'orca-serve.service'])
            with patch.object(restart, '_wait_ready', side_effect=ValueError), \
                    patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart, '_runtime', return_value=None), \
                    patch.object(restart, '_service', return_value={'MainPID': '0', 'ActiveState': 'failed'}), \
                    patch.object(restart, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'exhausted'):
                    restart._restart_service({}, plan, journal)
                run.assert_not_called()

    def test_recovery_rejects_expired_idle_authorization_inside_open_plan_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self.plan(root)
            now = restart.datetime.now(restart.timezone.utc)
            proof = {'plan_id': plan['id'], 'idle': True,
                     'confirmed_at': (now - timedelta(minutes=10)).isoformat(),
                     'expires_at': (now - timedelta(minutes=1)).isoformat()}
            path = root / 'idle.json'
            path.write_text(json.dumps(proof))
            path.chmod(0o600)
            plan['confirmations'] = {'fleet_idle': str(path), 'project_alpha_idle': str(path)}
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'attempts': 1})
            with patch.object(restart, '_wait_ready', side_effect=ValueError), \
                    patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart, '_runtime', return_value=None), \
                    patch.object(restart.orca_capture, '_processes', return_value=([], [], [])), \
                    patch.object(restart, '_service', return_value={'MainPID': '0', 'ActiveState': 'failed'}), \
                    patch.object(restart, 'run') as command:
                with self.assertRaises(ValueError):
                    restart._restart_service({}, plan, journal)
                command.assert_not_called()
            self.assertEqual(journal.step('restart')['receipt']['attempts'], 1)

    def test_recovery_requires_complete_model_free_current_census(self):
        for census in (([{'kind': 'agent', 'errors': []}], [], []),
                       ([], ['proc_scan_incomplete'], []),
                       ([], [], [{'reason': 'unreadable'}]),
                       ([{'kind': 'daemon_broker', 'errors': ['process_identity_changed']}], [], [])):
            with self.subTest(census=census), tempfile.TemporaryDirectory() as tmp:
                plan = self.plan(Path(tmp))
                journal = restart.Journal(plan)
                journal.event('restart', 'intent', {'attempts': 1})
                with patch.object(restart, '_wait_ready', side_effect=ValueError), \
                        patch.object(restart, '_process_state', return_value='gone'), \
                        patch.object(restart, '_runtime', return_value=None), \
                        patch.object(restart, '_service', return_value={'MainPID': '0', 'ActiveState': 'failed'}), \
                        patch.object(restart.orca_capture, '_processes', return_value=census), \
                        patch.object(restart, 'run') as command:
                    with self.assertRaises(ValueError):
                        restart._restart_service({}, plan, journal)
                    command.assert_not_called()
                self.assertEqual(journal.step('restart')['receipt']['attempts'], 1)

    def coordinator(self):
        process = {'pid': 99, 'start_ticks': 2, 'cwd': '/main', 'kind': 'agent',
                   'identity_stable': True, 'errors': [], 'sessions': [{'id': 's', 'complete': True}]}
        terminal = {'handle': 'old', 'incarnationId': 'inc', 'worktreePath': '/main',
                    'connected': True, 'writable': True, 'orphaned': False, 'agentIdentity': 'omp'}
        project = {'name': 'test', 'mode': 'main', 'terminal': 'old', 'run': 'run',
                   'worktree': '/main', 'service': 'fleet-test.service',
                   'main_process': process, 'terminal_metadata': terminal}
        snapshot = {'boot_id': 'boot', 'runtime': {'runtimeId': 'runtime'},
                    'processes': [process], 'terminals': [terminal], 'errors': []}
        return project, snapshot

    def test_surviving_main_never_duplicates_and_remap_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            after = json.loads(json.dumps(before))
            journal = restart.Journal(self.plan(Path(tmp)))
            with patch.object(restart, '_process_state', return_value='live'), \
                    patch.object(restart.orca_capture, '_processes', return_value=(after['processes'], [], [])), \
                    patch.object(restart, 'orca', side_effect=lambda _c, *args:
                                 {'terminals': after['terminals']} if args[:2] == ('terminal', 'list')
                                 else {'run': {'id': 'run'}}), \
                    patch.object(restart, '_new_main') as new:
                after['terminals'][0]['handle'] = 'remapped'
                with self.assertRaises(ValueError):
                    restart._coordinator({}, project, before, after, journal)
                new.assert_not_called()
                after['terminals'][0]['handle'] = 'old'
                self.assertEqual(restart._coordinator({}, project, before, after, journal), 'old')
                new.assert_not_called()

    def test_survivor_resume_rejects_changed_live_session_or_disconnected_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            after = json.loads(json.dumps(before))
            original = before['processes'][0]
            cases = [
                ([{**original, 'sessions': [{'id': 'changed', 'complete': True}]}],
                 after['terminals']),
                ([original], [{**after['terminals'][0], 'connected': False}]),
            ]
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            for processes, terminals in cases:
                with self.subTest(processes=processes, terminals=terminals), \
                        patch.object(restart, '_process_state', return_value='live'), \
                        patch.object(restart.orca_capture, '_processes', return_value=(processes, [], [])), \
                        patch.object(restart, 'orca', side_effect=lambda _c, *args:
                                     {'terminals': terminals} if args[:2] == ('terminal', 'list')
                                     else {'run': {'id': 'run'}}), \
                        patch.object(restart, '_new_main') as new:
                    with self.assertRaises(ValueError):
                        restart._coordinator({}, project, before, after, journal)
                    new.assert_not_called()
                    self.assertFalse(journal.done('coordinator-test'))

    def test_surviving_main_refuses_competing_agent_or_incomplete_current_census(self):
        project, before = self.coordinator()
        original = before['processes'][0]
        cases = [
            ([original, {**original, 'pid': 100}], [], []),
            ([original], ['proc_scan_incomplete'], []),
            ([original], [], [{'reason': 'unreadable'}]),
            ([original, {**original, 'pid': 100, 'identity_stable': False}], [], []),
        ]
        for cached in (False, True):
            for census in cases:
                with self.subTest(cached=cached, census=census), tempfile.TemporaryDirectory() as tmp:
                    journal = restart.Journal(self.plan(Path(tmp).resolve()))
                    if cached:
                        journal.event('coordinator-test', 'done', {'terminal': 'old'})
                    prior = json.loads(json.dumps(journal.step('coordinator-test')))
                    with patch.object(restart, '_process_state', return_value='live'), \
                            patch.object(restart.orca_capture, '_processes', return_value=census), \
                            patch.object(restart, 'orca', side_effect=lambda _c, *args:
                                         {'terminals': before['terminals']} if args[:2] == ('terminal', 'list')
                                         else {'run': {'id': 'run'}}), \
                            patch.object(restart, '_new_main') as new:
                        with self.assertRaises(ValueError):
                            restart._coordinator({}, project, before, before, journal)
                        new.assert_not_called()
                    self.assertEqual(journal.step('coordinator-test'), prior)

    def test_stale_after_cannot_hide_a_restored_main_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            after = json.loads(json.dumps(before))
            after['processes'] = []
            after['terminals'][0]['agentIdentity'] = None
            current = json.loads(json.dumps(after))
            current['processes'] = [{**before['processes'][0], 'pid': 100}]
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            with patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart.orca_capture, '_processes', return_value=(current['processes'], [], [])), \
                    patch.object(restart, '_new_main') as new:
                with self.assertRaises(ValueError):
                    restart._coordinator({}, project, before, after, journal)
                new.assert_not_called()
            self.assertFalse(journal.done('coordinator-test'))

    def test_gone_main_requires_current_connected_empty_ready_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            after = json.loads(json.dumps(before))
            after['processes'] = []
            ready = {**after['terminals'][0], 'agentIdentity': None}
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            cases = [
                {**ready, 'connected': False}, {**ready, 'writable': False},
                {**ready, 'orphaned': True}, {**ready, 'agentIdentity': 'unknown'},
            ]
            with patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart.orca_capture, '_processes', return_value=([], [], [])), \
                    patch.object(restart.orca_capture, 'collect') as collect, \
                    patch.object(restart, 'orca', return_value={'terminal': {'tail': ['dev@host:~$']}}) as native, \
                    patch.object(restart, '_new_main', return_value='new') as new:
                for terminal in cases:
                    with self.subTest(terminal=terminal):
                        collect.return_value = {**after, 'terminals': [terminal]}
                        with self.assertRaises(ValueError):
                            restart._coordinator({}, project, before, after, journal)
                        new.assert_not_called()
                collect.return_value = {**after, 'terminals': [ready]}
                native.return_value = {'terminal': {'tail': ['still starting...']}}
                with self.assertRaisesRegex(ValueError, 'ready shell'):
                    restart._coordinator({}, project, before, after, journal)
                new.assert_not_called()
                native.return_value = {'terminal': {'tail': ['dev@host:~$']}}
                self.assertEqual(restart._coordinator({}, project, before, after, journal), 'new')
            self.assertEqual(journal.step('coordinator-test')['receipt']['terminal'], 'new')
            self.assertTrue(journal.done('fresh-check-test'))

    def test_shell_coordinator_requires_its_current_run_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            project['mode'] = 'shell'
            after = json.loads(json.dumps(before))
            after['terminals'][0]['agentIdentity'] = None
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            for bound in (None, {'id': 'different-run'}, {'id': 'run'}):
                with self.subTest(binding=bound), patch.object(restart, 'orca', side_effect=[
                        {'terminals': after['terminals']},
                        {'terminal': {'tail': ['dev@host:~$']}}, {'run': bound}]):
                    if bound != {'id': 'run'}:
                        with self.assertRaisesRegex(ValueError, 'Run binding changed'):
                            restart._coordinator({}, project, before, after, journal)
                        self.assertFalse(journal.done('coordinator-test'))
                    else:
                        self.assertEqual(restart._coordinator({}, project, before, after, journal), 'old')

    def main_proc(self, root, directory, handle, logs):
        proc = root / 'proc' / '300'
        proc.mkdir(parents=True)
        fields = ['0'] * 20
        fields[0], fields[1], fields[2], fields[4], fields[5], fields[19] = 'S', '1', '300', '1', '300', '3'
        (proc / 'stat').write_text('300 (process) ' + ' '.join(fields))
        (proc / 'status').write_text('Uid: ' + ' '.join([str(os.getuid())] * 4) + '\n')
        (proc / 'exe').symlink_to('/usr/bin/omp')
        (proc / 'cwd').symlink_to(root)
        (proc / 'cmdline').write_bytes(b'omp\0--session-dir\0' + str(directory).encode() + b'\0')
        (proc / 'environ').write_bytes(('ORCA_TERMINAL_HANDLE=' + handle + '\0').encode())
        (proc / 'cgroup').write_text('0::/user.slice/session.scope\n')
        (proc / 'fd').mkdir()
        for number, log in enumerate(logs):
            (proc / 'fd' / str(number)).symlink_to(log)
        return {'pid': 300, 'start_ticks': 3, 'cwd': str(root), 'kind': 'agent',
                'identity_stable': True, 'errors': [],
                'sessions': [restart.orca_capture._session(log) for log in logs]}

    def test_new_main_acceptance_requires_sole_complete_census_after_startup(self):
        for case in ('competing', 'partial', 'unknown', 'startup_then_complete', 'wrong_terminal'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                directory = root / 'sessions'
                directory.mkdir(mode=0o700)
                project, before = self.coordinator()
                project['worktree'] = str(root)
                project['command'] = ['omp', '--session-dir', str(directory), 'Main prompt']
                log = directory / 'fresh.jsonl'
                events = [{'type': 'session', 'id': 'fresh', 'cwd': str(root)},
                          {'type': 'model_change', 'model': 'provider/model'},
                          {'type': 'thinking_level_change', 'thinkingLevel': 'medium'}]
                log.write_text(''.join(json.dumps(event) + '\n' for event in events))
                fresh = self.main_proc(root, directory, 'other' if case == 'wrong_terminal' else 'new', [log])
                competing = {**fresh, 'pid': 301,
                             'sessions': [{**fresh['sessions'][0], 'path': str(root / 'other.jsonl')}]}
                partial = ([fresh], ['proc_scan_incomplete'], [])
                scans = {'competing': [([fresh, competing], [], [])],
                         'partial': [partial], 'unknown': [([fresh], [], [{'reason': 'unreadable'}])],
                         'startup_then_complete': [partial, ([fresh], [], [])],
                         'wrong_terminal': [([fresh], [], [])]}[case]
                terminal = {**before['terminals'][0], 'handle': 'new', 'incarnationId': 'new-inc',
                            'worktreePath': str(root), 'worktreeId': 'tree'}
                journal = restart.Journal(self.plan(root))
                journal.data['timeout_seconds'] = 1
                journal.event('main-test', 'created',
                              {'terminal': 'new', 'incarnationId': 'new-inc',
                               'terminal_incarnation_id': 'new-inc', 'session_dir': str(directory),
                               'worktree': {'path': str(root), 'id': 'tree'}})
                def native(_c, *args):
                    if args[:2] == ('terminal', 'show'):
                        return {'terminal': terminal}
                    if args[:2] == ('orchestration', 'run-current'):
                        return {'run': {'id': 'run'}}
                    self.fail('created receipt attempted another mutation')
                with patch.object(restart, 'orca', side_effect=native), \
                        patch.object(preflight, 'PROC', root / 'proc'), \
                        patch.object(restart.orca_capture, '_processes', side_effect=scans), \
                        patch.object(restart.time, 'monotonic',
                                     side_effect=[0, 0] if case == 'startup_then_complete' else [0, 2]), \
                        patch.object(restart.time, 'sleep'):
                    if case == 'startup_then_complete':
                        self.assertEqual(restart._new_main({}, project, journal), 'new')
                        self.assertEqual(journal.step('main-test')['receipt']['process']['pid'], 300)
                        self.assertTrue(journal.done('main-test'))
                    else:
                        with self.assertRaises(ValueError):
                            restart._new_main({}, project, journal)
                        self.assertEqual(journal.step('main-test')['status'], 'created')

    def test_recorded_fresh_main_recovers_cutover_without_another_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for status in ('created', 'done'):
                with self.subTest(receipt=status):
                    case = root / status
                    case.mkdir()
                    directory = case / 'sessions'
                    directory.mkdir(mode=0o700)
                    project, before = self.coordinator()
                    project['worktree'] = str(case)
                    project['command'] = ['omp', '--session-dir', str(directory), 'Main prompt']
                    logs = [directory / 'fresh.jsonl', directory / 'fresh/helpers/helper.jsonl']
                    for log in logs:
                        log.parent.mkdir(parents=True, exist_ok=True)
                        events = [{'type': 'session', 'id': log.stem, 'cwd': str(case)},
                                  {'type': 'model_change', 'model': 'provider/model'},
                                  {'type': 'thinking_level_change', 'thinkingLevel': 'medium'}]
                        log.write_text(''.join(json.dumps(event) + '\n' for event in events))
                    fresh = self.main_proc(case, directory, 'new', logs)
                    terminal = {**before['terminals'][0], 'handle': 'new', 'incarnationId': 'new-inc',
                                'worktreePath': str(case), 'worktreeId': 'tree'}
                    receipt = {'terminal': 'new', 'incarnationId': 'new-inc',
                               'terminal_incarnation_id': 'new-inc',
                               'session_dir': str(directory), 'worktree': {'path': str(case), 'id': 'tree'}}
                    if status == 'done':
                        receipt['process'] = fresh
                    journal = restart.Journal(self.plan(case))
                    journal.data['timeout_seconds'] = 1
                    journal.event('main-test', status, receipt)
                    def native(_c, *args):
                        if args[:2] == ('terminal', 'show'):
                            return {'terminal': terminal}
                        if args[:2] == ('terminal', 'list'):
                            return {'terminals': [terminal]}
                        if args[:2] == ('orchestration', 'run-current'):
                            return {'run': {'id': 'run'}}
                        self.fail('receipt recovery attempted a new terminal or other mutation')
                    with patch.object(restart, 'orca', side_effect=native), \
                            patch.object(preflight, 'PROC', case / 'proc'), \
                            patch.object(restart.orca_capture, '_processes', return_value=([fresh], [], [])), \
                            patch.object(restart, '_process_state',
                                         side_effect=lambda process: 'gone' if process['pid'] == 99 else 'live'), \
                            patch.object(restart, '_fresh_main_ready') as authorize_creation:
                        self.assertEqual(restart._coordinator({}, project, before, before, journal), 'new')
                    authorize_creation.assert_not_called()
                    self.assertTrue(journal.done('main-test'))
                    self.assertTrue(journal.done('coordinator-test'))
                    self.assertEqual(journal.step('main-test')['receipt']['process']['pid'], 300)

    def test_cached_supervisor_receipt_is_not_current_health_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self.coordinator()
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            journal.event('supervisor-test', 'done', {'active': True})
            with patch.object(restart, 'run', return_value='inactive'), \
                    patch('fleet.service.install') as install:
                with self.assertRaisesRegex(ValueError, 'no longer active'):
                    restart._install(project, journal)
            install.assert_not_called()

    def test_ambiguous_supervisor_environment_files_require_manual_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = self.coordinator()
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            with patch.object(restart, 'run', return_value='/first (ignore_errors=no) /second (ignore_errors=no)'), \
                    patch('fleet.service.install') as install:
                with self.assertRaisesRegex(ValueError, 'EnvironmentFile binding ambiguous'):
                    restart._install(project, journal)
            install.assert_not_called()
            self.assertFalse(journal.step('supervisor-test'))

    def test_cached_coordinator_does_not_trust_old_binding_or_main_liveness(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, before = self.coordinator()
            journal = restart.Journal(self.plan(Path(tmp).resolve()))
            journal.event('coordinator-test', 'done', {'terminal': 'old'})
            with patch.object(restart, 'orca', side_effect=[
                    {'terminals': before['terminals']}, {'run': None}]):
                with self.assertRaisesRegex(ValueError, 'cached coordinator Run binding changed'):
                    restart._coordinator({}, project, before, before, journal)
            with patch.object(restart, 'orca', side_effect=[
                    {'terminals': before['terminals']}, {'run': {'id': 'run'}}]), \
                    patch.object(restart.orca_capture, '_processes', return_value=([], [], [])), \
                    patch.object(restart, '_process_state', return_value='gone'), \
                    patch.object(restart, '_new_main') as new:
                with self.assertRaisesRegex(ValueError, 'no longer positively alive'):
                    restart._coordinator({}, project, before, before, journal)
                new.assert_not_called()

    def test_announcement_failure_never_reports_complete_or_retries_ambiguous_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self.plan(root)
            journal = restart.Journal(plan)
            project, snapshot = self.coordinator()
            project['mode'] = 'shell'
            config_path = root / 'config.yml'
            config_path.write_text(yaml.safe_dump({'coordinator': {'terminal': 'old', 'run': 'run'}}))
            config_path.chmod(0o600)
            project['config'] = str(config_path)
            ready = {**snapshot['terminals'][0], 'agentIdentity': None}
            proof = {'projects': [project]}
            restart._atomic(journal.directory / 'preflight.json', proof)
            journal.event('preflight', 'done', {'sha256': restart._digest(proof)})
            journal.event('restart', 'done', {'runtime_id': 'new-runtime'})
            journal.event('coordinator-test', 'done', {'terminal': 'old'})
            journal.event('config-test', 'done', {'terminal': 'old'})
            c = {'coordinator': {'run': 'run'}}
            def native_response(_c, *args):
                if args[:2] == ('terminal', 'list'):
                    return {'terminals': [ready]}
                if args[:2] == ('terminal', 'read'):
                    return {'terminal': {'tail': ['dev@host:~$']}}
                if args[:2] == ('orchestration', 'run-current'):
                    return {'run': {'id': 'run'}}
                if args[:2] == ('orchestration', 'send'):
                    raise CommandError('ambiguous transport')
                self.fail('unexpected external mutation')
            with patch.dict(os.environ, {'INVOCATION_ID': 'external-service'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=plan), \
                    patch.object(restart, '_capture', return_value={'runtime': {'runtimeId': 'new-runtime'}}), \
                    patch.object(restart, '_restart_service'), \
                    patch.object(restart, '_runtime', return_value={'ready': True}), \
                    patch.object(restart, 'run', return_value='active'), \
                    patch.object(restart, 'orca', side_effect=native_response) as native:
                first = restart.conduct(c, 'private-plan')
                second = restart.conduct(c, 'private-plan')
            self.assertEqual(first['event'], 'failed_manual_reconciliation')
            self.assertEqual(second['event'], 'failed_manual_reconciliation')
            self.assertEqual(sum(call.args[1:3] == ('orchestration', 'send') for call in native.call_args_list), 2)
            persisted = json.loads(journal.path.read_text())
            self.assertEqual(persisted['status'], 'failed_manual_reconciliation')
            self.assertEqual(persisted['steps']['failure']['receipt']['stage'], 'notifications')
            self.assertEqual(persisted['steps']['notify-reporting-test-run']['status'], 'intent')

    def test_main_create_ambiguous_receipt_never_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = restart.Journal(self.plan(root))
            journal.event('main-test', 'intent', {'command_sha256': 'digest'})
            with patch.object(restart, 'orca') as orca:
                with self.assertRaisesRegex(ValueError, 'never duplicate'):
                    restart._new_main({}, {'name': 'test'}, journal)
                orca.assert_not_called()

    def test_config_cutover_preserves_other_settings_and_user_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'config.yml'
            original = {'coordinator': {'terminal': 'old', 'run': 'run'},
                        'status': {'token_env': 'PRIVATE_ENV_NAME'}, 'custom': ['keep']}
            path.write_text(yaml.safe_dump(original))
            path.chmod(0o600)
            project = {'name': 'test', 'config': str(path), 'terminal': 'old', 'run': 'run'}
            journal = restart.Journal(self.plan(root))
            restart._configure(project, 'new', journal)
            expected = {**original, 'coordinator': {'terminal': 'new', 'run': 'run'}}
            self.assertEqual(yaml.safe_load(path.read_text()), expected)
            self.assertNotIn('PRIVATE_ENV_NAME', journal.path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            # A distinct plan must not overwrite a coordinator edited by the user.
            other = self.plan(root)
            other['state_dir'] = str(root / 'other')
            with self.assertRaisesRegex(ValueError, 'config changed'):
                restart._configure(project, 'third', restart.Journal(other))
            self.assertEqual(yaml.safe_load(path.read_text()), expected)

    def test_cached_config_receipt_refuses_current_terminal_or_run_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / 'config.yml'
            path.write_text(yaml.safe_dump({'coordinator': {'terminal': 'old', 'run': 'run'}}))
            path.chmod(0o600)
            project = {'name': 'test', 'config': str(path), 'terminal': 'old', 'run': 'run'}
            journal = restart.Journal(self.plan(root))
            restart._configure(project, 'new', journal)
            for coordinator in ({'terminal': 'other', 'run': 'run'},
                                {'terminal': 'new', 'run': 'other'}):
                with self.subTest(coordinator=coordinator):
                    path.write_text(yaml.safe_dump({'coordinator': coordinator}))
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'config changed'):
                        restart._configure(project, 'new', journal)
                    self.assertEqual(path.read_bytes(), before)

    def test_stop_receipt_does_not_expose_journal_payload(self):
        events = [{'UNIT': 'orca-serve.service', 'MESSAGE_ID': 'de5b426a63be47a7b6ac3eaac82e2f6f',
                   '__MONOTONIC_TIMESTAMP': '1000000', 'MESSAGE': 'private'},
                  {'UNIT': 'orca-serve.service', 'MESSAGE_ID': '9d1aaa27d60140bd96365438aad20286b',
                   '__MONOTONIC_TIMESTAMP': '1250000', 'MESSAGE': 'private SIGKILL'}]
        with patch.object(restart, 'run', return_value='\n'.join(json.dumps(e) for e in events)):
            receipt = restart._stop_receipt(1, 'orca-serve.service')
        self.assertEqual(receipt, {'stop_duration_seconds': .25, 'sigkill_reached': True})
        self.assertNotIn('private', json.dumps(receipt))
        with patch.object(restart, 'run', side_effect=CommandError('private')):
            self.assertEqual(restart._stop_receipt(1, 'orca-serve.service'),
                             {'stop_duration_seconds': None, 'sigkill_reached': None})


if __name__ == '__main__':
    unittest.main()

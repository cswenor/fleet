import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import orca_capture
from fleet import orca_restart
from fleet import orca_restart_preflight as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(preflight, 'PROC', self.proc).start()
        self.cwd = self.root / 'checkout'
        self.cwd.mkdir()
        self.logs = self.root / 'sessions'
        self.logs.mkdir(mode=0o700)
        self.log = self.logs / 'session.jsonl'
        self.events = [
            {'type': 'session', 'id': 'session', 'cwd': str(self.cwd)},
            {'type': 'model_change', 'model': 'provider/model'},
            {'type': 'thinking_level_change', 'thinkingLevel': 'medium'},
            {'type': 'message', 'message': {'role': 'assistant', 'stopReason': 'stop',
                                          'content': [{'type': 'text', 'text': 'private transcript'}]}},
        ]
        self.write_events()
        self.target_argv = ['/opt/Orca.AppImage', 'serve', '--private-argument']
        self.make_proc(99, 1, 990, '/opt/Orca.AppImage', self.target_argv,
                       '/system.slice/orca-serve.service')
        self.make_proc(100, 99, 1000, '/opt/node', ['node', 'runtime'],
                       '/system.slice/orca-serve.service')
        self.agent_argv = ['omp', '--session-dir', str(self.logs)]
        self.make_proc(200, 1, 2000, '/opt/omp', self.agent_argv,
                       '/user.slice/user-1000.slice/session.scope', 'main')
        (self.proc / '200/fd/4').symlink_to(self.log)
        self.terminal = {'handle': 'main', 'incarnationId': 'incarnation', 'worktreeId': 'tree',
                         'worktreePath': str(self.cwd), 'connected': True, 'writable': True,
                         'orphaned': False, 'agentIdentity': 'omp', 'binding': {'id': 'run'},
                         'tabId': 'tab', 'leafId': 'leaf'}
        self.record = {'session_dir': str(self.logs), 'terminal': 'main',
                       'terminal_incarnation_id': 'incarnation',
                       'worktree': {'path': str(self.cwd), 'id': 'tree'}}
        self.records_path = self.private('records.json', json.dumps(self.record))
        self.config_path = self.private('fleet.yml', 'private config')
        self.handoff = self.private('handoff.txt', 'Persisted restart handoff')
        now = datetime.now(timezone.utc)
        self.now = now
        self.plan_data = {
            'version': 1, 'id': 'restart-1', 'state_dir': str(self.root / 'journal'),
            'execute_after': (now - timedelta(minutes=5)).isoformat(),
            'expires_at': (now + timedelta(minutes=30)).isoformat(),
            'service': 'orca-serve.service',
            'target': {'pid': 99, 'start_ticks': 990, 'exe': '/opt/Orca.AppImage',
                       'argv': self.target_argv, 'cwd': str(self.cwd),
                       'runtime_id': 'runtime', 'runtime_pid': 100},
            'projects': [{'name': 'fleet', 'config': str(self.config_path),
                          'worktree': str(self.cwd), 'handoff': str(self.handoff),
                          'command': ['omp', '--session-dir', str(self.root / 'fresh')],
                          'delivery': True, 'mode': 'main'}],
            'session_records': [str(self.records_path)], 'confirmations': {},
        }
        for name in ('fleet_idle', 'project_alpha_idle'):
            proof = {'plan_id': 'restart-1', 'confirmed_at': now.isoformat(),
                     'expires_at': (now + timedelta(minutes=20)).isoformat(), 'idle': True}
            self.plan_data['confirmations'][name] = str(self.private(name + '.json', json.dumps(proof)))
        self.plan_path = self.private('plan.yml', yaml.safe_dump(self.plan_data))
        self.c = {'root': str(self.cwd), 'repo': 'owner/fleet', 'orca_repo_id': 'repo',
                  '_path': str(self.config_path), 'coordinator': {'run': 'run', 'terminal': 'main'},
                  'orca': '/opt/orca',
                  'builder': {'slots': [], 'resolved_model': 'provider/model', 'idle_seconds': 60}}
        self.snapshot = {'runtime': {'runtimeId': 'runtime'}, 'runtime_process': {'pid': 100},
                         'terminals': [self.terminal], 'processes': [self.agent_row()],
                         'errors': [], 'proc_diagnostics': [],
                         'services': [{'unit': 'fleet-owner-fleet.service', 'LoadState': 'loaded',
                                       'ActiveState': 'active', 'SubState': 'running'}]}
        self.inventory = {'errors': [], 'builders': [],
                          'worktrees': [{'id': 'tree', 'path': str(self.cwd)}]}
        self.service_text = ('ActiveState=active\nMainPID=99\nControlGroup=/system.slice/orca-serve.service\n'
                             'KillMode=mixed\nSendSIGKILL=no\nTimeoutStopUSec=1min 30s\n')
        self.commands = []
        patch.object(preflight.config, 'load', return_value=self.c).start()
        patch.object(preflight.health, 'collect', side_effect=lambda c: copy.deepcopy(self.inventory)).start()
        patch.object(preflight.orca_capture, 'collect', side_effect=lambda *args: copy.deepcopy(self.snapshot)).start()
        patch.object(preflight, 'run', side_effect=self.system_read).start()
        patch.object(preflight, 'orca', side_effect=self.native_read).start()
        patch.object(preflight, 'json_run', return_value={'held': ['session'], 'unknown': []}).start()

    def private(self, name, content):
        path = self.root / name
        path.write_text(content)
        path.chmod(0o600)
        return path

    def write_events(self):
        self.log.write_text(''.join(json.dumps(event) + '\n' for event in self.events))

    def make_proc(self, pid, ppid, start, exe, argv, cgroup, handle=None):
        proc = self.proc / str(pid)
        proc.mkdir()
        fields = ['0'] * 20
        fields[0], fields[1], fields[2], fields[4], fields[5], fields[19] = 'S', str(ppid), str(pid), '1', str(pid), str(start)
        (proc / 'stat').write_text(f'{pid} (process) ' + ' '.join(fields))
        (proc / 'status').write_text('Uid: ' + ' '.join([str(os.getuid())] * 4) + '\n')
        (proc / 'exe').symlink_to(exe)
        (proc / 'cwd').symlink_to(self.cwd)
        (proc / 'cmdline').write_bytes(b'\0'.join(v.encode() for v in argv) + b'\0')
        (proc / 'environ').write_bytes(('SECRET=must-not-appear\0' + ('ORCA_TERMINAL_HANDLE=' + handle + '\0' if handle else '')).encode())
        (proc / 'cgroup').write_text('0::' + cgroup + '\n')
        (proc / 'fd').mkdir()

    def agent_row(self):
        return {'pid': 200, 'start_ticks': 2000, 'cwd': str(self.cwd),
                'identity_stable': True, 'kind': 'agent', 'session_status': 'observed',
                'errors': [], 'argv_shape': ['--session-dir'],
                'sessions': [orca_capture._session(self.log)]}

    def test_failed_or_inactive_supervisor_blocks_restart(self):
        for state in ('failed', 'inactive'):
            with self.subTest(state=state):
                self.snapshot['services'][0]['ActiveState'] = state
                self.snapshot['services'][0]['SubState'] = 'dead'
                proof = preflight.inspect(self.load(), self.c)
                self.assertIn('fleet_supervisor_not_running', proof['blockers'])

    def system_read(self, argv):
        self.commands.append(argv)
        if argv[:2] == ['systemctl', 'show']:
            return self.service_text
        if argv == ['sudo', '-n', '-l', '--', 'systemctl', 'restart', 'orca-serve.service']:
            return 'authorization receipt deliberately not retained'
        self.fail('preflight attempted a mutation')

    def native_read(self, c, *args):
        self.commands.append(list(args))
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.terminal.copy()}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': {'tail': getattr(self, 'screen', [' π > ◑ model', '╰─'])}}
        if args == ('worktree', 'ps'):
            return {'worktrees': [{'worktreeId': 'tree', 'agents': [
                {'paneKey': 'tab:leaf', 'agentType': 'omp', 'state': 'idle',
                 'programStatus': {'state': 'idle'}}]}]}
        self.fail('preflight attempted a mutation')

    def load(self, data=None):
        if data is not None:
            self.plan_path.write_text(yaml.safe_dump(data))
        return preflight.load_plan(self.plan_path)

    def inspect(self, data=None):
        return preflight.inspect(self.load(data), self.c)

    def test_all_thinking_glyphs_accept_idle_main(self):
        for glyph in '⦸○◔◐◑◒◓◕◉⟳':
            with self.subTest(glyph=glyph):
                self.screen = [f' π > {glyph} Opus 5.5 >', '╰─']
                result = self.inspect()
                self.assertEqual(result['blockers'], [])
                self.assertTrue(all(check['status'] == 'passed' for check in
                                    result['projects'][0]['main_idle_checks'].values()))

    def test_main_background_working_and_draft_still_block_with_reasons(self):
        for screen, failed in (
                ([' π > ◒ Opus > ⚙ 1', '╰─'], 'background_job'),
                (['Waiting for background job bg_1', ' ⠙ 3s > ◒ Opus > ⚙ 1', '╰─'], 'background_job'),
                ([' ⠙ 3s > ◒ Opus >', '╰─'], 'footer_composer'),
                ([' π > ◒ Opus >', '╰─ draft'], 'footer_composer'),
                ([' π > ? Opus >', '╰─'], 'footer_composer')):
            with self.subTest(screen=screen):
                self.screen = screen
                result = self.inspect()
                self.assertIn('fleet_idle_binding_unproven', result['blockers'])
                check = result['projects'][0]['main_idle_checks'][failed]
                self.assertEqual(check['status'], 'failed')
                if failed == 'background_job':
                    self.assertEqual(check['reason'], 'background job')

    def test_main_checks_accumulate_without_leaking_exception_text(self):
        self.events.append({'type': 'message', 'message': {'role': 'user'}})
        self.write_events()
        self.screen = [' π > ◒ Opus > ⚙ 1', '╰─']
        self.c['builder']['slots'] = ['/lane']
        result = self.inspect()
        project = result['projects'][0]
        self.assertEqual(project['main_idle_checks']['session_final_stop']['status'], 'failed')
        self.assertEqual(project['main_idle_checks']['lease']['status'], 'skipped')
        self.assertEqual(project['main_idle_checks']['foreground_pgrp']['status'], 'passed')
        self.assertEqual(project['idle_checks']['builder lane /lane']['status'], 'failed')
        self.assertNotIn('private transcript', json.dumps(result))

    def test_pane_lease_and_foreground_failures_are_distinct(self):
        native = self.native_read
        def working(c, *args):
            result = native(c, *args)
            if args == ('worktree', 'ps'):
                result['worktrees'][0]['agents'][0]['state'] = 'working'
            return result
        with patch.object(preflight, 'orca', side_effect=working), \
                patch.object(preflight, 'json_run', return_value={'held': [], 'unknown': []}), \
                patch.object(preflight, '_process', return_value={
                    'start_ticks': 2000, 'tty': 1, 'pgrp': 200, 'foreground_pgrp': 300}):
            checks = {}
            with self.assertRaises(ValueError):
                preflight._main_idle(self.c, self.record, self.terminal, self.agent_row(), checks)
        for name in ('pane_agent_state', 'lease', 'foreground_pgrp'):
            self.assertEqual(checks[name]['status'], 'failed')

    def test_complete_proof_is_read_only_and_preserves_survival_fields(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}
        result = self.inspect()
        after = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}
        self.assertEqual(result['blockers'], [])
        self.assertEqual(before, after)
        self.assertFalse((self.root / 'journal').exists())
        self.assertFalse((self.root / 'fresh').exists())
        project = result['projects'][0]
        self.assertEqual(project['main_process']['pid'], 200)
        self.assertEqual(project['main_process']['start_ticks'], 2000)
        self.assertEqual(project['main_process']['sessions'][0]['path'], str(self.log))
        self.assertEqual(project['main_process']['cgroup'], ['/user.slice/user-1000.slice/session.scope'])
        self.assertEqual(project['terminal_metadata']['incarnationId'], 'incarnation')
        self.assertEqual(result['snapshot']['service_state']['TimeoutStopUSec'], '1min 30s')
        self.assertNotIn('argv', result['target'])
        self.assertNotIn('--private-argument', json.dumps(result))
        self.assertNotIn('must-not-appear', json.dumps(result))
        self.assertNotIn('private transcript', json.dumps(result))

    def test_private_main_prompt_absent_from_cli_output_and_persisted_preflight(self):
        marker = 'private-main-prompt-must-never-leave-plan'
        self.plan_data['projects'][0]['command'].append(marker)
        self.load(self.plan_data)
        dry = orca_restart.conduct(self.c, self.plan_path, dry_run=True)
        self.assertNotIn(marker, json.dumps(dry))
        with patch.dict(os.environ, {'INVOCATION_ID': 'external-service'}, clear=True), \
                patch.object(orca_restart, '_capture', side_effect=ValueError('stop after proof')), \
                patch.object(orca_restart, '_runtime', return_value=None):
            outcome = orca_restart.conduct(self.c, self.plan_path)
        self.assertEqual(outcome['event'], 'failed_manual_reconciliation')
        evidence = Path(self.plan_data['state_dir']) / 'preflight.json'
        self.assertNotIn(marker, evidence.read_text())
        self.assertNotIn('command', json.loads(evidence.read_text())['projects'][0])
        # Command hydration must not mutate the retained proof in memory/on disk.
        self.assertNotIn(marker, json.dumps(outcome))

    def test_plan_rejects_unknown_keys_wrong_types_and_nonfresh_commands(self):
        mutations = [lambda p: p.update(extra=True), lambda p: p.update(version=True),
                     lambda p: p.update(timeout_seconds=True), lambda p: p.update(launch_attempts=4),
                     lambda p: p.update(allow_systemd_sigkill='yes'),
                     lambda p: p.update(service='other.service'),
                     lambda p: p['target'].update(pid=True),
                     lambda p: p['target'].update(extra=True),
                     lambda p: p.update(expires_at=p['execute_after']),
                     lambda p: p['projects'][0].update(command=['omp', '--resume', '--session-dir', '/new']),
                     lambda p: p['projects'][0].update(command=['omp', '--session-dir=/new']),
                     lambda p: p['projects'][0].update(mode='shell'),
                     lambda p: p['projects'].append(copy.deepcopy(p['projects'][0]))]
        for mutate in mutations:
            data = copy.deepcopy(self.plan_data)
            mutate(data)
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, '^invalid or nonprivate Orca restart plan$'):
                self.load(data)

    def test_private_plan_symlink_duplicates_and_state_directory(self):
        self.plan_path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.load()
        self.plan_path.chmod(0o600)
        link = self.root / 'plan-link.yml'
        link.symlink_to(self.plan_path)
        with self.assertRaises(ValueError):
            preflight.load_plan(link)
        self.plan_path.write_text(yaml.safe_dump(self.plan_data) + 'id: duplicate\n')
        with self.assertRaises(ValueError):
            self.load()
        state = self.root / 'journal'
        state.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            self.load(self.plan_data)

    def test_target_requires_exact_identity_uid_and_runtime_ancestry(self):
        plan = self.load()
        for key, value in [('start_ticks', 1), ('exe', '/other'), ('argv', ['other']),
                           ('cwd', '/other'), ('runtime_id', 'other'), ('runtime_pid', 200)]:
            changed = copy.deepcopy(plan)
            changed['target'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight._target(changed, self.snapshot)
        self.snapshot['runtime_process']['pid'] = 200
        changed = copy.deepcopy(plan)
        changed['target']['runtime_pid'] = 200
        with self.assertRaises(ValueError):
            preflight._target(changed, self.snapshot)
        (self.proc / '99/status').write_text('Uid: 999999 999999 999999 999999\n')
        with self.assertRaises(ValueError):
            preflight._target(plan, self.snapshot)

    def test_service_cgroup_and_sigkill_authorization_are_not_assumed(self):
        self.service_text = self.service_text.replace('SendSIGKILL=no', 'SendSIGKILL=yes')
        result = self.inspect()
        self.assertIn('systemd_sigkill_not_approved', result['blockers'])
        self.assertEqual(result['actions'][1]['stop_policy']['SendSIGKILL'], 'yes')
        data = copy.deepcopy(self.plan_data)
        data['allow_systemd_sigkill'] = True
        self.assertEqual(self.inspect(data)['blockers'], [])
        (self.proc / '99/cgroup').write_text('0::/unrelated\n')
        self.assertIn('service_restart_authority_unproven', self.inspect(data)['blockers'])

    def test_stale_mismatched_and_nonprivate_confirmations_block(self):
        path = Path(self.plan_data['confirmations']['fleet_idle'])
        proof = json.loads(path.read_text())
        for key, value in [('plan_id', 'other'), ('idle', False),
                           ('confirmed_at', (self.now - timedelta(hours=1)).isoformat()),
                           ('confirmed_at', (self.now + timedelta(minutes=1)).isoformat()),
                           ('expires_at', (self.now - timedelta(seconds=1)).isoformat())]:
            changed = dict(proof, **{key: value})
            path.write_text(json.dumps(changed))
            self.assertIn('fleet_idle_confirmation_invalid', self.inspect()['blockers'])
        path.write_text(json.dumps(proof))
        path.chmod(0o644)
        self.assertIn('fleet_idle_confirmation_invalid', self.inspect()['blockers'])

    def test_idle_confirmation_can_precede_scheduled_execution(self):
        # A Main writes the confirmation before going idle; the external timer
        # must not require an agent turn exactly at the scheduled restart time.
        plan = copy.deepcopy(self.plan_data)
        plan['execute_after'] = (self.now + timedelta(minutes=5)).isoformat()
        path = Path(plan['confirmations']['fleet_idle'])
        preflight._confirm(plan, path, self.now)
        proof = json.loads(path.read_text())
        proof['confirmed_at'] = (self.now - timedelta(hours=1)).isoformat()
        path.write_text(json.dumps(proof))
        with self.assertRaises(ValueError):
            preflight._confirm(plan, path, self.now)

    def test_stale_or_future_handoff_is_not_authority_to_start_main(self):
        boundary = preflight._timestamp(self.plan_data['execute_after']).timestamp() - 1800
        os.utime(self.handoff, (boundary - 1, boundary - 1))
        self.assertIn('fleet_project_invalid', self.inspect()['blockers'])
        os.utime(self.handoff, (boundary, boundary))
        self.assertEqual(self.inspect()['blockers'], [])
        future = datetime.now(timezone.utc).timestamp() + 3600
        os.utime(self.handoff, (future, future))
        self.assertIn('fleet_project_invalid', self.inspect()['blockers'])

    def test_self_updated_main_keeps_exact_executable_binding(self):
        executable = '/opt/omp.1793967975370.308783.0.bak (deleted)'
        link = self.proc / '200/exe'
        link.unlink()
        link.symlink_to(executable)
        self.snapshot['processes'][0]['exe'] = executable
        self.assertEqual(self.inspect()['blockers'], [])
        link.unlink()
        link.symlink_to('/opt/omp.1793967975371.308783.0.bak (deleted)')
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_missing_handoff_binding_and_live_composer_block(self):
        self.handoff.write_text('')
        self.assertIn('fleet_project_invalid', self.inspect()['blockers'])
        self.handoff.write_text('handoff')
        self.terminal['binding'] = {'id': 'wrong'}
        self.assertIn('fleet_idle_binding_unproven', self.inspect()['blockers'])
        self.terminal['binding'] = {'id': 'run'}
        native = self.native_read
        def draft(c, *args):
            if args[:2] == ('terminal', 'read'):
                return {'terminal': {'tail': [' π > ◑ model', 'draft', '╰─']}}
            return native(c, *args)
        with patch.object(preflight, 'orca', side_effect=draft):
            self.assertIn('fleet_idle_binding_unproven', self.inspect()['blockers'])

    def test_every_owned_session_must_match_even_outside_configured_worktrees(self):
        extra = copy.deepcopy(self.snapshot['processes'][0])
        extra.update(pid=201, start_ticks=2010, cwd='/unconfigured')
        self.snapshot['processes'].append(extra)
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_actual_session_directory_open_log_and_terminal_route_required(self):
        for filename, content in [('cmdline', b'omp\0--session-dir\0/other\0'),
                                  ('environ', b'ORCA_TERMINAL_HANDLE=other\0')]:
            path = self.proc / '200' / filename
            previous = path.read_bytes()
            path.write_bytes(content)
            self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])
            path.write_bytes(previous)
        (self.proc / '200/fd/4').unlink()
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_incomplete_capture_and_model_proof_block_but_proc_diagnostics_remain(self):
        self.snapshot['errors'] = ['proc_scan_incomplete']
        self.snapshot['proc_diagnostics'] = [{'pid': 777, 'reason': 'unreadable', 'field': 'exe'}]
        result = self.inspect()
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['snapshot']['proc_diagnostics'][0]['pid'], 777)
        self.snapshot['errors'].append('owned_omp_incomplete')
        self.assertIn('capture_owned_omp_incomplete', self.inspect()['blockers'])
        self.snapshot['processes'][0]['sessions'][0]['complete'] = False
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_nested_helper_logs_are_bound_but_foreign_or_incomplete_logs_block(self):
        nested = self.log.with_suffix('') / 'worker'
        nested.mkdir(parents=True)
        helper = nested / 'helper.jsonl'
        helper.write_text(self.log.read_text())
        (self.proc / '200/fd/5').symlink_to(helper)
        self.snapshot['processes'][0]['sessions'].append(orca_capture._session(helper))
        self.assertEqual(self.inspect()['blockers'], [])
        manual = dict(self.record)
        del manual['session_dir']
        manual.update(pid=200, start_ticks=2000, session_path=str(self.log))
        self.records_path.write_text(json.dumps(manual))
        self.assertEqual(self.inspect()['blockers'], [])
        self.snapshot['processes'][0]['sessions'][-1]['complete'] = False
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])
        foreign = self.root / 'foreign.jsonl'
        foreign.write_text(self.log.read_text())
        (self.proc / '200/fd/6').symlink_to(foreign)
        self.snapshot['processes'][0]['sessions'][-1] = orca_capture._session(foreign)
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_manual_main_requires_exact_pid_start_open_session_and_idle(self):
        record = dict(self.record)
        del record['session_dir']
        record.update(pid=200, start_ticks=2000, session_path=str(self.log))
        self.records_path.write_text(json.dumps(record))
        (self.proc / '200/cmdline').write_bytes(b'omp\0')
        self.assertEqual(self.inspect()['blockers'], [])
        record['start_ticks'] = 2001
        self.records_path.write_text(json.dumps(record))
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_manual_main_compaction_uses_same_completed_turn_boundary(self):
        record = dict(self.record)
        del record['session_dir']
        record.update(session_path=str(self.log))
        self.events.extend([
            {'type': 'compaction'},
            {'type': 'message', 'message': {'role': 'custom', 'customType': 'eval-state-context'}}])
        self.write_events()
        self.assertEqual(preflight._idle_session(record)['id'], self.events[0]['id'])
        self.events.append({'type': 'message', 'message': {'role': 'user'}})
        self.write_events()
        with self.assertRaisesRegex(ValueError, 'final assistant'):
            preflight._idle_session(record)

    def test_manual_main_later_work_or_unknown_event_is_not_idle(self):
        record = dict(self.record)
        del record['session_dir']
        record.update(pid=200, start_ticks=2000, session_path=str(self.log))
        self.records_path.write_text(json.dumps(record))
        self.events.append({'type': 'unknown', 'body': 'secret'})
        self.write_events()
        self.snapshot['processes'][0] = self.agent_row()
        self.assertIn('fleet_idle_binding_unproven', self.inspect()['blockers'])

    def test_sessions_in_restarted_service_cgroup_block(self):
        (self.proc / '200/cgroup').write_text('0::/system.slice/orca-serve.service/child\n')
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_lane_absence_or_failed_idle_proof_blocks(self):
        self.c['builder']['slots'] = [str(self.cwd)]
        self.assertIn('fleet_idle_binding_unproven', self.inspect()['blockers'])
        self.inventory['builders'] = [{'path': str(self.cwd), 'terminal': 'main',
                                       'connected': True, 'shell_ready': False,
                                       'binding': {'state': 'none'}}]
        with patch.object(preflight.restart, '_idle', side_effect=ValueError('private failure')):
            result = self.inspect()
        self.assertIn('fleet_idle_binding_unproven', result['blockers'])
        self.assertNotIn('private failure', json.dumps(result))

    def test_unresolved_main_command_blocks_without_skipping_identity_capture(self):
        data = copy.deepcopy(self.plan_data)
        data['projects'][0]['command'] = []
        result = self.inspect(data)
        self.assertIn('fleet:main_command_missing', result['blockers'])
        self.assertEqual(result['projects'][0]['main_process']['pid'], 200)

    def test_service_permission_failure_and_missing_session_record_do_not_leak(self):
        with patch.object(preflight, 'run', side_effect=ValueError('credential secret')):
            result = self.inspect()
        self.assertIn('service_restart_authority_unproven', result['blockers'])
        self.assertNotIn('credential secret', json.dumps(result))
        self.records_path.write_text('{}')
        self.assertIn('owned_session_launch_match_unproven', self.inspect()['blockers'])

    def test_shell_mode_never_launches_and_requires_positive_shell(self):
        data = copy.deepcopy(self.plan_data)
        project = data['projects'][0]
        project['mode'] = 'shell'
        del project['command'], project['handoff']
        self.snapshot['processes'] = []
        self.assertIn('fleet_idle_binding_unproven', self.inspect(data)['blockers'])
        with patch.object(preflight, 'orca', return_value={'terminal': {'tail': ['dev@host:~$']}}):
            result = self.inspect(data)
        self.assertEqual(result['blockers'], [])
        self.assertNotIn('main_process', result['projects'][0])


if __name__ == '__main__':
    unittest.main()

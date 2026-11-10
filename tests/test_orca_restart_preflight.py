import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import config
from fleet.commands import CommandError
from fleet import orca_restart_preflight as preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.cwd = self.root / 'checkout'
        self.cwd.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(preflight, 'PROC', self.proc).start()
        self.target_argv = ['/opt/Orca.AppImage', 'serve', '--private-argument']
        self.make_proc(99, 1, 990, '/opt/Orca.AppImage', self.target_argv)
        self.make_proc(100, 99, 1000, '/opt/node', ['node', 'runtime'])
        self.config_data = {
            'version': 1, 'repo': 'owner/fleet', 'root': str(self.cwd),
            'orca_repo_id': 'repo', 'pad_workspace': 'fleet', 'orca': '/opt/orca',
            'coordinator': {'run': 'run', 'terminal': 'main'},
            'builder': {'model': '@builder', 'resolved_model': 'provider/model',
                        'rules_file': 'AGENTS.md', 'slots': [str(self.root / 'lane')]},
        }
        self.config_path = self.private('fleet.yml', yaml.safe_dump(self.config_data))
        self.c = config.load(self.config_path)
        self.plan_data = {
            'version': 2, 'id': 'restart-1', 'state_dir': str(self.root / 'journal'),
            'service': 'orca-serve.service',
            'projects': [{'name': 'fleet', 'config': str(self.config_path)}],
        }
        self.plan_path = self.private('plan.yml', yaml.safe_dump(self.plan_data))
        self.terminal = {'handle': 'main', 'incarnationId': 'incarnation',
                         'worktreePath': str(self.cwd), 'connected': True,
                         'writable': True, 'orphaned': False, 'binding': {'id': 'run'}}
        self.snapshot = {
            'runtime': {'runtimeId': 'runtime'}, 'runtime_process': {'pid': 100, 'running': True},
            'terminals': [self.terminal], 'processes': [], 'errors': [],
            'services': [{'unit': 'fleet-owner-fleet.service', 'LoadState': 'loaded',
                          'ActiveState': 'active', 'SubState': 'running'}],
            'mailboxes': {'run': {'run': {'id': 'run', 'coordinator_handle': 'main'},
                                  'messages': [{'id': 'pending'}], 'complete': True}},
        }
        self.service_text = ('ActiveState=active\nMainPID=99\nControlGroup=/system.slice/orca-serve.service\n'
                             'KillMode=mixed\nSendSIGKILL=no\nTimeoutStopUSec=1min 30s\n')
        self.commands = []
        self.capture = patch.object(preflight.orca_capture, 'collect',
                                    side_effect=lambda *args, **kwargs: copy.deepcopy(self.snapshot)).start()
        patch.object(preflight, 'run', side_effect=self.system_read).start()

    def private(self, name, content):
        path = self.root / name
        path.write_text(content)
        path.chmod(0o600)
        return path

    def make_proc(self, pid, ppid, start, exe, argv):
        proc = self.proc / str(pid)
        proc.mkdir()
        fields = ['0'] * 20
        fields[0], fields[1], fields[2], fields[4], fields[5], fields[19] = 'S', str(ppid), str(pid), '1', str(pid), str(start)
        (proc / 'stat').write_text(f'{pid} (process) ' + ' '.join(fields))
        (proc / 'status').write_text('Uid: ' + ' '.join([str(os.getuid())] * 4) + '\n')
        (proc / 'exe').symlink_to(exe)
        (proc / 'cwd').symlink_to(self.cwd)
        (proc / 'cmdline').write_bytes(b'\0'.join(v.encode() for v in argv) + b'\0')
        (proc / 'cgroup').write_text('0::/system.slice/orca-serve.service\n')

    def system_read(self, argv):
        self.commands.append(argv)
        if argv[:2] == ['/usr/bin/systemctl', 'show']:
            return self.service_text
        if argv == ['/usr/bin/sudo', '-n', '-l', '--', '/usr/bin/systemctl', 'restart', 'orca-serve.service']:
            return 'authorization receipt deliberately not retained'
        self.fail('preflight attempted an unexpected command or mutation')

    def load(self, data=None):
        if data is not None:
            self.plan_path.write_text(yaml.safe_dump(data))
        return preflight.load_plan(self.plan_path)

    def inspect(self, data=None):
        return preflight.inspect(self.load(data), self.c)

    def test_minimal_real_configuration_needs_no_session_or_scheduling_metadata(self):
        result = self.inspect()
        self.assertEqual(result['blockers'], [])
        self.assertEqual(set(result), {'blockers', 'target', 'snapshot', 'projects', 'actions'})
        self.assertEqual(result['target'], {
            'pid': 99, 'start_ticks': 990, 'exe': '/opt/Orca.AppImage',
            'argv_sha256': hashlib.sha256(json.dumps(self.target_argv).encode()).hexdigest(),
            'cwd': str(self.cwd),
            'runtime_id': 'runtime', 'runtime_pid': 100,
        })
        self.assertNotIn('--private-argument', json.dumps(result))
        self.assertEqual(result['projects'], [{
            'name': 'fleet', 'config': str(self.config_path), 'terminal': 'main',
            'run': 'run', 'service': 'fleet-owner-fleet.service',
        }])
        self.capture.assert_called_once_with(self.c, ['fleet-owner-fleet.service'], run_ids=['run'])
        self.assertFalse((self.root / 'journal').exists())
        action = result['actions'][-1]
        self.assertEqual(action['action'], 'confirm_run_consumption')
        self.assertEqual(action['timeout_seconds'], 120)
        self.assertEqual(action['default_timeout_seconds'], 120)
        self.assertEqual(action['queries'], [{
            'project': 'fleet', 'terminal': 'main', 'run': 'run',
            'argv': ['/opt/orca', 'orchestration', 'check', '--terminal', 'main',
                     '--run', 'run', '--peek', '--json'],
        }])
        self.assertIn('without another notification', action['description'])

    def test_consumption_timeout_bounds_and_dry_run_override(self):
        for value in (0, 601, True, 2.5, '120', None):
            data = copy.deepcopy(self.plan_data)
            data['consumption_timeout_seconds'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(data)
        for value in (1, 600):
            data = copy.deepcopy(self.plan_data)
            data['consumption_timeout_seconds'] = value
            with self.subTest(value=value):
                self.assertEqual(self.load(data)['consumption_timeout_seconds'], value)
                self.assertEqual(self.inspect(data)['actions'][-1]['timeout_seconds'], value)

    def test_plan_defaults_and_bounds(self):
        plan = self.load()
        self.assertEqual(plan['timeout_seconds'], 60)
        self.assertIs(plan['allow_systemd_sigkill'], False)
        self.assertEqual(plan['consumption_timeout_seconds'], 120)
        for value in (0, 301, True, 2.5, '60'):
            data = copy.deepcopy(self.plan_data)
            data['timeout_seconds'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(data)

    def test_closed_schema_rejects_obsolete_fields(self):
        for name in ('execute_after', 'expires_at', 'target', 'session_records',
                     'confirmations', 'launch_attempts'):
            data = copy.deepcopy(self.plan_data)
            data[name] = []
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load(data)
        for name in ('worktree', 'delivery', 'mode', 'command', 'handoff'):
            data = copy.deepcopy(self.plan_data)
            data['projects'][0][name] = 'obsolete'
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load(data)

    def test_only_version_two_and_granted_service_are_valid(self):
        for name, value in (('version', 1), ('version', True), ('service', 'other.service'),
                            ('allow_systemd_sigkill', 'yes')):
            data = copy.deepcopy(self.plan_data)
            data[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.load(data)

    def test_private_paths_and_unique_projects_required(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.load()
        self.config_path.chmod(0o600)
        alias = self.root / 'alias.yml'
        alias.symlink_to(self.config_path)
        for path in ('relative.yml', str(alias)):
            data = copy.deepcopy(self.plan_data)
            data['projects'][0]['config'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.load(data)
        data = copy.deepcopy(self.plan_data)
        data['projects'].append(dict(data['projects'][0]))
        with self.assertRaises(ValueError):
            self.load(data)
        self.plan_path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.load()

    def test_symlinked_state_and_writable_ancestor_are_rejected(self):
        state = self.root / 'real-state'
        state.mkdir(mode=0o700)
        alias = self.root / 'alias-state'
        alias.symlink_to(state)
        data = copy.deepcopy(self.plan_data)
        data['state_dir'] = str(alias)
        with self.assertRaises(ValueError):
            self.load(data)
        self.root.chmod(0o777)
        try:
            with self.assertRaises(ValueError):
                self.load()
        finally:
            self.root.chmod(0o700)

    def test_duplicate_yaml_keys_rejected(self):
        self.plan_path.write_text(yaml.safe_dump(self.plan_data) + 'version: 2\n')
        with self.assertRaises(ValueError):
            preflight.load_plan(self.plan_path)

    def test_no_idle_or_model_process_census_requirements(self):
        self.snapshot['errors'] = ['proc_scan_incomplete', 'owned_omp_incomplete']
        self.snapshot['processes'] = [{'pid': 200, 'errors': ['session_unavailable'],
                                      'identity_stable': False, 'sessions': []}]
        self.snapshot['terminals'].append({'handle': 'busy-builder', 'connected': True,
                                           'binding': None, 'agentIdentity': 'omp'})
        result = self.inspect()
        self.assertEqual(result['blockers'], [])
        self.assertNotIn('idle', json.dumps(result['projects']))

    def test_inactive_supervisors_block(self):
        for state in ('failed', 'inactive', 'activating'):
            self.snapshot['services'][0]['ActiveState'] = state
            with self.subTest(state=state):
                self.assertIn('fleet_supervisor_not_running', self.inspect()['blockers'])

    def test_missing_or_duplicate_supervisor_blocks(self):
        unit = self.snapshot['services'][0]
        for units in ([], [unit, unit]):
            self.snapshot['services'] = units
            with self.subTest(units=units):
                self.assertIn('fleet_supervisor_not_running', self.inspect()['blockers'])

    def test_coordinator_binding_and_connection_required_not_caller_identity(self):
        # Dry-run is permitted from a builder: no ORCA_TERMINAL_HANDLE gate here.
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'builder'}):
            self.assertEqual(self.inspect()['blockers'], [])
        for key, value in (('connected', False), ('binding', None), ('binding', {'id': 'wrong'}),
                           ('orphaned', True), ('incarnationId', None), ('worktreePath', '/wrong')):
            original = self.terminal[key]
            self.terminal[key] = value
            with self.subTest(key=key, value=value):
                self.assertIn('fleet_coordinator_binding_unproven', self.inspect()['blockers'])
            self.terminal[key] = original

    def test_incomplete_pending_mail_blocks_even_without_capture_error(self):
        for mailbox in ({}, {'complete': False}, {'complete': True, 'run': {'id': 'wrong'}},
                        {'complete': True, 'run': {'id': 'run', 'coordinator_handle': 'wrong'}}):
            self.snapshot['mailboxes']['run'] = mailbox
            with self.subTest(mailbox=mailbox):
                self.assertIn('fleet_pending_mail_incomplete', self.inspect()['blockers'])

    def test_capture_failures_are_symbolic(self):
        self.capture.side_effect = CommandError('private failure details')
        result = self.inspect()
        self.assertIn('capture_unavailable', result['blockers'])
        self.assertNotIn('private failure details', json.dumps(result))

    def test_missing_runtime_identity_blocks(self):
        for runtime_id, pid in ((None, 100), ('runtime', None), ('runtime', True)):
            self.snapshot['runtime']['runtimeId'] = runtime_id
            self.snapshot['runtime_process']['pid'] = pid
            with self.subTest(runtime_id=runtime_id, pid=pid):
                self.assertIn('target_identity_unproven', self.inspect()['blockers'])

    def test_runtime_must_descend_from_exact_service_process(self):
        self.make_proc(101, 1, 1010, '/opt/node', ['node', 'unrelated'])
        self.snapshot['runtime_process']['pid'] = 101
        self.assertIn('target_identity_unproven', self.inspect()['blockers'])

    def test_service_and_runtime_cgroup_must_correspond(self):
        for pid in (99, 100):
            path = self.proc / str(pid) / 'cgroup'
            original = path.read_text()
            path.write_text('0::/system.slice/orca-serve.service-evil\n')
            with self.subTest(pid=pid):
                self.assertIn('target_identity_unproven', self.inspect()['blockers'])
            path.write_text(original)

    def test_unowned_process_blocks(self):
        (self.proc / '99/status').write_text('Uid: ' + ' '.join([str(os.getuid() + 1)] * 4) + '\n')
        self.assertIn('target_identity_unproven', self.inspect()['blockers'])

    def test_changed_process_or_service_identity_blocks(self):
        original = preflight._process
        calls = 0
        def changed(pid):
            nonlocal calls
            row = original(pid)
            calls += 1
            if calls >= 3:
                row['start_ticks'] += 1
            return row
        with patch.object(preflight, '_process', side_effect=changed):
            self.assertIn('target_identity_unproven', self.inspect()['blockers'])
        original_state = preflight._service_state
        calls = 0
        def changed_service(service):
            nonlocal calls
            row = original_state(service)
            calls += 1
            if calls >= 2:
                row['MainPID'] = '100'
            return row
        with patch.object(preflight, '_service_state', side_effect=changed_service):
            self.assertIn('target_identity_unproven', self.inspect()['blockers'])

    def test_inactive_service_unknown_policy_and_sudo_denial_block(self):
        for old, new in (('ActiveState=active', 'ActiveState=inactive'),
                         ('MainPID=99', 'MainPID=0'), ('KillMode=mixed', 'KillMode=unknown'),
                         ('SendSIGKILL=no', 'SendSIGKILL=unknown')):
            original = self.service_text
            self.service_text = original.replace(old, new)
            with self.subTest(new=new):
                self.assertIn('service_restart_authority_unproven', self.inspect()['blockers'])
            self.service_text = original
        system_read = self.system_read
        def denied(argv):
            if argv[0] == '/usr/bin/sudo':
                raise CommandError('private sudo diagnostics')
            return system_read(argv)
        with patch.object(preflight, 'run', side_effect=denied):
            result = self.inspect()
            self.assertIn('service_restart_authority_unproven', result['blockers'])
            self.assertNotIn('private sudo diagnostics', json.dumps(result))

    def test_systemd_sigkill_needs_explicit_plan_approval(self):
        self.service_text = self.service_text.replace('SendSIGKILL=no', 'SendSIGKILL=yes')
        self.assertIn('systemd_sigkill_not_approved', self.inspect()['blockers'])
        data = copy.deepcopy(self.plan_data)
        data['allow_systemd_sigkill'] = True
        self.assertEqual(self.inspect(data)['blockers'], [])


    def test_bare_orca_resolves_using_system_path_not_caller_path(self):
        self.c['orca'] = 'orca-ide'
        with patch.object(preflight.shutil, 'which', return_value='/usr/bin/orca-ide') as which:
            self.assertEqual(self.inspect()['blockers'], [])
            which.assert_called_once_with('orca-ide', path=os.defpath)
        self.assertEqual(self.c['orca'], '/usr/bin/orca-ide')
        self.c['orca'] = 'missing'
        with patch.object(preflight.shutil, 'which', return_value=None):
            self.assertEqual(self.inspect()['blockers'], ['orca_executable_unavailable'])


if __name__ == '__main__':
    unittest.main()

import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import patch
from fleet import config, launchd, orca_capture, restart, service, service_env
from fleet.commands import CommandError


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'private.env'
        self.path.write_text("# comment\nTOKEN='fake-secret $HOME $(false)'\nEMPTY=\nPLAIN=value\n")
        self.path.chmod(0o600)

    def test_literals_are_not_shell_expanded(self):
        self.assertEqual(service_env.read_environment(self.path),
                         {'TOKEN': 'fake-secret $HOME $(false)', 'EMPTY': '', 'PLAIN': 'value'})

    def test_mode_owner_symlink_and_nonregular_are_refused(self):
        for mode in (0o644, 0o400, 0o700):
            self.path.chmod(mode)
            with self.assertRaises(ValueError):
                service_env.read_environment(self.path)
        self.path.chmod(0o600)
        with patch.object(service_env.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(ValueError):
                service_env.read_environment(self.path)
        link = self.path.with_name('link')
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            service_env.read_environment(link)
        fifo = self.path.with_name('fifo')
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            service_env.read_environment(fifo)

    def test_invalid_line_does_not_disclose_value(self):
        for line in ('BAD KEY=fake-secret', 'TOKEN="fake-secret', 'TOKEN=fake secret', 'TOKEN=bad\0secret'):
            self.path.write_text('# comment\n' + line + '\n')
            with self.assertRaisesRegex(ValueError, '^invalid service environment line 2$'):
                service_env.read_environment(self.path)

    def test_startup_revalidates_and_only_exec_receives_values(self):
        with patch('sys.argv', ['loader', str(self.path), '/fleet', 'serve']), \
                patch.object(service_env.os, 'execve') as execute:
            service_env.main()
        self.assertEqual(execute.call_args.args[0:2], ('/fleet', ['/fleet', 'serve']))
        self.assertEqual(execute.call_args.args[2]['TOKEN'], 'fake-secret $HOME $(false)')
        self.path.chmod(0o644)
        output = io.StringIO()
        with patch('sys.argv', ['loader', str(self.path), '/fleet', 'serve']), \
                patch.object(service_env.os, 'execve') as execute, contextlib.redirect_stderr(output):
            self.assertEqual(service_env.main(), 2)
        execute.assert_not_called()
        self.assertNotIn('fake-secret', output.getvalue())


class LaunchAgentTests(unittest.TestCase):
    def test_install_reinstall_and_private_plist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / 'token.env'
            env.write_text('TOKEN=fake-secret\n')
            env.chmod(0o600)
            calls = []
            unloading = False
            def command(argv):
                calls.append(argv)
                nonlocal unloading
                if argv[1] == 'bootout':
                    unloading = True
                if argv[1] == 'bootstrap':
                    unloading = False
                if argv[1] == 'print':
                    if unloading:
                        raise CommandError('missing')
                    return '\tstate = running\n\tpid = 42\n\tenvironment = {\n\t\tTOKEN = fake-secret\n\t}\n'
                return ''
            with patch.object(launchd.Path, 'home', return_value=root), \
                    patch.object(launchd.shutil, 'which', return_value='/path with spaces/fleet'), \
                    patch.object(launchd, 'run', side_effect=command):
                result = launchd.install('fleet-owner-project.service', ['serve', '--no-deliver'], '/project space', str(env))
            agent = plistlib.loads(Path(result['plist']).read_bytes())
            self.assertEqual(agent['WorkingDirectory'], '/project space')
            self.assertEqual(agent['ProgramArguments'][-3:], ['/path with spaces/fleet', 'serve', '--no-deliver'])
            self.assertIn(str(env), agent['ProgramArguments'])
            self.assertNotIn('fake-secret', Path(result['plist']).read_text() + json.dumps(result))
            self.assertEqual(stat.S_IMODE(Path(result['plist']).stat().st_mode), 0o600)
            self.assertEqual(calls[1][2], f'gui/{os.getuid()}/fleet-owner-project')

    def test_missing_job_bootstraps_but_unknown_readiness_never_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def command(argv):
                calls.append(argv[1])
                if argv[1] == 'print':
                    raise CommandError('missing')
                return ''
            with patch.object(launchd.Path, 'home', return_value=Path(tmp)), \
                    patch.object(launchd.shutil, 'which', return_value='/fleet'), \
                    patch.object(launchd, 'run', side_effect=command):
                with self.assertRaises(CommandError):
                    launchd.install('fleet-test.service', ['serve'], tmp)
            self.assertEqual(calls, ['print', 'bootstrap', 'print'])

    def test_running_without_pid_does_not_establish_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(launchd.Path, 'home', return_value=Path(tmp)), \
                    patch.object(launchd.shutil, 'which', return_value='/fleet'), \
                    patch.object(launchd, 'status', side_effect=[CommandError('missing'), {'state': 'running', 'pid': None}]), \
                    patch.object(launchd, 'run'), \
                    patch.object(launchd.time, 'monotonic', side_effect=[0, 1, 11]), \
                    patch.object(launchd.time, 'sleep'):
                with self.assertRaisesRegex(ValueError, 'did not become running with a PID'):
                    launchd.install('fleet-test.service', ['serve'], tmp)

    def test_status_excludes_environment_and_raw_output(self):
        with patch.object(launchd, 'run', return_value='\tstate = running\n\tpid = 99\n\t\tpid = 666\n\t\tTOKEN = fake-secret\n'):
            self.assertEqual(launchd.status('fleet-test.service'),
                             {'unit': 'fleet-test.service', 'label': 'fleet-test', 'state': 'running', 'pid': 99})

    def test_reinstall_waits_for_old_job_and_refuses_unload_timeout(self):
        for disappears in (True, False):
            with self.subTest(disappears=disappears), tempfile.TemporaryDirectory() as tmp:
                registered = True
                unloading = False
                observations = 0
                started = False
                def command(argv):
                    nonlocal registered, unloading, observations, started
                    if argv[1] == 'print':
                        if unloading:
                            observations += 1
                            if disappears and observations == 3:
                                registered = False
                        if not registered:
                            raise CommandError('missing')
                        return '\tstate = running\n\tpid = 42\n'
                    if argv[1] == 'bootout':
                        unloading = True
                    if argv[1] == 'bootstrap':
                        self.assertFalse(registered, 'must not bootstrap an unloading job')
                        started = registered = True
                        unloading = False
                    return ''
                with patch.object(launchd.Path, 'home', return_value=Path(tmp)), \
                        patch.object(launchd.shutil, 'which', return_value='/fleet'), \
                        patch.object(launchd, 'run', side_effect=command), \
                        patch.object(launchd.time, 'sleep'), \
                        patch.object(launchd.time, 'monotonic', side_effect=range(30)):
                    if disappears:
                        launchd.install('fleet-test.service', ['serve'], tmp)
                        self.assertTrue(started)
                    else:
                        with self.assertRaisesRegex(ValueError, 'did not finish unloading'):
                            launchd.install('fleet-test.service', ['serve'], tmp)
                        self.assertFalse(started)


class PortabilityTests(unittest.TestCase):
    def test_macos_restart_never_sends_input(self):
        with patch.object(restart.sys, 'platform', 'darwin'), patch.object(restart, 'orca') as native:
            with self.assertRaisesRegex(ValueError, 'automatic restart unsupported on this platform'):
                restart._foreground({}, {}, '/slot', 'terminal')
        native.assert_not_called()
        with patch.object(restart.sys, 'platform', 'darwin'), patch.object(restart, 'orca') as native:
            with self.assertRaisesRegex(ValueError, 'automatic restart unsupported on this platform'):
                restart.restart({}, 'TASK-45', '/slot', 'TASK-36')
        native.assert_not_called()

    def test_macos_capture_is_partial_without_procfs(self):
        def native(c, *args):
            if args == ('status',):
                return {'runtime': {'runtimeId': 'stable'}}
            if args[:2] == ('terminal', 'list'):
                return {'terminals': []}
            if args[:2] == ('orchestration', 'inbox'):
                return {'messages': []}
            if args[:2] == ('orchestration', 'check'):
                return {'runId': 'run', 'messages': [], 'count': 0}
            return {'run': {'id': 'run', 'coordinator_handle': 'term'}}
        with patch.object(orca_capture.sys, 'platform', 'darwin'), \
                patch.object(orca_capture, 'orca', side_effect=native), \
                patch.object(launchd, 'run', return_value='\tstate = running\n\tpid = 99\n\t\tTOKEN = fake-secret\n'):
            result = orca_capture.collect({'root': '/', 'builder': {'slots': []}, 'coordinator': {'run': 'run'}}, ['fleet-test.service'])
        self.assertEqual(result['errors'], ['boot_identity_unavailable', 'local_linux_proc_unavailable'])
        self.assertIsNone(result['boot_id'])
        self.assertEqual(result['processes'], [])
        self.assertEqual(result['services'][0]['pid'], 99)
        self.assertNotIn('fake-secret', json.dumps(result))

    def test_orca_discovery_platform_and_override_precedence(self):
        # Exercise config.load using the repository's portable example structure.
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fleet.yml'
            c = {'version': 1, 'repo': 'owner/repo', 'root': tmp, 'orca_repo_id': 'repo', 'pad_workspace': 'test',
                 'builder': {'model': 'p/m', 'resolved_model': 'p/m', 'rules_file': 'rules', 'slots': [tmp]},
                 'coordinator': {'terminal': 'main', 'run': 'run'}}
            for platform, expected in (('darwin', 'orca'), ('linux', 'orca-ide')):
                path.write_text(yaml.safe_dump(c))
                with patch.object(config.sys, 'platform', platform), \
                        patch.dict(os.environ, {}, clear=True), patch.object(config.shutil, 'which', side_effect=lambda name: '/bin/' + name):
                    self.assertEqual(config.load(path)['orca'], '/bin/' + expected)
                with patch.dict(os.environ, {'ORCA_CLI_COMMAND': '/explicit/env'}):
                    self.assertEqual(config.load(path)['orca'], '/explicit/env')
            c['orca'] = '/explicit/config'
            path.write_text(yaml.safe_dump(c))
            with patch.dict(os.environ, {'ORCA_CLI_COMMAND': '/explicit/env'}):
                self.assertEqual(config.load(path)['orca'], '/explicit/config')

    def test_linux_unit_contract_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(service.sys, 'platform', 'linux'), \
                    patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp, 'PATH': '/safe/bin'}), \
                    patch.object(service.shutil, 'which', return_value='/fleet'), patch.object(service, 'run') as command:
                service._install('fleet-test.service', ['serve'], '/project', 'Supervisor')
            text = (root / 'systemd/user/fleet-test.service').read_text()
            self.assertEqual(text, '[Unit]\nDescription=Supervisor\nAfter=default.target\n\n[Service]\nType=simple\nWorkingDirectory=/project\nExecStart="/fleet" "serve"\nEnvironment="PATH=/safe/bin"\n\nRestart=on-failure\nRestartSec=15\nUMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n')
            self.assertEqual([c.args[0] for c in command.call_args_list], [
                ['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'enable', 'fleet-test.service'],
                ['systemctl', '--user', 'restart', 'fleet-test.service'], ['systemctl', '--user', 'is-active', '--quiet', 'fleet-test.service']])

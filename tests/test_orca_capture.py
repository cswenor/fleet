import json
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import orca_capture as capture
from fleet import orca_restart as conductor
from fleet import orca_restart_preflight as preflight


class CaptureTests(unittest.TestCase):
    def setUp(self):
        platform_patch = patch.object(capture.sys, 'platform', 'linux')
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_latest_resolved_events_and_partial_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.jsonl'
            events = [
                {'type': 'session', 'id': 's', 'cwd': '/slot', 'secret': 'private'},
                {'type': 'model_change', 'model': 'provider/old'},
                {'type': 'thinking_level_change', 'thinkingLevel': 'low'},
                {'type': 'message', 'body': 'private'},
                {'type': 'model_change', 'model': 'provider/current', 'resolvedModelIsFallback': True},
                {'type': 'thinking_level_change', 'thinkingLevel': 'medium'},
            ]
            path.write_text(''.join(json.dumps(e) + '\n' for e in events))
            result = capture._session(path)
            self.assertEqual(result['model'], {'model': 'provider/current', 'resolvedModelIsFallback': True})
            self.assertEqual(result['thinking'], {'thinkingLevel': 'medium'})
            self.assertTrue(result['complete'])
            self.assertNotIn('private', json.dumps(result))
            with path.open('a') as stream:
                stream.write('{"type":')
            self.assertFalse(capture._session(path)['complete'])

    def test_private_text_excluded_and_runtime_change_not_success(self):
        calls = []
        def native(config, *args):
            calls.append(args)
            if args == ('status',):
                return {'runtime': {'runtimeId': 'before' if len(calls) == 1 else 'after',
                                    'appVersion': '1', 'credential': 'private'},
                        'app': {'pid': 123, 'secret': 'private'}}
            if args[:2] == ('terminal', 'list'):
                return {'terminals': [{'handle': 'term', 'worktreePath': '/slot',
                                       'title': 'private', 'preview': 'private'}]}
            if args[1] in ('run-current', 'run-show'):
                return {'run': {'id': 'run', 'objective': 'private'}}
            if args[1] == 'inbox':
                return {'messages': [{'id': 'msg', 'read': 0, 'sequence': 7,
                                      'body': 'private', 'subject': 'private', 'payload': 'private'}]}
            self.fail('mutation or unsupported read')
        c = {'root': '/root', 'builder': {'slots': ['/slot']}, 'coordinator': {'run': 'run'}}
        with patch.object(capture, 'orca', side_effect=native), \
                patch.object(capture, '_processes', return_value=([], [], [])), \
                patch.object(capture, 'run', return_value='ActiveState=active\nMainPID=9\n'):
            result = capture.collect(c, ['fleet-test.service'])
        self.assertIn('runtime_identity_unstable', result['errors'])
        self.assertEqual(result['mailbox']['messages'], [{'id': 'msg', 'read': 0, 'sequence': 7}])
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['services'][0]['ActiveState'], 'active')

    def test_no_overwrite_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'before'
            result = {'runtime': {}, 'terminals': [], 'processes': [], 'errors': []}
            argv = ['capture', '--config', 'config', '--out', str(directory)]
            with patch('sys.argv', argv), patch.object(capture, 'load', return_value={'repo': 'a/b'}), \
                    patch.object(capture, 'collect', return_value=result) as collect:
                self.assertEqual(capture.main(), 0)
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                path = directory / 'snapshot.json'
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(capture.main(), 2)
                self.assertEqual(collect.call_count, 1)
                self.assertEqual(json.loads(path.read_text()), result)

    def test_owned_agents_outside_slots_and_sessionless_internal_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / 'omp'
            exe.touch()
            cwd = root / 'outside-slots'
            cwd.mkdir()
            cases = [
                (101, 'omp (deleted)', b'omp\0--model\0capture-sensitive-argv-value\0'),
                (102, 'omp', b'omp\0__omp_worker_js_eval_process\0'),
                (103, 'omp.1793967975370.308783.0.bak (deleted)', b'omp\0--resume\0capture-sensitive-argv-value\0'),
                (104, 'omp.1793967975370.308783.0.bak', b'omp\0'),
                (105, 'omp.unrelated.bak (deleted)', b'omp\0'),
                (106, 'omp.1793967975370.308783.0.bak (deleted)', b'python\0'),
            ]
            for pid, executable, command in cases:
                proc = root / str(pid)
                proc.mkdir()
                (proc / 'status').write_text('Uid: 1234 1234 1234 1234\n')
                fields = ['0'] * 20
                fields[0], fields[19] = 'S', '123'
                (proc / 'stat').write_text(f'{pid} (omp) ' + ' '.join(fields))
                (proc / 'exe').symlink_to(root / executable)
                (proc / 'cwd').symlink_to(cwd)
                (proc / 'cmdline').write_bytes(command)
                (proc / 'fd').mkdir()
                (proc / 'fd/0').symlink_to('pipe:[123]')
            # Process credentials differ from the fake proc directory's owner.
            with patch.object(capture, 'PROC', root), patch.object(capture.os, 'getuid', return_value=1234):
                rows, errors, diagnostics = capture._processes({'/configured-slot'})
            by_pid = {r['pid']: r for r in rows}
            self.assertEqual(by_pid[101]['errors'], ['session_metadata_incomplete'])
            self.assertEqual(by_pid[101]['argv_shape'], ['--model'])
            self.assertFalse(by_pid[101]['configured_cwd'])
            self.assertEqual(by_pid[102]['session_status'], 'not_applicable_internal_worker')
            self.assertEqual(set(by_pid), {101, 102, 103})
            self.assertEqual(by_pid[103]['exe'], str(root / cases[2][1]))
            self.assertTrue(by_pid[103]['identity_stable'])
            self.assertEqual(by_pid[103]['errors'], ['session_metadata_incomplete'])
            self.assertEqual(by_pid[102]['errors'], [])
            self.assertEqual(errors, ['owned_omp_incomplete'])
            self.assertEqual(diagnostics, [])
            self.assertNotIn('capture-sensitive-argv-value', json.dumps(rows))

    def test_unknown_credentials_are_incomplete_not_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '101').mkdir()
            (root / '101/status').write_text('Uid: unknown\n')
            with patch.object(capture, 'PROC', root):
                rows, errors, diagnostics = capture._processes(set())
            self.assertEqual(rows, [])
            self.assertEqual(errors, ['proc_scan_incomplete'])
            self.assertEqual(diagnostics, [{'pid': 101, 'owned': None, 'uids': None, 'field': 'owner',
                                            'errno': None, 'reason': 'unreadable'}])

    def test_unreadable_executable_requires_positive_exit_evidence_not_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '101').mkdir()
            (root / '101/status').write_text('Uid: 1234 1234 1234 1234\n')
            (root / '101/comm').write_text('systemd\n')
            for current, expected in (('S', 'unreadable'), ('Z', 'zombie_no_executable')):
                with self.subTest(state=current), patch.object(capture, 'PROC', root), \
                        patch.object(capture.os, 'getuid', return_value=1234), \
                        patch.object(capture, '_stat', side_effect=[{'start_ticks': 123, 'state': 'S'},
                                                                   {'start_ticks': 123, 'state': current}]):
                    rows, errors, diagnostics = capture._processes(set())
                self.assertEqual(rows, [])
                self.assertEqual(errors, ['proc_scan_incomplete'] if current == 'S' else [])
                self.assertEqual(diagnostics[0]['reason'], expected)


class ArgvSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.proc = self.root / '200'
        self.proc.mkdir()
        self.cwd = self.root / 'project'
        self.cwd.mkdir()
        self.logs = self.root / 'sessions'
        self.logs.mkdir(mode=0o700)
        self.log = self.logs / 'canonical.jsonl'
        self.now = 1767225600
        self.events = [
            {'type': 'session', 'id': 'new-session', 'cwd': str(self.cwd),
             'timestamp': datetime.fromtimestamp(self.now - 60, timezone.utc).isoformat()},
            {'type': 'model_change', 'model': 'provider/current', 'resolvedModelIsFallback': False},
            {'type': 'thinking_level_change', 'thinkingLevel': 'medium'},
            {'type': 'message', 'body': 'private transcript'},
        ]
        self.write_log()
        uid = os.getuid()
        (self.proc / 'status').write_text(f'Uid: {uid} {uid} {uid} {uid}\n')
        fields = ['0'] * 20
        fields[0], fields[19] = 'S', str(20 * os.sysconf('SC_CLK_TCK'))
        (self.proc / 'stat').write_text('200 (omp) ' + ' '.join(fields))
        executable = self.root / 'omp'
        executable.touch()
        (self.proc / 'exe').symlink_to(executable)
        (self.proc / 'cwd').symlink_to(self.cwd)
        (self.proc / 'fd').mkdir()
        (self.proc / 'environ').write_bytes(b'ORCA_TERMINAL_HANDLE=terminal\0')
        (self.proc / 'cgroup').write_text('0::/user.slice/disposable.scope\n')
        boot = self.root / 'sys/kernel/random/boot_id'
        boot.parent.mkdir(parents=True)
        boot.write_text('boot')
        self.command()
        for mocked in (
            patch.object(capture, 'PROC', self.root),
            patch.object(preflight, 'PROC', self.root),
            patch.object(capture.sys, 'platform', 'linux'),
            patch.object(capture.time, 'CLOCK_BOOTTIME', 7, create=True),
            patch.object(capture.time, 'clock_gettime', return_value=100),
            patch.object(capture.time, 'time', return_value=self.now),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def write_log(self):
        self.log.write_text(''.join(json.dumps(event) + '\n' for event in self.events))

    def command(self, directory=None, extra=()):
        argv = ['omp', '--session-dir', str(directory or self.logs), *extra, 'private prompt']
        (self.proc / 'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in argv) + b'\0')

    def row(self):
        return capture._processes({str(self.cwd)})[0][0]

    def test_fdless_live_argv_retains_actual_model_and_session_binding(self):
        row = self.row()
        self.assertEqual(row['errors'], [])
        self.assertEqual(row['sessions'][0]['id'], 'new-session')
        self.assertEqual(row['sessions'][0]['model']['model'], 'provider/current')
        self.assertEqual(row['argv_session']['session_id'], 'new-session')
        self.assertNotIn('private prompt', json.dumps(row))
        self.assertNotIn('private transcript', json.dumps(row))

    def test_symlinked_directory_or_parent_refuses_argv_capture(self):
        link = self.root / 'link'
        link.symlink_to(self.logs, target_is_directory=True)
        parent_link = self.root / 'parent-link'
        parent_link.symlink_to(self.root, target_is_directory=True)
        for directory in (link, parent_link / 'sessions'):
            with self.subTest(directory=directory):
                self.command(directory)
                row = self.row()
                self.assertIn('argv_session_unproven', row['errors'])
                self.assertEqual(row['sessions'], [])

    def test_foreign_owned_directory_refuses_argv_capture(self):
        uid = os.getuid()
        (self.proc / 'status').write_text(f'Uid: {uid + 1} {uid} {uid} {uid}\n')
        row = self.row()
        self.assertIn('argv_session_unproven', row['errors'])
        self.assertEqual(row['sessions'], [])

    def test_group_writable_directory_refuses_argv_capture(self):
        self.logs.chmod(0o770)
        row = self.row()
        self.assertIn('argv_session_unproven', row['errors'])
        self.assertEqual(row['sessions'], [])

    def test_zero_or_multiple_canonical_roots_refuse_argv_capture(self):
        other = self.logs / 'other.jsonl'
        other.write_text(self.log.read_text())
        self.assertIn('argv_session_unproven', self.row()['errors'])
        other.unlink()
        self.log.unlink()
        self.assertIn('argv_session_unproven', self.row()['errors'])

    def test_recorded_session_id_mismatch_refuses_argv_binding(self):
        row = self.row()
        record = {'session_dir': str(self.logs), 'session_id': 'different-session'}
        with self.assertRaises(ValueError):
            preflight._match_logs(row, record, set())

    def test_resume_never_uses_argv_directory_as_session_proof(self):
        self.command(extra=('--resume', 'old-session'))
        row = self.row()
        self.assertEqual(row['sessions'], [])
        self.assertIn('session_metadata_incomplete', row['errors'])

    def test_log_predating_process_refuses_argv_capture(self):
        self.events[0]['timestamp'] = datetime.fromtimestamp(self.now - 90, timezone.utc).isoformat()
        self.write_log()
        self.assertIn('argv_session_unproven', self.row()['errors'])

    def cached_config(self):
        row = self.row()
        plan = {'id': 'argv-guard', 'state_dir': str(self.root / 'journal'),
                'execute_after': '2019-01-01T00:00:00+00:00',
                'expires_at': '2099-01-01T00:00:00+00:00'}
        journal = conductor.Journal(plan)
        proof = {'snapshot': {'boot_id': 'boot', 'processes': [row]}}
        conductor._atomic(journal.directory / 'preflight.json', proof)
        journal.event('preflight', 'done', {'sha256': conductor._digest(proof)})
        config = self.root / 'config.yml'
        config.write_text('coordinator:\n  terminal: old\n  run: run\n')
        config.chmod(0o600)
        project = {'name': 'test', 'terminal': 'old', 'run': 'run', 'config': str(config)}
        return journal, project, config

    def test_cached_directory_permissions_cannot_authorize_config_cutover(self):
        journal, project, config = self.cached_config()
        before = config.read_bytes()
        self.logs.chmod(0o770)
        with patch.object(conductor, '_process_state', return_value='live'), self.assertRaises(ValueError):
            conductor._configure(project, 'replacement', journal)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(journal.step('config-test'))

    def test_changed_live_argv_cannot_authorize_config_cutover(self):
        journal, project, config = self.cached_config()
        before = config.read_bytes()
        self.command(extra=('--no-extensions',))
        with patch.object(conductor, '_process_state', return_value='live'), self.assertRaises(ValueError):
            conductor._configure(project, 'replacement', journal)
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(journal.step('config-test'))

    def test_resumed_legacy_main_remains_fd_bound(self):
        self.command(extra=('--resume', 'old-session'))
        self.events[0]['timestamp'] = datetime.fromtimestamp(self.now - 90, timezone.utc).isoformat()
        self.write_log()
        (self.proc / 'fd/3').symlink_to(self.log)
        row = self.row()
        self.assertEqual(row['errors'], [])
        self.assertEqual(row['sessions'][0]['id'], 'new-session')
        self.assertNotIn('argv_session', row)


if __name__ == '__main__':
    unittest.main()

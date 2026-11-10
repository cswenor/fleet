import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import orca_restart as restart
from fleet.commands import CommandError


class ConductorTests(unittest.TestCase):
    def plan(self, root):
        return {'version': 2, 'id': 'drill', 'state_dir': str(root / 'journal'),
                'service': 'orca-serve.service', 'timeout_seconds': 1,
                'allow_systemd_sigkill': False,
                'projects': [{'name': 'project', 'config': '/private/project.yml'}]}

    def config(self):
        return {'orca': '/opt/orca', 'root': '/project',
                'coordinator': {'terminal': 'main', 'run': 'main-run'}}

    def project(self):
        return {'name': 'project', 'config': '/private/project.yml', 'terminal': 'owner',
                'run': 'project-run', 'service': 'fleet-project.service'}

    def snapshot(self, replacement=False):
        terminals = [{'handle': handle, 'incarnationId': handle + '-inc',
                      'worktreePath': '/project', 'connected': True, 'writable': True,
                      'orphaned': False, 'binding': {'id': run}}
                     for handle, run in [('main', 'main-run'), ('owner', 'project-run')]]
        return {'boot_id': 'boot', 'errors': [], 'terminals': terminals,
                'runtime': {'runtimeId': 'new' if replacement else 'old',
                            'state': 'ready', 'reachable': True},
                'services': [{'unit': 'fleet-project.service', 'ActiveState': 'active'}],
                'mailboxes': {run: {'complete': True, 'messages': [{'id': run + '-pending'}]}
                              for run in ('main-run', 'project-run')}}

    def proof(self):
        return {'blockers': [], 'target': {'pid': 987, 'start_ticks': 12,
                                         'runtime_id': 'old', 'runtime_pid': 987,
                                         'exe': '/opt/orca-server', 'argv': ['serve'],
                                         'cwd': '/project'},
                'snapshot': self.snapshot(), 'projects': [self.project()],
                'actions': [{'argv': list(restart.RESTART_ARGV)}]}

    def native(self, _c, *args, **kwargs):
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.snapshot()['terminals'][0]}
        if args[:2] == ('orchestration', 'run-current'):
            return {'run': {'id': 'main-run'}}
        if args[:2] == ('orchestration', 'send'):
            return {'message': {'id': 'question-receipt'}}
        if args[:2] == ('orchestration', 'check'):
            self.assertEqual(args, ('orchestration', 'check', '--terminal', 'owner',
                                    '--run', 'project-run', '--peek'))
            return {'runId': 'project-run', 'count': 0, 'messages': []}
        self.fail('unexpected native mutation or query: ' + repr(args))

    def test_private_journal_and_changed_plan_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'argv': restart.RESTART_ARGV})
            recovered = restart.Journal(plan)
            self.assertEqual(recovered.step('restart')['status'], 'intent')
            self.assertEqual(journal.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual(journal.path.stat().st_mode & 0o777, 0o600)
            plan['timeout_seconds'] = 2
            with self.assertRaisesRegex(ValueError, 'plan changed'):
                restart.Journal(plan)

    def test_ambiguous_restart_is_observed_never_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            journal.event('restart', 'intent', {'target': self.proof()['target']})
            with patch.object(restart, '_wait_ready', side_effect=restart.PreservationError('no retry')), \
                    patch.object(restart, 'run') as command:
                with self.assertRaisesRegex(ValueError, 'no retry'):
                    restart._restart_service(self.config(), plan, self.proof(), journal)
                command.assert_not_called()
            receipt = {'runtime_id': 'new', 'service_pid': 111}
            with patch.object(restart, '_wait_ready', return_value=receipt), \
                    patch.object(restart, 'run') as command:
                restart._restart_service(self.config(), plan, self.proof(), journal)
                command.assert_not_called()
            self.assertEqual(journal.step('restart')['receipt'], receipt)

    def test_restart_intent_is_durable_before_single_privileged_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            def command(argv, **kwargs):
                retained = json.loads(journal.path.read_text())
                self.assertEqual(retained['steps']['restart']['status'], 'intent')
                self.assertEqual(argv, restart.RESTART_ARGV)
                raise CommandError('ambiguous subprocess result')
            with patch.object(restart, 'inspect', return_value=self.proof()), \
                    patch.object(restart, '_caller'), patch.object(restart, 'run', side_effect=command) as mutation, \
                    patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}):
                restart._restart_service(self.config(), plan, self.proof(), journal)
            self.assertEqual(mutation.call_count, 1)
            self.assertTrue(journal.done('restart'))

    def test_execute_from_surviving_main_preserves_and_notifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            after = self.snapshot(True)
            after['mailboxes']['project-run']['messages'].append({'id': 'new-mail'})
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=plan), \
                    patch.object(restart, 'inspect', return_value=self.proof()), \
                    patch.object(restart, 'orca', side_effect=self.native) as native, \
                    patch.object(restart.orca_capture, 'collect', return_value=after), \
                    patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                    patch.object(restart, '_service', return_value={'ActiveState': 'active', 'MainPID': '111'}), \
                    patch.object(restart, 'run', return_value='') as command:
                result = restart.conduct(self.config(), 'plan')
            self.assertEqual(result['event'], 'complete')
            self.assertEqual(result['pending_mail']['project-run'], {'lost': [], 'new': ['new-mail']})
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0], restart.RESTART_ARGV)
            sends = [call.args for call in native.call_args_list if call.args[1:3] == ('orchestration', 'send')]
            self.assertEqual(len(sends), 1)
            args = sends[0]
            self.assertEqual(args[args.index('--from') + 1], 'main')
            self.assertEqual(args[args.index('--run') + 1], 'main-run')
            self.assertEqual(args[args.index('--to') + 1], 'run:project-run')
            self.assertEqual(args[args.index('--type') + 1], 'question')
            retained = json.loads(Path(result['journal']).read_text())
            self.assertEqual(retained['steps']['notify-complete-project']['receipt']['message_id'], 'question-receipt')
            self.assertTrue(retained['steps']['notify-complete-project']['receipt']['enqueue_receipt_only'])
            self.assertEqual(result['consumption'][0]['status'], 'consumed')
            self.assertEqual(result['consumption'][0]['pending_count'], 0)
            self.assertEqual(result['attention_required'], [])

    def test_resume_keeps_preservation_after_mail_consumption_but_checks_current_liveness(self):
        for live in (True, False):
            with self.subTest(live=live), tempfile.TemporaryDirectory() as tmp:
                plan = self.plan(Path(tmp))
                current = self.snapshot(True)
                for mailbox in current['mailboxes'].values():
                    mailbox['messages'] = []
                if not live:
                    current['terminals'].pop()
                with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                        patch.object(restart, 'load_plan', return_value=plan), \
                        patch.object(restart, 'inspect', return_value=self.proof()), \
                        patch.object(restart, 'orca', side_effect=self.native) as native, \
                        patch.object(restart.orca_capture, 'collect', side_effect=[self.snapshot(True), current]), \
                        patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                        patch.object(restart, '_service', return_value={'ActiveState': 'active', 'MainPID': '111'}), \
                        patch.object(restart, 'run', return_value='') as command:
                    first = restart.conduct(self.config(), 'plan')
                    self.assertEqual(first['event'], 'complete')
                    after_path = Path(plan['state_dir']) / 'after.json'
                    original_after = after_path.read_bytes()
                    resumed = restart.conduct(self.config(), 'plan')
                self.assertEqual(after_path.read_bytes(), original_after)
                self.assertEqual(resumed['pending_mail'], first['pending_mail'])
                self.assertEqual(command.call_count, 1)
                self.assertEqual(resumed['event'], 'complete' if live else 'failed_manual_reconciliation')
                if not live:
                    self.assertIn('terminal', resumed['reason'])
                sends = [call for call in native.call_args_list if call.args[1:3] == ('orchestration', 'send')]
                self.assertEqual(len(sends), 1 if live else 2)
                observed = restart.Journal(plan).evidence('resume-observation')
                self.assertEqual(observed['mailboxes']['project-run']['messages'], [])

    def test_missing_terminal_lost_mail_and_inactive_supervisor_fail(self):
        for failure in ('terminal', 'mail', 'supervisor', 'binding', 'service'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                after = self.snapshot(True)
                if failure == 'terminal':
                    after['terminals'].pop()
                elif failure == 'mail':
                    after['mailboxes']['project-run']['messages'] = []
                elif failure == 'supervisor':
                    after['services'][0]['ActiveState'] = 'inactive'
                elif failure == 'binding':
                    after['terminals'][1]['binding'] = {'id': 'other-run'}
                active = 'inactive' if failure == 'service' else 'active'
                with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                        patch.object(restart, 'load_plan', return_value=self.plan(Path(tmp))), \
                        patch.object(restart, 'inspect', return_value=self.proof()), \
                        patch.object(restart, 'orca', side_effect=self.native), \
                        patch.object(restart.orca_capture, 'collect', return_value=after), \
                        patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                        patch.object(restart, '_service', return_value={'ActiveState': active, 'MainPID': '111'}), \
                        patch.object(restart, 'run', return_value=''):
                    result = restart.conduct(self.config(), 'plan')
                self.assertEqual(result['event'], 'failed_manual_reconciliation')
                retained = json.loads(Path(result['journal']).read_text())
                self.assertEqual(retained['steps']['failure']['status'], 'failed_manual_reconciliation')
                if failure == 'mail':
                    self.assertEqual(result['pending_mail']['project-run']['lost'], ['project-run-pending'])

    def test_resume_before_restart_refreshes_new_terminal_and_mail_inventory(self):
        for added in ('terminal', 'mail'):
            with self.subTest(added=added), tempfile.TemporaryDirectory() as tmp:
                plan = self.plan(Path(tmp))
                journal = restart.Journal(plan)
                journal.record('preflight', self.proof())
                journal.record('before', self.snapshot())
                fresh = self.proof()
                if added == 'terminal':
                    fresh['snapshot']['terminals'].append({
                        'handle': 'new-terminal', 'incarnationId': 'new-inc',
                        'worktreePath': '/project', 'connected': True, 'orphaned': False})
                else:
                    fresh['snapshot']['mailboxes']['project-run']['messages'].append({'id': 'new-pending'})
                with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                        patch.object(restart, 'load_plan', return_value=plan), \
                        patch.object(restart, 'inspect', return_value=fresh), \
                        patch.object(restart, 'orca', side_effect=self.native), \
                        patch.object(restart.orca_capture, 'collect', return_value=self.snapshot(True)), \
                        patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                        patch.object(restart, '_service', return_value={'ActiveState': 'active', 'MainPID': '111'}), \
                        patch.object(restart, 'run', return_value=''):
                    result = restart.conduct(self.config(), 'plan')
                self.assertEqual(result['event'], 'failed_manual_reconciliation')
                refreshed = restart.Journal(plan).evidence('before')
                if added == 'terminal':
                    self.assertIn('new-terminal', [row['handle'] for row in refreshed['terminals']])
                    self.assertIn('terminal', result['reason'])
                else:
                    self.assertEqual(result['pending_mail']['project-run']['lost'], ['new-pending'])

    def test_ambiguous_question_never_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = restart.Journal(self.plan(Path(tmp)))
            with patch.object(restart, '_caller'), \
                    patch.object(restart, 'orca', side_effect=CommandError('ambiguous enqueue')) as native:
                self.assertEqual(restart._notify(self.config(), [self.project()], journal, 'complete'), ['notify-complete-project'])
                self.assertEqual(restart._notify(self.config(), [self.project()], journal, 'complete'), ['notify-complete-project'])
            self.assertEqual(native.call_count, 1)
            self.assertEqual(journal.step('notify-complete-project')['status'], 'intent')

    def test_missing_receipt_is_ambiguous_not_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = restart.Journal(self.plan(Path(tmp)))
            with patch.object(restart, '_caller'), patch.object(restart, 'orca', return_value={}):
                pending = restart._notify(self.config(), [self.project()], journal, 'complete')
            self.assertEqual(pending, ['notify-complete-project'])
            self.assertFalse(journal.done('notify-complete-project'))

    def test_recovery_reports_final_outcome_after_prior_block_or_failure(self):
        for previous in ('blocked', 'failed'):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as tmp:
                journal = restart.Journal(self.plan(Path(tmp)))
                with patch.object(restart, '_caller'), \
                        patch.object(restart, 'orca', side_effect=[
                            {'message': {'id': 'earlier'}},
                            {'message': {'id': 'final'}}]) as native:
                    restart._notify(self.config(), [self.project()], journal, previous)
                    restart._notify(self.config(), [self.project()], journal, 'complete')
                    restart._notify(self.config(), [self.project()], journal, 'complete')
                self.assertEqual(native.call_count, 2)
                self.assertEqual(journal.step('notify-complete-project')['receipt']['message_id'], 'final')

    def test_wrong_caller_cannot_restart_or_impersonate_sender(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'builder'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=self.plan(Path(tmp))), \
                    patch.object(restart, 'orca') as native, patch.object(restart, 'run') as command:
                result = restart.conduct(self.config(), 'plan')
            self.assertEqual(result['event'], 'failed_manual_reconciliation')
            native.assert_not_called()
            command.assert_not_called()

    def test_dry_run_is_read_only_and_includes_before_and_exact_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'builder'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=plan), \
                    patch.object(restart, 'inspect', return_value=self.proof()), \
                    patch.object(restart, 'orca') as native, patch.object(restart, 'run') as command:
                result = restart.conduct(self.config(), 'plan', dry_run=True)
            self.assertEqual(result['before'], self.snapshot())
            self.assertEqual(result['actions'][0]['argv'], restart.RESTART_ARGV)
            self.assertFalse(Path(plan['state_dir']).exists())
            native.assert_not_called()
            command.assert_not_called()

    def test_incomplete_before_mail_withholds_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = self.snapshot()
            before['mailboxes']['project-run']['complete'] = False
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=self.plan(Path(tmp))), \
                    patch.object(restart, 'inspect', side_effect=[
                        self.proof(), {**self.proof(), 'snapshot': before}]), \
                    patch.object(restart, 'orca', side_effect=self.native), \
                    patch.object(restart, 'run') as command:
                result = restart.conduct(self.config(), 'plan')
            self.assertEqual(result['event'], 'failed_manual_reconciliation')
            command.assert_not_called()
            retained = json.loads(Path(result['journal']).read_text())
            self.assertNotIn('restart', retained['steps'])

    def test_changed_authority_withholds_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(Path(tmp))
            journal = restart.Journal(plan)
            checked = self.proof()
            checked['blockers'] = ['service_restart_authority_unproven']
            with patch.object(restart, 'inspect', return_value=checked), \
                    patch.object(restart, 'run') as command:
                with self.assertRaisesRegex(ValueError, 'authority'):
                    restart._restart_service(self.config(), plan, self.proof(), journal)
            command.assert_not_called()
            self.assertFalse(journal.step('restart'))

    def test_readiness_rejects_inactive_service_and_unchanged_pid(self):
        for active, pid in [('inactive', '111'), ('active', '987')]:
            clock = [0.0]
            with self.subTest(active=active, pid=pid), \
                    patch.object(restart, '_service', return_value={'ActiveState': active, 'MainPID': pid}), \
                    patch.object(restart, 'orca', return_value={'runtime': self.snapshot(True)['runtime']}), \
                    patch.object(restart.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(restart.time, 'sleep',
                                 side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
                with self.assertRaisesRegex(ValueError, 'no retry'):
                    restart._wait_ready(self.config(), {'service': 'orca-serve.service', 'timeout_seconds': 1},
                                        self.proof()['target'])

    def test_readiness_probes_share_deadline_and_reject_late_success(self):
        for phase in ('service', 'runtime'):
            with self.subTest(phase=phase):
                clock = [0.0]
                def service(unit, timeout):
                    clock[0] += timeout if phase == 'service' else 0.25
                    return {'ActiveState': 'active', 'MainPID': '111'}
                def runtime(c, *args, timeout):
                    clock[0] += timeout
                    return {'runtime': self.snapshot(True)['runtime']}
                with patch.object(restart.time, 'monotonic', side_effect=lambda: clock[0]), \
                        patch.object(restart, '_service', side_effect=service), \
                        patch.object(restart, 'orca', side_effect=runtime) as native:
                    with self.assertRaisesRegex(ValueError, 'no retry'):
                        restart._wait_ready(self.config(),
                            {'service': 'orca-serve.service', 'timeout_seconds': 1},
                            self.proof()['target'])
                self.assertEqual(clock[0], 1)
                if phase == 'service':
                    native.assert_not_called()

    def pending(self):
        return {'runId': 'project-run', 'count': 1, 'requestId': 'native-request',
                'messages': [{'id': 'question-receipt', 'run_id': 'project-run',
                              'to_handle': 'run:project-run',
                              'delivery_contract': 'current_delivery',
                              'deliveryId': 'native-delivery'}]}

    def observe_consumption(self, root, responses, timeout=2, receipt=True):
        plan = {**self.plan(root), 'consumption_timeout_seconds': timeout}
        journal = restart.Journal(plan)
        if receipt:
            journal.event('notify-complete-project', 'done',
                          {'message_id': 'question-receipt', 'enqueue_receipt_only': True})
        else:
            journal.event('notify-complete-project', 'intent', {'destination_run': 'project-run'})
        clock = [1000.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(restart.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(restart.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(restart.time, 'sleep', side_effect=sleep), \
                patch.object(restart, 'orca', side_effect=responses) as native:
            report = restart._consumption(self.config(), plan, [self.project()], journal, 'complete')
        for call in native.call_args_list:
            self.assertEqual(call.args[1:], ('orchestration', 'check', '--terminal', 'owner',
                                            '--run', 'project-run', '--peek'))
            self.assertGreater(call.kwargs['timeout'], 0)
            self.assertLessEqual(call.kwargs['timeout'], timeout)
        return report, journal, native

    def test_consumption_retains_native_identities_but_never_message_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = self.pending()
            pending.update(subject='private-subject', body='private-body',
                           arbitrary={'credential': 'private-extra'},
                           mutation={'requestId': 'native-mutation', 'token': 'private-token'})
            pending['messages'][0].update(body='private-body', subject='private-subject',
                                          arbitrary={'secret': 'private-extra'})
            report, journal, native = self.observe_consumption(Path(tmp), [
                pending, {'runId': 'project-run', 'count': 0, 'messages': []}])
            row = report['projects'][0]
            self.assertEqual((row['status'], row['pending_count']), ('consumed', 0))
            self.assertEqual(row['result_message_id'], 'question-receipt')
            self.assertEqual(row['elapsed_seconds'], 1)
            self.assertEqual(report['attention_required'], [])
            batches = [journal.evidence(key) for key in journal.data['steps']
                       if key.startswith('consumption-complete-peek-')]
            self.assertEqual(batches[0]['requestId'], 'native-request')
            self.assertEqual(batches[0]['messages'][0]['deliveryId'], 'native-delivery')
            self.assertEqual(batches[0]['mutation']['requestId'], 'native-mutation')
            for path in Path(tmp).rglob('*.json'):
                self.assertNotIn('private-', path.read_text())
            self.assertEqual(native.call_count, 2)
            # Finished receipts resume without enqueue, peek or another success notice.
            with patch.object(restart, 'orca') as command:
                resumed = restart._consumption(self.config(), journal.plan, [self.project()],
                                               restart.Journal(journal.plan), 'complete')
            command.assert_not_called()
            self.assertEqual(resumed, report)

    def test_consumption_timeout_names_project_main_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, journal, native = self.observe_consumption(
                Path(tmp), [self.pending(), self.pending()])
            row = report['attention_required'][0]
            self.assertEqual(row['status'], 'undrained')
            self.assertEqual(row['pending_count'], 1)
            self.assertEqual(row['elapsed_seconds'], 2)
            self.assertEqual(row['project'], 'project')
            self.assertEqual(row['main'], 'owner')
            self.assertEqual(row['run'], 'project-run')
            self.assertIn('Nudge Main owner', row['nudge'])
            self.assertEqual(native.call_count, 2)
            self.assertEqual(journal.evidence('consumption-complete'), report)

    def test_incomplete_pending_inventory_is_unknown_not_consumed(self):
        invalid = [
            {}, {'runId': 'project-run', 'count': 0},
            {'runId': 'other', 'count': 0, 'messages': []},
            {'runId': 'project-run', 'count': 0, 'messages': [], 'truncated': True},
            {'runId': 'project-run', 'count': 0, 'messages': [], 'acknowledged': 0},
            {**self.pending(), 'count': 0},
            {**self.pending(), 'messages': [{'id': 'question-receipt'}]},
            {'runId': 'project-run', 'count': 100, 'messages': self.pending()['messages'] * 100},
        ]
        for pending in invalid:
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as tmp:
                report, _, _ = self.observe_consumption(Path(tmp), [pending], timeout=1)
                row = report['attention_required'][0]
                self.assertEqual(row['status'], 'unknown')
                self.assertIsNone(row['pending_count'])
                self.assertFalse(row['inventory_complete'])

    def test_ambiguous_notification_and_api_outage_never_claim_consumption(self):
        for response, receipt in [
                ({'runId': 'project-run', 'count': 0, 'messages': []}, False),
                (CommandError('outage'), True)]:
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as tmp:
                report, journal, native = self.observe_consumption(
                    Path(tmp), [response], timeout=1, receipt=receipt)
                self.assertEqual(report['attention_required'][0]['status'], 'unknown')
                self.assertEqual(native.call_count, 1)
                if not receipt:
                    self.assertIsNone(report['projects'][0]['result_message_id'])
                    self.assertEqual(journal.step('notify-complete-project')['status'], 'intent')

    def test_resuming_consumption_uses_original_deadline_and_durable_enqueue(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = {**self.plan(Path(tmp)), 'consumption_timeout_seconds': 120}
            journal = restart.Journal(plan)
            journal.event('notify-complete-project', 'done',
                          {'message_id': 'question-receipt', 'enqueue_receipt_only': True})
            journal.event('consumption-complete-started', 'started', {'started_at': 900})
            with patch.object(restart.time, 'time', return_value=1019), \
                    patch.object(restart.time, 'monotonic', return_value=10), \
                    patch.object(restart, 'orca', return_value={
                        'runId': 'project-run', 'count': 0, 'messages': []}) as native:
                report = restart._consumption(self.config(), plan, [self.project()],
                                              restart.Journal(plan), 'complete')
            self.assertEqual(native.call_count, 1)
            self.assertEqual(native.call_args.kwargs['timeout'], 1)
            self.assertEqual(report['projects'][0]['result_message_id'], 'question-receipt')
            self.assertEqual(report['projects'][0]['elapsed_seconds'], 119)
            self.assertEqual(report['projects'][0]['status'], 'consumed')

    def test_undrained_report_warns_without_preservation_failure_or_extra_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = {**self.plan(Path(tmp)), 'consumption_timeout_seconds': 1}
            clock = [1000.0]
            def native(c, *args, **kwargs):
                if args[:2] == ('orchestration', 'check'):
                    return self.pending()
                return self.native(c, *args, **kwargs)
            def sleep(seconds):
                clock[0] += seconds
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}, clear=True), \
                    patch.object(restart, 'load_plan', return_value=plan), \
                    patch.object(restart, 'inspect', return_value=self.proof()), \
                    patch.object(restart, 'orca', side_effect=native) as commands, \
                    patch.object(restart.orca_capture, 'collect', return_value=self.snapshot(True)), \
                    patch.object(restart, '_wait_ready', return_value={'runtime_id': 'new', 'service_pid': 111}), \
                    patch.object(restart, '_service', return_value={'ActiveState': 'active', 'MainPID': '111'}), \
                    patch.object(restart, 'run', return_value=''), \
                    patch.object(restart.time, 'time', side_effect=lambda: clock[0]), \
                    patch.object(restart.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(restart.time, 'sleep', side_effect=sleep):
                result = restart.conduct(self.config(), 'plan')
            self.assertEqual(result['event'], 'complete')
            self.assertEqual(result['attention_required'][0]['status'], 'undrained')
            self.assertIn('Nudge Main owner', result['attention_required'][0]['nudge'])
            sends = [call for call in commands.call_args_list
                     if call.args[1:3] == ('orchestration', 'send')]
            self.assertEqual(len(sends), 1)

if __name__ == '__main__':
    unittest.main()

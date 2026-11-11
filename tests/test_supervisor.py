import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import yaml
from fleet import config, state, supervisor
from fleet.commands import CommandError


def pr(state_name='open'):
    return {'number': 1, 'url': 'https://example/pr/1', 'branch': 'feature', 'head': 'sha',
            'state': state_name, 'checks': [], 'comments': [], 'reviews': []}


def sample(p):
    return {'version': 1, 'repo': 'owner/project', 'errors': [],
            'github': {'prs': [p], 'merge_group_runs': []},
            'inventory': {'errors': [], 'stray_worktrees': [], 'worktrees': [{'id': 'w', 'branch': 'feature'}],
                          'builders': [{'terminal': 'builder', 'worktree': 'w', 'connected': True, 'flags': []}]},
            'quota': {'known': True, 'may_start': True}, 'disk': {'low': False}}


class DeliveryTests(unittest.TestCase):
    def test_lane_free_reserves_before_unlocked_delivery_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'repo': 'owner/project',
                 'coordinator': {'terminal': 'main', 'run': 'run'}}
            identity = {'id': 'launch', 'slot': '/slot', 'ref': 'TASK-1',
                        'terminal': 'builder', 'terminal_incarnation_id': 'inc'}
            state.write(c, 'launches', {'/slot': identity})
            state.write(c, 'threads-TASK-1', {'TASK-1': {'id': 'thread', 'attempts': {
                'launch': {'slot': '/slot', 'terminal': 'builder',
                           'settled': True, 'result': 'Published'}}}})
            event = {'key': 'lane_free:launch', 'subject': 'Lane free', 'body': 'Done',
                     'lane_free': identity}
            def native(*args):
                with state.lock(c, 'launcher', operation='test native checkpoint'), state.lock(c, 'needs-next'):
                    self.assertEqual(state.read(c, 'needs-next')['launch']['status'], 'sending')
                return {'message': {'id': 'msg_notice'}}
            with patch.object(supervisor, 'orca', side_effect=native) as send:
                supervisor.enqueue(c, event, 'run:run')
                self.assertEqual(state.read(c, 'needs-next')['launch']['message_id'], 'msg_notice')
                self.assertTrue(supervisor.enqueue(c, event, 'run:run')['deduplicated'])
                self.assertEqual(send.call_count, 1)

    def test_known_lane_receipt_survives_launcher_or_report_contention(self):
        for held in ('launcher', 'needs-next'):
            with self.subTest(held=held), tempfile.TemporaryDirectory() as directory:
                c = {'_state': directory, 'repo': 'owner/project', 'hooks': {},
                     'status': {'url': '', 'token_env': ''}, 'builder': {'max': 0},
                     'coordinator': {'terminal': 'main', 'run': 'run'}}
                identity = {'id': 'launch', 'slot': '/slot', 'ref': 'TASK-1',
                            'terminal': 'builder', 'terminal_incarnation_id': 'inc'}
                state.write(c, 'launches', {'/slot': identity})
                state.write(c, 'threads-TASK-1', {'TASK-1': {'id': 'thread', 'attempts': {
                    'launch': {'slot': '/slot', 'terminal': 'builder',
                               'settled': True, 'result': 'Published'}}}})
                event = {'key': 'lane_free:launch', 'subject': 'Lane free', 'body': 'Done',
                         'lane_free': identity}
                lease = state.lock(c, held)
                def native(*args):
                    lease.__enter__()
                    return {'message': {'id': 'msg_known'}}
                try:
                    with patch.object(supervisor, 'orca', side_effect=native) as send:
                        supervisor.enqueue(c, event, 'run:run')
                        if held == 'needs-next':
                            saved = state.read(c, 'supervisor')['pending_receipts']['launch']
                            self.assertEqual(saved['message_id'], 'msg_known')
                        else:
                            self.assertEqual(state.read(c, 'needs-next')['launch']['message_id'], 'msg_known')
                        self.assertEqual(send.call_count, 1)
                finally:
                    lease.__exit__(None, None, None)
                if held == 'needs-next':
                    snapshot = sample(pr())
                    snapshot['inventory']['builders'] = []
                    c['coordinator']['wake'] = False
                    with patch.object(supervisor.snapshot, 'collect', return_value=snapshot), patch.object(
                            supervisor, 'orca', side_effect=AssertionError('must not resend')):
                        supervisor.cycle(c)
                    self.assertEqual(state.read(c, 'supervisor')['pending_receipts'], {})
                self.assertEqual(state.read(c, 'needs-next')['launch']['message_id'], 'msg_known')
                with patch.object(supervisor, 'orca', side_effect=AssertionError('must not resend')):
                    self.assertTrue(supervisor.enqueue(c, event, 'run:run')['deduplicated'])

    def test_park_native_proof_and_delivery_release_launcher_and_recheck_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'repo': 'owner/project',
                 'coordinator': {'terminal': 'main', 'run': 'run'}}
            attempt = {'path': '/slot', 'launch_record_id': 'launch', 'ref': 'TASK-1',
                       'terminal': 'builder', 'incarnation_id': 'inc', 'worktree': 'tree'}
            event = {'key': 'park:launch', 'subject': 'PARK', 'body': 'Park',
                     'park': True, 'recipients': ['builder'], 'attempt': attempt}
            with state.slot_lock(c, attempt['path']), patch.object(
                    supervisor, 'orca', side_effect=AssertionError('busy PARK lease native I/O')):
                with self.assertRaisesRegex(ValueError, 'slot-'):
                    supervisor.enqueue(c, event, 'builder')
            for replaced in (False, True):
                with self.subTest(replaced=replaced):
                    state.write(c, 'launches', {'/slot': {'id': 'launch'}})
                    deliveries = []
                    def native(c, *args):
                        with self.assertRaisesRegex(ValueError, 'slot-'):
                            with state.slot_lock(c, attempt['path']):
                                self.fail('PARK proof/send must exclude terminal reuse')
                        with state.lock(c, 'launcher', operation='test native checkpoint'):
                            pass
                        if args[:2] == ('terminal', 'show'):
                            return {'terminal': {'connected': True, 'writable': True,
                                                 'worktreeId': 'tree', 'incarnationId': 'inc'}}
                        if args[:2] == ('terminal', 'read'):
                            if replaced:
                                state.write(c, 'launches', {'/slot': {'id': 'replacement'}})
                            return {'terminal': {}}
                        deliveries.append(args)
                        return {'message': {'id': 'msg_park'}}
                    with patch.object(supervisor, 'orca', side_effect=native), \
                            patch.object(supervisor.health, 'shell_ready', return_value=False):
                        if replaced:
                            with self.assertRaisesRegex(CommandError, 'park owner identity'):
                                supervisor.enqueue(c, event, 'builder')
                        else:
                            supervisor.enqueue(c, event, 'builder')
                    self.assertEqual(len(deliveries), 0 if replaced else 1)

    def test_partial_failure_survives_restart_without_resending_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            calls = []
            def enqueue(config, event, recipient):
                calls.append(recipient)
                if recipient == 'builder' and calls.count('builder') == 1:
                    raise CommandError('offline')
                return {'accepted': True}
            with patch.object(supervisor.snapshot, 'collect', return_value=sample(pr())), patch.object(supervisor, 'enqueue', side_effect=enqueue):
                supervisor.cycle(c)
            with patch.object(supervisor.snapshot, 'collect', return_value=sample(pr('merged'))), patch.object(supervisor, 'enqueue', side_effect=enqueue):
                _, failed = supervisor.cycle(c)
                self.assertEqual(failed['pending'], 1)
                _, recovered = supervisor.cycle(c)
                self.assertEqual(recovered['pending'], 0)
                supervisor.cycle(c)
            self.assertEqual(calls, ['run:r', 'builder', 'builder'])
            self.assertEqual(state.read(c, 'supervisor')['pending'], [])

    def test_alert_recovery_allows_a_new_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            s = sample(pr())
            s['inventory']['builders'] = []
            calls = []
            with patch.object(supervisor.snapshot, 'collect', return_value=s), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(e['subject'])):
                s['quota'] = {'known': True, 'may_start': False, 'reason': 'reserve'}
                supervisor.cycle(c)
                supervisor.cycle(c)
                s['quota'] = {'known': True, 'may_start': True}
                supervisor.cycle(c)
                s['quota'] = {'known': True, 'may_start': False, 'reason': 'reserve'}
                supervisor.cycle(c)
            self.assertEqual([subject for subject in calls if subject == 'Fleet quota reserve: new builders withheld'],
                             ['Fleet quota reserve: new builders withheld'] * 2)

    def test_initial_unhealthy_or_terminal_pr_is_delivered_once_to_both_inboxes(self):
        scenarios = [pr('merged'), pr('closed'), {**pr(), 'merge_state': 'DIRTY'},
                     {**pr(), 'checks': [{'name': 'CI', 'conclusion': 'failure'}]}]
        for p in scenarios:
            with self.subTest(pr=p), tempfile.TemporaryDirectory() as directory:
                c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
                calls = []
                with patch.object(supervisor.snapshot, 'collect', return_value=sample(p)), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(r)):
                    supervisor.cycle(c)
                    supervisor.cycle(c)
                self.assertEqual(calls, ['run:r', 'builder'])

    def test_inventory_outage_keeps_owner_delivery_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            calls = []
            unavailable = sample(pr('merged'))
            del unavailable['inventory']
            with patch.object(supervisor.snapshot, 'collect', return_value=unavailable), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(r)):
                _, report = supervisor.cycle(c)
                self.assertEqual(report['pending'], 1)
            with patch.object(supervisor.snapshot, 'collect', return_value=sample(pr('merged'))), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(r)):
                _, report = supervisor.cycle(c)
                self.assertEqual(report['pending'], 0)
            self.assertEqual(calls, ['run:r', 'builder'])

    def test_old_saved_baseline_is_not_proof_of_an_initial_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            s = sample({**pr(), 'merge_state': 'DIRTY'})
            state.write(c, 'supervisor', {'github': s['github'], 'pending': [], 'sent': {}, 'active_alerts': []})
            calls = []
            with patch.object(supervisor.snapshot, 'collect', return_value=s), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append((r, e['subject']))):
                supervisor.cycle(c)
                supervisor.cycle(c)
            self.assertEqual(calls, [('run:r', 'PR #1 initial state: DIRTY'), ('builder', 'PR #1 initial state: DIRTY')])

    def test_alert_content_change_notifies_without_repeating_identical_content(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            s = sample(pr())
            s['disk'] = {'low': True, 'available': 10}
            calls = []
            with patch.object(supervisor.snapshot, 'collect', return_value=s), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(e['body'])):
                supervisor.cycle(c)
                supervisor.cycle(c)
                s['disk']['available'] = 5
                supervisor.cycle(c)
                supervisor.cycle(c)
                s['disk']['low'] = False
                supervisor.cycle(c)
                s['disk']['low'] = True
                supervisor.cycle(c)
            self.assertEqual(len(calls), 3)
            self.assertNotEqual(calls[0], calls[1])
            self.assertEqual(calls[1], calls[2])

    def test_identical_github_relays_dedupe_across_restart_but_transitions_recur(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            calls = []
            def event(occurrence, body):
                return {'key': f'pr:1:checks:{occurrence}', 'subject': 'Checks changed',
                        'body': body, 'branch': 'feature'}
            events = [[event('one', 'failure')], [event('two', 'failure')],
                      [event('three', 'success')], [event('one', 'failure')]]
            with patch.object(supervisor.snapshot, 'collect', return_value=sample(pr())), \
                    patch.object(supervisor.github, 'events', side_effect=events), \
                    patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append((r, e['body']))):
                for _ in events:
                    supervisor.cycle(c)
            self.assertEqual(calls, [('run:r', 'failure'), ('builder', 'failure'),
                                     ('run:r', 'success'), ('builder', 'success'),
                                     ('run:r', 'failure'), ('builder', 'failure')])

    def test_duplicate_github_event_does_not_resend_successful_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            calls = []
            def enqueue(config, event, recipient):
                calls.append(recipient)
                if recipient == 'builder':
                    raise CommandError('private message', code='recipient_run_mismatch')
                return {'accepted': True}
            def events(before, current):
                return [{'key': 'pr:1:checks:' + str(len(calls)), 'subject': 'Checks',
                         'body': 'same failure', 'branch': 'feature'}]
            with patch.object(supervisor.snapshot, 'collect', return_value=sample(pr())), \
                    patch.object(supervisor.github, 'events', side_effect=events), \
                    patch.object(supervisor, 'enqueue', side_effect=enqueue):
                _, first = supervisor.cycle(c)
                _, second = supervisor.cycle(c)
            self.assertEqual(calls, ['run:r', 'builder', 'builder'])
            self.assertEqual(first['pending'], 1)
            self.assertEqual(second['pending'], 1)
            self.assertEqual(second['delivery_failures'],
                             [{'recipient': 'builder', 'code': 'recipient_run_mismatch'}])

    def test_group_failure_recovery_recurrence_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
            group = {'id': 42, 'conclusion': 'failure', 'prs': [1],
                     'url': 'https://example/group/42', 'membership_complete': True}
            state.write(c, 'supervisor', {'github': {'prs': [pr()], 'merge_group_runs': []},
                                        'pending': [], 'sent': {}, 'active_alerts': []})
            calls = []
            for conclusion in ['failure', 'failure', 'success', 'failure', 'failure']:
                s = sample(pr())
                s['github']['merge_group_runs'] = [{**group, 'conclusion': conclusion}]
                with patch.object(supervisor.snapshot, 'collect', return_value=s), \
                        patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(r)):
                    supervisor.cycle(c)
            self.assertEqual(calls, ['run:r', 'builder', 'run:r', 'builder'])

class CollectionAlertTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'_state': directory.name, 'coordinator': {'run': 'r', 'wake': False},
                  'hooks': {}, 'status': {'url': '', 'token_env': ''}}
        self.s = sample(pr())
        self.s['inventory']['builders'] = []
        self.calls = []
        collect = patch.object(supervisor.snapshot, 'collect', return_value=self.s)
        collect.start()
        self.addCleanup(collect.stop)
        enqueue = patch.object(supervisor, 'enqueue',
                               side_effect=lambda c, event, recipient: self.calls.append(event))
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def test_two_failures_persist_and_relay_latest_reason_once(self):
        for key in ('github', 'inventory', 'coordinator', 'disk', 'quota'):
            with self.subTest(key=key):
                state.write(self.c, 'supervisor', {
                    'github': {}, 'pending': [], 'sent': {}, 'active_alerts': {}})
                self.calls.clear()
                label = key + ' collection unavailable'
                self.s['errors'] = [label]
                self.s['error_details'] = {label: 'CommandError: first failure'}
                supervisor.cycle(self.c)
                self.assertEqual(self.calls, [])
                self.assertEqual(state.read(self.c, 'supervisor')['collection_failure_streaks'],
                                 {label: 1})
                self.s['error_details'][label] = 'CommandError: latest failure'
                supervisor.cycle(self.c)
                supervisor.cycle(self.c)
                self.assertEqual([e['body'] for e in self.calls],
                                 [label + ': CommandError: latest failure'])

    def test_recovery_and_different_key_break_consecutive_failures(self):
        for errors in (['github collection unavailable'], [],
                       ['github collection unavailable'], ['disk collection unavailable']):
            self.s['errors'] = errors
            supervisor.cycle(self.c)
        self.assertEqual(self.calls, [])
        self.s['errors'] = ['disk collection unavailable']
        supervisor.cycle(self.c)
        self.assertEqual([e['body'] for e in self.calls], ['disk collection unavailable'])



class QuotaAlertTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'_state': directory.name, 'coordinator': {'run': 'r'}, 'hooks': {},
                  'status': {'url': '', 'token_env': ''}, 'builder': {'max': 3}}
        self.s = sample(pr())
        self.s['quota'] = {'known': False, 'may_start': False, 'reason': 'usage unavailable or stale'}
        self.calls = []
        self.collect = patch.object(supervisor.snapshot, 'collect', return_value=self.s)
        self.collect.start()
        self.addCleanup(self.collect.stop)
        enqueue = patch.object(supervisor, 'enqueue',
                               side_effect=lambda c, event, recipient: self.calls.append(event))
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def test_transient_unknown_withholds_starts_without_alerting(self):
        supervisor.cycle(self.c)
        self.assertFalse(supervisor.snapshot.may_start(self.c, self.s)['may_start'])
        self.assertEqual(self.calls, [])
        self.assertEqual(state.read(self.c, 'supervisor')['quota_unknown_cycles'], 1)
        self.s['quota'] = {'known': True, 'may_start': True}
        supervisor.cycle(self.c)
        self.assertEqual(self.calls, [])
        self.assertEqual(state.read(self.c, 'supervisor')['quota_unknown_cycles'], 0)

    def test_default_threshold_persists_and_deduplicates(self):
        for _ in range(2):
            supervisor.cycle(self.c)
            self.assertEqual(self.calls, [])
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['key'].startswith('quota:'))
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)

    def test_recovery_resets_configurable_threshold_and_recurrence(self):
        self.c['quota'] = {'unknown_alert_cycles': 2}
        supervisor.cycle(self.c)
        self.s['quota'] = {'known': True, 'may_start': True}
        supervisor.cycle(self.c)
        self.s['quota'] = {'known': False, 'may_start': False, 'reason': 'unknown'}
        supervisor.cycle(self.c)
        self.assertEqual(self.calls, [])
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)
        self.s['quota'] = {'known': True, 'may_start': True}
        supervisor.cycle(self.c)
        self.s['quota'] = {'known': False, 'may_start': False, 'reason': 'unknown'}
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 2)

    def test_known_reserve_alerts_immediately_and_resets_unknown_streak(self):
        self.s['inventory']['builders'] = []
        supervisor.cycle(self.c)
        self.s['quota'] = {'known': True, 'may_start': False, 'reason': 'reserve'}
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['key'].startswith('quota:reserve:'))
        self.assertEqual(state.read(self.c, 'supervisor')['quota_unknown_cycles'], 0)

    def test_quota_collection_exception_uses_same_debounce(self):
        self.collect.stop()
        self.c.update(root=self.c['_state'], repo='owner/project',
                      quota={}, disk={'path': self.c['_state'], 'min_free_gb': 0})
        with patch.object(supervisor.snapshot.quota, 'collect', side_effect=CommandError('unavailable')), \
                patch.object(supervisor.snapshot.health, 'collect', return_value=self.s['inventory']), \
                patch.object(supervisor.snapshot.health, 'coordinator', return_value={
                    'terminal': 'main', 'state': 'up'}), \
                patch.object(supervisor.snapshot.github, 'collect', return_value=self.s['github']):
            for _ in range(2):
                s, _ = supervisor.cycle(self.c)
                self.assertIn('quota collection unavailable', s['errors'])
                self.assertFalse(supervisor.snapshot.may_start(self.c, s)['may_start'])
                self.assertEqual(self.calls, [])
            supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual({e['subject'] for e in self.calls},
                         {'Fleet collection error', 'Fleet quota reserve: new builders withheld'})

    def test_unrelated_errors_do_not_wait_for_quota_threshold(self):
        self.s['errors'] = ['github collection unavailable']
        self.s['inventory']['errors'] = ['terminal collection unavailable']
        supervisor.cycle(self.c)
        self.assertEqual([e['body'] for e in self.calls], ['terminal collection unavailable'])
        supervisor.cycle(self.c)
        self.assertEqual([e['body'] for e in self.calls],
                         ['terminal collection unavailable', 'github collection unavailable'])

    def test_threshold_one_alerts_on_first_unknown(self):
        self.c['quota'] = {'unknown_alert_cycles': 1}
        supervisor.cycle(self.c)
        self.assertEqual(len(self.calls), 1)


class QuotaConfigTests(unittest.TestCase):
    def test_unknown_alert_cycles_default_and_positive_integer_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.yml'
            c = {'version': 1, 'repo': 'owner/project', 'root': directory,
                 'orca_repo_id': 'repo', 'pad_workspace': 'workspace', 'orca': '/orca',
                 'builder': {'model': 'model', 'resolved_model': 'provider/model',
                             'rules_file': 'rules', 'slots': [directory]},
                 'coordinator': {'run': 'r', 'terminal': 'main'}}
            path.write_text(yaml.safe_dump(c))
            self.assertEqual(config.load(path)['quota']['unknown_alert_cycles'], 3)
            for value in (1, 5):
                c['quota'] = {'unknown_alert_cycles': value}
                path.write_text(yaml.safe_dump(c))
                self.assertEqual(config.load(path)['quota']['unknown_alert_cycles'], value)
            for value in (0, -1, True, False, 1.5, '3', None):
                with self.subTest(value=value):
                    c['quota'] = {'unknown_alert_cycles': value}
                    path.write_text(yaml.safe_dump(c))
                    with self.assertRaisesRegex(ValueError, 'unknown_alert_cycles'):
                        config.load(path)


if __name__ == '__main__':
    unittest.main()

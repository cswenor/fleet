import tempfile
import unittest
from unittest.mock import patch
from fleet import state, supervisor
from fleet.commands import CommandError


def pr(state_name='open'):
    return {'number': 1, 'url': 'https://example/pr/1', 'branch': 'feature', 'head': 'sha',
            'state': state_name, 'checks': [], 'comments': [], 'reviews': []}


def sample(p):
    return {'version': 1, 'repo': 'owner/project', 'errors': [],
            'github': {'prs': [p], 'merge_group_runs': []},
            'inventory': {'errors': [], 'stray_worktrees': [], 'worktrees': [{'id': 'w', 'branch': 'feature'}],
                          'builders': [{'terminal': 'builder', 'worktree': 'w', 'connected': True, 'flags': []}]},
            'quota': {'may_start': True}, 'disk': {'low': False}}


class DeliveryTests(unittest.TestCase):
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
            calls = []
            with patch.object(supervisor.snapshot, 'collect', return_value=s), patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: calls.append(e['subject'])):
                s['quota'] = {'may_start': False, 'reason': 'reserve'}
                supervisor.cycle(c)
                supervisor.cycle(c)
                s['quota'] = {'may_start': True}
                supervisor.cycle(c)
                s['quota'] = {'may_start': False, 'reason': 'reserve'}
                supervisor.cycle(c)
            self.assertEqual(calls, ['Fleet quota reserve: new builders withheld'] * 2)

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


if __name__ == '__main__':
    unittest.main()

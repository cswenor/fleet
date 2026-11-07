import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import config, health, state, supervisor
from fleet.commands import CommandError


def snapshot():
    return {'errors': [], 'github': {'prs': [
        {'number': 1, 'url': 'https://example/pr/1', 'branch': 'feature', 'head': 'sha',
         'state': 'open', 'checks': [], 'comments': [], 'reviews': []}], 'merge_group_runs': []},
        'inventory': {'errors': [], 'stray_worktrees': [],
                      'worktrees': [{'id': 'w', 'branch': 'feature'}],
                      'builders': [{'terminal': 'builder', 'worktree': 'w', 'path': '/slot',
                                    'task': 'TASK-37', 'title': 'Builder', 'model': 'provider/model',
                                    'connected': True, 'flags': []}]},
        'quota': {'known': True, 'may_start': True}, 'disk': {'low': False}}


class UrgentRelayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'_state': directory.name, 'coordinator': {'run': 'r', 'terminal': 'main'},
                  'repo': 'owner/project', 'hooks': {}, 'status': {'url': '', 'token_env': ''}}
        self.s = snapshot()
        self.calls = []
        collect = patch.object(supervisor.snapshot, 'collect', side_effect=lambda c: copy.deepcopy(self.s))
        collect.start()
        self.addCleanup(collect.stop)
        enqueue = patch.object(supervisor, 'enqueue', side_effect=self.enqueue)
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def enqueue(self, c, event, recipient):
        self.calls.append((copy.deepcopy(event), recipient))
        return {'accepted': True}

    def cycle(self, now):
        with patch.object(supervisor.time, 'time', return_value=now):
            return supervisor.cycle(self.c)

    def urgent_calls(self):
        return [(event, recipient) for event, recipient in self.calls if event.get('type') == 'question']

    def test_default_health_and_stray_events_are_urgent_and_other_alerts_ordinary(self):
        self.s['inventory']['builders'][0]['flags'] = [
            'provider_stalled', 'idle', 'wrong_model', 'waiting_dialog']
        self.s['inventory']['stray_worktrees'] = ['/stray']
        self.cycle(0)
        self.cycle(1)
        self.assertEqual(len(self.urgent_calls()), 4)
        self.assertTrue(all(e['subject'].startswith('[urgent] ') for e, _ in self.urgent_calls()))
        ordinary = [e for e, _ in self.calls if e.get('type', 'status') == 'status']
        self.assertEqual([e['subject'] for e in ordinary], ['Builder waiting_dialog: TASK-37'])
        self.s['inventory']['builders'][0]['flags'] = []
        self.s['inventory']['stray_worktrees'] = []
        self.cycle(2)
        self.s['inventory']['builders'][0]['flags'] = ['wrong_model']
        self.cycle(3)
        self.assertEqual(len(self.urgent_calls()), 5)

    def test_configured_event_subset_and_empty_list_keep_other_relays_status(self):
        self.c['urgent'] = {'events': ['wrong_model']}
        self.s['inventory']['builders'][0]['flags'] = ['wrong_model', 'provider_stalled']
        self.cycle(0)
        self.assertEqual(len(self.urgent_calls()), 1)
        self.c['urgent']['events'] = []
        self.cycle(1)
        self.assertEqual(self.calls[-1][0]['subject'], 'Builder wrong_model: TASK-37')
        self.assertEqual(self.calls[-1][0].get('type', 'status'), 'status')

    def test_stuck_boundary_ignores_comment_churn_and_persists_dedup(self):
        self.cycle(100)
        self.cycle(2799)
        self.assertEqual(self.urgent_calls(), [])
        self.s['github']['prs'][0].update(updated_at='later', comments=[{'id': 9}])
        self.cycle(2800)
        self.assertEqual([r for _, r in self.urgent_calls()], ['run:r', 'builder'])
        self.assertEqual(state.read(self.c, 'supervisor')['pr_progress']['1']['since'], 100)
        self.cycle(5000)
        self.assertEqual(len(self.urgent_calls()), 2)
        del self.s['github']
        self.cycle(5100)
        self.s['github'] = snapshot()['github']
        self.cycle(5200)
        self.assertEqual(len(self.urgent_calls()), 2)

    def test_progress_recovers_then_stuck_can_recur(self):
        for field, value in [('state', 'closed'), ('head', 'newsha'), ('merge_state', 'CLEAN'),
                             ('checks', [{'id': 1, 'name': 'CI', 'status': 'completed', 'conclusion': 'success'}]),
                             ('reviews', [{'id': 2, 'state': 'APPROVED', 'submitted_at': 'later'}])]:
            with self.subTest(field=field):
                ledger = {}
                current = snapshot()['github']
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 0), [])
                self.assertEqual(len(supervisor.stuck_pr_alerts(self.c, ledger, current, 2700)), 1)
                current['prs'][0][field] = value
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 2701), [])
                if field == 'state':
                    self.assertEqual(ledger['pr_progress'], {})
                    current['prs'][0]['state'] = 'open'
                    supervisor.stuck_pr_alerts(self.c, ledger, current, 2701)
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 5400), [])
                self.assertEqual(len(supervisor.stuck_pr_alerts(self.c, ledger, current, 5401)), 1)
        self.cycle(0)
        self.cycle(2700)
        self.s['github']['prs'][0]['head'] = 'newsha'
        self.cycle(2701)
        self.cycle(5401)
        self.assertEqual(len(self.urgent_calls()), 4)

    def test_check_and_review_order_is_not_progress_and_queue_change_is(self):
        ledger = {}
        current = snapshot()['github']
        current['prs'][0].update(checks=[{'id': 2}, {'id': 1}], reviews=[{'id': 4}, {'id': 3}])
        supervisor.stuck_pr_alerts(self.c, ledger, current, 0)
        current['prs'][0]['checks'].reverse()
        current['prs'][0]['reviews'].reverse()
        self.assertEqual(len(supervisor.stuck_pr_alerts(self.c, ledger, current, 2700)), 1)
        current['merge_queue'] = [{'state': 'QUEUED', 'pullRequest': {'number': 1}}]
        self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 2701), [])
        current['prs'] = []
        supervisor.stuck_pr_alerts(self.c, ledger, current, 2800)
        self.assertEqual(ledger['pr_progress'], {})

    def test_short_threshold_retry_survives_recovery_without_resending_run(self):
        self.c['urgent'] = {'pr_stuck_minutes': 0.5}
        self.cycle(0)
        def fail_builder(c, event, recipient):
            self.enqueue(c, event, recipient)
            if recipient == 'builder':
                raise CommandError('offline')
        with patch.object(supervisor, 'enqueue', side_effect=fail_builder):
            _, report = self.cycle(30)
            self.assertEqual(report['pending'], 1)
        self.s['github']['prs'][0]['head'] = 'progress'
        self.cycle(31)
        stuck = [(e, r) for e, r in self.urgent_calls() if e['key'].startswith('pr_stuck:')]
        self.assertEqual([r for _, r in stuck], ['run:r', 'builder', 'builder'])
        self.assertEqual(stuck[0][0]['key'], stuck[-1][0]['key'])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_merge_group_failure_initial_delivery_dedup_recovery_and_recurrence(self):
        group = {'id': 42, 'conclusion': 'failure', 'prs': [1],
                 'url': 'https://example/group/42', 'membership_complete': True}
        for conclusion in ['failure', 'failure', 'success', 'failure', 'failure']:
            self.s['github']['merge_group_runs'] = [{**group, 'conclusion': conclusion}]
            self.cycle(0)
        self.assertEqual([r for _, r in self.urgent_calls()], ['run:r', 'builder'] * 2)
        self.assertTrue(all(e['key'].startswith('group:') for e, _ in self.urgent_calls()))


class CompletedIdleTests(unittest.TestCase):
    enqueue = UrgentRelayTests.enqueue
    cycle = UrgentRelayTests.cycle
    urgent_calls = UrgentRelayTests.urgent_calls

    def setUp(self):
        UrgentRelayTests.setUp(self)
        self.c.update(root='/project', pad_workspace='workspace')
        self.builder = self.s['inventory']['builders'][0]
        self.builder.update(flags=['idle'], incarnation_id='incarnation')
        self.launch = {'id': 'attempt', 'ref': 'TASK-37', 'terminal': 'builder',
                       'terminal_incarnation_id': 'incarnation', 'at': 1767225600}
        state.write(self.c, 'launches', {'/slot': self.launch})
        self.attempt = {'slot': '/slot', 'terminal': 'builder', 'settled': True,
                        'result': 'Published. Next TASK-38.'}
        self.save_attempt()
        self.item = {'fields': {'status': 'done'}}
        self.comments = []
        self.pad_calls = []
        pad = patch.object(supervisor, 'json_run', side_effect=self.pad)
        pad.start()
        self.addCleanup(pad.stop)

    def save_attempt(self):
        state.write(self.c, 'threads-TASK-37',
                    {'TASK-37': {'id': 'thread', 'attempts': {'attempt': self.attempt}}})

    def pad(self, argv, cwd):
        self.pad_calls.append(argv)
        return self.item if 'show' in argv else self.comments

    def test_settled_done_suppresses_only_idle(self):
        self.builder['flags'] += ['wrong_model', 'provider_stalled']
        self.cycle(0)
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder wrong_model: TASK-37',
                          '[urgent] Builder provider_stalled: TASK-37'])

    def test_unsettled_or_resultless_attempt_keeps_idle_urgent(self):
        for change in ({'settled': False}, {'result': ''}):
            with self.subTest(change=change):
                self.attempt.update(settled=True, result='Published')
                self.attempt.update(change)
                self.save_attempt()
                state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                                                  'active_alerts': []})
                self.calls.clear()
                self.cycle(0)
                self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                                 ['[urgent] Builder idle: TASK-37'])
        self.assertEqual(self.pad_calls, [])

    def test_unknown_settlement_keeps_idle_urgent(self):
        for raw in ('{', '[]', '{"TASK-37": null}'):
            with self.subTest(raw=raw):
                (Path(self.c['_state']) / 'threads-TASK-37.json').write_text(raw)
                state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                                                  'active_alerts': []})
                self.calls.clear()
                self.cycle(0)
                self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                                 ['[urgent] Builder idle: TASK-37'])

    def test_replacement_terminal_or_launch_does_not_inherit_settlement(self):
        for change in ({'terminal': 'replacement'}, {'id': 'new-attempt'},
                       {'terminal_incarnation_id': 'replacement'}):
            with self.subTest(change=change):
                state.write(self.c, 'launches', {'/slot': {**self.launch, **change}})
                state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                                                  'active_alerts': []})
                self.calls.clear()
                self.cycle(0)
                self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                                 ['[urgent] Builder idle: TASK-37'])

    def test_unreadable_pad_keeps_idle_urgent(self):
        with patch.object(supervisor, 'json_run', side_effect=CommandError('offline')):
            self.cycle(0)
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])

    def test_positive_completion_is_cached_only_until_launch_changes(self):
        self.cycle(0)
        with patch.object(supervisor, 'json_run', side_effect=CommandError('offline')):
            self.cycle(1)
            self.assertEqual(self.urgent_calls(), [])
            state.write(self.c, 'launches', {'/slot': {**self.launch, 'id': 'new'}})
            self.cycle(2)
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])
        self.assertEqual(len(self.pad_calls), 1)

    def test_negative_completion_is_retried(self):
        self.item['fields']['status'] = 'in-progress'
        self.attempt['result'] = 'Published without next ref'
        self.save_attempt()
        self.cycle(0)
        self.item['fields']['status'] = 'done'
        self.cycle(1)
        self.assertNotIn('health:w:idle', state.read(self.c, 'supervisor')['active_alerts'])
        self.assertEqual(len([argv for argv in self.pad_calls if 'show' in argv]), 2)

    def test_handoff_requires_later_publication_evidence_on_next_item(self):
        from fleet import restart
        self.item['fields']['status'] = 'in-progress'
        for body, created, suppress in (
                ('TASK-37 handoff: next CLI piece', '2026-01-02T00:00:00Z', True),
                ('TASK-37 handoff: next CLI piece', '2025-12-31T00:00:00Z', False),
                ('TASK-370 handoff: unrelated', '2026-01-02T00:00:00Z', False)):
            with self.subTest(body=body, created=created):
                self.c.pop('_completed_idle_launches', None)
                self.calls.clear()
                state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                                                  'active_alerts': []})
                comments = [{'id': 'handoff', 'body': body, 'created_at': created}]
                def pad(argv, cwd):
                    return self.item if 'show' in argv else comments
                pr = {'number': 1, 'title': 'TASK-37', 'body': '',
                      'url': 'https://example/pr/1', 'mergedAt': '2026-01-01T00:00:00Z'}
                page = {'data': {'search': {'issueCount': 1, 'nodes': [pr],
                        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
                with patch.object(restart, 'json_run', side_effect=pad), \
                        patch.object(restart, 'gh', return_value=page):
                    self.cycle(0)
                self.assertEqual(self.urgent_calls() == [], suppress)

    def test_finished_comments_can_supply_next_ref_but_api_failure_keeps_alert(self):
        from fleet import restart
        self.item['fields']['status'] = 'in-progress'
        self.attempt['result'] = 'Published'
        self.save_attempt()
        self.comments = [{'body': 'Next TASK-38 handoff'}]
        with patch.object(restart, 'json_run', side_effect=self.pad), \
                patch.object(restart, 'gh', side_effect=CommandError('offline')):
            self.cycle(0)
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])


class UrgentConfigAndDeliveryTests(unittest.TestCase):
    def test_native_type_is_question_only_for_explicit_urgent_events(self):
        c = {'coordinator': {'terminal': 'main', 'run': 'r'}, 'repo': 'owner/project'}
        event = {'key': 'health:w:wrong_model', 'subject': 'Wrong model', 'body': 'detail'}
        with patch.object(supervisor, 'orca') as send:
            supervisor.enqueue(c, supervisor.urgent_event(c, event), 'run:r')
            argv = send.call_args.args
            self.assertEqual(argv[argv.index('--type') + 1], 'question')
            self.assertEqual(argv[argv.index('--subject') + 1], '[urgent] Wrong model')
            supervisor.enqueue(c, event, 'run:r')
            argv = send.call_args.args
            self.assertEqual(argv[argv.index('--type') + 1], 'status')

    def test_existing_idle_threshold_is_strictly_exceeded(self):
        terminal = {'connected': True, 'lastOutputAt': 1000,
                    'projection': {'provider': {'model': 'provider/model'}}}
        for now, idle in [(2701, False), (2702, True)]:
            _, flags, _ = health.classify(terminal, '', 'provider/model', 2700, now)
            self.assertEqual('idle' in flags, idle)

    def test_config_defaults_overrides_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.yml'
            c = {'version': 1, 'repo': 'owner/project', 'root': directory,
                 'orca_repo_id': 'repo', 'pad_workspace': 'workspace', 'orca': '/orca',
                 'builder': {'model': 'model', 'resolved_model': 'provider/model',
                             'rules_file': 'rules', 'slots': [directory]},
                 'coordinator': {'run': 'r', 'terminal': 'main'}}
            path.write_text(yaml.safe_dump(c))
            self.assertEqual(config.load(path)['urgent'],
                             {'events': list(config.URGENT_EVENTS), 'pr_stuck_minutes': 45})
            for urgent in [{'events': []}, {'events': ['wrong_model'], 'pr_stuck_minutes': 0.5}]:
                c['urgent'] = urgent
                path.write_text(yaml.safe_dump(c))
                loaded = config.load(path)['urgent']
                self.assertEqual(loaded['events'], urgent['events'])
            for urgent in [None, [], {'events': 'all'}, {'events': ['unknown']},
                           {'events': ['idle', 'idle']}, {'events': [None]},
                           *({'pr_stuck_minutes': x} for x in [0, -1, True, '45', None, float('inf'), float('nan')])]:
                with self.subTest(urgent=urgent):
                    c['urgent'] = urgent
                    path.write_text(yaml.safe_dump(c))
                    with self.assertRaisesRegex(ValueError, 'urgent'):
                        config.load(path)


if __name__ == '__main__':
    unittest.main()

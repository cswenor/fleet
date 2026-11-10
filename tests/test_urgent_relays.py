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
        self.cycle(2.5)
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

    def test_paused_prs_clear_timers_and_resume_with_full_interval(self):
        for pause in ({'draft': True}, {'labels': ['Parked']},
                      {'draft': True, 'labels': ['parked']}):
            with self.subTest(pause=pause):
                current = snapshot()['github']
                pr = current['prs'][0]
                ledger = {}
                supervisor.stuck_pr_alerts(self.c, ledger, current, 0)
                pr.update(pause)
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 2700), [])
                self.assertEqual(ledger['pr_progress'], {})
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 9000), [])
                pr.update(draft=False, labels=[])
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 9001), [])
                self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 11700), [])
                self.assertEqual([e['pr'] for e in supervisor.stuck_pr_alerts(
                    self.c, ledger, current, 11701)], [1])

    def test_configured_parked_labels_and_empty_override(self):
        current = snapshot()['github']
        current['prs'][0]['labels'] = ['On Hold']
        self.c['github'] = {'parked_labels': ['on hold']}
        ledger = {}
        supervisor.stuck_pr_alerts(self.c, ledger, current, 0)
        self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 2700), [])
        self.c['github']['parked_labels'] = []
        supervisor.stuck_pr_alerts(self.c, ledger, current, 2701)
        self.assertEqual([e['pr'] for e in supervisor.stuck_pr_alerts(
            self.c, ledger, current, 5401)], [1])
        current['prs'][0]['draft'] = True
        self.assertEqual(supervisor.stuck_pr_alerts(self.c, ledger, current, 8101), [])

    def test_pause_discards_pending_stuck_but_keeps_other_relays(self):
        for pause in ({'draft': True}, {'labels': ['parked']}):
            with self.subTest(pause=pause):
                self.s = snapshot()
                self.calls.clear()
                state.write(self.c, 'supervisor', {
                    'pending': [], 'sent': {}, 'github': {}, 'active_alerts': {}})
                self.cycle(0)
                with patch.object(supervisor, 'enqueue', side_effect=CommandError('offline')):
                    self.cycle(2700)
                self.assertEqual(len(state.read(self.c, 'supervisor')['pending']), 1)
                self.s['github']['prs'][0].update(
                    **pause, checks=[{'id': 1, 'name': 'CI', 'conclusion': 'failure'}])
                self.cycle(2701)
                self.assertFalse(any(e['key'].startswith('pr_stuck:') for e, _ in self.calls))
                self.assertTrue(any(e['key'].startswith('pr:') for e, _ in self.calls))
                self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

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

    def test_unknown_queue_holds_pending_stuck_delivery(self):
        self.cycle(0)
        with patch.object(supervisor, 'enqueue', side_effect=CommandError('offline')):
            self.cycle(2700)
        before = len(self.calls)
        self.s['github']['merge_queue_unknown'] = True
        self.cycle(3000)
        self.assertEqual(len(self.calls), before)
        self.s['github']['merge_queue_unknown'] = False
        self.cycle(3001)
        self.assertEqual([r for e, r in self.calls if e['key'].startswith('pr_stuck:')],
                         ['run:r', 'builder'])

    def test_unknown_queue_preserves_partial_receipts_past_retention(self):
        self.cycle(0)

        def fail_builder(c, event, recipient):
            if recipient == 'builder':
                raise CommandError('offline')
            return self.enqueue(c, event, recipient)

        with patch.object(supervisor, 'enqueue', side_effect=fail_builder):
            self.cycle(2700)
        self.s['github']['merge_queue_unknown'] = True
        self.cycle(8 * 86400)
        self.s['github']['merge_queue_unknown'] = False
        self.cycle(8 * 86400 + 1)
        self.assertEqual([r for e, r in self.calls if e['key'].startswith('pr_stuck:')],
                         ['run:r', 'builder'])

    def test_merge_group_failure_initial_delivery_dedup_recovery_and_recurrence(self):
        group = {'id': 42, 'conclusion': 'failure', 'prs': [1],
                 'url': 'https://example/group/42', 'membership_complete': True}
        for conclusion in ['failure', 'failure', 'success', 'failure', 'failure']:
            self.s['github']['merge_group_runs'] = [{**group, 'conclusion': conclusion}]
            self.cycle(0)
        self.assertEqual([r for _, r in self.urgent_calls()], ['run:r', 'builder'] * 2)
        self.assertTrue(all(e['key'].startswith('group:') for e, _ in self.urgent_calls()))

    def test_superseded_cancelled_group_is_one_status_per_run(self):
        self.s['github']['merge_queue'] = [
            {'headCommit': {'oid': 'current'}, 'pullRequest': {'number': 1}}]
        group = {'id': 42, 'head_sha': 'old', 'conclusion': 'cancelled',
                 'prs': [1, 2], 'url': 'https://example/group/42',
                 'membership_complete': True}
        self.s['github']['merge_group_runs'] = [group]
        self.cycle(0)
        # Later-discovered membership must not produce another run relay.
        group['prs'].append(3)
        self.cycle(1)
        self.cycle(2)
        self.assertEqual(len(self.calls), 1)
        event, recipient = self.calls[0]
        self.assertEqual(recipient, 'run:r')
        self.assertEqual(event.get('type', 'status'), 'status')
        self.assertEqual(event['subject'], 'Merge group 42 superseded, cancelled')
        self.assertIn('superseded, cancelled', event['body'])

    def test_current_cancelled_real_failure_and_unknown_queue_remain_urgent(self):
        cases = [
            ('cancelled', 'current', False, True),
            ('failure', 'old', False, True),
            ('timed_out', 'old', False, True),
            ('cancelled', 'old', True, True),
            ('cancelled', 'old', False, False),
            ('cancelled', None, False, True),
        ]
        for index, (conclusion, head, unknown, has_queue) in enumerate(cases):
            with self.subTest(case=cases[index]):
                self.calls.clear()
                self.s['github']['merge_queue_unknown'] = unknown
                if has_queue:
                    self.s['github']['merge_queue'] = [
                        {'headCommit': {'oid': 'current'}}]
                else:
                    self.s['github'].pop('merge_queue', None)
                self.s['github']['merge_group_runs'] = [{
                    'id': index, 'head_sha': head, 'conclusion': conclusion, 'prs': [1],
                    'url': 'https://example/group', 'membership_complete': True}]
                self.cycle(index)
                self.assertEqual([r for _, r in self.urgent_calls()], ['run:r', 'builder'])
                self.assertTrue(all(e['subject'].startswith('[urgent] Merge group')
                                    for e, _ in self.calls))

    def test_pending_cancelled_group_downgrades_after_supersession(self):
        self.s['github']['merge_queue'] = [{'headCommit': {'oid': 'old'}}]
        self.s['github']['merge_group_runs'] = [{
            'id': 42, 'head_sha': 'old', 'conclusion': 'cancelled', 'prs': [1, 2],
            'url': 'https://example/group/42', 'membership_complete': True}]
        with patch.object(supervisor, 'enqueue', side_effect=CommandError('offline')):
            self.cycle(0)
        self.s['github']['merge_queue'] = []
        self.cycle(1)
        self.cycle(2)
        self.assertEqual([(e['type'], r) for e, r in self.calls], [('status', 'run:r')])
        self.assertIn('superseded, cancelled', self.calls[0][0]['body'])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_supersession_retry_preserves_successful_coordinator_receipt(self):
        self.s['github']['merge_queue'] = [{'headCommit': {'oid': 'old'}}]
        self.s['github']['merge_group_runs'] = [{
            'id': 42, 'head_sha': 'old', 'conclusion': 'cancelled', 'prs': [1],
            'url': 'https://example/group/42', 'membership_complete': True}]

        def fail_builder(c, event, recipient):
            if recipient == 'builder':
                raise CommandError('offline')
            return self.enqueue(c, event, recipient)

        with patch.object(supervisor, 'enqueue', side_effect=fail_builder):
            self.cycle(0)
        self.s['github']['merge_queue'] = []
        self.cycle(1)
        self.assertEqual([(e['type'], r) for e, r in self.calls], [('question', 'run:r')])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_pending_real_failure_is_not_downgraded_by_later_cancellation(self):
        self.s['github']['merge_queue'] = []
        group = {'id': 42, 'head_sha': 'old', 'conclusion': 'failure', 'prs': [1],
                 'url': 'https://example/group/42', 'membership_complete': True}
        self.s['github']['merge_group_runs'] = [group]
        with patch.object(supervisor, 'enqueue', side_effect=CommandError('offline')):
            self.cycle(0)
        group['conclusion'] = 'cancelled'
        self.cycle(1)
        failures = [(e, r) for e, r in self.calls if ':failure:' in e['key']]
        self.assertEqual([(e['type'], r) for e, r in failures],
                         [('question', 'run:r'), ('question', 'builder')])
        self.assertEqual([(e['type'], r) for e, r in self.calls if ':cancelled:' in e['key']],
                         [('status', 'run:r')])


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
        from fleet import restart
        gh = patch.object(restart, 'gh', side_effect=CommandError('offline'))
        gh.start()
        self.addCleanup(gh.stop)
        restart_pad = patch.object(restart, 'json_run',
                                   side_effect=lambda argv, cwd: self.item if 'show' in argv
                                   else self.comments)
        restart_pad.start()
        self.addCleanup(restart_pad.stop)

    def save_attempt(self):
        state.write(self.c, 'threads-TASK-37',
                    {'TASK-37': {'id': 'thread', 'attempts': {'attempt': self.attempt}}})

    def pad(self, argv, cwd):
        self.pad_calls.append(argv)
        return self.item if 'show' in argv else self.comments

    def test_reopen_invalidates_completed_cache_and_settlement_restores_it(self):
        self.assertTrue(supervisor._completed_idle(self.c, self.builder))
        self.attempt.update(attempt_id='attempt/continuation', settled=False)
        self.save_attempt()
        self.assertFalse(supervisor._completed_idle(self.c, self.builder))
        self.attempt.update(settled=True, result='Continuation published')
        self.save_attempt()
        self.assertTrue(supervisor._completed_idle(self.c, self.builder))

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
        self.assertEqual(len([argv for argv in self.pad_calls if 'show' in argv]), 1)

    def test_negative_completion_is_retried(self):
        self.item['fields']['status'] = 'in-progress'
        self.attempt['result'] = 'Published without next ref'
        self.save_attempt()
        self.cycle(0)
        self.item['fields']['status'] = 'done'
        self.cycle(1)
        self.cycle(2)
        self.assertEqual(len(self.urgent_calls()), 1)
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


class MissingReadyReportTests(unittest.TestCase):
    enqueue = UrgentRelayTests.enqueue
    cycle = UrgentRelayTests.cycle
    urgent_calls = UrgentRelayTests.urgent_calls
    save_attempt = CompletedIdleTests.save_attempt
    pad = CompletedIdleTests.pad

    def setUp(self):
        CompletedIdleTests.setUp(self)
        from fleet import restart
        def completion(c, ref, finished, launch, **kwargs):
            if ref == finished:
                raise ValueError('no same-ref handoff')
            return {'handoff': 'handoff'}
        proof = patch.object(restart, '_completion', side_effect=completion)
        self.proof = proof.start()
        self.addCleanup(proof.stop)
        # Keep these tests focused on missing-ready delivery, not PR-stuck timers.
        self.s.pop('github')

    def fallback_calls(self):
        return [(event, recipient) for event, recipient in self.calls
                if event['key'].startswith('missing_ready:')]

    def test_default_timer_boundary_and_nonurgent_status(self):
        self.cycle(100)
        self.cycle(999)
        self.assertEqual(self.fallback_calls(), [])
        self.cycle(1000)
        calls = self.fallback_calls()
        self.assertEqual(len(calls), 1)
        event, recipient = calls[0]
        self.assertIn('settled, no ready report', event['subject'])
        self.assertEqual(event['type'], 'status')
        self.assertEqual(recipient, 'run:r')
        self.assertEqual(self.urgent_calls(), [])

    def test_settled_timestamp_and_configured_boundary(self):
        self.c['builder'] = {'ready_report_minutes': 0.5}
        self.attempt['settled_at'] = 100
        self.save_attempt()
        self.cycle(129)
        self.assertEqual(self.fallback_calls(), [])
        self.cycle(130)
        self.assertEqual(len(self.fallback_calls()), 1)

    def test_invalid_legacy_timestamps_start_at_verified_observation(self):
        for value in (None, True, -1, float('nan'), float('inf'), 2000):
            with self.subTest(value=value):
                self.attempt['settled_at'] = value
                self.save_attempt()
                ledger = {}
                self.assertEqual(supervisor.missing_ready_reports(self.c, self.s, ledger, 100), [])
                self.assertEqual(next(iter(ledger['missing_ready_reports'].values()))['since'], 100)

    def test_once_survives_restart_disappearance_and_receipt_expiry(self):
        self.cycle(0)
        self.cycle(900)
        self.c.pop('_completed_idle_launches', None)
        self.c.pop('_verified_ready_handoffs', None)
        self.s['inventory']['builders'] = []
        self.cycle(901)
        self.s['inventory']['builders'] = [self.builder]
        self.cycle(8 * 86400)
        self.cycle(9 * 86400)
        self.assertEqual(len(self.fallback_calls()), 1)

    def test_ready_receipt_is_reread_despite_positive_cache(self):
        self.cycle(0)
        self.attempt['ready'] = {'message_id': 'native-message', 'recorded': False}
        self.save_attempt()
        self.cycle(900)
        self.assertEqual(self.fallback_calls(), [])

    def test_ready_without_message_id_does_not_prove_delivery(self):
        self.attempt['ready'] = {'recorded': True, 'next_ref': 'TASK-38'}
        self.save_attempt()
        self.cycle(0)
        self.cycle(900)
        self.assertEqual(len(self.fallback_calls()), 1)

    def test_no_deliver_persists_single_pending_identity_until_retry(self):
        self.cycle(0)
        with patch.object(supervisor.time, 'time', return_value=900):
            _, report = supervisor.cycle(self.c, deliver=False)
        self.assertEqual(report['pending'], 1)
        self.assertEqual(self.fallback_calls(), [])
        key = state.read(self.c, 'supervisor')['pending'][0]['key']
        self.c.pop('_verified_ready_handoffs', None)
        self.cycle(901)
        self.cycle(902)
        self.assertEqual([event['key'] for event, _ in self.fallback_calls()], [key])

    def test_failed_enqueue_retries_same_event(self):
        self.cycle(0)
        with patch.object(supervisor, 'enqueue', side_effect=CommandError('offline')):
            _, report = self.cycle(900)
        self.assertEqual(report['pending'], 1)
        key = state.read(self.c, 'supervisor')['pending'][0]['key']
        self.cycle(901)
        self.assertEqual([event['key'] for event, _ in self.fallback_calls()], [key])

    def test_pending_is_discarded_if_ready_arrives_or_launch_replaced(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                                                  'active_alerts': []})
                state.write(self.c, 'launches', {'/slot': self.launch})
                self.attempt.pop('ready', None)
                self.save_attempt()
                self.cycle(0)
                with patch.object(supervisor.time, 'time', return_value=900):
                    supervisor.cycle(self.c, deliver=False)
                if replacement:
                    state.write(self.c, 'launches', {'/slot': {**self.launch, 'id': 'replacement'}})
                else:
                    self.attempt['ready'] = {'message_id': 'native-message'}
                    self.save_attempt()
                self.cycle(901)
                self.assertEqual(self.fallback_calls(), [])
                self.assertFalse(any(event.get('missing_ready') for event in
                                     state.read(self.c, 'supervisor')['pending']))

    def test_done_without_handoff_never_starts_timer(self):
        self.proof.return_value = {'handoff': None}
        self.proof.side_effect = None
        self.cycle(0)
        self.cycle(900)
        self.assertEqual(self.fallback_calls(), [])
        self.assertEqual(state.read(self.c, 'supervisor')['missing_ready_reports'], {})
        self.assertEqual(self.urgent_calls(), [])

    def test_same_ref_handoff_can_trigger_missing_ready_fallback(self):
        self.attempt['result'] = 'Published. Next TASK-37 handoff: implement the next piece.'
        self.save_attempt()
        self.proof.side_effect = None
        self.proof.return_value = {'handoff': 'same-ref-handoff'}
        self.cycle(0)
        self.cycle(900)
        calls = self.fallback_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn('handoff to TASK-37', calls[0][0]['body'])

    def test_unknown_handoff_keeps_idle_alert_and_no_fallback(self):
        self.item['fields']['status'] = 'in-progress'
        self.proof.side_effect = CommandError('offline')
        self.cycle(0)
        self.cycle(900)
        self.assertEqual(self.fallback_calls(), [])
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])

    def test_unsettled_attempt_does_not_start_fallback_timer(self):
        self.attempt['settled'] = False
        self.save_attempt()
        self.cycle(0)
        self.cycle(900)
        self.assertEqual(self.fallback_calls(), [])
        self.assertEqual([e['subject'] for e, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])

    def test_replacement_launch_earns_its_own_fallback(self):
        self.cycle(0)
        self.cycle(900)
        replacement = {**self.launch, 'id': 'replacement'}
        state.write(self.c, 'launches', {'/slot': replacement})
        state.write(self.c, 'threads-TASK-37',
                    {'TASK-37': {'id': 'thread', 'attempts': {'replacement': self.attempt}}})
        self.cycle(901)
        self.cycle(1801)
        events = [event for event, _ in self.fallback_calls()]
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]['key'], events[1]['key'])
    def test_reopened_attempt_restarts_ready_timer_and_invalidates_pending_report(self):
        self.cycle(0)
        with patch.object(supervisor.time, 'time', return_value=900):
            supervisor.cycle(self.c, deliver=False)
        self.attempt.update(attempt_id='attempt/continuation', settled=False)
        self.save_attempt()
        self.cycle(901)
        self.assertEqual(self.fallback_calls(), [])
        self.attempt.update(settled=True, settled_at=1000, result='Continued. Next TASK-38.')
        self.save_attempt()
        self.cycle(1000)
        self.cycle(1899)
        self.assertEqual(self.fallback_calls(), [])
        self.cycle(1900)
        self.assertEqual(len(self.fallback_calls()), 1)


    def test_pending_unknown_attempt_is_retained_without_delivery(self):
        self.cycle(0)
        with patch.object(supervisor.time, 'time', return_value=900):
            supervisor.cycle(self.c, deliver=False)
        (Path(self.c['_state']) / 'threads-TASK-37.json').write_text('{')
        self.cycle(901)
        self.assertEqual(self.fallback_calls(), [])
        self.assertTrue(any(event.get('missing_ready') for event in
                            state.read(self.c, 'supervisor')['pending']))
        self.save_attempt()
        self.cycle(902)
        self.assertEqual(len(self.fallback_calls()), 1)


class LaneFreeReportTests(unittest.TestCase):
    enqueue = UrgentRelayTests.enqueue
    cycle = UrgentRelayTests.cycle
    urgent_calls = UrgentRelayTests.urgent_calls
    save_attempt = CompletedIdleTests.save_attempt
    pad = CompletedIdleTests.pad

    def setUp(self):
        CompletedIdleTests.setUp(self)
        self.s.pop('github')
        self.builder['ui_state'] = 'idle'
        from fleet import restart
        self.finished_completion = restart._finished_completion
        proof = patch.object(restart, '_finished_completion', return_value={'pr': 'merged'})
        self.proof = proof.start()
        self.addCleanup(proof.stop)

    def lane_calls(self):
        return [(event, recipient) for event, recipient in self.calls if event.get('lane_free')]

    def queue(self):
        with patch.object(supervisor.time, 'time', return_value=100):
            supervisor.cycle(self.c, deliver=False)

    def test_once_per_launch_independent_of_sent_expiry_and_no_generic_idle(self):
        self.cycle(100)
        self.cycle(8 * 86400)
        self.cycle(9 * 86400)
        self.assertEqual(len(self.calls), 1)
        event, recipient = self.lane_calls()[0]
        self.assertEqual(event['type'], 'question')
        self.assertEqual(event['subject'], 'Lane free: /slot finished TASK-37; needs next task')
        self.assertEqual(recipient, 'run:r')
        from fleet.mailbox import classify
        rows, counts = classify([{'type': 'question', 'subject': event['subject'],
                                 'body': '[fleet owner/project] ' + event['body'],
                                 'from_handle': 'main'}], 'main')
        self.assertEqual(rows[0]['classification'], 'builder_decision_request')
        self.assertEqual(counts['builder_decision_request'], 1)

    def test_supported_shell_also_qualifies(self):
        self.builder.update(ui_state='unknown', shell_ready=True, flags=[])
        self.cycle(100)
        self.assertEqual(len(self.lane_calls()), 1)

    def test_unsettled_blocked_stalled_or_unknown_idle_never_qualify(self):
        for change in ({'ui_state': 'unknown'}, {'connected': False},
                       {'flags': ['idle', 'stalled']}, {'flags': ['idle', 'provider_stalled']},
                       {'flags': ['idle', 'blocked']}):
            with self.subTest(change=change):
                builder = {**self.builder, **change}
                self.assertIsNone(supervisor._lane_free_candidate(self.c, builder))
        self.attempt['settled'] = False
        self.save_attempt()
        self.cycle(100)
        self.assertEqual(self.lane_calls(), [])

    def test_unknown_completion_retains_generic_idle_fallback(self):
        self.item['fields']['status'] = 'in-progress'
        self.proof.side_effect = CommandError('offline')
        self.cycle(100)
        self.assertEqual(self.lane_calls(), [])
        self.assertEqual([event['subject'] for event, _ in self.urgent_calls()],
                         ['[urgent] Builder idle: TASK-37'])

    def test_ready_and_needs_next_receipts_suppress_before_and_after_queue(self):
        for queued in (False, True):
            for report in ('ready', 'needs_next', 'global'):
                with self.subTest(queued=queued, report=report):
                    state.write(self.c, 'supervisor', {'github': {}, 'pending': [],
                                                     'sent': {}, 'active_alerts': []})
                    state.write(self.c, 'needs-next', {})
                    self.attempt.pop('ready', None)
                    self.attempt.pop('needs_next', None)
                    self.save_attempt()
                    if queued:
                        self.queue()
                    if report == 'global':
                        state.write(self.c, 'needs-next', {'attempt': {'sending': True}})
                    else:
                        self.attempt[report] = {'message_id': 'native'}
                        self.save_attempt()
                    self.cycle(101)
                    self.assertEqual(self.lane_calls(), [])
                    self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_replacement_invalidates_pending_and_earns_own_report(self):
        self.queue()
        replacement = {**self.launch, 'id': 'replacement'}
        state.write(self.c, 'launches', {'/slot': replacement})
        self.cycle(101)
        self.assertEqual(self.lane_calls(), [])
        state.write(self.c, 'threads-TASK-37',
                    {'TASK-37': {'id': 'thread', 'attempts': {'replacement': self.attempt}}})
        self.cycle(102)
        self.assertEqual(len(self.lane_calls()), 1)
        self.assertEqual(self.lane_calls()[0][0]['lane_free']['id'], 'replacement')

    def test_pending_does_not_deliver_while_stalled_and_resumes_same_event(self):
        self.queue()
        key = state.read(self.c, 'supervisor')['pending'][0]['key']
        self.builder['flags'] = ['idle', 'stalled']
        self.cycle(101)
        self.assertEqual(self.lane_calls(), [])
        self.builder['flags'] = ['idle']
        self.cycle(102)
        self.assertEqual([event['key'] for event, _ in self.lane_calls()], [key])

    def test_deferred_lane_free_survives_reopened_attempt_until_settlement(self):
        self.queue()
        key = state.read(self.c, 'supervisor')['pending'][0]['key']
        self.attempt.update(attempt_id='attempt/continuation', settled=False)
        self.save_attempt()
        self.cycle(101)
        self.assertEqual(self.lane_calls(), [])
        self.attempt.update(settled=True, result='continuation complete')
        self.save_attempt()
        self.cycle(102)
        self.cycle(103)
        self.assertEqual([e['key'] for e, _ in self.lane_calls()], [key])
        self.assertEqual(self.lane_calls()[0][0]['lane_free']['attempt_id'], 'attempt/continuation')

    def test_lane_free_replaces_old_missing_ready_fallback(self):
        self.queue()
        ledger = state.read(self.c, 'supervisor')
        identity = ledger['pending'][0]['lane_free']
        ledger['pending'].insert(0, {'key': 'missing_ready:old', 'type': 'status',
                                     'subject': 'old fallback', 'body': 'old',
                                     'missing_ready': identity, 'branch': None})
        state.write(self.c, 'supervisor', ledger)
        self.cycle(1000)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.lane_calls()), 1)

    def test_explicit_successful_park_receipt_counts_without_merge(self):
        self.c['builder'] = {'slots': ['/slot']}
        self.launch['worktree'] = {'path': '/slot'}
        state.write(self.c, 'launches', {'/slot': self.launch})
        at = self.launch['at'] + 1
        self.attempt.update(settled_at=at, result='PARKED pushed WIP', branch='task-37-wip')
        self.save_attempt()
        receipt = {'launch_id': 'attempt', 'ref': 'TASK-37', 'slot': '/slot',
                   'terminal': 'builder', 'terminal_incarnation_id': 'incarnation',
                   'head': 'a' * 40, 'branch': 'task-37-wip', 'resume_note': 'note',
                   'at': at, 'message_id': 'park-message', 'recorded': True,
                   'attempt': 'attempt', 'body': self.attempt['result']}
        state.write(self.c, 'parks', {'attempt': receipt})
        self.proof.side_effect = self.finished_completion
        self.cycle(100)
        self.assertEqual(len(self.lane_calls()), 1)
        self.assertEqual(self.lane_calls()[0][0]['type'], 'question')


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
            for github in (None, [], {'parked_labels': 'parked'},
                           {'parked_labels': [None]}, {'parked_labels': [' ']}):
                c['github'] = github
                path.write_text(yaml.safe_dump(c))
                with self.subTest(github=github), self.assertRaisesRegex(ValueError, 'github'):
                    config.load(path)
            for labels in ([], ['parked'], ['on hold', 'paused']):
                c['github'] = {'parked_labels': labels}
                path.write_text(yaml.safe_dump(c))
                self.assertEqual(config.load(path)['github']['parked_labels'], labels)
            c.pop('github')
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
            c.pop('urgent')
            for key in ('ready_report_minutes', 'stall_minutes', 'stall_after_ci_minutes'):
                for minutes in (0.5, 15):
                    c['builder'][key] = minutes
                    path.write_text(yaml.safe_dump(c))
                    self.assertEqual(config.load(path)['builder'][key], minutes)
                for minutes in (0, -1, True, '15', None, float('inf'), float('nan')):
                    with self.subTest(key=key, minutes=minutes):
                        c['builder'][key] = minutes
                        path.write_text(yaml.safe_dump(c))
                        with self.assertRaisesRegex(ValueError, 'builder.' + key):
                            config.load(path)
                c['builder'].pop(key)


if __name__ == '__main__':
    unittest.main()

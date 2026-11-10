import copy
import unittest
from unittest.mock import patch

from fleet import github, supervisor
from fleet.commands import CommandError
from test_urgent_relays import snapshot


class QueueProgressTests(unittest.TestCase):
    def setUp(self):
        self.current = snapshot()['github']
        self.entry = {'state': 'QUEUED', 'headCommit': {'oid': 'group'},
                      'pullRequest': {'number': 1}, 'runs': []}
        self.current['merge_queue'] = [self.entry]
        self.ledger = {}
        self.c = {'repo': 'owner/project'}

    def alerts(self, now):
        return supervisor.stuck_pr_alerts(self.c, self.ledger, self.current, now)

    def test_running_group_progress_and_idle_boundary_then_dequeue(self):
        self.entry['runs'] = [{'id': 10, 'status': 'queued', 'conclusion': None, 'jobs': []}]
        self.assertEqual(self.alerts(0), [])
        run = self.entry['runs'][0]
        run.update(status='in_progress', jobs=[{'id': 20, 'status': 'in_progress'}])
        self.assertEqual(self.alerts(2600), [])
        self.assertEqual(self.alerts(2700), [])
        run['jobs'][0]['status'] = 'completed'
        self.assertEqual(self.alerts(5200), [])
        self.assertEqual(self.alerts(7899), [])
        self.assertEqual([e['pr'] for e in self.alerts(7900)], [1])
        self.current['merge_queue'] = []
        self.assertEqual(self.alerts(7901), [])
        self.assertEqual([e['pr'] for e in self.alerts(10601)], [1])

    def test_missing_group_run_sticks_after_full_interval(self):
        self.assertEqual(self.alerts(0), [])
        self.assertEqual([e['pr'] for e in self.alerts(2700)], [1])

    def test_unknown_does_not_change_timer_or_trigger_alert(self):
        for scope in ('entry', 'queue'):
            with self.subTest(scope=scope):
                self.entry.pop('progress_unknown', None)
                self.current.pop('merge_queue_unknown', None)
                self.ledger = {}
                self.alerts(0)
                prior = copy.deepcopy(self.ledger)
                if scope == 'entry':
                    self.entry['progress_unknown'] = True
                else:
                    self.current['merge_queue_unknown'] = True
                self.assertEqual(self.alerts(3000), [])
                self.assertEqual(self.ledger, prior)
                self.entry.pop('progress_unknown', None)
                self.current.pop('merge_queue_unknown', None)
                self.assertEqual([e['pr'] for e in self.alerts(3001)], [1])

    def test_enrichment_matches_exact_head_and_reuses_shared_group(self):
        entries = [copy.deepcopy(self.entry), {**copy.deepcopy(self.entry), 'pullRequest': {'number': 2}}]
        calls = []

        def api(c, *args):
            calls.append(args[-1])
            if '/jobs?' in args[-1]:
                return [{'jobs': [{'id': 20, 'status': 'in_progress', 'conclusion': None,
                                   'steps': [{'number': 1, 'status': 'completed'}]}]}]
            return [{'workflow_runs': [
                {'id': 10, 'head_sha': 'group', 'status': 'in_progress', 'conclusion': None},
                {'id': 11, 'head_sha': 'obsolete-group', 'status': 'completed', 'conclusion': 'failure'}]}]

        with patch.object(github, 'gh', side_effect=api):
            github._queue_progress(self.c, entries, [])
        self.assertEqual(len(calls), 2)
        self.assertEqual([run['id'] for run in entries[0]['runs']], [10])
        self.assertEqual(entries[0]['runs'], entries[1]['runs'])
        self.assertEqual(entries[0]['runs'][0]['jobs'][0]['steps'][0]['status'], 'completed')
        with patch.object(github, 'gh') as api:
            github._queue_progress(self.c, [], [])
        api.assert_not_called()

    def test_jobs_or_run_failure_is_unknown_not_empty(self):
        for jobs_fail in (False, True):
            with self.subTest(jobs_fail=jobs_fail):
                entry = copy.deepcopy(self.entry)
                entry.pop('runs')

                def api(c, *args):
                    if jobs_fail and '/jobs?' not in args[-1]:
                        return [{'workflow_runs': [{'id': 10, 'head_sha': 'group',
                                                   'status': 'in_progress', 'conclusion': None}]}]
                    raise CommandError('unavailable')

                unavailable = []
                with patch.object(github, 'gh', side_effect=api):
                    github._queue_progress(self.c, [entry], unavailable)
                self.assertTrue(entry['progress_unknown'])
                self.assertNotIn('runs', entry)
                self.assertTrue(unavailable)

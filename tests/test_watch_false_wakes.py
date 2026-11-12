"""PR38/PR55/TASK-64 watcher false-wake regression scenarios."""
import os
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import patch

from fleet import github, watcher
from test_initial_events import history, pull


class WatchFalseWakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = {'repo': 'owner/repo', '_state': self.temp.name}
        self.env = patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'lane-a'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def watch(self, current, **kwargs):
        with patch.object(github, 'collect', return_value=current):
            return watcher.watch(self.c, 7, timeout=0, **kwargs)

    def test_rearmed_initial_failure_waits_and_override_reemits(self):
        current = history(pull(checks=[{'id': 1, 'name': 'CI', 'conclusion': 'failure'}]))
        first = self.watch(current)
        self.assertIn('failing checks: CI', first['events'][0]['subject'])
        self.assertEqual(self.watch(current)['event'], 'timeout')
        self.assertEqual(self.watch(current, include_seen=True), first)
        self.assertEqual(self.watch(current)['event'], 'timeout')

    def test_receipts_are_scoped_to_caller_repo_and_pr(self):
        current = history(pull(merge_state='DIRTY'))
        self.watch(current)
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'lane-b'}):
            self.assertIn('events', self.watch(current))
        self.c['repo'] = 'other/repo'
        self.assertIn('events', self.watch(current))
        self.c['repo'] = 'owner/repo'
        with patch.object(github, 'collect', return_value=history(pull(number=8, merge_state='DIRTY'))):
            self.assertIn('events', watcher.watch(self.c, 8, timeout=0))
        self.assertEqual(self.watch(current)['event'], 'timeout')

    def test_fallback_identity_and_anonymous_compatibility(self):
        current = history(pull(state='closed'))
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''}):
            self.assertIn('events', self.watch(current, terminal='fallback'))
            self.assertEqual(self.watch(current, terminal='fallback')['event'], 'timeout')
            self.assertIn('events', self.watch(current))
            self.assertIn('events', self.watch(current))
        # Explicit fallback never overrides a native identity.
        self.assertIn('events', self.watch(current, terminal='fallback'))
        self.assertEqual(self.watch(current, terminal='different')['event'], 'timeout')

    def test_new_head_timestamp_or_failure_reemits(self):
        failed = pull(checks=[{'id': 1, 'name': 'CI', 'conclusion': 'failure'}])
        for changed in [pull(**(failed | {'head': 'new'})),
                        pull(**(failed | {'updated_at': 'later'})),
                        pull(**(failed | {'checks': [{'id': 2, 'name': 'CI', 'conclusion': 'failure'}]}))]:
            with self.subTest(changed=changed):
                self.assertIn('events', self.watch(history(failed), include_seen=True))
                self.assertIn('events', self.watch(history(changed)))
                self.assertEqual(self.watch(history(changed))['event'], 'timeout')

    def test_seen_failure_still_wakes_for_live_changes_and_records_diagnosis(self):
        before = history(pull(checks=[{'id': 1, 'name': 'CI', 'conclusion': 'failure'}]))
        after = history(pull(checks=[{'id': 2, 'name': 'CI', 'conclusion': 'failure'}]))
        self.watch(before)
        with patch.object(github, 'collect', side_effect=[before, after]), patch.object(watcher.time, 'sleep'):
            result = watcher.watch(self.c, 7, timeout=30)
        self.assertIn('failing checks: CI', result['events'][0]['subject'])
        self.assertEqual(self.watch(after)['event'], 'timeout')

    def test_other_pr_and_superseded_same_pr_groups_do_not_wake(self):
        old = {'id': 5, 'head_sha': 'old', 'conclusion': 'failure', 'url': 'old',
               'prs': [7], 'membership_complete': True}
        new = old | {'id': 6, 'head_sha': 'new', 'conclusion': None, 'url': 'new'}
        other = old | {'id': 8, 'prs': [8]}
        self.assertEqual(self.watch(history(pull(), runs=[old, new, other]))['event'], 'timeout')
        new_failed = new | {'conclusion': 'failure'}
        result = self.watch(history(pull(), runs=[old, new_failed, other]))
        self.assertEqual([e['subject'] for e in result['events']], ['Merge group 6 failure'])
        self.assertEqual(self.watch(history(pull(), runs=[old, new_failed, other]))['event'], 'timeout')

    def test_cancelled_workflow_replacement_waits_then_reports_new_failure_once(self):
        from test_workflow_checks import WorkflowChecksTests, check, workflow

        fixture = WorkflowChecksTests()
        old = check(11, 1)
        pending = fixture.collect(
            [old, check(12, 2, status='in_progress', conclusion=None)],
            [workflow(1), workflow(2, status='in_progress', conclusion=None)])
        self.assertEqual(self.watch(history(pending))['event'], 'timeout')
        failed = fixture.collect(
            [old, check(12, 2, conclusion='failure')],
            [workflow(1), workflow(2, conclusion='failure')])
        result = self.watch(history(failed))
        self.assertIn('failing checks: CI', result['events'][0]['subject'])
        self.assertEqual(self.watch(history(failed))['event'], 'timeout')


class SelectiveMergeGroupReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = {'repo': 'owner/repo', '_state': self.temp.name}
        github._pr_group_cache.clear()
        github._workflow_check_cache.clear()
        self.pr = {'number': 7, 'title': 'Feature', 'html_url': 'https://example/pr/7',
                   'head': {'sha': 'pr-head', 'ref': 'feature'}, 'state': 'open',
                   'mergeable_state': 'clean', 'created_at': '2026-11-06T00:00:00Z',
                   'updated_at': '2026-11-07T12:00:00Z', 'closed_at': None}
        self.checks = []
        self.runs = []
        self.queued = False
        self.calls = []
        self.now = datetime(2026, 11, 7, 12, 30, tzinfo=timezone.utc)
        clock = patch.object(github, 'datetime')
        self.clock = clock.start()
        self.clock.now.side_effect = lambda zone: self.now
        self.clock.fromisoformat.side_effect = datetime.fromisoformat
        self.addCleanup(clock.stop)
        api = patch.object(github, 'gh', side_effect=self.response)
        api.start()
        self.addCleanup(api.stop)
        group_api = patch.object(github.groups, 'gh', side_effect=self.response)
        group_api.start()
        self.addCleanup(group_api.stop)

    def response(self, c, *args):
        self.calls.append(args)
        endpoint = args[-1]
        if args[1] == 'graphql':
            entry = {'id': 'queue-entry'} if self.queued else None
            return {'data': {'repository': {'pullRequest': {'mergeQueueEntry': entry}}}}
        if endpoint == 'repos/owner/repo/pulls/7':
            return self.pr
        if '/check-runs?' in endpoint:
            return [{'check_runs': self.checks}]
        if '/statuses?' in endpoint:
            return [[]]
        if '/actions/runs?event=merge_group' in endpoint:
            return [{'workflow_runs': self.runs}]
        if '/compare/' in endpoint:
            sha = endpoint.split('...')[1].split('?')[0]
            return [{'commits': [{'sha': sha, 'parents': [
                {'sha': 'a' * 40}, {'sha': self.pr['head']['sha']}],
                'commit': {'message': 'Merge pull request #7'}}]}]
        if endpoint.endswith('/comments?per_page=100') or endpoint.endswith('/reviews?per_page=100'):
            return [[]]
        raise AssertionError(args)

    def group_reads(self):
        return [args[-1] for args in self.calls if '/actions/runs?event=merge_group' in args[-1]]

    def group_run(self, number, conclusion='failure'):
        return {'id': number, 'head_branch': 'gh-readonly-queue/main/pr-7-' + 'a' * 40,
                'head_sha': f'group-{number}', 'pull_requests': [],
                'created_at': f'2026-11-07T12:0{number}:00Z',
                'status': 'completed' if conclusion else 'in_progress',
                'conclusion': conclusion, 'html_url': f'https://example/runs/{number}'}

    def test_unchanged_nonqueued_pr_skips_merge_group_history_after_cold_read(self):
        first = github.collect(self.c, 7)
        self.assertEqual(len(self.group_reads()), 1)
        self.assertEqual(github.collect(self.c, 7), first)
        self.assertEqual(github.collect(self.c, 7), first)
        self.assertEqual(len(self.group_reads()), 1)
        # Queue membership is observed each time, including the cold snapshot;
        # merge-group history still stays cached for unchanged, nonqueued PRs.
        self.assertEqual(sum(args[1] == 'graphql' for args in self.calls), 3)

    def test_changes_and_queue_entry_refresh_history(self):
        github.collect(self.c, 7)
        self.checks = [{'id': 1, 'name': 'CI', 'status': 'in_progress', 'conclusion': None}]
        github.collect(self.c, 7)
        self.assertEqual(len(self.group_reads()), 2)
        self.pr['head']['sha'] = 'new-head'
        github.collect(self.c, 7)
        self.assertEqual(len(self.group_reads()), 3)
        self.pr['updated_at'] = '2026-11-07T12:01:00Z'
        github.collect(self.c, 7)
        self.assertEqual(len(self.group_reads()), 4)
        self.queued = True
        github.collect(self.c, 7)
        github.collect(self.c, 7)
        self.assertEqual(len(self.group_reads()), 6)

    def test_cold_same_head_failure_and_warm_supersession_remain_visible(self):
        self.runs = [self.group_run(1)]
        first = github.collect(self.c, 7)
        self.assertEqual([e['subject'] for e in watcher._initial_events(first, 7)],
                         ['Merge group 1 failure'])
        self.runs.append(self.group_run(2, None))
        pending = github.collect(self.c, 7)
        self.assertEqual(watcher._initial_events(pending, 7), [])
        self.runs[-1] = self.group_run(2)
        failed = github.collect(self.c, 7)
        self.assertEqual([e['subject'] for e in watcher._initial_events(failed, 7)],
                         ['Merge group 2 failure'])
        self.assertEqual(len(self.group_reads()), 3)

    def test_nonqueued_pr_discovers_failure_of_initially_pending_unresolved_group(self):
        self.runs = [self.group_run(1, None)]
        first = github.collect(self.c, 7)
        self.assertEqual(watcher._initial_events(first, 7), [])
        self.assertEqual(first['merge_group_runs'][0]['prs'], [])
        self.runs[0] = self.group_run(1)
        failed = github.collect(self.c, 7)
        self.assertEqual([e['subject'] for e in watcher._initial_events(failed, 7)],
                         ['Merge group 1 failure'])

    def test_bucket_url_is_stable_but_exact_cutoff_is_not(self):
        self.runs = [self.group_run(1) | {'created_at': '2026-11-06T12:40:00Z'}]
        self.assertEqual(len(github.collect(self.c, 7)['merge_group_runs']), 1)
        self.now = self.now.replace(minute=45)
        self.assertEqual(github.collect(self.c, 7)['merge_group_runs'], [])
        self.assertEqual(self.group_reads()[0], self.group_reads()[1])

    def test_queue_lookup_errors_are_not_treated_as_absent_membership(self):
        github.collect(self.c, 7)
        def unavailable(c, *args):
            if args[1] == 'graphql':
                return {'errors': [{'message': 'unavailable'}]}
            return self.response(c, *args)

        with patch.object(github, 'gh', side_effect=unavailable):
            current = github.collect(self.c, 7)
        self.assertTrue(current['prs'][0]['merge_queue_unknown'])
        self.assertTrue(current['unavailable'])
        self.assertEqual(len(self.group_reads()), 2)

    def test_unresolved_failure_is_retried_without_pr_changes(self):
        self.runs = [self.group_run(1) | {
            'head_branch': 'gh-readonly-queue/main/pr-8-' + 'a' * 40}]
        with patch.object(github.groups, 'gh', side_effect=github.CommandError('unavailable')):
            first = github.collect(self.c, 7)
        self.assertEqual(watcher._initial_events(first, 7), [])
        second = github.collect(self.c, 7)
        self.assertEqual([e['subject'] for e in watcher._initial_events(second, 7)],
                         ['Merge group 1 failure'])
        self.assertEqual(len(self.group_reads()), 2)

    def test_global_commit_query_bucket_preserves_exact_window(self):
        commit_urls = []

        def global_response(c, *args):
            endpoint = args[-1]
            if args[1] == 'graphql':
                return {'data': {'repository': {'mergeQueue': None}}}
            if endpoint == 'repos/owner/repo':
                return {'default_branch': 'main'}
            if '/pulls?state=all' in endpoint:
                return []
            if '/pulls?state=open' in endpoint:
                return [[]]
            if '/actions/runs?event=merge_group' in endpoint:
                return [{'workflow_runs': []}]
            if '/commits?' in endpoint:
                commit_urls.append(endpoint)
                return [[{'sha': str(minute), 'html_url': 'https://example/commit',
                          'commit': {'committer': {'date': f'2026-11-06T12:{minute}:00Z'}}}
                         for minute in (20, 40)]]
            raise AssertionError(args)

        with patch.object(github, 'gh', side_effect=global_response):
            self.assertEqual([x['sha'] for x in github.collect(self.c)['commits']], ['40'])
            self.now = self.now.replace(minute=45)
            self.assertEqual(github.collect(self.c)['commits'], [])
        self.assertEqual(commit_urls[0], commit_urls[1])

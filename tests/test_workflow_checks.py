import unittest
from unittest.mock import patch

from fleet import github
from fleet.commands import CommandError


def workflow(run_id, **changes):
    return {'id': run_id, 'workflow_id': 10, 'head_sha': 'sha',
            'run_number': run_id, 'check_suite_id': run_id + 100,
            'status': 'completed', 'conclusion': 'cancelled', **changes}


def check(check_id, run_id=None, **changes):
    result = {'id': check_id, 'name': 'CI', 'status': 'completed',
              'conclusion': 'cancelled', 'completed_at': '2026-11-06T01:00:00Z'}
    if run_id is not None:
        result['check_suite'] = {'id': run_id + 100}
    return {**result, **changes}


class WorkflowChecksTests(unittest.TestCase):
    def setUp(self):
        github._workflow_check_cache.clear()
        self.addCleanup(github._workflow_check_cache.clear)

    def collect(self, checks, runs, statuses=()):
        # Each fixture represents independent remote history, not a cached cycle.
        github._workflow_check_cache.clear()
        github._pr_group_cache.clear()
        pr = {'number': 7, 'title': 'Feature', 'html_url': 'https://example/pr/7',
              'head': {'sha': 'sha', 'ref': 'feature'}, 'state': 'open',
              'mergeable_state': 'clean', 'created_at': '2026-11-06T00:00:00Z',
              'updated_at': '2026-11-06T00:00:00Z', 'closed_at': None}

        def response(c, *args):
            endpoint = args[-1]
            if args[1] == 'graphql':
                return {'data': {'repository': {'pullRequest': {'mergeQueueEntry': None}}}}
            if endpoint == 'repos/owner/repo/pulls/7':
                return pr
            if '/check-runs?' in endpoint:
                return [{'check_runs': checks}]
            if '/actions/runs?head_sha=' in endpoint:
                self.assertIn('head_sha=sha&per_page=100&page=1', endpoint)
                return {'workflow_runs': runs}
            if '/statuses?' in endpoint:
                return [list(statuses)]
            if '/actions/runs?event=merge_group' in endpoint:
                return [{'workflow_runs': []}]
            if endpoint.endswith('/comments?per_page=100') or endpoint.endswith('/reviews?per_page=100'):
                return [[]]
            raise AssertionError(endpoint)

        with patch.object(github, 'gh', side_effect=response), \
                patch.object(github.groups, 'resolve', return_value=[]) as groups:
            result = github.collect({'repo': 'owner/repo'}, 7)
        self.assertEqual(groups.call_args.kwargs, {'pr_number': 7})
        return result['prs'][0]

    def test_cancelled_replacement_pending_success_and_failure(self):
        for status, conclusion in [('in_progress', None), ('completed', 'success'),
                                   ('completed', 'failure')]:
            with self.subTest(status=status, conclusion=conclusion):
                current = self.collect(
                    [check(11, 1), check(12, 2, status=status, conclusion=conclusion)],
                    [workflow(1), workflow(2, status=status, conclusion=conclusion)])
                self.assertEqual([c['id'] for c in current['checks']], [12])
                event = github.initial_event(current)
                self.assertEqual(event is not None, conclusion == 'failure')
                if event:
                    self.assertIn('failing checks: CI', event['subject'])

    def test_completed_non_success_superseded_but_success_and_unknown_retained(self):
        for status, conclusion, removed in [
                ('completed', 'failure', True), ('completed', 'timed_out', True),
                ('completed', 'neutral', True), ('completed', 'success', False),
                ('completed', None, False), ('in_progress', None, False)]:
            with self.subTest(status=status, conclusion=conclusion):
                current = self.collect([check(11, 1)], [
                    workflow(1, status=status, conclusion=conclusion),
                    workflow(2, status='queued', conclusion=None)])
                self.assertEqual(bool(current['checks']), not removed)

    def test_newer_run_must_match_workflow_and_head(self):
        for changes in [{'workflow_id': 20}, {'head_sha': 'other'},
                        {'workflow_id': None}, {'head_sha': None}, {'run_number': 0}]:
            with self.subTest(changes=changes):
                current = self.collect([check(11, 1)], [workflow(1), workflow(2, **changes)])
                self.assertEqual([c['id'] for c in current['checks']], [11])

    def test_unknown_linkage_and_ambiguous_suite_retain_checks(self):
        for unknown in [check(11), check(11, 999),
                        check(11, workflow_run_id=999),
                        check(11, details_url='https://github.com/owner/repo/pull/7'),
                        check(11, 1, workflow_run_id=999)]:
            with self.subTest(check=unknown):
                current = self.collect([unknown], [workflow(1), workflow(2)])
                self.assertEqual([c['id'] for c in current['checks']], [11])
        current = self.collect([check(11, 1)], [workflow(1), workflow(2, check_suite_id=101)])
        self.assertEqual([c['id'] for c in current['checks']], [11])

    def test_workflow_id_and_actions_details_url_link_without_suite(self):
        for linkage in [{'workflow_run_id': 1},
                        {'details_url': 'https://github.com/owner/repo/actions/runs/1/job/50'}]:
            with self.subTest(linkage=linkage):
                current = self.collect([check(11, **linkage)], [workflow(1), workflow(2)])
                self.assertEqual(current['checks'], [])

    def test_unsuperseded_failure_and_legacy_statuses_remain(self):
        legacy = {'id': 31, 'context': 'CI', 'state': 'failure',
                  'updated_at': '2026-11-06T02:00:00Z'}
        current = self.collect([check(11, 1)], [workflow(1)], [legacy])
        self.assertEqual([c['id'] for c in current['checks']], [11, 31])
        current = self.collect([check(11, 1)], [workflow(1), workflow(2)], [legacy])
        self.assertEqual(current['checks'], [{'id': 31, 'name': 'CI', 'status': 'failure',
                                             'conclusion': 'failure',
                                             'completed_at': legacy['updated_at']}])


    def test_same_producer_newer_checks_supersede_without_workflow_history(self):
        for old_conclusion in ('failure', 'cancelled'):
            for status, conclusion in [('queued', None), ('in_progress', None),
                                       ('completed', 'success')]:
                with self.subTest(old=old_conclusion, status=status):
                    current = self.collect(
                        [check(12, status=status, conclusion=conclusion,
                               app={'id': 1}, check_suite={'id': 101}),
                         check(11, conclusion=old_conclusion,
                               app={'id': 1}, check_suite={'id': 101})], [])
                    self.assertEqual([c['id'] for c in current['checks']], [12])
                    self.assertIsNone(github.initial_event(current))
                    self.assertEqual(github.checks_conclusion(current),
                                     'success' if conclusion == 'success' else 'pending')

    def test_independent_app_or_suite_failure_survives_collection(self):
        for app, suite in [(2, 101), (1, 102), (None, None)]:
            with self.subTest(app=app, suite=suite):
                current = self.collect([
                    check(11, conclusion='failure', app={'id': 1}, check_suite={'id': 101}),
                    check(12, conclusion='success', app={'id': app}, check_suite={'id': suite})], [])
                self.assertEqual(github.checks_conclusion(current), 'failure')
                self.assertEqual([row['id'] for row in current['checks']], [11, 12])

    def test_unsuperseded_cancellation_is_failure(self):
        current = self.collect([check(11)], [])
        self.assertEqual(github.checks_conclusion(current), 'failure')
        self.assertIn('failing checks: CI', github.initial_event(current)['subject'])

    def test_latest_commit_status_retains_failure_alongside_newer_check(self):
        statuses = [{'id': 31, 'context': 'CI', 'state': 'failure'},
                    {'id': 30, 'context': 'CI', 'state': 'success'}]
        current = self.collect([check(100, conclusion='success')], [], statuses)
        self.assertEqual([c['id'] for c in current['checks']], [31, 100])
        self.assertEqual(github.checks_conclusion(current), 'failure')
        self.assertIn('failing checks: CI', github.initial_event(current)['subject'])

    def test_workflow_history_is_bounded_and_keeps_unlinked_checks(self):
        rows = [workflow(n, workflow_id=n) for n in range(1000, 1100)]
        with patch.object(github, 'gh', return_value={'workflow_runs': rows}) as gh:
            actual = github._workflow_checks({'repo': 'owner/repo'}, 'sha', [check(11, 1)])
        self.assertEqual(actual, [check(11, 1)])
        self.assertEqual(gh.call_count, 3)
        self.assertEqual([call.args[-1].rsplit('=', 1)[-1] for call in gh.call_args_list],
                         ['1', '2', '3'])
        self.assertTrue(all(call.args[1] == 'api' and '--paginate' not in call.args
                            for call in gh.call_args_list))

    def test_unavailable_workflow_history_retains_failed_checks(self):
        with patch.object(github, 'gh', side_effect=CommandError('unavailable')) as gh:
            actual = github._workflow_checks({'repo': 'owner/repo'}, 'sha', [check(11, 1)])
        self.assertEqual(actual, [check(11, 1)])
        self.assertEqual(gh.call_count, 1)

    def test_healthy_and_pending_heads_need_no_workflow_reads(self):
        for checks in [[], [check(11, 1, conclusion='success')],
                       [check(11, 1, status='in_progress', conclusion=None)]]:
            with self.subTest(checks=checks), patch.object(github, 'gh') as gh:
                self.assertEqual(github._workflow_checks({'repo': 'owner/repo'}, 'sha', checks), checks)
                gh.assert_not_called()

    def test_unchanged_check_evidence_reuses_supersession_and_failure_invalidates(self):
        c = {'repo': 'owner/repo'}
        checks = [check(11, 1), check(12, 2, status='in_progress', conclusion=None)]
        runs = [workflow(1), workflow(2, status='in_progress', conclusion=None)]
        with patch.object(github, 'gh', return_value={'workflow_runs': runs}) as gh:
            self.assertEqual(github._workflow_checks(c, 'sha', checks), [checks[1]])
            # Output keeps fresh non-identity fields rather than cached raw checks.
            refreshed = [checks[0], checks[1] | {'name': 'renamed CI'}]
            self.assertEqual(github._workflow_checks(c, 'sha', refreshed), [refreshed[1]])
            self.assertEqual(gh.call_count, 1)
            failed = [checks[0], check(12, 2, conclusion='failure')]
            runs[1] = workflow(2, conclusion='failure')
            self.assertEqual(github._workflow_checks(c, 'sha', failed), [failed[1]])
            self.assertEqual(gh.call_count, 2)
            github._workflow_checks({'repo': 'different/repo'}, 'sha', failed)
            self.assertEqual(gh.call_count, 3)

    def test_transient_history_error_is_retried_for_unchanged_checks(self):
        checks = [check(11, 1)]
        with patch.object(github, 'gh', side_effect=[
                CommandError('temporary outage'),
                {'workflow_runs': [workflow(1), workflow(2)]}]) as gh:
            self.assertEqual(github._workflow_checks({'repo': 'owner/repo'}, 'sha', checks), checks)
            self.assertEqual(github._workflow_checks({'repo': 'owner/repo'}, 'sha', checks), [])
            self.assertEqual(gh.call_count, 2)

    def test_foreign_url_or_conflicting_suite_cannot_hide_failure(self):
        for linkage in [
                {'details_url': 'https://other.example/owner/repo/actions/runs/1'},
                {'details_url': 'https://github.com/owner/repo/actions/runs/1', 'check_suite': {'id': 999}},
                {'workflow_run_id': 1, 'check_suite': {'id': 102}}]:
            with self.subTest(linkage=linkage):
                current = self.collect([check(11, **linkage)], [workflow(1), workflow(2)])
                self.assertEqual([r['id'] for r in current['checks']], [11])



if __name__ == '__main__':
    unittest.main()

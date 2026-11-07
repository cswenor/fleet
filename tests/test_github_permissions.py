import tempfile
import unittest
from unittest.mock import patch

from fleet import github, watcher
from fleet.commands import CommandError
from test_workflow_checks import check


def denied(endpoint):
    error = CommandError('gh api ' + endpoint + ': Resource not accessible by integration (HTTP 403)')
    error.status = 403
    error.endpoint = endpoint
    return error


class PermissionTests(unittest.TestCase):
    def setUp(self):
        github._pr_group_cache.clear()
        github._workflow_check_cache.clear()
        self.addCleanup(github._pr_group_cache.clear)
        self.addCleanup(github._workflow_check_cache.clear)
        self.pr = {'number': 7, 'title': 'Feature', 'html_url': 'https://example/pr/7',
                   'head': {'sha': 'sha', 'ref': 'feature'}, 'state': 'open',
                   'mergeable_state': 'clean', 'created_at': '2026-11-06T00:00:00Z',
                   'updated_at': '2026-11-06T00:00:00Z', 'closed_at': None}

    def response(self, c, *args):
        endpoint = args[-1]
        if endpoint == 'repos/owner/repo/pulls/7':
            return self.pr
        if '/check-runs?' in endpoint:
            return [{'check_runs': [check(11, 1)]}]
        if '/actions/runs?' in endpoint:
            raise denied(endpoint.split('?')[0])
        if '/statuses?' in endpoint:
            return [[]]
        if endpoint.endswith('/comments?per_page=100') or endpoint.endswith('/reviews?per_page=100'):
            return [[]]
        raise AssertionError(endpoint)

    def test_optional_actions_denial_retains_failure_and_watcher_reports_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(github, 'gh', side_effect=self.response):
            result = watcher.watch({'repo': 'owner/repo', '_state': directory}, 7, timeout=0)
        self.assertIn('failing checks: CI', result['events'][0]['subject'])
        self.assertNotIn(('owner/repo', 'sha'), github._workflow_check_cache)

    def test_group_denial_retains_known_failure_history(self):
        c = {'repo': 'owner/repo'}
        run = {'id': 1, 'head_sha': 'group', 'prs': [7], 'conclusion': 'failure',
               'membership_complete': True, 'url': 'https://example/run'}
        with patch.object(github, 'gh', side_effect=self.response):
            initial = github.collect(c, 7)
        # A permission-limited refresh must never discard known own-group failures.
        signature = '{}'
        github._pr_group_cache[('owner/repo', 7)] = (signature, [run])
        with patch.object(github, 'gh', side_effect=self.response):
            current = github.collect(c, 7)
            repeated = github.collect(c, 7)
        self.assertEqual(current['merge_group_runs'], [run])
        self.assertEqual(repeated['merge_group_runs'], [run])
        self.assertTrue(current['unavailable'])
        self.assertEqual(initial['prs'][0]['checks'][0]['conclusion'], 'cancelled')

    def test_queue_permission_denial_is_unknown_not_absent_and_watch_continues(self):
        def response(c, *args):
            if args[1] == 'graphql':
                raise denied('graphql')
            if '/check-runs?' in args[-1]:
                return [{'check_runs': []}]
            if '/actions/runs?event=merge_group' in args[-1]:
                return [{'workflow_runs': []}]
            return self.response(c, *args)
        with tempfile.TemporaryDirectory() as directory, patch.object(github, 'gh', side_effect=response):
            c = {'repo': 'owner/repo', '_state': directory}
            github.collect(c, 7)
            result = watcher.watch(c, 7, timeout=0)
        self.assertEqual(result['event'], 'timeout')
        self.assertTrue(any('graphql' in message for message in result['github_unknown']))

    def test_required_check_data_denial_still_fails(self):
        def response(c, *args):
            if '/check-runs?' in args[-1]:
                raise denied(args[-1].split('?')[0])
            return self.response(c, *args)
        with patch.object(github, 'gh', side_effect=response), self.assertRaises(CommandError):
            github.collect({'repo': 'owner/repo'}, 7)

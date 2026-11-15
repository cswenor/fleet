import copy
from datetime import datetime, timezone, timedelta
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fleet import github, github_idle
from fleet.commands import CommandError


class IdleCollectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name}
        self.now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.prs = []
        self.entries = []
        self.tip = 'base'
        self.queue_unknown = False
        self.runs = []
        self.paths = []
        self.mock = patch.object(github, 'gh', side_effect=self.response)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def response(self, c, *args):
        path = args[-1]
        self.paths.append(path)
        if args[1] == 'graphql':
            if self.queue_unknown:
                raise CommandError('queue unavailable')
            if any('number=' in x for x in args):
                return {'data': {'repository': {'pullRequest': {'mergeQueueEntry': None}}}}
            return {'data': {'repository': {'mergeQueue': {'entries': {
                'nodes': copy.deepcopy(self.entries), 'pageInfo': {'hasNextPage': False}}}}}}
        if path == 'repos/owner/repo':
            return {'full_name': 'owner/repo', 'default_branch': 'main'}
        if '/git/ref/heads/' in path:
            return {'object': {'sha': self.tip}}
        if '/pulls?state=all' in path:
            return copy.deepcopy(self.prs)
        if '/pulls?state=open' in path:
            return [[copy.deepcopy(p) for p in self.prs if p['state'] == 'open']]
        if '/check-runs?' in path:
            return [{'check_runs': []}]
        if '/statuses?' in path or '/comments?' in path or '/reviews?' in path:
            return [[]]
        if '/actions/runs?' in path:
            return [{'workflow_runs': copy.deepcopy(self.runs)}]
        if '/commits?' in path:
            return [[{'sha': self.tip, 'html_url': 'https://example/commit/' + self.tip,
                      'commit': {'committer': {'date': self.now}}}]]
        raise AssertionError(path)

    def pr(self, state='open', merged=None):
        return {'number': 7, 'title': 'Feature', 'html_url': 'https://example/pr/7',
                'head': {'sha': 'feature', 'ref': 'feature'}, 'state': state,
                'mergeable_state': 'clean', 'created_at': self.now, 'updated_at': self.now,
                'closed_at': self.now if state == 'closed' else None, 'merged_at': merged}

    def collect(self, previous):
        self.paths.clear()
        return github.collect(self.c, idle_previous=previous)

    def test_unchanged_idle_skips_commit_scan_and_group_enrichment(self):
        first = self.collect({})
        self.assertTrue(first['idle'])
        with patch.object(github.groups, 'resolve', side_effect=AssertionError('idle enrichment')):
            second = self.collect(first)
        self.assertTrue(second['idle'])
        self.assertEqual(second['commits'], first['commits'])
        self.assertFalse(any('/commits?' in path for path in self.paths))

    def test_new_pr_and_queue_entry_leave_idle_and_enrich(self):
        quiet = self.collect({})
        self.prs = [self.pr()]
        opened = self.collect(quiet)
        self.assertFalse(opened['idle'])
        self.assertEqual(opened['prs'][0]['state'], 'open')
        self.assertTrue(any('/check-runs?' in path for path in self.paths))
        self.prs = [self.pr()]
        self.entries = [{'position': 1, 'state': 'QUEUED', 'headCommit': {'oid': 'group'},
                         'pullRequest': {'number': 7}}]
        queued = self.collect(quiet)
        self.assertFalse(queued['idle'])
        self.assertEqual(queued['merge_queue'][0]['runs'], [])
        self.assertTrue(any('head_sha=group' in path for path in self.paths))

    def test_push_rescans_commits_then_stable_probe_reenters_idle(self):
        first = self.collect({})
        self.tip = 'pushed'
        pushed = self.collect(first)
        self.assertFalse(pushed['idle'])
        self.assertEqual(pushed['commits'][0]['sha'], 'pushed')
        self.assertTrue(self.collect(pushed)['idle'])

    def test_short_lived_pr_and_observed_open_terminal_transitions_are_not_lost(self):
        quiet = self.collect({})
        for merged in (None, self.now):
            with self.subTest(merged=merged):
                self.prs = [self.pr('closed', merged)]
                terminal = self.collect(quiet)
                self.assertFalse(terminal['idle'])
                self.assertEqual(terminal['prs'][0]['state'], 'merged' if merged else 'closed')
                self.prs = [self.pr()]
                opened = self.collect(quiet)
                self.prs = [self.pr('closed', merged)]
                events = github.events(opened, self.collect(opened))
                self.assertTrue(any(e['key'].startswith('pr:7:state:') for e in events))

    def test_unknown_queue_never_reuses_idle_history(self):
        first = self.collect({})
        first['merge_queue_unknown'] = True
        self.queue_unknown = True
        unknown = self.collect(first)
        self.assertFalse(unknown['idle'])
        self.assertTrue(unknown['merge_queue_unknown'])
        self.assertTrue(any('/commits?' in path for path in self.paths))

    def test_merge_group_rerun_is_observed_even_without_pr_or_tip_changes(self):
        first = self.collect({})
        self.runs = [{'id': 1, 'status': 'completed', 'conclusion': 'failure',
                      'head_sha': 'group', 'head_branch': 'gh-readonly-queue/main/pr-7-x',
                      'created_at': self.now, 'html_url': 'https://example/run/1'}]
        resolved = [{**self.runs[0], 'prs': [7], 'membership_complete': True,
                     'url': 'https://example/run/1'}]
        with patch.object(github.groups, 'resolve', return_value=resolved):
            changed = self.collect(first)
        self.assertFalse(changed['idle'])
        self.assertTrue(any(e['key'].startswith('group:1:failure:')
                            for e in github.events(first, changed)))

    def test_rolling_window_expires_history_without_network_commit_scan(self):
        first = self.collect({})
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        first['commits'].append({'sha': 'old', 'date': old, 'url': 'https://example/old'})
        second = self.collect(first)
        self.assertEqual([r['sha'] for r in second['commits']], ['base'])
        self.assertFalse(any('/commits?' in path for path in self.paths))

    def test_new_pr_during_full_backoff_is_detected_on_next_normal_probe(self):
        clock = [0]
        scheduler = github_idle.Scheduler(60)
        def collect(c, result, **kwargs):
            return github.collect(c, **kwargs)
        with patch.object(github_idle, 'time', SimpleNamespace(
                time=lambda: clock[0], monotonic=lambda: clock[0])):
            for expected in (120, 240, 480, 600):
                scheduler.collect(self.c, {}, collect)
                self.assertEqual(scheduler.interval, expected)
                if expected != 600:
                    clock[0] = scheduler.due
            clock[0] += 60
            self.prs = [self.pr()]
            result = {}
            opened = scheduler.collect(self.c, result, collect)
        self.assertEqual(opened['prs'][0]['state'], 'open')
        self.assertTrue(opened['full_collection'])
        self.assertEqual(scheduler.interval, 60)
        self.assertTrue(any('branch=main' == path for path in self.paths))

    def test_failed_open_probe_still_queries_queue_and_never_reuses_history(self):
        first = self.collect({})
        respond = self.response
        self.paths.clear()
        def failed(c, *args):
            if '/pulls?state=open' in args[-1]:
                raise CommandError('open PR read unavailable')
            return respond(c, *args)
        with patch.object(github, 'gh', side_effect=failed):
            with self.assertRaisesRegex(CommandError, 'open PR read unavailable'):
                github.collect(self.c, idle_previous=first)
        self.assertIn('branch=main', self.paths)

    def test_unresolved_failed_group_retries_membership_and_delivers_recovered_member(self):
        self.runs = [{'id': 1, 'status': 'completed', 'conclusion': 'failure',
                      'head_sha': 'group', 'head_branch': 'gh-readonly-queue/main/pr-7-x',
                      'created_at': self.now, 'html_url': 'https://example/run/1'}]
        partial = {**self.runs[0], 'prs': [7], 'membership_complete': False,
                   'url': 'https://example/run/1'}
        complete = {**partial, 'prs': [7, 8], 'membership_complete': True}
        with patch.object(github.groups, 'resolve', side_effect=[[partial], [complete]]):
            first = self.collect({})
            recovered = self.collect(first)
        self.assertFalse(first['idle'])
        self.assertEqual([(e['pr'], e['subject']) for e in github.events(first, recovered)],
                         [(8, 'Merge group 1 failure')])

    def test_full_refresh_rechecks_queue_and_commits_even_when_idle_probes_unchanged(self):
        first = self.collect({})
        self.paths.clear()
        refreshed = github.collect(self.c, idle_previous=first, idle_full=True)
        self.assertTrue(refreshed['full_collection'])
        self.assertTrue(refreshed['idle'])
        self.assertIn('branch=main', self.paths)
        self.assertTrue(any('/commits?' in path for path in self.paths))

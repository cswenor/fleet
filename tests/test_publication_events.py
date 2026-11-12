import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from fleet import github, watcher
from fleet.commands import CommandError
from test_initial_events import history, pull


class PublicationSnapshotTests(unittest.TestCase):
    def setUp(self):
        github._pr_group_cache.clear()
        github._workflow_check_cache.clear()
        self.addCleanup(github._pr_group_cache.clear)
        self.addCleanup(github._workflow_check_cache.clear)
        self.c = {'repo': 'owner/repo', 'publication': {
            'mode': 'auto_merge', 'review_app': 'fleet-review', 'hold_label': 'release-hold'}}
        self.pr = {'number': 7, 'title': 'Feature', 'html_url': 'https://example/pr/7',
                   'head': {'sha': 'head', 'ref': 'feature'}, 'state': 'open',
                   'mergeable_state': 'clean', 'created_at': '2026-11-06T00:00:00Z',
                   'updated_at': '2026-11-06T00:00:00Z', 'closed_at': None,
                   'draft': False, 'labels': [{'name': 'release-hold'}],
                   'auto_merge': {'enabled_by': {'login': 'release-owner', 'type': 'User'},
                                  'merge_method': 'squash'}}
        self.queue = {'id': 'queue-entry'}
        self.reviews = []
        self.calls = []
        self.app = {'id': 100, 'slug': 'fleet-review'}
        self.bot = {'id': 200, 'login': 'fleet-review[bot]', 'type': 'Bot',
                    'html_url': 'https://github.com/apps/fleet-review'}

    def response(self, c, *args):
        self.calls.append(args)
        endpoint = args[-1]
        if args[1] == 'graphql':
            if any('mergeQueue(branch:' in arg for arg in args):
                return {'data': {'repository': {'mergeQueue': None}}}
            if isinstance(self.queue, Exception):
                raise self.queue
            return {'data': {'repository': {'pullRequest': {'mergeQueueEntry': self.queue}}}}
        if endpoint == 'repos/owner/repo/pulls/7':
            return copy.deepcopy(self.pr)
        if '/pulls?' in endpoint:
            return [copy.deepcopy(self.pr)] if '--slurp' not in args else [[copy.deepcopy(self.pr)]]
        if '/check-runs?' in endpoint:
            return [{'check_runs': [{'id': 11, 'name': 'review', 'head_sha': 'head',
                                    'status': 'completed', 'conclusion': 'success',
                                    'app': self.app}]}]
        if endpoint.endswith('/reviews?per_page=100'):
            return [copy.deepcopy(self.reviews)]
        if '/actions/runs?' in endpoint:
            return [{'workflow_runs': []}]
        if '/statuses?' in endpoint or endpoint.endswith('/comments?per_page=100'):
            return [[]]
        if endpoint == 'repos/owner/repo':
            return {'default_branch': 'main'}
        if '/commits?sha=main' in endpoint:
            return [[]]
        raise AssertionError(args)

    def collect(self):
        with patch.object(github, 'gh', side_effect=self.response), \
                patch.object(github.groups, 'resolve', return_value=[]):
            return github.collect(self.c, 7)

    def review(self, number=1, **changes):
        return {'id': number, 'state': 'APPROVED', 'commit_id': 'head',
                'submitted_at': '2026-11-06T00:00:00Z', 'user': self.bot, **changes}

    def test_rest_auto_merge_actor_queue_entry_and_check_producer_are_preserved(self):
        current = self.collect()['prs'][0]
        self.assertEqual(current['auto_merge'], self.pr['auto_merge'])
        self.assertFalse(current['auto_merge_unknown'])
        self.assertEqual(current['merge_queue_entry'], {'id': 'queue-entry'})
        self.assertFalse(current['merge_queue_unknown'])
        self.assertEqual(current['hold_label'], 'release-hold')
        self.assertEqual(current['checks'][0]['app_slug'], 'fleet-review')
        self.assertNotIn('eligible', current)
        # Snapshot collection shares its live queue read with the history cache.
        self.assertEqual(sum(args[1] == 'graphql' for args in self.calls), 1)

    def test_repository_snapshot_collects_each_open_pr_publication_state(self):
        def response(c, *args):
            if '/pulls?' in args[-1]:
                prs = [copy.deepcopy(self.pr), copy.deepcopy(self.pr) | {'number': 8}]
                return [prs] if '--slurp' in args else prs
            return self.response(c, *args)

        with patch.object(github, 'gh', side_effect=response), \
                patch.object(github.groups, 'resolve', return_value=[]):
            current = github.collect(self.c)
        self.assertEqual([pr['number'] for pr in current['prs']], [7, 8])
        for pr in current['prs']:
            self.assertEqual(pr['auto_merge'], self.pr['auto_merge'])
            self.assertEqual(pr['merge_queue_entry'], self.queue)
            self.assertFalse(pr['merge_queue_unknown'])
        self.assertFalse(current['merge_queue_unknown'])
        for number in (7, 8):
            self.assertTrue(any(f'number={number}' in args for args in self.calls))

    def test_missing_auto_merge_and_denied_queue_are_explicitly_unknown(self):
        self.pr.pop('auto_merge')
        self.queue = CommandError('Resource not accessible by integration (HTTP 403)')
        current = self.collect()
        pr = current['prs'][0]
        self.assertIsNone(pr['auto_merge'])
        self.assertTrue(pr['auto_merge_unknown'])
        self.assertIsNone(pr['merge_queue_entry'])
        self.assertTrue(pr['merge_queue_unknown'])
        self.assertTrue(current['unavailable'])

    def test_rest_review_without_app_resolves_github_bot_identity(self):
        self.reviews = [self.review(performed_via_github_app=None),
                        self.review(2, commit_id='old-head')]
        reviews = self.collect()['prs'][0]['reviews']
        self.assertEqual(reviews[0]['user'], self.bot)
        self.assertIsNone(reviews[0]['performed_via_github_app'])
        self.assertEqual(reviews[0]['app_slug'], 'fleet-review')
        self.assertEqual(reviews[0]['app_identity_source'], 'github_bot_profile')
        self.assertEqual(reviews[0]['classification'], 'current_review_app')
        self.assertTrue(reviews[0]['current_head'])
        self.assertTrue(reviews[0]['review_app_match'])
        self.assertEqual(reviews[1]['commit_id'], 'old-head')
        self.assertFalse(reviews[1]['current_head'])
        self.assertEqual(reviews[1]['classification'], 'stale')
        self.assertEqual(reviews[0]['user']['html_url'], 'https://github.com/apps/fleet-review')

    def test_wrong_app_and_human_lookalike_never_classify_as_current_review_app(self):
        self.reviews = [
            self.review(performed_via_github_app={'id': 300, 'slug': 'other-app'}),
            self.review(2, user={**self.bot, 'type': 'User'}),
            self.review(3, user={**self.bot, 'html_url': 'https://example.com/apps/fleet-review'}),
            self.review(4, commit_id=None),
        ]
        reviews = self.collect()['prs'][0]['reviews']
        self.assertEqual(reviews[0]['classification'], 'wrong_app')
        self.assertFalse(reviews[0]['review_app_match'])
        for review in reviews[1:3]:
            self.assertIsNone(review['app_slug'])
            self.assertIsNone(review['review_app_match'])
            self.assertEqual(review['classification'], 'unknown_app')
        self.assertIsNone(reviews[3]['current_head'])
        self.assertEqual(reviews[3]['classification'], 'unknown_head')

    def test_missing_or_noncanonical_profile_preserves_unknown_app_identity(self):
        for profile in (None, 'https://github.com/fleet-review',
                        'https://github.com.evil/apps/fleet-review',
                        'http://github.com/apps/fleet-review',
                        'https://github.com/apps/wrong-app',
                        'https://github.com/apps/fleet-review?spoof=1',
                        'https://[invalid'):
            with self.subTest(profile=profile):
                user = {**self.bot, 'html_url': profile}
                self.reviews = [self.review(user=user)]
                review = self.collect()['prs'][0]['reviews'][0]
                self.assertIsNone(review['review_app_match'])
                self.assertEqual(review['user'], user)
                self.assertIsNone(review['app_identity_source'])
                self.assertEqual(review['classification'], 'unknown_app')

    def test_github_bot_profile_can_positively_identify_a_wrong_app(self):
        self.reviews = [self.review(user={
            'id': 301, 'type': 'Bot', 'login': 'other-app[bot]',
            'html_url': 'https://github.com/apps/other-app'})]
        review = self.collect()['prs'][0]['reviews'][0]
        self.assertEqual(review['app_slug'], 'other-app')
        self.assertFalse(review['review_app_match'])
        self.assertEqual(review['classification'], 'wrong_app')

    def test_push_reclassifies_same_review_as_stale(self):
        self.reviews = [self.review()]
        before = self.collect()
        self.pr['head']['sha'] = 'new-head'
        after = self.collect()
        review = after['prs'][0]['reviews'][0]
        self.assertFalse(review['current_head'])
        self.assertTrue(review['review_app_match'])
        self.assertEqual(review['classification'], 'stale')
        self.assertTrue(any(':reviews:' in event['key'] for event in github.events(before, after)))

    def test_closed_historical_pr_never_queries_queue_or_review_identity(self):
        self.pr['state'] = 'closed'
        self.collect()
        self.assertFalse(any(args[1] == 'graphql' for args in self.calls))
        self.assertFalse(any(args[-1].startswith('apps/') for args in self.calls))


class PublicationEventTests(unittest.TestCase):
    def pr(self, **changes):
        return pull(labels=[], draft=False, auto_merge=None, auto_merge_unknown=False,
                    merge_queue_entry=None, merge_queue_unknown=False, hold_label='release-hold') | changes

    def transitions(self, before, after, **options):
        return github.events(history(before), history(after), **options)

    def test_semantic_transitions_reach_both_default_and_aggregate_consumers(self):
        base = self.pr()
        cases = [
            ({'labels': ['Release-Hold']}, 'hold added', 'hold removed'),
            ({'draft': True}, 'draft converted', 'draft ready'),
            ({'auto_merge': {'enabled_by': {'login': 'owner'}}}, 'auto_merge armed', 'auto_merge disarmed'),
            ({'merge_queue_entry': {'id': 'queue-entry'}}, 'merge_queue entered', 'merge_queue exited'),
        ]
        for changes, enabled, disabled in cases:
            for aggregate in (False, True):
                with self.subTest(changes=changes, aggregate=aggregate):
                    after = base | changes
                    events = self.transitions(base, after, aggregate_checks=aggregate)
                    self.assertEqual([event['subject'] for event in events], [f'PR #7 {enabled}'])
                    self.assertEqual([event['subject'] for event in self.transitions(after, base)],
                                     [f'PR #7 {disabled}'])
                    self.assertEqual(self.transitions(after, after), [])

    def test_unknown_never_looks_like_disarmed_dequeued_or_a_transition(self):
        for field, unknown in [('auto_merge', 'auto_merge_unknown'),
                               ('merge_queue_entry', 'merge_queue_unknown')]:
            active = self.pr(**{field: {'id': 'active'}})
            unavailable = self.pr(**{unknown: True})
            inactive = self.pr()
            for before, after in [(active, unavailable), (unavailable, inactive),
                                  (inactive, unavailable), (unavailable, active)]:
                with self.subTest(field=field, before=before, after=after):
                    self.assertEqual(self.transitions(before, after), [])
            self.assertIsNone(github.initial_event(unavailable))

    def test_initial_observation_exposes_active_states_without_fabricating_absence(self):
        current = self.pr(labels=['RELEASE-HOLD'], draft=True,
                          auto_merge={'enabled_by': {'login': 'owner'}},
                          merge_queue_entry={'id': 'queue-entry'})
        event = github.initial_event(current)
        for detail in ('hold active', 'draft', 'auto_merge armed', 'merge_queue entered'):
            self.assertIn(detail, event['body'])
        self.assertEqual(github.events({}, history(current)), [event])
        self.assertEqual(watcher._initial_events(history(current), 7), [event])
        self.assertEqual(self.transitions(current, current), [])
        self.assertIsNone(github.initial_event(self.pr()))

    def test_label_reordering_and_case_only_hold_change_do_not_spam(self):
        before = self.pr(labels=['bug', 'Release-Hold'])
        self.assertEqual(self.transitions(before, before | {'labels': ['release-HOLD', 'bug']}), [])
        events = self.transitions(before, before | {'labels': ['bug', 'Release-Hold', 'ready']})
        self.assertEqual(len(events), 1)
        self.assertIn(':labels:', events[0]['key'])
        self.assertIn('"added": ["ready"]', events[0]['body'])
        self.assertEqual(self.transitions(before, before | {'labels': ['bug']})[0]['subject'],
                         'PR #7 hold removed')

    def test_legacy_snapshots_and_manual_fixtures_remain_compatible(self):
        legacy = pull()
        self.assertEqual(self.transitions(legacy, self.pr()), [])
        self.assertEqual(github.events({}, history(legacy)), [])
        self.assertEqual(github.events(history(legacy), history(legacy)), [])
        default_hold = pull(labels=['HOLD'])
        self.assertIn('hold active', github.initial_event(default_hold)['body'])
        # A legacy snapshot without labels cannot prove a hold transition.
        self.assertEqual(self.transitions(legacy, pull(labels=['hold'])), [])

    def test_watcher_wakes_on_each_publication_transition(self):
        for changes, detail in [({'labels': ['release-hold']}, 'hold added'),
                                ({'draft': True}, 'draft converted'),
                                ({'auto_merge': {'enabled_by': {'login': 'owner'}}}, 'auto_merge armed'),
                                ({'merge_queue_entry': {'id': 'q'}}, 'merge_queue entered')]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory, \
                    patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''}), \
                    patch.object(github, 'collect', side_effect=[history(self.pr()), history(self.pr(**changes))]), \
                    patch.object(watcher.time, 'sleep'):
                result = watcher.watch({'repo': 'owner/repo', '_state': directory}, 7, timeout=60)
            self.assertEqual([event['subject'] for event in result['events']], [f'PR #7 {detail}'])

    def test_terminal_outcome_remains_distinct_from_disarming_and_queue_exit(self):
        before = self.pr(auto_merge={'enabled_by': {'login': 'owner'}},
                         merge_queue_entry={'id': 'q'})
        for state in ('merged', 'closed'):
            events = self.transitions(before, self.pr(state=state))
            self.assertEqual([event['subject'] for event in events],
                             ['PR #7 auto_merge disarmed', 'PR #7 merge_queue exited',
                              f'PR #7 state changed: {state}'])

    def test_queue_reentry_keeps_distinct_identity_even_without_pr_timestamp_change(self):
        absent = self.pr()
        first = self.pr(merge_queue_entry={'id': 'first-entry'})
        second = self.pr(merge_queue_entry={'id': 'second-entry'})
        entered = self.transitions(absent, first)[0]
        reentered = self.transitions(absent, second)[0]
        self.assertNotEqual(entered['key'], reentered['key'])
        exited = self.transitions(first, absent)[0]
        exited_again = self.transitions(second, absent)[0]
        self.assertNotEqual(exited['key'], exited_again['key'])
        self.assertEqual(self.transitions(first, first), [])


class CarrierReviewTests(unittest.TestCase):
    def setUp(self):
        self.c = {'repo': 'owner/project', 'publication': {'review_app': 'review-app'}}
        self.pr = {'number': 7, 'head': {'sha': 'carrier'}, 'base': {'sha': 'base'}}
        self.reviews = [{'id': 1, 'state': 'APPROVED', 'commit_id': 'reviewed',
                         'user': {'type': 'Bot', 'login': 'review-app[bot]',
                                  'html_url': 'https://github.com/apps/review-app'}}]
        self.parents = [{'sha': 'reviewed'}, {'sha': 'base'}]

    def normalize(self, comparison=None):
        unavailable = []
        def api(c, *args):
            if '/git/commits/' in args[-1]:
                return {'parents': self.parents}
            return comparison
        with patch.object(github, 'gh', side_effect=api) as request:
            carrier = github._review_carrier(self.c, self.pr, self.reviews, unavailable)
        return github._review_snapshot(self.c, self.reviews, 'carrier', carrier), unavailable, request

    def test_base_merge_carrier_is_display_only_not_exact_head_approval(self):
        reviews, unavailable, request = self.normalize()
        review = reviews[0]
        self.assertEqual(review['head_display'], 'unverified carrier of reviewed head reviewed')
        self.assertEqual(review['classification'], 'carrier_of_reviewed_head')
        self.assertFalse(review['current_head'])
        self.assertTrue(review['review_app_match'])
        self.assertEqual(review['carrier']['cleanliness'], 'unverified')
        self.assertEqual(unavailable, [])
        self.assertEqual(request.call_count, 1)

    def test_second_parent_must_be_proven_on_base_history(self):
        self.parents[1]['sha'] = 'older-base'
        for comparison, carried in (
                ({'status': 'ahead', 'merge_base_commit': {'sha': 'older-base'}}, True),
                ({'status': 'diverged', 'merge_base_commit': {'sha': 'common'}}, False),
                ({'status': 'ahead', 'merge_base_commit': {'sha': 'common'}}, False)):
            with self.subTest(comparison=comparison):
                reviews, _, _ = self.normalize(comparison)
                self.assertEqual(reviews[0]['carrier'] is not None, carried)

    def test_nonmerge_wrong_first_parent_and_octopus_are_not_carriers(self):
        for parents in ([{'sha': 'reviewed'}], list(reversed(self.parents)),
                        self.parents + [{'sha': 'third'}]):
            with self.subTest(parents=parents):
                self.parents = parents
                reviews, _, _ = self.normalize()
                self.assertEqual(reviews[0]['head_display'], 'stale head')
                self.assertIsNone(reviews[0]['carrier'])

    def test_exact_head_is_current_independently_of_review_app_identity(self):
        for user in ({'login': 'human', 'type': 'User'},
                     {'login': 'other[bot]', 'type': 'Bot',
                      'html_url': 'https://github.com/apps/other'}):
            with self.subTest(user=user):
                self.reviews[0].update(commit_id='carrier', user=user)
                reviews, _, request = self.normalize()
                self.assertTrue(reviews[0]['current_head'])
                self.assertEqual(reviews[0]['head_display'], 'current head')
                self.assertIsNone(reviews[0]['carrier'])
                request.assert_not_called()

    def test_dismissed_or_unknown_approval_cannot_prove_carrier(self):
        for change in ({'state': 'DISMISSED'}, {'commit_id': None}):
            with self.subTest(change=change):
                reviews = [{**self.reviews[0], **change}]
                with patch.object(github, 'gh', return_value={'parents': self.parents}):
                    self.assertIsNone(github._review_carrier(self.c, self.pr, reviews, []))

    def test_unavailable_ancestry_retains_stale_display_and_reports_uncertainty(self):
        unavailable = []
        with patch.object(github, 'gh', side_effect=CommandError('unavailable')):
            carrier = github._review_carrier(self.c, self.pr, self.reviews, unavailable)
        self.assertIsNone(carrier)
        self.assertIn('carrier evidence unavailable', unavailable[0])

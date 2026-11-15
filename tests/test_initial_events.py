import os
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import patch
from fleet import github, groups, watcher


def pull(**changes):
    return {'number': 7, 'url': 'https://example/pr/7', 'branch': 'feature', 'head': 'sha',
            'updated_at': '2026-11-06T00:00:00Z', 'state': 'open', 'merge_state': 'CLEAN',
            'checks': [], 'comments': [], 'reviews': [], **changes}


def history(*prs, runs=()):
    return {'prs': list(prs), 'merge_group_runs': list(runs)}


class InitialStateTests(unittest.TestCase):
    def setUp(self):
        anonymous = patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''})
        anonymous.start()
        self.addCleanup(anonymous.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name}
        auth = patch('fleet.github_auth.resolve', return_value=(None, {'kind': 'app_installation'}))
        auth.start()
        self.addCleanup(auth.stop)

    def test_watch_reports_actionable_initial_state_without_waiting(self):
        scenarios = [
            (pull(merge_state='DIRTY'), 'DIRTY'),
            (pull(checks=[{'name': 'CI', 'conclusion': 'failure'}]), 'failing checks: CI'),
            (pull(state='merged'), 'merged'),
            (pull(state='closed'), 'closed'),
        ]
        for pr, diagnostic in scenarios:
            with self.subTest(diagnostic=diagnostic), patch.object(github, 'collect', return_value=history(pr)), patch.object(watcher.time, 'sleep', side_effect=AssertionError('actionable initial state must not poll')):
                result = watcher.watch(self.c, 7)
                self.assertEqual(result['pr'], 7)
                self.assertIn(diagnostic, result['events'][0]['body'])

    def test_watch_wakes_after_long_pending_check_and_review_check_failures(self):
        for name, status, conclusion in [
            ('long CI', 'completed', 'failure'),
            ('codex-review', 'failure', 'failure'),
        ]:
            with self.subTest(name=name):
                pending = history(pull(checks=[{'id': 1, 'name': name,
                                                'status': 'pending', 'conclusion': None}]))
                failed = history(pull(checks=[{'id': 1, 'name': name,
                                               'status': status, 'conclusion': conclusion}]))
                clock = [0]
                def sleep(seconds):
                    clock[0] += seconds
                with patch.object(github, 'collect', side_effect=[pending] * 6 + [failed]), \
                        patch.object(watcher.time, 'sleep', side_effect=sleep), \
                        patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]):
                    result = watcher.watch(self.c, 7, timeout=600, interval=60)
                event = result['events'][0]
                self.assertEqual(event['pr'], 7)
                self.assertIn('failing checks: ' + name, event['subject'])
                self.assertEqual(github.events(failed, failed), [])

    def test_watch_initial_group_failures_are_scoped_to_known_membership(self):
        failed = {'id': 5, 'conclusion': 'failure', 'url': 'https://example/run/5',
                  'prs': [8], 'membership_complete': True}
        scenarios = [
            ('unrelated', [8], True, False),
            ('own', [7], True, True),
            ('secondary member', [8, 7], True, True),
            ('known partial member', [7], False, True),
            ('unknown members', [], False, False),
            ('other partial members', [8], False, False),
        ]
        for label, members, complete, relevant in scenarios:
            with self.subTest(label=label):
                current = history(pull(), runs=[{**failed, 'prs': members,
                                                 'membership_complete': complete}])
                with patch.object(github, 'collect', return_value=current):
                    result = watcher.watch(self.c, 7, timeout=0)
                if relevant:
                    self.assertEqual([(e['pr'], e['subject']) for e in result['events']],
                                     [(7, 'Merge group 5 failure')])
                else:
                    self.assertEqual(result, {'pr': 7, 'event': 'timeout', 'seconds': 0})

    def test_watch_initial_dirty_excludes_unrelated_group_failure(self):
        failed = {'id': 5, 'conclusion': 'failure', 'url': 'https://example/run/5',
                  'prs': [8], 'membership_complete': True}
        with patch.object(github, 'collect', return_value=history(pull(merge_state='DIRTY'), runs=[failed])):
            result = watcher.watch(self.c, 7)
        self.assertEqual([e['pr'] for e in result['events']], [7])
        self.assertIn('DIRTY', result['events'][0]['body'])

    def test_watch_continues_past_initial_unrelated_group_failure(self):
        failed = {'id': 5, 'conclusion': 'failure', 'url': 'https://example/run/5',
                  'prs': [8], 'membership_complete': True}
        before = history(pull(), runs=[failed])
        after = history(pull(merge_state='DIRTY'), runs=[failed])
        with patch.object(github, 'collect', side_effect=[before, after]), patch.object(watcher.time, 'sleep'):
            result = watcher.watch(self.c, 7)
        self.assertEqual([e['pr'] for e in result['events']], [7])
        self.assertIn('DIRTY', result['events'][0]['body'])

    def test_watch_initial_superseded_groups(self):
        old = {'id': 5, 'head_sha': 'old-group', 'conclusion': 'failure',
               'url': 'https://example/run/5', 'prs': [7],
               'membership_complete': True, 'created_at': '2026-11-06T00:00:00Z'}
        newer = {**old, 'id': 6, 'head_sha': 'new-group',
                 'url': 'https://example/run/6',
                 'created_at': '2026-11-06T01:00:00Z'}
        scenarios = [
            ('pending successor', newer | {'conclusion': None}, []),
            ('failed successor', newer, [6]),
            ('same group pending workflow', newer | {'head_sha': old['head_sha'], 'conclusion': None}, [5]),
            ('same group successful workflow', newer | {'head_sha': old['head_sha'], 'conclusion': 'success'}, [5]),
            ('unknown successor group', newer | {'head_sha': None, 'conclusion': None}, [5]),
            ('partial successor membership', newer | {'membership_complete': False, 'conclusion': None}, [5]),
            ('single failure', None, [5]),
            ('unknown successor membership', newer | {'prs': []}, [5]),
            ('unrelated successor', newer | {'prs': [8]}, [5]),
            ('missing timestamps', newer | {'created_at': None, 'conclusion': None}, []),
            ('equal timestamps', newer | {'created_at': old['created_at'], 'conclusion': None}, [5]),
            ('older despite higher id', newer | {'created_at': '2026-11-05T00:00:00Z', 'conclusion': None}, [5]),
        ]
        for label, successor, expected in scenarios:
            with self.subTest(label=label):
                current = history(pull(), runs=[old] + ([successor] if successor else []))
                with patch.object(github, 'collect', return_value=current):
                    result = watcher.watch(self.c, 7, timeout=0)
                self.assertEqual([e['subject'] for e in result.get('events', [])],
                                 [f'Merge group {n} failure' for n in expected])

    def test_watch_initial_group_lineage_requires_member_head_proof(self):
        failed = {'id': 5, 'head_sha': 'synthetic', 'conclusion': 'failure',
                  'url': 'https://example/run/5', 'prs': [7], 'membership_complete': True}
        for heads, expected in [({}, True), ({'8': 'old'}, True),
                                ({'7': 'sha'}, True), ({'7': 'old'}, False)]:
            with self.subTest(heads=heads), patch.object(github, 'collect',
                    return_value=history(pull(), runs=[failed | {'member_heads': heads}])):
                result = watcher.watch(self.c, 7, timeout=0)
            self.assertEqual(bool(result.get('events')), expected)

    def test_watch_newer_group_live_failure_still_wakes(self):
        old = {'id': 5, 'head_sha': 'old-group', 'conclusion': 'failure',
               'url': 'https://example/run/5', 'prs': [7], 'membership_complete': True}
        newer = {**old, 'id': 6, 'head_sha': 'new-group',
                 'conclusion': None, 'url': 'https://example/run/6'}
        before = history(pull(), runs=[old, newer])
        after = history(pull(), runs=[old, newer | {'conclusion': 'failure'}])
        with patch.object(github, 'collect', side_effect=[before, after]), \
                patch.object(watcher.time, 'sleep'):
            result = watcher.watch(self.c, 7)
        self.assertEqual([e['subject'] for e in result['events']], ['Merge group 6 failure'])

    def test_daemon_first_sighting_and_new_unhealthy_pr_notify_once(self):
        current = history(pull(merge_state='DIRTY'))
        initial = github.events({}, current)
        self.assertEqual([(e['pr'], e['branch']) for e in initial], [(7, 'feature')])
        self.assertIn('DIRTY', initial[0]['body'])
        self.assertEqual(github.events(current, current), [])
        self.assertEqual(github.events(history(), current), initial)
        self.assertEqual(github.events({}, history(pull())), [])

    def test_repeated_close_transition_is_not_deduped_as_the_previous_close(self):
        opened = history(pull())
        closed = history(pull(state='closed', updated_at='2026-11-06T01:00:00Z'))
        reopened = history(pull(updated_at='2026-11-06T02:00:00Z'))
        reclosed = history(pull(state='closed', updated_at='2026-11-06T03:00:00Z'))
        self.assertNotEqual(github.events(opened, closed)[0]['key'], github.events(reopened, reclosed)[0]['key'])


class GroupTests(unittest.TestCase):
    def setUp(self):
        anonymous = patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''})
        anonymous.start()
        self.addCleanup(anonymous.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name}
        auth = patch('fleet.github_auth.resolve', return_value=(None, {'kind': 'app_installation'}))
        auth.start()
        self.addCleanup(auth.stop)

    def test_synthetic_chain_excludes_side_history_and_includes_every_group_member(self):
        commit = lambda sha, parent, number: {'sha': sha, 'parents': [{'sha': parent}, {'sha': 'side'}], 'commit': {'message': f'Title (#{number})'}}
        commits = [commit('group-b', 'group-a', 8), commit('group-a', 'base', 7), commit('side', 'other', 99)]
        self.assertEqual(groups.members(commits, 'base', 'group-b'),
                         ([7, 8], True, {'7': 'side', '8': 'side'}))
        self.assertEqual(groups.members(commits[0:1], 'base', 'group-b'),
                         ([8], False, {'8': 'side'}))

    def test_resolution_bounds_pending_lookups_for_aggregate_and_per_pr(self):
        base = 'a' * 40
        def run(n, conclusion=None):
            return {'id': n, 'head_sha': f'group-{n}', 'conclusion': conclusion,
                    'branch': f'gh-readonly-queue/main/pr-7-{base}',
                    'created_at': f'2026-11-06T0{n}:00:00Z'}
        def compare(c, *args):
            head = args[-1].split('...')[1].split('?')[0]
            return [{'commits': [{'sha': head, 'parents': [{'sha': base}, {'sha': 'pr-head'}],
                                  'commit': {'message': 'Feature (#7)'}}]}]
        for number, expected in [(None, ['group-2', 'group-5']), (7, ['group-2', 'group-5'])]:
            with self.subTest(number=number), patch.object(groups.state, 'read', return_value={}), \
                    patch.object(groups.state, 'write'), patch.object(groups, 'gh', side_effect=compare) as gh:
                resolved = groups.resolve({'repo': 'owner/repo'},
                                          [run(1), run(2, 'failure'), run(3), run(4), run(5)],
                                          pr_number=number)
            self.assertEqual([call.args[-1].split('...')[1].split('?')[0] for call in gh.call_args_list], expected)
            by_id = {r['id']: r for r in resolved}
            self.assertEqual(by_id[1]['prs'], [])
            self.assertEqual(by_id[3]['prs'], [])
            self.assertEqual(by_id[5]['prs'], [7])
            self.assertEqual(by_id[2]['member_heads'], {'7': 'pr-head'})

    def test_failed_pending_lookup_is_memoized_only_within_collection(self):
        from fleet.commands import CommandError
        base = 'a' * 40
        failed = {'id': 2, 'head_sha': 'old', 'conclusion': 'failure',
                  'branch': f'gh-readonly-queue/main/pr-7-{base}'}
        pending = {'id': 3, 'head_sha': 'new', 'conclusion': None,
                   'branch': f'gh-readonly-queue/main/pr-8-{base}'}
        with patch.object(groups.state, 'read', return_value={}), \
                patch.object(groups.state, 'write') as save, \
                patch.object(groups, 'gh', side_effect=CommandError('lookup failed')) as gh:
            runs = groups.resolve({'repo': 'owner/repo'}, [failed, pending, pending | {'id': 4}], pr_number=7)
        self.assertEqual(gh.call_count, 2)
        self.assertEqual(save.call_args.args[-1], {})
        self.assertFalse(runs[-1]['membership_complete'])

    def test_queued_heads_resolve_pending_and_success_without_failures(self):
        base = 'a' * 40
        for conclusion in (None, 'success'):
            with self.subTest(conclusion=conclusion):
                run = {'id': 1, 'head_sha': 'group', 'conclusion': conclusion,
                       'branch': f'gh-readonly-queue/main/pr-8-{base}'}
                commits = [
                    {'sha': 'group', 'parents': [{'sha': 'first'}, {'sha': 'head-8'}],
                     'commit': {'message': 'Second (#8)'}},
                    {'sha': 'first', 'parents': [{'sha': base}, {'sha': 'head-7'}],
                     'commit': {'message': 'First (#7)'}}]
                with patch.object(groups, 'gh', return_value=[{'commits': commits}]) as compare:
                    resolved = groups.resolve(self.c, [run, run | {'id': 2}],
                                              queued_heads={'group'})
                self.assertEqual(compare.call_count, 1 if conclusion is None else 0)
                for evidence in resolved:
                    self.assertEqual(evidence['prs'], [7, 8])
                    self.assertTrue(evidence['membership_complete'])
                    self.assertEqual(evidence['member_heads'], {'7': 'head-7', '8': 'head-8'})

    def test_aggregate_successors_are_bounded_for_each_failed_member(self):
        base = 'a' * 40
        runs = [
            {'id': n, 'head_sha': f'group-{n}', 'conclusion': 'failure' if n == 1 else None,
             'branch': f'gh-readonly-queue/main/pr-8-{base}'} for n in range(1, 6)]
        membership = {1: [7, 8], 2: [7, 8], 3: [7], 4: [8], 5: [99]}
        def compare(c, *args):
            head = args[-1].split('...')[1].split('?')[0]
            numbers = membership[int(head.split('-')[-1])]
            commits = []
            parent = base
            for index, number in enumerate(numbers):
                sha = head if index == len(numbers) - 1 else head + '-first'
                commits.append({'sha': sha, 'parents': [{'sha': parent}, {'sha': f'head-{number}'}],
                                'commit': {'message': f'Feature (#{number})'}})
                parent = sha
            return [{'commits': commits}]
        with patch.object(groups, 'gh', side_effect=compare) as lookup:
            resolved = groups.resolve(self.c, runs)
        self.assertEqual([call.args[-1].split('...')[1].split('?')[0]
                          for call in lookup.call_args_list],
                         ['group-1', 'group-5', 'group-4', 'group-3'])
        self.assertFalse(resolved[1]['membership_complete'])
        self.assertEqual(resolved[2]['member_heads'], {'7': 'head-7'})
        self.assertEqual(resolved[3]['member_heads'], {'8': 'head-8'})
        self.assertEqual(resolved[4]['prs'], [99])

    def test_collection_resolves_shared_queue_and_ejected_failure_evidence(self):
        base = 'a' * 40
        now = datetime.now(timezone.utc).isoformat()
        prs = [
            {'number': number, 'title': 'Feature', 'html_url': f'https://example/pr/{number}',
             'head': {'sha': f'head-{number}', 'ref': f'feature-{number}'}, 'state': 'open',
             'mergeable_state': 'clean', 'created_at': now, 'updated_at': now,
             'closed_at': None} for number in (7, 8)]
        def run(n, head, conclusion):
            return {'id': n, 'head_sha': head, 'conclusion': conclusion,
                    'status': 'in_progress' if conclusion is None else 'completed',
                    'head_branch': f'gh-readonly-queue/main/pr-8-{base}',
                    'html_url': f'https://example/run/{n}', 'created_at': now}
        for conclusion, queued, old_failure in [
                (None, True, False), ('success', True, False),
                (None, True, True), ('success', False, True), ('failure', False, False)]:
            with self.subTest(conclusion=conclusion, queued=queued, old_failure=old_failure):
                github._pr_group_cache.clear()
                runs = ([run(1, 'old', 'failure')] if old_failure else []) + [
                    run(2, 'new', conclusion), run(3, 'new', conclusion)]
                # IDs order equal timestamps, as GitHub timestamps can tie.
                for row in runs:
                    row.pop('created_at')
                entry = {'id': 'queue-entry', 'headCommit': {'oid': 'new'}}
                entries = [{'position': index, 'state': 'QUEUED',
                            'headCommit': {'oid': 'new'}, 'pullRequest': {'number': number}}
                           for index, number in enumerate((7, 8))] if queued else []
                def response(c, *args):
                    endpoint = args[-1]
                    if args[1] == 'graphql':
                        if any(arg.startswith('number=') for arg in args):
                            return {'data': {'repository': {'pullRequest': {
                                'mergeQueueEntry': entry if queued else None}}}}
                        return {'data': {'repository': {'mergeQueue': {'entries': {
                            'nodes': entries, 'pageInfo': {'hasNextPage': False}}}}}}
                    if endpoint == 'repos/owner/repo':
                        return {'default_branch': 'main', 'full_name': 'owner/repo'}
                    if endpoint in ('repos/owner/repo/pulls/7', 'repos/owner/repo/pulls/8'):
                        return prs[int(endpoint[-1]) - 7]
                    if '/pulls?' in endpoint:
                        return [prs] if '--slurp' in args else prs
                    if '/check-runs?' in endpoint:
                        return [{'check_runs': []}]
                    if '/actions/runs?' in endpoint:
                        return [{'workflow_runs': [] if 'head_sha=' in endpoint else runs}]
                    if any(part in endpoint for part in
                           ('/statuses?', '/comments?', '/reviews?', '/commits?')):
                        return [[]]
                    raise AssertionError(endpoint)
                def compare(c, *args):
                    head = args[-1].split('...')[1].split('?')[0]
                    return [{'commits': [
                        {'sha': head, 'parents': [{'sha': head + '-first'}, {'sha': 'head-8'}],
                         'commit': {'message': 'Second (#8)'}},
                        {'sha': head + '-first', 'parents': [{'sha': base}, {'sha': 'head-7'}],
                         'commit': {'message': 'First (#7)'}}]}]
                with patch.object(github, 'gh', side_effect=response), \
                        patch.object(groups, 'gh', side_effect=compare) as lookup, \
                        patch.object(groups.state, 'read', return_value={}), \
                        patch.object(groups.state, 'write'):
                    aggregate = github.collect(self.c)
                    aggregate_calls = lookup.call_count
                    direct = github.collect(self.c, 7)
                self.assertEqual(aggregate['merge_group_runs'], direct['merge_group_runs'])
                self.assertEqual(aggregate_calls, 2 if old_failure else 1)
                for evidence in aggregate['merge_group_runs']:
                    self.assertTrue(evidence['membership_complete'])
                    self.assertEqual(evidence['prs'], [7, 8])
                    self.assertEqual(evidence['member_heads'], {'7': 'head-7', '8': 'head-8'})
                self.assertEqual(aggregate['merge_group_runs'][-1]['conclusion'], conclusion)
                self.assertEqual(aggregate['merge_group_runs'][-1]['status'],
                                 'in_progress' if conclusion is None else 'completed')
        github._pr_group_cache.clear()

    def test_secondary_group_member_receives_failure_even_when_head_checks_do_not_change(self):
        a, b = pull(), pull(number=8, branch='second')
        before = history(a, b)
        failed = {'id': 5, 'branch': 'gh-readonly-queue/main/pr-8-base', 'conclusion': 'failure', 'url': 'https://example/run/5', 'prs': [7, 8], 'membership_complete': True}
        after = history(a, b, runs=[failed])
        self.assertEqual({(e['pr'], e['branch']) for e in github.events(before, after)}, {(7, 'feature'), (8, 'second')})
        partially_known = history(a, b, runs=[{**failed, 'prs': [8], 'membership_complete': False}])
        self.assertEqual([e['pr'] for e in github.events(partially_known, after)], [7])
        # A per-PR watcher only loads its own PR; membership must still wake it.
        scoped = history(a, runs=[failed])
        with patch.object(github, 'collect', side_effect=[history(a), scoped]), patch.object(watcher.time, 'sleep'):
            result = watcher.watch(self.c, 7)
        self.assertEqual([(e['pr'], e['subject']) for e in result['events']], [(7, 'Merge group 5 failure')])


if __name__ == '__main__':
    unittest.main()

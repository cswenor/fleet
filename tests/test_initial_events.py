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
    def test_watch_reports_actionable_initial_state_without_waiting(self):
        scenarios = [
            (pull(merge_state='DIRTY'), 'DIRTY'),
            (pull(checks=[{'name': 'CI', 'conclusion': 'failure'}]), 'failing checks: CI'),
            (pull(state='merged'), 'merged'),
            (pull(state='closed'), 'closed'),
        ]
        for pr, diagnostic in scenarios:
            with self.subTest(diagnostic=diagnostic), patch.object(github, 'collect', return_value=history(pr)), patch.object(watcher.time, 'sleep', side_effect=AssertionError('actionable initial state must not poll')):
                result = watcher.watch({}, 7)
                self.assertEqual(result['pr'], 7)
                self.assertIn(diagnostic, result['events'][0]['body'])

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
                    result = watcher.watch({}, 7, timeout=0)
                if relevant:
                    self.assertEqual([(e['pr'], e['subject']) for e in result['events']],
                                     [(7, 'Merge group 5 failure')])
                else:
                    self.assertEqual(result, {'pr': 7, 'event': 'timeout', 'seconds': 0})

    def test_watch_initial_dirty_excludes_unrelated_group_failure(self):
        failed = {'id': 5, 'conclusion': 'failure', 'url': 'https://example/run/5',
                  'prs': [8], 'membership_complete': True}
        with patch.object(github, 'collect', return_value=history(pull(merge_state='DIRTY'), runs=[failed])):
            result = watcher.watch({}, 7)
        self.assertEqual([e['pr'] for e in result['events']], [7])
        self.assertIn('DIRTY', result['events'][0]['body'])

    def test_watch_continues_past_initial_unrelated_group_failure(self):
        failed = {'id': 5, 'conclusion': 'failure', 'url': 'https://example/run/5',
                  'prs': [8], 'membership_complete': True}
        before = history(pull(), runs=[failed])
        after = history(pull(merge_state='DIRTY'), runs=[failed])
        with patch.object(github, 'collect', side_effect=[before, after]), patch.object(watcher.time, 'sleep'):
            result = watcher.watch({}, 7)
        self.assertEqual([e['pr'] for e in result['events']], [7])
        self.assertIn('DIRTY', result['events'][0]['body'])

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
    def test_synthetic_chain_excludes_side_history_and_includes_every_group_member(self):
        commit = lambda sha, parent, number: {'sha': sha, 'parents': [{'sha': parent}, {'sha': 'side'}], 'commit': {'message': f'Title (#{number})'}}
        commits = [commit('group-b', 'group-a', 8), commit('group-a', 'base', 7), commit('side', 'other', 99)]
        self.assertEqual(groups.members(commits, 'base', 'group-b'), ([7, 8], True))
        self.assertEqual(groups.members(commits[0:1], 'base', 'group-b'), ([8], False))

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
            result = watcher.watch({}, 7)
        self.assertEqual([(e['pr'], e['subject']) for e in result['events']], [(7, 'Merge group 5 failure')])


if __name__ == '__main__':
    unittest.main()

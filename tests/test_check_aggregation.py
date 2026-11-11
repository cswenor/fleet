import unittest

from fleet import github


def check(check_id, name='CI', status='completed', conclusion='success', **changes):
    return {'id': check_id, 'name': name, 'status': status,
            'conclusion': conclusion, 'app_id': 1, 'check_suite_id': 10, **changes}


def pull(checks=(), **changes):
    return {'number': 7, 'url': 'https://example/pr/7', 'branch': 'feature',
            'head': 'sha', 'state': 'open', 'merge_state': 'CLEAN',
            'updated_at': '2026-11-06T00:00:00Z', 'checks': list(checks),
            'comments': [], 'reviews': [], **changes}


def history(p):
    return {'prs': [p], 'merge_group_runs': []}


def check_events(before, after, **options):
    return [event for event in github.events(history(before), history(after), **options)
            if ':checks:' in event['key']]


class CheckAggregationTests(unittest.TestCase):
    def test_overall_conclusions(self):
        scenarios = [
            ([], 'pending'),
            ([check(1, status='queued', conclusion=None)], 'pending'),
            ([check(1, status='in_progress', conclusion=None)], 'pending'),
            ([check(1, status='pending', conclusion='pending')], 'pending'),
            ([check(1)], 'success'),
            ([check(1, conclusion='neutral'), check(2, 'docs', conclusion='skipped')], 'success'),
            ([check(1, status='success')], 'success'),
            ([check(1, conclusion='cancelled')], 'failure'),
            ([check(1, conclusion='failure'), check(2, 'docs', 'queued', None)], 'failure'),
            ([check(1, status='error', conclusion='error')], 'failure'),
        ]
        for checks, expected in scenarios:
            with self.subTest(checks=checks):
                self.assertEqual(github.checks_conclusion(pull(checks)), expected)

    def test_aggregate_pending_transitions_to_success_or_failure(self):
        pending = pull([check(1, status='in_progress', conclusion=None)])
        for conclusion in ('success', 'failure', 'cancelled'):
            with self.subTest(conclusion=conclusion):
                finished = pull([check(1, conclusion=conclusion)])
                events = check_events(pending, finished, aggregate_checks=True)
                self.assertEqual(len(events), 1)
                overall = 'success' if conclusion == 'success' else 'failure'
                self.assertIn('checks changed: ' + overall, events[0]['subject'])
                self.assertIn('"conclusion": "' + overall + '"', events[0]['body'])
                self.assertEqual(check_events(finished, finished, aggregate_checks=True), [])

    def test_individual_pending_and_failed_transitions_are_silent_in_aggregate_mode(self):
        scenarios = [
            (pull([check(1, status='queued', conclusion=None)]),
             pull([check(1, status='in_progress', conclusion=None)])),
            (pull([check(1, status='in_progress', conclusion=None),
                   check(2, 'docs', 'queued', None)]),
             pull([check(1), check(2, 'docs', 'queued', None)])),
            (pull([check(1, conclusion='failure'), check(2, 'docs', 'queued', None)]),
             pull([check(1, conclusion='failure'), check(2, 'docs', conclusion='failure')])),
            (pull([check(1)]), pull([check(2)])),
        ]
        for before, after in scenarios:
            with self.subTest(before=before, after=after):
                self.assertEqual(check_events(before, after, aggregate_checks=True), [])
                self.assertEqual(len(check_events(before, after)), 1)

    def test_head_change_emits_one_aggregate_check_event_even_without_check_changes(self):
        for conclusion in (None, 'success', 'failure'):
            checks = [check(1, status='queued' if conclusion is None else 'completed',
                            conclusion=conclusion)]
            before = pull(checks)
            for after in (pull(checks, head='new-head'), pull(head='new-head')):
                with self.subTest(conclusion=conclusion, after=after):
                    events = check_events(before, after, aggregate_checks=True)
                    self.assertEqual(len(events), 1)
                    self.assertIn('"head": "new-head"', events[0]['body'])
                    self.assertEqual(check_events(after, after, aggregate_checks=True), [])

    def test_old_snapshots_use_effective_checks_for_initial_and_change_failure_summaries(self):
        for old_conclusion in ('failure', 'cancelled'):
            for status, conclusion in (('in_progress', None), ('completed', 'success')):
                with self.subTest(old=old_conclusion, status=status):
                    checks = [check(11, conclusion=old_conclusion),
                              check(12, status=status, conclusion=conclusion)]
                    current = pull(checks)
                    self.assertIsNone(github.initial_event(current))
                    self.assertEqual(github.initial_event(pull(checks, merge_state='DIRTY')),
                                     github.initial_event(pull([checks[1]], merge_state='DIRTY')))
                    events = check_events(pull(), current)
                    self.assertEqual(len(events), 1)
                    self.assertNotIn('failing checks:', events[0]['subject'])
                    self.assertEqual(github.checks_conclusion(current),
                                     'success' if conclusion == 'success' else 'pending')

    def test_same_name_checks_on_other_heads_do_not_supersede_each_other(self):
        checks = [check(11, conclusion='failure', head_sha='old'),
                  check(12, head_sha='new')]
        self.assertEqual(github.checks_conclusion(pull(checks)), 'failure')

    def test_failure_summary_keeps_unversioned_checks(self):
        current = pull([{'name': 'CI', 'conclusion': 'failure'}, check(12)])
        self.assertIn('failing checks: CI', github.initial_event(current)['subject'])

    def test_independent_or_unknown_producers_cannot_hide_failure(self):
        failed = check(11, conclusion='failure')
        for newer in [check(12, app_id=2), check(12, check_suite_id=20),
                      check(12, app_id=None), check(12, check_suite_id=None)]:
            with self.subTest(newer=newer):
                current = pull([failed, newer])
                self.assertEqual(github.checks_conclusion(current), 'failure')
                self.assertIn('failing checks: CI', github.initial_event(current)['subject'])
        legacy = pull([{'id': 11, 'name': 'CI', 'status': 'completed',
                        'conclusion': 'failure'}, check(12)])
        self.assertEqual(github.checks_conclusion(legacy), 'failure')


if __name__ == '__main__':
    unittest.main()

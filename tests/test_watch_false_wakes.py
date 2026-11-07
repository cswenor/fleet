"""PR38/PR55/TASK-64 watcher false-wake regression scenarios."""
import os
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

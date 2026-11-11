import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fleet import relays, state


class RelayAssessmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/project', '_state': directory.name,
                  'coordinator': {'terminal': 'main', 'run': 'run'},
                  'builder': {'slots': ['/lane']}}
        self.launch = {'terminal': 'lane', 'at': 1794358800}
        state.write(self.c, 'launches', {'/lane': self.launch})
        self.message = {'id': 'old', 'from_handle': 'main', 'to_handle': 'lane',
                        'run_id': 'run', 'type': 'status', 'read': 0,
                        'created_at': '2026-11-10T00:00:00Z',
                        'body': '[fleet owner/project] PRIVATE BODY', 'thread_id': None}

    def report(self, messages, complete=True):
        return relays.assess(self.c, 'lane', self.launch, messages, complete=complete)

    def test_all_stale_assessment_never_consumes(self):
        def native(c, verb, *args):
            if verb == 'run-current':
                return {'run': {'id': 'run', 'coordinator_handle': 'main'}}
            self.assertEqual(verb, 'inbox')
            return {'messages': [self.message]}
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}), patch.object(
                relays, 'request', side_effect=native):
            result = relays.clear_stale(self.c, 'lane', dry_run=True)
        self.assertTrue(result['would_be_all_stale'])
        self.assertEqual(result['stale_relays'], 1)
        self.assertFalse(result['mutation_supported'])
        self.assertNotIn('PRIVATE BODY', json.dumps(result))

    def test_other_mail_and_current_relays_block_all_stale(self):
        variants = [{'type': 'question'}, {'type': 'escalation'},
                    {'thread_id': 'decision-reply'}, {'from_handle': 'builder'},
                    {'run_id': 'other'}, {'to_handle': 'other'},
                    {'body': 'not a supervisor relay'},
                    {'created_at': '2026-11-12T00:00:00Z'},
                    {'created_at': 'invalid'}]
        for changed in variants:
            with self.subTest(changed=changed):
                result = self.report([self.message, self.message | changed])
                self.assertFalse(result['would_be_all_stale'])
                self.assertEqual(sum(row['count'] for row in result['blockers']), 1)
                self.assertEqual(result['unread'], 2)

    def test_cap_and_unknown_launch_never_prove_all_stale(self):
        self.assertFalse(self.report([self.message], complete=False)['would_be_all_stale'])
        self.launch['at'] = None
        self.assertFalse(self.report([self.message])['would_be_all_stale'])

    def test_only_current_configured_lane_and_main_can_assess(self):
        with patch.object(relays, 'request') as request:
            with self.assertRaisesRegex(ValueError, '--dry-run'):
                relays.clear_stale(self.c, 'lane')
            with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'builder'}):
                with self.assertRaisesRegex(ValueError, 'Main terminal'):
                    relays.clear_stale(self.c, 'lane', dry_run=True)
            request.assert_not_called()
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'main'}), patch.object(
                relays, 'request', return_value={'run': {'id': 'run', 'coordinator_handle': 'main'}}) as request:
            with self.assertRaisesRegex(ValueError, 'configured lane'):
                relays.clear_stale(self.c, 'other', dry_run=True)
            self.assertEqual(request.call_count, 1)

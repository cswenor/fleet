from contextlib import ExitStack
from copy import deepcopy
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from fleet import publication


HEAD = 'a' * 40


class ArmingTests(unittest.TestCase):
    def setUp(self):
        self.c = {'repo': 'owner/project', 'root': '.',
                  'publication': {'mode': 'auto_merge', 'release': 'none', 'merge_method': 'squash'},
                  'review_bot': {'apps': {'builder': {'env_file': '/private/builder.env'}}}}
        self.pull = {'number': 7, 'state': 'open', 'merged': False, 'draft': False,
                     'head': {'sha': HEAD, 'repo': {'full_name': 'owner/project'}},
                     'base': {'ref': 'main', 'repo': {'full_name': 'owner/project'}},
                     'labels': [], 'body': None, 'auto_merge': None}
        self.reviews = []
        self.revoked = []
        self.executions = []
        self.owner = Mock()
        self.owner.request.side_effect = self.read
        self.builder = Mock()
        self.builder.request.return_value = {'token': 'fresh-builder-installation-token'}
        self.scoped = Mock()
        self.scoped.request.side_effect = lambda method, path, body=None: self.revoked.append((method, path))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        transport = self.stack.enter_context(patch.object(publication, 'GitHub'))
        transport.owner.return_value = self.owner
        transport.return_value = self.scoped
        self.stack.enter_context(patch.object(publication, 'app_client', return_value=self.builder))
        self.stack.enter_context(patch.object(publication, 'verify_app'))
        self.stack.enter_context(patch.object(publication, 'verify_installation', return_value=123))
        self.protection = self.stack.enter_context(patch.object(
            publication, 'verify_live_ruleset', return_value={'verified': True}))
        self.stack.enter_context(patch.object(publication.subprocess, 'run', side_effect=self.execute))
        self.queue = self.stack.enter_context(patch.object(publication, '_queued', return_value=None))
        self.exit_code = 0
        self.process_error = None
        self.after_process = None

    def read(self, method, path, body=None):
        self.assertEqual(method, 'GET')
        if '/reviews?' in path:
            return deepcopy(self.reviews)
        self.assertEqual(path, '/repos/owner/project/pulls/7')
        return deepcopy(self.pull)

    def execute(self, argv, **kwargs):
        self.executions.append((argv, kwargs))
        if self.after_process:
            self.after_process()
        else:
            self.pull['auto_merge'] = {'enabled_by': {'login': 'project-builder[bot]'}}
        if self.process_error:
            raise self.process_error
        return subprocess.CompletedProcess(argv, self.exit_code)

    def arm(self):
        return publication.arm(self.c, 7, HEAD)

    def test_pending_checks_do_not_block_builder_only_exact_head_arming(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'operator-token', 'GITHUB_TOKEN': 'other-operator',
                                     'GH_ENTERPRISE_TOKEN': 'enterprise-secret', 'REVIEW_PRIVATE_KEY': 'review-key'}):
            result = self.arm()
        self.assertEqual(result['state'], 'armed')
        self.assertEqual(result['arming_actor'], 'project-builder[bot]')
        argv, options = self.executions[0]
        self.assertEqual(options['env']['GH_TOKEN'], 'fresh-builder-installation-token')
        for name in ('GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'REVIEW_PRIVATE_KEY'):
            self.assertNotIn(name, options['env'])
        self.assertGreater(options['timeout'], 0)
        self.assertLessEqual(options['timeout'], 60)
        self.assertNotIn('fresh-builder-installation-token', json.dumps(argv))
        self.assertNotIn('token', json.dumps(result))
        self.assertEqual(self.revoked, [('DELETE', '/installation/token')])


    def test_live_protection_failure_cannot_be_overridden_by_a_snapshot(self):
        self.c['publication']['verified'] = True
        self.protection.side_effect = ValueError('protection unavailable')
        with self.assertRaises(ValueError):
            self.arm()
        self.assertEqual(self.executions, [])

    def test_invalid_inputs_and_manual_policy_never_arm(self):
        for pr, head in ((0, HEAD), (True, HEAD), ('7', HEAD), (7, 'abc'), (7, 'g' * 40)):
            with self.subTest(pr=pr, head=head), self.assertRaises(ValueError):
                publication.arm(self.c, pr, head)
        self.c['publication']['mode'] = 'manual'
        with self.assertRaises(ValueError):
            self.arm()
        self.assertEqual(self.executions, [])

    def test_live_pr_identity_and_authorization_fail_closed(self):
        changes = [
            {'state': 'closed'}, {'draft': True}, {'draft': None}, {'merged': None},
            {'head': {'sha': 'b' * 40, 'repo': {'full_name': 'owner/project'}}},
            {'head': {'sha': HEAD, 'repo': {'full_name': 'fork/project'}}},
            {'base': {'ref': 'other', 'repo': {'full_name': 'owner/project'}}},
            {'base': {'ref': 'main', 'repo': {'full_name': 'other/project'}}},
            {'labels': [{'name': 'HoLd'}]}, {'labels': None}, {'labels': [{}]},
            {'body': '- [ ] Gate: production proof'},
            {'body': '> 1. * [ ] Gate: nested'},
            {'body': '<!--\n- [ ]\r\n  Gate: hidden\n-->'}]
        original = deepcopy(self.pull)
        for change in changes:
            with self.subTest(change=change):
                self.pull = {**deepcopy(original), **change}
                with self.assertRaises(ValueError):
                    self.arm()
        self.assertEqual(self.executions, [])
        self.assertEqual(len(self.revoked), len(changes))

    def review(self, status, user=1, number=1, time='2026-11-01T00:00:00Z'):
        return {'state': status, 'id': number, 'submitted_at': time,
                'user': {'type': 'User', 'id': user}}

    def test_effective_human_veto_is_per_author_and_comments_do_not_withdraw(self):
        self.reviews = [self.review('CHANGES_REQUESTED'),
                        self.review('COMMENTED', number=2),
                        self.review('APPROVED', user=2, number=3)]
        with self.assertRaisesRegex(ValueError, 'veto'):
            self.arm()
        self.assertEqual(self.executions, [])

    def test_latest_substantive_review_withdraws_only_its_authors_veto(self):
        self.reviews = [self.review('APPROVED', number=3),
                        self.review('CHANGES_REQUESTED', number=1)]
        self.assertEqual(self.arm()['state'], 'armed')

    def test_review_read_failure_revokes_without_running_command(self):
        self.owner.request.side_effect = ValueError('visibility unavailable')
        with self.assertRaises(ValueError):
            self.arm()
        self.assertEqual(self.executions, [])
        self.assertEqual(self.revoked, [('DELETE', '/installation/token')])

    def test_subprocess_failure_and_timeout_do_not_expose_credentials(self):
        for error in (OSError('fresh-builder-installation-token operator-token'),
                      subprocess.TimeoutExpired('secret command', 60)):
            with self.subTest(error=type(error).__name__):
                self.process_error = error
                result = self.arm()
                self.assertFalse(result['command_succeeded'])
                self.assertTrue(result['blockers'])
                self.assertNotIn('fresh-builder-installation-token', json.dumps(result))
                self.assertNotIn('operator-token', json.dumps(result))
                self.assertEqual(self.revoked[-1], ('DELETE', '/installation/token'))

    def test_observed_terminal_outcome_survives_command_failure(self):
        for merged in (True, False):
            with self.subTest(merged=merged):
                self.exit_code = 1
                self.after_process = lambda: self.pull.update(state='closed', merged=merged, auto_merge=None)
                self.pull.update(state='open', merged=False, auto_merge=None)
                result = self.arm()
                self.assertEqual(result['state'], 'merged' if merged else 'closed')
                self.assertIsNone(result['arming_actor'])

    def test_successful_process_is_not_proof_of_native_arming(self):
        self.after_process = lambda: None
        result = self.arm()
        self.assertEqual(result['state'], 'not_armed')
        self.assertTrue(result['blockers'])

    def test_native_queue_entry_without_auto_merge_is_successful_queued_observation(self):
        self.c['review_bot']['ruleset'] = {'merge_queue': True}
        self.after_process = lambda: None
        self.queue.return_value = {'id': 'native-entry'}
        result = self.arm()
        self.assertEqual(result['state'], 'queued')
        self.assertTrue(result['merge_queue'])
        self.assertEqual(result['merge_queue_entry'], {'id': 'native-entry'})
        self.assertIsNone(result['arming_actor'])
        self.assertNotIn('blockers', result)

    def test_unavailable_queue_visibility_is_unknown_not_not_armed(self):
        self.after_process = lambda: None
        self.queue.side_effect = ValueError('private queue diagnostic')
        result = self.arm()
        self.assertEqual(result['state'], 'unknown')
        self.assertIsNone(result['merge_queue'])
        self.assertNotIn('private queue diagnostic', json.dumps(result))

    def test_ruleset_removed_during_authentication_prevents_arming(self):
        self.protection.side_effect = [{'verified': True}, ValueError('disabled protection')]
        with self.assertRaises(ValueError):
            self.arm()
        self.assertEqual(self.executions, [])
        self.assertEqual(self.revoked, [('DELETE', '/installation/token')])

    def test_cleanup_failure_preserves_observed_mutation_and_reports_uncertainty(self):
        from fleet.review_bot_api import APIError
        self.scoped.request.side_effect = APIError(500)
        result = self.arm()
        self.assertEqual(result['state'], 'armed')
        self.assertEqual(result['arming_actor'], 'project-builder[bot]')
        self.assertTrue(result['blockers'])

    def test_head_changed_during_arming_is_not_reported_as_requested_head_success(self):
        self.after_process = lambda: self.pull.update(
            head={'sha': 'b' * 40, 'repo': {'full_name': self.c['repo']}},
            auto_merge={'enabled_by': {'login': 'project-builder[bot]'}})
        result = self.arm()
        self.assertEqual(result['observed_head'], 'b' * 40)
        self.assertTrue(result['blockers'])


class GuardTests(unittest.TestCase):
    def test_manual_guard_needs_no_operator_credentials(self):
        self.assertEqual(publication.guard({}), {'required': False, 'verified': True})

    def test_unknown_unsafe_and_failed_verification_withhold_without_secrets(self):
        c = {'publication': {'mode': 'auto_merge'}}
        for value in (None, {}, {'verified': False}):
            with self.subTest(value=value), patch.object(publication, 'verify_live_ruleset', return_value=value):
                self.assertFalse(publication.guard(c)['verified'])
        with patch.object(publication, 'verify_live_ruleset', side_effect=ValueError('private-token')):
            result = publication.guard(c)
        self.assertFalse(result['verified'])
        self.assertNotIn('private-token', json.dumps(result))

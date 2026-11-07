import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fleet import commands, github, github_api, state


def response(body=None, *, status=200, headers=None, code=0):
    text = f'HTTP/2.0 {status} Status\r\n'
    for key, value in (headers or {}).items():
        text += f'{key}: {value}\r\n'
    text += '\r\n' + ('' if body is None else json.dumps(body))
    return subprocess.CompletedProcess([], code, text, 'secret stderr')


def budget(remaining=5000, reset=2000, limit=5000):
    return {'X-RateLimit-Remaining': str(remaining),
            'X-RateLimit-Reset': str(reset), 'X-RateLimit-Limit': str(limit)}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = {'root': '/', 'repo': 'owner/repo', '_state': self.directory.name}
        environment = patch.dict(os.environ, {'GH_TOKEN': 'ghp_test_secret', 'GH_HOST': 'github.com'})
        environment.start()
        self.addCleanup(environment.stop)
        clock = patch.object(github_api.time, 'time', return_value=1000)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def test_consumer_reuses_304_body_without_leaking_mutations(self):
        session = github_api.Session(self.c)
        replies = [response([{'id': 1}], headers={'ETag': '"one"'}),
                   response(status=304, code=1), response(status=304)]
        with patch.object(commands, '_execute', side_effect=replies) as execute:
            with session:
                first = github.pages(self.c, 'repos/owner/repo/issues')
                first[0]['id'] = 99
            # A retained session works across disjoint watcher polls.
            with session:
                self.assertEqual(github.pages(self.c, 'repos/owner/repo/issues'), [{'id': 1}])
                self.assertEqual(github.pages(self.c, 'repos/owner/repo/issues'), [{'id': 1}])
        self.assertEqual(session.stats, {'requests': 3, 'not_modified': 2})
        self.assertIn('If-None-Match: "one"', execute.call_args.args[0])
        self.assertNotIn('--paginate', execute.call_args.args[0])

    def test_consumer_follows_and_revalidates_each_page(self):
        next_url = 'https://api.github.com/repos/owner/repo/issues?page=2'
        replies = [response([{'id': 1}], headers={
                       'ETag': '"a"', 'Link': f'<{next_url}>; rel="next"'}),
                   response([{'id': 2}], headers={'ETag': '"b"'}),
                   response(status=304), response(status=304)]
        with github_api.Session(self.c) as session:
            with patch.object(commands, '_execute', side_effect=replies) as execute:
                for _ in range(2):
                    self.assertEqual(github.pages(self.c, 'repos/owner/repo/issues'),
                                     [{'id': 1}, {'id': 2}])
                self.assertIn(next_url, execute.call_args_list[1].args[0])
                self.assertIn('If-None-Match: "a"', execute.call_args_list[2].args[0])
                self.assertIn('If-None-Match: "b"', execute.call_args_list[3].args[0])
        self.assertEqual(session.stats, {'requests': 4, 'not_modified': 2})

    def test_enterprise_pagination_uses_stock_gh_host_and_rejects_other_hosts(self):
        next_url = 'https://github.corp.example/api/v3/repos/owner/repo/issues?page=2'
        with patch.dict(os.environ, {'GH_HOST': 'github.corp.example'}):
            with github_api.Session(self.c), patch.object(commands, '_execute', side_effect=[
                    response([{'id': 1}], headers={'Link': f'<{next_url}>; rel="next"'}),
                    response([{'id': 2}])]):
                self.assertEqual(github.pages(self.c, 'repos/owner/repo/issues'),
                                 [{'id': 1}, {'id': 2}])
            with github_api.Session(self.c), patch.object(commands, '_execute', return_value=
                    response([], headers={'Link': '<https://other.example/issues>; rel="next"'})) as execute:
                with self.assertRaisesRegex(commands.CommandError, 'pagination link'):
                    github.pages(self.c, 'repos/owner/repo/issues')
                self.assertEqual(execute.call_count, 1)

    def test_object_page_boundaries_are_preserved(self):
        replies = [response({'check_runs': [1]}, headers={
            'Link': '<https://api.github.com/checks?page=2>; rel="next"'}),
            response({'check_runs': [2]})]
        with github_api.Session(self.c), patch.object(commands, '_execute', side_effect=replies):
            self.assertEqual(commands.gh(self.c, 'api', '--paginate', '--slurp', 'checks'),
                             [{'check_runs': [1]}, {'check_runs': [2]}])

    def test_plain_paginate_retains_invalid_json_contract(self):
        replies = [response([1], headers={
            'Link': '<https://api.github.com/checks?page=2>; rel="next"'}), response([2])]
        with github_api.Session(self.c), patch.object(commands, '_execute', side_effect=replies):
            with self.assertRaisesRegex(commands.CommandError, 'invalid command JSON'):
                commands.gh(self.c, 'api', '--paginate', 'checks')

    def test_low_budget_returns_current_read_and_suppresses_next_until_reset(self):
        with github_api.Session(self.c) as session:
            with patch.object(commands, '_execute', side_effect=[
                    response([1], headers=budget(remaining=499)),
                    response([2], headers=budget(reset=3000))]) as execute:
                self.assertEqual(github.pages(self.c, 'issues'), [1])
                self.assertEqual(session.budget, {'reset': 2000, 'remaining': 499, 'limit': 5000})
                for _ in range(2):
                    with self.assertRaises(github_api.BudgetLow) as caught:
                        github.pages(self.c, 'issues')
                    self.assertEqual(caught.exception.reset, 2000)
                self.assertEqual(execute.call_count, 1)
                self.clock.return_value = 2000
                self.assertEqual(github.pages(self.c, 'issues'), [2])
                self.assertEqual(session.blocked_until, 0)
        self.assertEqual(session.stats['requests'], 2)

    def test_exact_ten_percent_does_not_block(self):
        with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                response([], headers=budget(remaining=500))):
            github.pages(self.c, 'issues')
            self.assertEqual(session.blocked_until, 0)

    def test_nonzero_403_and_429_headers_set_bounded_backoff(self):
        for status in (403, 429):
            with self.subTest(status=status):
                with github_api.Session(self.c) as session:
                    with patch.object(commands, '_execute', return_value=response(
                            {'message': 'secret body'}, status=status, code=1,
                            headers={'Retry-After': '120'})) as execute:
                        for _ in range(2):
                            with self.assertRaises(github_api.BudgetLow) as caught:
                                github.pages(self.c, 'issues')
                            self.assertNotIn('secret', str(caught.exception))
                        self.assertEqual(execute.call_count, 1)
                        self.assertEqual(session.blocked_until, 1120)
                self.assertEqual(session.stats['requests'], 1)
        with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                response(status=429, headers={'Retry-After': '999999'})):
            with self.assertRaises(github_api.BudgetLow):
                github.pages(self.c, 'issues')
            self.assertEqual(session.blocked_until, 1000 + github_api.MAX_BACKOFF)

    def test_retry_after_http_date(self):
        with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                response(status=429, headers={'Retry-After': 'Thu, 01 Jan 1970 00:20:00 GMT'})):
            with self.assertRaises(github_api.BudgetLow):
                github.pages(self.c, 'issues')
            self.assertEqual(session.blocked_until, 1200)

    def test_short_secondary_retry_does_not_wait_for_healthy_primary_reset(self):
        with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                response(status=403, headers={**budget(), 'Retry-After': '5'})):
            with self.assertRaises(github_api.BudgetLow):
                github.pages(self.c, 'issues')
            self.assertEqual(session.blocked_until, 1005)

    def test_nonzero_success_response_is_rejected_without_headers_or_body(self):
        with github_api.Session(self.c), patch.object(commands, '_execute', return_value=
                response({'private': 'body'}, code=1, headers={'X-Secret': 'private'})):
            with self.assertRaises(commands.GhError) as caught:
                commands.gh(self.c, 'api', 'issues?access_token=private')
            self.assertEqual(caught.exception.status, 200)
            self.assertEqual(caught.exception.endpoint, 'issues')
            self.assertNotIn('private', str(caught.exception))

    def test_uncached_304_and_malformed_headers_are_safe_errors(self):
        replies = [response(status=304, code=1),
                   subprocess.CompletedProcess([], 1, 'secret non-header output', 'ghp_secret')]
        with github_api.Session(self.c), patch.object(commands, '_execute', side_effect=replies):
            for _ in replies:
                with self.assertRaises(commands.CommandError) as caught:
                    commands.gh(self.c, 'api', 'issues')
                self.assertNotIn('secret', str(caught.exception))

    def test_external_next_link_never_receives_a_request(self):
        with github_api.Session(self.c), patch.object(commands, '_execute', return_value=
                response([], headers={'Link': '<https://evil.example/private>; rel="next"'})) as execute:
            with self.assertRaisesRegex(commands.CommandError, 'pagination link'):
                github.pages(self.c, 'issues')
            self.assertEqual(execute.call_count, 1)

    def test_persistent_cache_is_private_and_reused_by_new_session(self):
        with github_api.Session(self.c, persistent=True), patch.object(commands, '_execute', return_value=
                response([{'id': 1}], headers={'ETag': '"persisted"', 'X-Secret': 'do-not-store'})):
            github.pages(self.c, 'issues')
        cache_file = Path(self.c['_state']) / 'github-api.json'
        self.assertEqual(os.stat(cache_file).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(cache_file.parent).st_mode & 0o777, 0o700)
        self.assertNotIn('do-not-store', cache_file.read_text())
        with github_api.Session(self.c, persistent=True) as session:
            with patch.object(commands, '_execute', return_value=response(status=304)) as execute:
                self.assertEqual(github.pages(self.c, 'issues'), [{'id': 1}])
                self.assertIn('If-None-Match: "persisted"', execute.call_args.args[0])
            self.assertEqual(session.stats, {'requests': 1, 'not_modified': 1})

    def test_persistent_backoff_survives_session_recreation(self):
        with github_api.Session(self.c, persistent=True), patch.object(commands, '_execute', return_value=
                response(status=403, code=1, headers=budget(remaining=0))):
            with self.assertRaises(github_api.BudgetLow):
                github.pages(self.c, 'issues')
        with github_api.Session(self.c, persistent=True), patch.object(commands, '_execute') as execute:
            with self.assertRaises(github_api.BudgetLow) as caught:
                github.pages(self.c, 'issues')
            self.assertEqual(caught.exception.reset, 2000)
            execute.assert_not_called()

    def test_memory_and_disk_cache_are_bounded_and_evict_oldest(self):
        with patch.object(github_api, 'MAX_ENTRIES', 2):
            with github_api.Session(self.c, persistent=True) as session:
                with patch.object(commands, '_execute', return_value=response([1], headers={'ETag': '"a"'})):
                    for endpoint in ('one', 'two', 'three'):
                        commands.gh(self.c, 'api', endpoint)
                self.assertEqual(len(session._cache), 2)
                with patch.object(commands, '_execute', return_value=response([1])) as execute:
                    commands.gh(self.c, 'api', 'one')
                    self.assertFalse(any('If-None-Match' in arg for arg in execute.call_args.args[0]))
            saved = state.read(self.c, 'github-api')
            self.assertLessEqual(len(saved['entries']), 2)
        with patch.object(github_api, 'MAX_CACHE_BYTES', 10):
            with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                    response(['large body'], headers={'ETag': '"a"'})):
                commands.gh(self.c, 'api', 'large')
                self.assertEqual(len(session._cache), 0)

    def test_corrupt_persistent_state_is_disposable(self):
        state.write(self.c, 'github-api', {'entries': [['bad', {}]]})
        session = github_api.Session(self.c, persistent=True)
        self.assertEqual(len(session._cache), 0)

    def test_active_context_restores_outer_session(self):
        outer = github_api.Session(self.c)
        inner = github_api.Session(self.c)
        with outer:
            self.assertIs(github_api.active_session(), outer)
            with inner:
                self.assertIs(github_api.active_session(), inner)
            self.assertIs(github_api.active_session(), outer)
        self.assertIsNone(github_api.active_session())

    def test_permission_403_is_not_a_budget_backoff(self):
        with github_api.Session(self.c) as session, patch.object(commands, '_execute', return_value=
                response(status=403, code=1, headers=budget())) as execute:
            for _ in range(2):
                with self.assertRaises(commands.GhError) as caught:
                    github.pages(self.c, 'repos/owner/repo/actions/runs?private=query')
                self.assertEqual(caught.exception.status, 403)
                self.assertEqual(caught.exception.endpoint, 'repos/owner/repo/actions/runs')
            self.assertEqual(session.blocked_until, 0)
            self.assertEqual(execute.call_count, 2)

    def test_gh_diagnostics_redact_tokens_auth_headers_and_url_queries(self):
        text = ('failed ghp_private github_pat_private gho_private ghu_private ghs_private ghr_private '
                'ghp_test_secret https://api.github.com/repos/o/r?token=private\n'
                'Authorization: Bearer hidden-value\nProxy-Authorization: Basic c2VjcmV0\n'
                'X-Auth-Token: unknown-secret\n' + 'x' * 500)
        process = subprocess.CompletedProcess([], 1, '', text)
        with patch.object(commands, '_execute', return_value=process):
            with self.assertRaises(commands.GhError) as caught:
                commands.gh(self.c, 'api', 'repos/o/r?token=private')
        message = str(caught.exception)
        for secret in ('ghp_private', 'github_pat_private', 'gho_private', 'ghu_private',
                       'ghs_private', 'ghr_private', 'ghp_test_secret', 'hidden-value',
                       'c2VjcmV0', 'unknown-secret', '?token='):
            self.assertNotIn(secret, message)
        self.assertIn('repos/o/r', message)
        self.assertLessEqual(len(message), 360)

    def test_environment_token_switch_does_not_reuse_persistent_body_or_backoff(self):
        with github_api.Session(self.c, persistent=True), patch.object(commands, '_execute', return_value=
                response([1], headers={'ETag': '\"private\"', **budget(remaining=0)})):
            commands.gh(self.c, 'api', 'issues')
        with patch.dict(os.environ, {'GH_TOKEN': 'ghp_other_secret'}):
            with github_api.Session(self.c, persistent=True) as session:
                with patch.object(commands, '_execute', return_value=response([2])) as execute:
                    self.assertEqual(commands.gh(self.c, 'api', 'issues'), [2])
                    self.assertFalse(any('If-None-Match' in a for a in execute.call_args.args[0]))
                self.assertEqual(session.blocked_until, 0)

    def test_stock_auth_token_is_fingerprinted_privately_for_persistence(self):
        with patch.dict(os.environ, {}, clear=True):
            auth = subprocess.CompletedProcess([], 0, 'ghp_stock_private\n', '')
            with patch.object(commands, '_execute', side_effect=[
                    auth, auth, response([1], headers={'ETag': '\"stock\"'})]):
                with github_api.Session(self.c, persistent=True):
                    commands.gh(self.c, 'api', 'issues')
            self.assertNotIn('ghp_stock_private', (Path(self.c['_state']) / 'github-api.json').read_text())
            with patch.object(commands, '_execute', side_effect=[auth, auth, response(status=304)]):
                with github_api.Session(self.c, persistent=True):
                    self.assertEqual(commands.gh(self.c, 'api', 'issues'), [1])


if __name__ == '__main__':
    unittest.main()

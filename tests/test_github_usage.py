import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fleet import commands, github_api, github_usage, state
from fleet.review_bot_api import GitHub, APIError


class UsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name, 'root': directory.name}
        self.token = github_usage._ACTIVE.set(None)
        self.addCleanup(github_usage._ACTIVE.reset, self.token)

    def test_rest_pages_304_graphql_and_failed_attempts_count_once(self):
        github_usage.configure(self.c, 'watch-pr', pr=7, ref='TASK-143')
        replies = [subprocess.CompletedProcess([], 0,
                   'HTTP/2.0 200 OK\nLink: <https://api.github.com/next>; rel="next"\n\n[]',
                   '> GET /first HTTP/1.1\n< HTTP/2.0 200 OK\nsecret body'),
                   subprocess.CompletedProcess([], 0, 'HTTP/2.0 200 OK\n\n[]',
                   '> GET /next HTTP/1.1\n< HTTP/2.0 200 OK'),
                   subprocess.CompletedProcess([], 0, 'HTTP/2.0 304 Not Modified\n\n',
                   '> GET /next HTTP/1.1\n< HTTP/2.0 304 Not Modified'),
                   subprocess.CompletedProcess([], 1, '',
                   '> POST /graphql HTTP/1.1\n< HTTP/2.0 403 Forbidden\nsecret body')]
        # 304 without an ETag cache is deliberately an error, but still consumed a request.
        with patch.object(commands.subprocess, 'run', side_effect=replies), github_api.Session(self.c):
            self.assertEqual(commands.gh(self.c, 'api', '--paginate', '--slurp', 'first'), [[], []])
            with self.assertRaises(commands.CommandError):
                commands.gh(self.c, 'api', 'next')
            with self.assertRaises(commands.GhError):
                commands.gh(self.c, 'api', 'graphql', '-f', 'query=secret')
        rows = github_usage.consumers(self.c)['consumers']
        self.assertEqual([(r['rest'], r['graphql']) for r in rows], [(3, 1)])
        self.assertEqual(rows[0]['pr'], 7)
        self.assertEqual(rows[0]['ref'], 'TASK-143')
        self.assertNotIn('secret', json.dumps(state.read(self.c, 'github-usage')))

    def test_http_failures_count_without_exposing_credentials(self):
        github_usage.configure(self.c, 'arm', pr=7)
        client = GitHub('secret-token')
        with patch.object(client.opener, 'open', side_effect=OSError('secret')), self.assertRaises(APIError):
            client.request('POST', '/app/installations/1/access_tokens')
        self.assertEqual(github_usage.consumers(self.c)['consumers'][0]['rest'], 1)
        self.assertNotIn('secret', json.dumps(state.read(self.c, 'github-usage')))

    def test_timeout_partial_trace_is_counted_and_never_reported(self):
        github_usage.configure(self.c, 'status')
        failure = subprocess.TimeoutExpired(['gh'], 1,
                    stderr=b'> POST /graphql HTTP/1.1\nsecret-token secret body')
        with patch.object(commands.subprocess, 'run', side_effect=failure):
            with self.assertRaises(commands.CommandError) as error:
                commands._execute(['gh', 'api', 'graphql'])
        self.assertNotIn('secret', str(error.exception))
        self.assertEqual(github_usage.consumers(self.c)['consumers'][0]['graphql'], 1)

    def test_hour_window_separates_processes_caps_and_reports_truncation(self):
        with patch.object(github_usage.time, 'time', return_value=7200):
            github_usage.configure(self.c, 'supervisor')
            github_usage.record(rest=10)
            with patch.object(github_usage, '_PROCESS', 'other'):
                github_usage.configure(self.c, 'watch-pr', pr=7)
                github_usage.record(graphql=4)
            self.assertEqual([r['rest'] + r['graphql'] for r in github_usage.consumers(self.c)['consumers']], [10, 4])
        with patch.object(github_usage.time, 'time', return_value=10801):
            self.assertEqual(github_usage.consumers(self.c)['consumers'], [])
            with patch.object(github_usage, 'MAX_ROWS', 1):
                github_usage.configure(self.c, 'status')
                github_usage.record(rest=1)
                with patch.object(github_usage, '_PROCESS', 'third'):
                    github_usage.configure(self.c, 'arm', pr=9)
                    github_usage.record(rest=1)
            self.assertEqual(len(state.read(self.c, 'github-usage')['rows']), 1)
            self.assertTrue(github_usage.consumers(self.c)['truncated'])

    def test_accounting_failure_cannot_turn_successful_mutation_into_retry(self):
        github_usage.configure(self.c, 'arm', pr=7)
        result = subprocess.CompletedProcess([], 0, '{}', '> POST /graphql HTTP/1.1\nsecret')
        stderr = io.StringIO()
        with patch.object(commands.subprocess, 'run', return_value=result), patch.object(
                github_usage.state, 'write', side_effect=OSError('secret')), patch('sys.stderr', stderr):
            self.assertEqual(commands.gh(self.c, 'api', 'graphql'), {})
        self.assertIn('attribution unavailable', stderr.getvalue())
        self.assertNotIn('secret', stderr.getvalue())

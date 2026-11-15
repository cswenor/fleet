import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fleet import commands, github_api, github_usage, snapshot, state
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
        # An uncached 304 is still an outbound attempt, but not a REST charge.
        with patch.object(commands.subprocess, 'run', side_effect=replies), github_api.Session(self.c):
            self.assertEqual(commands.gh(self.c, 'api', '--paginate', '--slurp', 'first'), [[], []])
            with self.assertRaises(commands.CommandError):
                commands.gh(self.c, 'api', 'next')
            with self.assertRaises(commands.GhError):
                commands.gh(self.c, 'api', 'graphql', '-f', 'query=secret')
        rows = github_usage.consumers(self.c)['consumers']
        self.assertEqual([(r['rest'], r['graphql'], r['not_modified']) for r in rows], [(2, 1, 1)])
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

    def test_trace_pairs_paginated_statuses_without_discounting_graphql(self):
        github_usage.configure(self.c, 'status')
        github_usage.trace(
            '* Request at 2026-11-15 12:00:00\n'
            '* Request to https://api.github.com/secret?page=1\n'
            '> GET /secret?page=1 HTTP/1.1\n> Authorization: secret-token\n'
            '< HTTP/2.0 200 OK\n< Etag: secret\n\nsecret body\n'
            '* Request took 10ms\n'
            '* Request to https://api.github.com/secret?page=2\n'
            '> GET /secret?page=2 HTTP/1.1\n< HTTP/2.0 304 Not Modified\n'
            '* Request took 10ms\n'
            '> POST /graphql HTTP/1.1\n< HTTP/2.0 304 Not Modified\n')
        row = github_usage.consumers(self.c)['consumers'][0]
        self.assertEqual((row['rest'], row['graphql'], row['not_modified']), (1, 1, 1))
        self.assertNotIn('secret', json.dumps(state.read(self.c, 'github-usage')))

    def test_unknown_failed_and_ambiguous_responses_remain_charged(self):
        cases = [
            ('> GET /secret HTTP/1.1\n', 1),
            ('> GET /secret HTTP/1.1\n< error: secret\n', 1),
            ('> GET /secret HTTP/1.1\n< HTTP/2.0 503 Unavailable\n', 1),
            ('< HTTP/2.0 304 Not Modified\n> GET /secret HTTP/1.1\n', 1),
            ('> GET /secret HTTP/1.1\n< HTTP/2.0 unknown\n', 1),
            ('> GET /secret HTTP/1.1\n> GET /other HTTP/1.1\n'
             '< HTTP/2.0 304 Not Modified\n* Request took 10ms\n'
             '< HTTP/2.0 200 OK\n', 2),
        ]
        for trace, charged in cases:
            with self.subTest(trace=trace), patch.object(github_usage, 'record') as record:
                github_usage.trace(trace.encode())
                record.assert_called_once_with(charged, 0, not_modified=0, identity='stock')

    def test_informational_status_does_not_consume_final_response(self):
        with patch.object(github_usage, 'record') as record:
            github_usage.trace('> GET /secret HTTP/1.1\r\n'
                               '< HTTP/1.1 100 Continue\r\n< HTTP/1.1 304 Not Modified\r\n')
            record.assert_called_once_with(0, 0, not_modified=1, identity='stock')

    def test_not_modified_aggregates_across_minutes_and_displays_separately(self):
        github_usage.configure(self.c, 'supervisor')
        with patch.object(github_usage.time, 'time', return_value=7200):
            github_usage.record(rest=2, not_modified=3)
        with patch.object(github_usage.time, 'time', return_value=7260):
            github_usage.record(graphql=1, not_modified=4)
            usage = github_usage.consumers(self.c)
        self.assertEqual(len(usage['consumers']), 1)
        row = usage['consumers'][0]
        self.assertEqual((row['rest'], row['graphql'], row['not_modified']), (2, 1, 7))
        rendered = snapshot.render({'repo': self.c['repo'], 'errors': [], 'github_usage': usage})
        self.assertIn('REST charged=2 304=7 GraphQL=1', rendered)

    def test_legacy_row_without_not_modified_remains_readable_and_writable(self):
        github_usage.configure(self.c, 'status')
        with patch.object(github_usage.time, 'time', return_value=7200):
            github_usage.record(rest=2)
            saved = state.read(self.c, 'github-usage')
            del saved['rows'][0]['not_modified']
            state.write(self.c, 'github-usage', saved)
            self.assertEqual(github_usage.consumers(self.c)['consumers'][0]['not_modified'], 0)
            github_usage.record(rest=1, not_modified=4)
            row = github_usage.consumers(self.c)['consumers'][0]
        self.assertEqual((row['rest'], row['not_modified']), (3, 4))

    def test_direct_client_observed_304_is_counted_once_without_changing_error(self):
        github_usage.configure(self.c, 'arm', pr=7)
        client = GitHub('secret-token', identity='app_installation:42')
        for path in ('/secret', '/graphql'):
            with self.subTest(path=path):
                failure = HTTPError('https://api.github.com' + path, 304, 'secret', {}, None)
                with patch.object(client.opener, 'open', side_effect=failure):
                    with self.assertRaises(APIError) as error:
                        client.request('GET', path)
                self.assertEqual(error.exception.status, 304)
        row = github_usage.consumers(self.c)['consumers'][0]
        self.assertEqual((row['rest'], row['graphql'], row['not_modified']), (0, 1, 1))
        self.assertEqual(row['identity'], 'app_installation:42')
        self.assertNotIn('secret', json.dumps(state.read(self.c, 'github-usage')))

    def test_direct_client_success_and_unknown_status_keep_mutation_results(self):
        github_usage.configure(self.c, 'arm', pr=7)
        client = GitHub('secret-token')
        for status in (200, 304, None):
            with self.subTest(status=status):
                response = io.BytesIO(b'{"ok": true}')
                if status is not None:
                    response.status = status
                with patch.object(client.opener, 'open', return_value=response):
                    self.assertEqual(client.request('POST', '/secret', {'secret': True}), {'ok': True})
        row = github_usage.consumers(self.c)['consumers'][0]
        self.assertEqual((row['rest'], row['graphql'], row['not_modified']), (2, 0, 1))
        self.assertNotIn('secret', json.dumps(state.read(self.c, 'github-usage')))

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

    def test_identity_pools_and_legacy_unknown_rows_do_not_coalesce(self):
        github_usage.configure(self.c, 'status')
        github_usage.record(rest=1)
        legacy = state.read(self.c, 'github-usage')
        del legacy['rows'][0]['identity']
        state.write(self.c, 'github-usage', legacy)
        github_usage.record(rest=2)
        github_usage.record(rest=3, identity='app_installation:42')
        totals = {row['identity']: row['rest']
                  for row in github_usage.consumers(self.c)['consumers']}
        self.assertEqual(totals, {'unknown': 1, 'stock': 2, 'app_installation:42': 3})

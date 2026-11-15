import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import Mock, patch

from fleet import commands, github_api, github_auth, github_usage, snapshot


class ReadAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c = {'repo': 'owner/repo', 'root': self.directory.name,
                  '_state': self.directory.name,
                  'github': {'read_auth': {'app_env_file': '/private/app.env', 'installation_id': 42}}}
        github_auth._TOKENS.clear()
        self.addCleanup(github_auth._TOKENS.clear)
        self.values = {'BUILDER_GITHUB_APP_ID': '7'}
        self.client = Mock()
        self.client.request.side_effect = self.request
        self.issued = 0
        self.installation = {'id': 42, 'app_id': 7, 'repository_selection': 'selected',
                             'account': {'login': 'owner'}, 'suspended_at': None,
                             'permissions': {'contents': 'write', 'issues': 'write', 'metadata': 'read',
                                             'pull_requests': 'read', 'checks': 'read', 'statuses': 'read',
                                             'actions': 'read', 'merge_queues': 'read'}}
        self.repositories = {'total_count': 1, 'repositories': [{'full_name': 'owner/repo'}]}
        self.scope = Mock()
        self.scope.request.side_effect = lambda method, path: self.repositories if method == 'GET' else None
        for target, kwargs in [
                ('fleet.github_auth.read_environment', {'return_value': self.values}),
                ('fleet.github_auth.app_client', {'return_value': self.client}),
                ('fleet.github_auth.GitHub', {'return_value': self.scope}),
                ('fleet.github_auth.time.time', {'return_value': 1000}),
                ('os.environ', {'GH_TOKEN': 'ghp_stock', 'GH_HOST': 'github.com'})]:
            p = patch.dict(target, kwargs) if target == 'os.environ' else patch(target, **kwargs)
            started = p.start()
            self.addCleanup(p.stop)
            if target.endswith('time.time'):
                self.clock = started

    def request(self, method, path, body=None):
        if method == 'GET':
            return self.installation
        self.issued += 1
        expiry = datetime.fromtimestamp(self.clock.return_value + 3600, timezone.utc).isoformat()
        return {'token': f'ghs_app_{self.issued}', 'expires_at': expiry}

    def test_cached_token_expires_and_refreshes_with_read_only_permissions(self):
        env, auth = github_auth.resolve(self.c)
        self.assertEqual(env['GH_TOKEN'], 'ghs_app_2')
        self.assertEqual(env['GITHUB_TOKEN'], 'ghs_app_2')
        self.assertEqual(auth, {'kind': 'app_installation', 'installation_id': 42})
        self.assertEqual(github_auth.resolve(self.c)[0]['GH_TOKEN'], 'ghs_app_2')
        self.assertEqual(self.issued, 2)
        self.assertEqual(self.client.request.call_args.args[2],
                         {'permissions': {'contents': 'read', 'issues': 'read', 'metadata': 'read',
                                          'pull_requests': 'read', 'checks': 'read', 'statuses': 'read',
                                          'actions': 'read', 'merge_queues': 'read'}})
        self.clock.return_value = 4540
        self.assertEqual(github_auth.resolve(self.c)[0]['GH_TOKEN'], 'ghs_app_4')
        self.assertEqual(self.issued, 4)
        self.scope.request.assert_any_call('DELETE', '/installation/token')

    def test_installation_boundaries_and_errors_fall_back_without_secret_details(self):
        for field, value in [('id', 99), ('app_id', 9), ('repository_selection', 'all'),
                             ('suspended_at', 'today'), ('account', {'login': 'other'})]:
            with self.subTest(field=field):
                original = self.installation[field]
                self.installation[field] = value
                env, auth = github_auth.resolve(self.c)
                self.assertIsNone(env)
                self.assertEqual(auth['kind'], 'stock')
                self.installation[field] = original
        self.repositories['total_count'] = 2
        self.assertIsNone(github_auth.resolve(self.c)[0])
        self.scope.request.assert_any_call('DELETE', '/installation/token')
        with patch('fleet.github_auth.app_client', side_effect=ValueError('private-key-secret')):
            self.assertNotIn('private-key-secret', json.dumps(github_auth.resolve(self.c)))

    def test_cache_keys_isolate_repo_installation_and_credentials(self):
        github_auth.resolve(self.c)
        other = copy.deepcopy(self.c)
        other['repo'] = 'owner/other'
        self.repositories['repositories'][0]['full_name'] = 'owner/other'
        self.assertEqual(github_auth.resolve(other)[0]['GH_TOKEN'], 'ghs_app_4')
        self.repositories['repositories'][0]['full_name'] = 'owner/repo'
        self.c['github']['read_auth']['installation_id'] = 43
        self.installation['id'] = 43
        self.assertEqual(github_auth.resolve(self.c)[0]['GH_TOKEN'], 'ghs_app_6')
        self.values['BUILDER_GITHUB_APP_ID'] = '8'
        self.installation['app_id'] = 8
        self.assertEqual(github_auth.resolve(self.c)[0]['GH_TOKEN'], 'ghs_app_8')

    def test_reads_override_cwd_credentials_but_writes_and_mutations_do_not(self):
        executable = Path(self.directory.name) / 'gh'
        executable.write_text(f'#!{sys.executable}\n' + r'''
import json, os, sys
if '-i' in sys.argv:
    print('HTTP/2.0 200 OK\r\nX-RateLimit-Limit: 15000\r\nX-RateLimit-Remaining: 14999\r\nX-RateLimit-Reset: 6000\r\n\r')
print(json.dumps({'actor': os.environ['GH_TOKEN']}))
if os.environ.get('GH_DEBUG') == 'api':
    sys.stderr.write('> POST /graphql HTTP/1.1\n' if 'graphql' in sys.argv else '> GET /fixture HTTP/1.1\n')
''')
        executable.chmod(0o700)
        usage_token = github_usage.configure(self.c, 'status')
        self.addCleanup(github_usage.reset, usage_token)
        with patch.dict(os.environ, {'PATH': self.directory.name + os.pathsep + os.environ['PATH']}), \
                patch('fleet.github_usage.enabled', return_value=True):
            for root in (self.directory.name, '/'):
                self.c['root'] = root
                self.assertEqual(commands.gh(self.c, 'api', 'repos/owner/repo')['actor'], 'ghs_app_2')
            with github_api.Session(self.c):
                self.assertEqual(commands.gh(self.c, 'api', 'graphql', '-f', 'query=query{viewer{login}}')['actor'], 'ghs_app_2')
                self.assertEqual(commands.gh(self.c, 'api', 'graphql', '-f', 'query=mutation{doThing}')['actor'], 'ghp_stock')
                self.assertEqual(commands.gh(self.c, 'api', '-X', 'POST', 'repos/owner/repo/issues')['actor'], 'ghp_stock')
            self.assertEqual(os.environ['GH_TOKEN'], 'ghp_stock')
            result = {'errors': []}
            with patch.object(snapshot.github, 'collect', side_effect=lambda c: commands.gh(c, 'api', 'repos/owner/repo')):
                snapshot._github(self.c, result)
            self.assertEqual(result['github_auth']['kind'], 'app_installation')
            self.assertIn('REST budget 14999/15000', snapshot.render({'repo': 'owner/repo', **result}))
            consumers = {row['identity']: (row['rest'], row['graphql'])
                         for row in github_usage.consumers(self.c)['consumers']}
            self.assertEqual(consumers, {'app_installation:42': (3, 1), 'stock': (1, 1)})

    def test_identity_switch_discards_cached_body_and_backoff(self):
        with github_api.Session(self.c, persistent=True) as session:
            with patch.object(commands, '_execute', return_value=subprocess.CompletedProcess([], 0,
                    'HTTP/2.0 200 OK\r\nETag: private\r\n\r\n[1]', '')):
                commands.gh(self.c, 'api', 'issues')
            session.blocked_until = 3000
            with patch('fleet.github_auth.read_environment', side_effect=ValueError('secret')):
                with patch.object(commands, '_execute', return_value=subprocess.CompletedProcess([], 0,
                        'HTTP/2.0 200 OK\r\n\r\n[2]', '')) as execute:
                    self.assertEqual(commands.gh(self.c, 'api', 'issues'), [2])
                    self.assertNotIn('If-None-Match: private', execute.call_args.args[0])
                    self.assertNotIn('env', execute.call_args.kwargs)
                    self.assertEqual(session.auth['kind'], 'stock')
        self.assertNotIn('ghs_app', (Path(self.directory.name) / 'github-api.json').read_text())

    def test_app_token_never_reaches_external_host(self):
        with github_api.Session(self.c), patch.object(commands, '_execute') as execute:
            with self.assertRaisesRegex(commands.CommandError, 'GitHub API host'):
                commands.gh(self.c, 'api', 'https://other.example/repos/owner/repo')
            execute.assert_not_called()

    def test_unusable_expiry_never_enters_token_cache(self):
        for expires in (None, 123, 'invalid', '1970-01-01T00:00:01Z'):
            with self.subTest(expires=expires):
                self.client.request.side_effect = lambda method, path, body=None: (
                    self.installation if method == 'GET' else
                    {'token': 'ghs_too_old', 'expires_at': expires})
                self.assertIsNone(github_auth.resolve(self.c)[0])
                self.assertFalse(github_auth._TOKENS)

    def test_missing_required_grants_name_permissions_and_fall_back_before_collection(self):
        del self.installation['permissions']['statuses']
        self.installation['permissions']['checks'] = 'none'
        env, auth = github_auth.resolve(self.c)
        self.assertIsNone(env)
        self.assertEqual(auth, {'kind': 'stock',
                               'fallback': 'read_auth App lacks checks:read, statuses:read'})
        self.assertEqual(self.issued, 0)
        self.assertFalse(github_auth._TOKENS)
        with patch.object(snapshot.github, 'collect', return_value={'prs': []}):
            result = {'errors': []}
            snapshot._github(self.c, result)
        self.assertEqual(result['github_auth'], auth)
        self.assertIn('read_auth App lacks checks:read, statuses:read',
                      snapshot.render({'repo': 'owner/repo', **result}))

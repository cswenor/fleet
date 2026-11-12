import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch

from fleet.review_bot_deploy import _BIND_ISOLATION, deploy, docker, topology
from fleet.review_bot_ruleset import desired_ruleset


class DeploymentTests(unittest.TestCase):
    def config(self):
        return {'root': '/checkout', 'repo': 'example/project', 'review_bot': {
            'webhook_url': 'https://review.example/github',
            'deployment': {'backend': 'compose', 'docker_context': 'explicit-context',
                           'directory': '/private/instance', 'health_url': 'https://review.example/health',
                           'control_env_file': '/private/control.env', 'broker_env_file': '/private/broker.env',
                           'gateway_env_file': '/private/gateway.env', 'runner_config_dir': '/private/config',
                           'broker_auth_dir': '/private/auth'}}}

    @unittest.skipUnless(shutil.which('node'), 'Node is required to exercise the target probe')
    def test_kernel_bind_identity_rejects_symlink_aliases_nested_mounts_and_unknown_roots(self):
        def mounts(auth='/srv/vault', extra='', device='0:1'):
            return ('1 0 0:1 /srv/runtime /probe/instance ro - ext4 /dev/test ro\n'
                    f'2 0 {device} {auth} /probe/auth ro - ext4 /dev/test ro\n'
                    '3 0 0:1 /etc/policy /probe/config ro - ext4 /dev/test ro\n' + extra)
        cases = [mounts(), mounts('/srv/runtime/work/vault'), mounts('/srv/runtime'),
                 mounts('/srv/runtime-old'), mounts('/vault', device='0:2',
                     extra='4 1 0:2 /vault /probe/instance/work/provider ro - ext4 /dev/other ro\n'),
                 mounts().replace('/probe/config', '/missing-config'),
                 mounts().replace('0:1 /srv/vault', 'unknown /srv/vault')]
        script = _BIND_ISOLATION + '\nprocess.stdout.write(JSON.stringify(JSON.parse(require("fs").readFileSync(0,"utf8")).map(isolatedMounts)));'
        result = subprocess.run(['node', '-e', script], input=json.dumps(cases),
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), [True, False, False, True, False, False, False])

    def test_bootstrap_plan_is_read_only_and_reports_missing_authority(self):
        c = self.config()
        with patch('fleet.review_bot_deploy.docker', side_effect=AssertionError('daemon access')), \
                patch('fleet.review_bot_deploy.read_environment', side_effect=AssertionError('secret access')), \
                patch('fleet.review_bot_deploy.GitHub.owner', side_effect=AssertionError('GitHub access')):
            result = deploy(c)
        self.assertFalse(result['ready'])
        self.assertFalse(result['apply'])
        self.assertEqual(set(result['compose']['services']), {'control', 'runner', 'broker', 'gateway'})
        self.assertIsNone(result['compose']['services']['broker']['image'])

    def test_incomplete_apply_refuses_before_any_side_effect(self):
        with patch('fleet.review_bot_deploy.docker', side_effect=AssertionError('daemon access')):
            with self.assertRaises(ValueError):
                deploy(self.config(), apply=True)

    def test_approve_plan_and_apply_refuse_missing_live_protection(self):
        c = self.config()
        c['review_bot']['deployment']['mode'] = 'approve'
        result = deploy(c, github=object())
        self.assertEqual(result['mode'], 'approve')
        self.assertTrue(any('approve deployment blocked' in item for item in result['blockers']))
        with patch('fleet.review_bot_deploy.docker', side_effect=AssertionError('daemon access')):
            with self.assertRaises(ValueError):
                deploy(c, apply=True, github=object())
        c['review_bot']['deployment']['mode'] = 'off'
        with self.assertRaises(ValueError):
            deploy(c)

    def test_anonymous_pull_ignores_credentials_preserves_endpoint_and_removes_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credentials = root / 'credentials'
            credentials.mkdir()
            (credentials / 'config.json').write_text('{"auths":{"ghcr.io":{"auth":"bogus"}}}')
            executable = root / 'docker'
            executable.write_text(
                '#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n'
                'if sys.argv[1:3] == ["--context", "owned-context"]:\n'
                ' print("ssh://configured-target")\n'
                'else:\n'
                ' p = Path(os.environ["DOCKER_CONFIG"])\n'
                ' assert not list(p.iterdir())\n'
                ' assert sys.argv[1:3] == ["--host", "ssh://configured-target"]\n'
                ' print(str(p))\n')
            executable.chmod(0o700)
            with patch.dict(os.environ, {'PATH': directory + os.pathsep + os.environ['PATH'],
                                         'DOCKER_CONFIG': str(credentials)}):
                temporary = docker('owned-context', ['image', 'pull', 'public-pin'], anonymous=True).strip()
            self.assertFalse(Path(temporary).exists())
            self.assertEqual(json.loads((credentials / 'config.json').read_text())['auths']['ghcr.io']['auth'], 'bogus')

    def test_overlapping_target_mounts_cannot_expose_the_provider_vault(self):
        for key, value in (('broker_auth_dir', '/private/instance/work/auth'),
                           ('runner_config_dir', '/private/auth/config')):
            c = self.config()
            c['review_bot']['deployment'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                deploy(c)

    def test_four_service_topology_isolated_native_and_unpublished(self):
        result = topology(self.config())
        services = result['services']
        self.assertEqual(services['runner']['networks'], ['review'])
        self.assertTrue(result['networks']['review']['internal'])
        self.assertTrue(result['networks']['broker']['internal'])
        self.assertTrue(result['networks']['ingress']['external'])
        self.assertEqual(services['control']['environment']['MERGE_LANE_ENABLED'], 'false')
        self.assertEqual(services['control']['environment']['MERGE_BATCH_ENABLED'], 'false')
        self.assertEqual(services['control']['environment']['REVIEW_BOT_MODE'], 'advisory')
        self.assertEqual(services['runner']['volumes'][0]['target'], '/work')
        self.assertTrue(services['runner']['volumes'][0]['read_only'])
        for service in services.values():
            self.assertNotIn('ports', service)
            self.assertNotIn('privileged', service)
            self.assertTrue(service['read_only'])
            self.assertEqual(service['cap_drop'], ['ALL'])
            for mount in service.get('volumes', []):
                self.assertFalse(mount['bind']['create_host_path'])
                self.assertNotIn('docker.sock', mount['source'])

    def test_docker_requires_context_and_sanitizes_failures(self):
        with self.assertRaises(ValueError):
            docker('', ['version'])
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'docker'
            executable.write_text('#!/bin/sh\nprintf "secret-daemon-error" >&2\nexit 1\n')
            executable.chmod(0o700)
            with patch.dict(os.environ, {'PATH': directory}):
                with self.assertRaises(ValueError) as raised:
                    docker('owned-context', ['version'])
            self.assertNotIn('secret-daemon-error', str(raised.exception))

    def test_docker_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'docker'
            executable.write_text('#!/bin/sh\nwhile :; do printf "1234567890123456789012345678901234567890"; done\n')
            executable.chmod(0o700)
            with patch.dict(os.environ, {'PATH': directory}), patch('fleet.review_bot_deploy._LIMIT', 100):
                with self.assertRaises(ValueError):
                    docker('owned-context', ['version'])

    def test_apply_keeps_role_credentials_private_and_rejects_builder_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            c = self.config()
            bot = c['review_bot']
            bot['source'] = {'repository': 'example/review-bot', 'version': 'v1.0.0',
                             'commit': 'a' * 40, 'image': 'ghcr.io/example/review-bot@sha256:' + 'b' * 64}
            bot['apps'] = {'owner': 'example', 'owner_type': 'organization'}
            control = {
                'GITHUB_APP_ID': '1', 'GITHUB_APP_PRIVATE_KEY_B64': 'review-key-private',
                'GITHUB_WEBHOOK_SECRET': 'webhook-private', 'OMP_GATEWAY_TOKEN': 'gateway-private',
                'TYPESAFE_API_KEY': 'judge-$private', 'REVIEW_BOT_LOGIN': 'project-review[bot]',
                'REVIEW_BOT_USER_ID': '100', 'BUILDER_BOT_LOGIN': 'project-builder[bot]',
                'BUILDER_BOT_USER_ID': '200', 'REVIEW_APPROVERS': 'owner:300'}
            def environment(name, values):
                path = Path(directory) / (name + '.env')
                path.write_text(''.join(key + '=' + value + '\n' for key, value in values.items()))
                path.chmod(0o600)
                return str(path)
            for index, role in enumerate(('review', 'builder'), 1):
                bot['apps'][role] = {'name': 'project-' + role, 'slug': 'project-' + role,
                                     'id': index, 'installation_id': index + 10,
                                     'env_file': environment(role, control if role == 'review' else {'BUILDER_KEY': 'builder-private'})}
            deployment = bot['deployment']
            deployment['ingress_network'] = 'existing-proxy'
            values = {'control': control, 'runner': {'OMP_GATEWAY_TOKEN': 'gateway-private'},
                      'broker': {'OMP_AUTH_BROKER_TOKEN': 'broker-private'},
                      'gateway': {'OMP_AUTH_BROKER_TOKEN': 'broker-private', 'OMP_GATEWAY_TOKEN': 'gateway-private'}}
            for role, env in values.items():
                deployment[role + '_env_file'] = environment(role + '-runtime', env)
            deployment['mode'] = 'approve'
            bot['ruleset'] = {'id': 99, 'required_checks': [{'context': 'CI', 'integration_id': 123}]}
            live = {**desired_ruleset(c), 'id': 99, 'source_type': 'Repository', 'source': c['repo']}
            class Owner:
                def request(self, method, path):
                    if path.endswith('/rulesets/99'):
                        return live
                    if '/rulesets?' in path:
                        return [{'id': 99}] if 'page=1' in path else []
                    return {'full_name': c['repo'], 'default_branch': 'main', 'allow_auto_merge': True}
            owner = Owner()
            documents = []
            def daemon(context, args, *, input=None):
                if args[0] == 'context':
                    return 'ssh://configured-target'
                if args[0] == 'network':
                    return 'bridge'
                if args[0] == 'compose':
                    documents.append(json.loads(input))
                return ''
            with patch('fleet.review_bot_deploy.docker', daemon), \
                    patch('fleet.review_bot_deploy.verify_pin'), \
                    patch('fleet.review_bot_deploy.app_client'), \
                    patch('fleet.review_bot_deploy.verify_app'), \
                    patch('fleet.review_bot_deploy.verify_installation'), \
                    patch('fleet.review_bot_deploy._probe_target'):
                result = deploy(c, apply=True, github=owner)
                self.assertTrue(result['deployed'])
                self.assertNotIn('gateway-private', json.dumps(result))
                self.assertNotIn('review-key-private', json.dumps(result))
                services = documents[0]['services']
                self.assertEqual(services['control']['environment']['TYPESAFE_API_KEY'], 'judge-$$private')
                self.assertNotIn('GITHUB_APP_PRIVATE_KEY_B64', services['runner']['environment'])
                self.assertNotIn('TYPESAFE_API_KEY', services['gateway']['environment'])
                self.assertEqual(services['runner']['environment']['OMP_GATEWAY_TOKEN'], 'gateway-private')
                self.assertEqual(services['control']['environment']['REVIEW_BOT_MODE'], 'approve')
                self.assertEqual(services['control']['environment']['MERGE_LANE_ENABLED'], 'false')
                self.assertEqual(services['control']['environment']['MERGE_BATCH_ENABLED'], 'false')
                live['bypass_actors'] = [{'actor_id': 1, 'actor_type': 'Integration', 'bypass_mode': 'always'}]
                with self.assertRaises(ValueError):
                    deploy(c, apply=True, github=owner)
                live['bypass_actors'] = []
                environment('runner-runtime', {**values['runner'], 'BUILDER_GITHUB_APP_PRIVATE_KEY_B64': 'builder-private'})
                with self.assertRaises(ValueError):
                    deploy(c, apply=True, github=owner)
            self.assertEqual(len(documents), 1)

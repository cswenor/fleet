from copy import deepcopy
from contextlib import redirect_stderr
from http.client import HTTPConnection
from io import StringIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import yaml

from fleet import config
from fleet.review_bot_api import APIError, GitHub
from fleet.review_bot_apps import ManifestCallback, initialize, manifest, verify_app, verify_installation
from fleet.review_bot_files import create_environment, private_parent, read_config, save_config
from fleet.review_bot_ruleset import desired_ruleset, ruleset, verify_live_ruleset


class ReviewBotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.private = self.base / 'private'
        self.private.mkdir(mode=0o700)
        self.path = self.private / 'fleet.yml'
        self.raw = {
            'version': 1, 'repo': 'owner/project', 'root': str(self.root), 'orca': '/orca',
            'orca_repo_id': 'repo', 'pad_workspace': 'workspace',
            'builder': {'model': 'provider/model', 'resolved_model': 'provider/model',
                        'rules_file': 'RULES.md', 'slots': [str(self.root)]},
            'coordinator': {'run': 'run', 'terminal': 'main'},
            'review_bot': {
                'apps': {'owner': 'owner', 'owner_type': 'organization',
                         'review': {'name': 'project-review', 'env_file': str(self.private / 'review.env')},
                         'builder': {'name': 'project-builder', 'env_file': str(self.private / 'builder.env')}},
                'webhook_url': 'https://review.example/github',
                'ruleset': {'required_checks': [{'context': 'CI', 'integration_id': 15368}]},
            },
        }

    def load(self):
        self.path.write_text(yaml.safe_dump(self.raw))
        self.path.chmod(0o600)
        c = config.load(self.path)
        c['_state'] = str(self.private / 'state')
        return c

    def source(self):
        return {'repository': 'example/review-bot', 'version': 'v1.2.3', 'commit': 'a' * 40,
                'image': 'ghcr.io/example/review-bot@sha256:' + 'b' * 64}

    def test_source_pin_rejects_mutable_partial_and_cross_repository_images(self):
        valid = self.source()
        for changed in ({'image': 'ghcr.io/example/review-bot:v1.2.3'},
                        {'image': 'ghcr.io/other/review-bot@sha256:' + 'b' * 64},
                        {'version': 'v01.2.3'}, {'commit': 'main'}):
            self.raw['review_bot']['source'] = {**valid, **changed}
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.load()
        self.raw['review_bot']['source'] = {'version': 'v1.2.3'}
        with self.assertRaises(ValueError):
            self.load()

    def test_app_credentials_cannot_cross_roles_checkout_or_owner(self):
        for mutate in (
                lambda bot: bot['apps']['builder'].update(env_file=bot['apps']['review']['env_file']),
                lambda bot: bot['apps']['review'].update(env_file=str(self.root / 'secret.env')),
                lambda bot: bot['apps'].update(owner='another-owner'),
                lambda bot: [bot['apps'][role].update(id=1007, slug='project-' + role) for role in ('review', 'builder')],
                lambda bot: bot['ruleset'].update(required_checks=[{'context': 'app-approval', 'integration_id': 15368}])):
            original = deepcopy(self.raw)
            mutate(self.raw['review_bot'])
            with self.assertRaises(ValueError):
                self.load()
            self.raw = original

    def test_unrepresentable_app_identity_is_rejected_before_runtime_rounding(self):
        self.raw['review_bot']['apps']['review'].update(id=9007199254740992, slug='project-review')
        with self.assertRaisesRegex(ValueError, 'JavaScript-safe'):
            self.load()

    def test_private_credential_promotion_never_overwrites_existing_file(self):
        path = self.private / 'review.env'
        create_environment(path, {'GITHUB_APP_ID': '1007'})
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            create_environment(path, {'GITHUB_APP_ID': '1008'})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_credential_parent_in_another_checkout_is_rejected_before_mkdir(self):
        another = self.base / 'another-checkout'
        another.mkdir()
        (another / '.git').write_text('gitdir: /unused/worktree-metadata\n')
        destination = another / 'private' / 'secret.env'
        with self.assertRaisesRegex(ValueError, 'Git checkout'):
            private_parent(destination)
        self.assertFalse(destination.parent.exists())

    def test_atomic_config_write_preserves_an_intervening_owner_edit(self):
        c = self.load()
        document, encoded = read_config(c)
        document['publication'] = {'mode': 'auto_merge', 'review_app': 'project-review'}
        owner_edit = self.path.read_bytes() + b'owner_note: keep-this\n'
        self.path.write_bytes(owner_edit)
        with self.assertRaisesRegex(ValueError, 'changed'):
            save_config(c, document, encoded)
        self.assertEqual(self.path.read_bytes(), owner_edit)

    def test_merge_group_manifest_has_required_merge_queue_permission(self):
        payload = manifest({'name': 'project-review'}, 'review',
                           'https://review.example/github', 'http://127.0.0.1/callback')
        self.assertIn('merge_group', payload['default_events'])
        self.assertEqual(payload['default_permissions']['merge_queues'], 'read')

    def test_review_app_requires_read_only_merge_queue_grant(self):
        c = self.load()
        app = c['review_bot']['apps']['review']
        app.update(id=1007, slug='project-review')
        payload = manifest(app, 'review', c['review_bot']['webhook_url'], 'http://127.0.0.1/callback')
        remote = {'id': app['id'], 'slug': app['slug'], 'name': app['name'],
                  'owner': {'login': 'owner', 'type': 'Organization'},
                  'permissions': {**payload['default_permissions'], 'metadata': 'read'},
                  'events': payload['default_events']}
        class Client:
            def request(self, method, path):
                if path == '/app':
                    return deepcopy(remote)
                return {'url': c['review_bot']['webhook_url']}
        self.assertEqual(verify_app(c, 'review', Client()), remote)
        for grant in (None, 'write'):
            with self.subTest(grant=grant):
                if grant is None:
                    remote['permissions'].pop('merge_queues')
                else:
                    remote['permissions']['merge_queues'] = grant
                with self.assertRaisesRegex(ValueError, 'permissions'):
                    verify_app(c, 'review', Client())

    def test_recorded_app_extras_require_exact_live_permissions(self):
        extras = {'actions': 'read', 'checks': 'read', 'statuses': 'read', 'workflows': 'write'}
        for role, recorded in (('builder', extras), ('review', {'discussions': 'read'})):
            with self.subTest(role=role):
                self.raw['review_bot']['apps'][role]['extra_permissions'] = recorded
                c = self.load()
                app = c['review_bot']['apps'][role]
                app.update(id=1007, slug='project-' + role)
                payload = manifest(app, role, c['review_bot']['webhook_url'], 'http://127.0.0.1/callback')
                self.assertTrue(set(recorded).isdisjoint(payload['default_permissions']))
                remote = {'id': app['id'], 'slug': app['slug'], 'name': app['name'],
                          'owner': {'login': 'owner', 'type': 'Organization'},
                          'permissions': {**payload['default_permissions'], 'metadata': 'read'},
                          'events': payload['default_events']}

                class Client:
                    def request(self, method, path):
                        return deepcopy(remote) if path == '/app' else {'url': c['review_bot']['webhook_url']}

                with self.assertRaisesRegex(ValueError, 'permissions'):
                    verify_app(c, role, Client())
                remote['permissions'].update(recorded)
                self.assertEqual(verify_app(c, role, Client()), remote)
                app.pop('extra_permissions')
                with self.assertRaisesRegex(ValueError, 'permissions'):
                    verify_app(c, role, Client())
                for permission in recorded:
                    remote['permissions'].pop(permission)
                self.assertEqual(verify_app(c, role, Client()), remote)

    def test_extra_permission_policy_is_enforced_at_config_load(self):
        forbidden = ({'administration': 'read'}, {'organization_projects': 'read'},
                     {'organization_custom': 'write'}, {'members': 'read'},
                     {'secrets': 'read'}, {'environments': 'read'}, {'actions': 'write'},
                     {'metadata': 'write'})
        for role in ('review', 'builder'):
            for extras in forbidden:
                with self.subTest(role=role, extras=extras):
                    self.raw['review_bot']['apps'][role]['extra_permissions'] = extras
                    with self.assertRaisesRegex(ValueError, 'forbidden extra permission'):
                        self.load()
            for extras in (None, [], {'checks': 'admin'}, {'Bad-Key': 'read'},
                           {'pull_requests': 'read'}):
                with self.subTest(role=role, extras=extras):
                    self.raw['review_bot']['apps'][role]['extra_permissions'] = extras
                    with self.assertRaises(ValueError):
                        self.load()
            self.raw['review_bot']['apps'][role].pop('extra_permissions')
        for permission in ('contents', 'workflows', 'discussions', 'issues', 'statuses'):
            self.raw['review_bot']['apps']['review']['extra_permissions'] = {permission: 'write'}
            with self.subTest(permission=permission), self.assertRaisesRegex(ValueError, 'forbidden'):
                self.load()

    def test_manifest_callback_rejects_wrong_state_and_never_logs_one_use_code(self):
        flow = ManifestCallback({'name': 'project-review'}, 'review', 'owner', 'organization',
                                'https://review.example/github')
        outcome = []
        errors = StringIO()
        with redirect_stderr(errors):
            thread = threading.Thread(target=lambda: outcome.append(flow.wait(10)))
            thread.start()
            self.addCleanup(thread.join, 12)
            port = urlsplit(flow.start_url).port
            connection = HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('GET', urlsplit(flow.callback_url).path + '?state=wrong&code=one-use-fixture-code')
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.close()
            connection = HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('GET', urlsplit(flow.callback_url).path + '?state=' + flow.nonce + '&code=one-use-fixture-code')
            response = connection.getresponse()
            self.assertEqual(response.status, 303)
            self.assertEqual(response.getheader('Referrer-Policy'), 'no-referrer')
            self.assertNotIn('one-use-fixture-code', response.getheader('Location'))
            response.read()
            connection.close()
            thread.join(5)
        self.assertEqual(outcome, ['one-use-fixture-code'])
        self.assertNotIn('one-use-fixture-code', errors.getvalue())

    def test_conversion_errors_do_not_expose_secret_request_path_or_body(self):
        github = GitHub()
        failure = HTTPError('https://api.github.com/app-manifests/one-use-secret/conversions',
                            422, 'sensitive provider response', {}, None)
        with patch.object(github.opener, 'open', side_effect=failure):
            with self.assertRaises(APIError) as raised:
                github.request('POST', '/app-manifests/one-use-secret/conversions')
        self.assertEqual(raised.exception.status, 422)
        self.assertNotIn('one-use-secret', str(raised.exception))
        self.assertNotIn('sensitive', str(raised.exception))

    def test_selected_token_cannot_conceal_a_broad_app_installation(self):
        c = self.load()
        c['review_bot']['apps']['review']['id'] = 1007
        installation = {'id': 1008, 'app_id': 1007, 'repository_selection': 'selected',
                        'account': {'login': 'owner'}, 'suspended_at': None}
        class Client:
            def request(self, method, path, body=None):
                return deepcopy(installation) if method == 'GET' else {'token': 'synthetic-installation-token'}
        class Scoped:
            def request(self, method, path, body=None):
                if method == 'DELETE':
                    return None
                return {'total_count': 2, 'repositories': [{'full_name': 'owner/project'}, {'full_name': 'owner/another'}]}
        with patch('fleet.review_bot_apps.GitHub', return_value=Scoped()):
            with self.assertRaisesRegex(ValueError, 'ONLY'):
                verify_installation(c, 'review', Client())
        installation['repository_selection'] = 'all'
        with self.assertRaisesRegex(ValueError, 'selected-repository'):
            verify_installation(c, 'review', Client())

    def existing(self, c):
        desired = desired_ruleset(c)
        return {**desired, 'id': 1009, 'source_type': 'Repository', 'source': c['repo']}

    def test_existing_protection_cannot_be_weakened_by_ruleset_replacement(self):
        c = self.load()
        existing = self.existing(c)
        pull = existing['rules'][0]['parameters']
        pull['required_approving_review_count'] = 2
        with self.assertRaisesRegex(ValueError, 'reduce'):
            desired_ruleset(c, existing)
        pull['required_approving_review_count'] = 1
        pull['require_code_owner_review'] = True
        existing['rules'].append({'type': 'required_signatures'})
        planned = desired_ruleset(c, existing)
        self.assertTrue(planned['rules'][0]['parameters']['require_code_owner_review'])
        self.assertIn({'type': 'required_signatures'}, planned['rules'])
        existing['conditions']['ref_name']['include'].append('refs/heads/release')
        with self.assertRaisesRegex(ValueError, 'only literal'):
            desired_ruleset(c, existing)

    def test_ruleset_403_is_a_capability_blocker_not_absence(self):
        c = self.load()
        class Client:
            def request(self, method, path, body=None):
                if path == '/repos/owner/project':
                    return {'full_name': 'owner/project', 'default_branch': 'main'}
                raise APIError(403)
        result = ruleset(c, github=Client())
        self.assertTrue(any('GitHub403' in blocker for blocker in result['blockers']))
        with self.assertRaisesRegex(ValueError, 'GitHub403'):
            ruleset(c, apply=True, github=Client())

    def test_changed_live_rules_invalidate_a_previously_reviewed_apply_plan(self):
        c = self.load()
        c['review_bot']['source'] = self.source()
        for index, role in enumerate(('review', 'builder')):
            c['review_bot']['apps'][role].update(id=1007 + index, slug='project-' + role, installation_id=1010 + index)
        live = self.existing(c)
        c['review_bot']['ruleset']['id'] = live['id']
        class Client:
            def request(self, method, path, body=None):
                if method != 'GET':
                    raise AssertionError('stale plan must never mutate GitHub')
                if path == '/repos/owner/project':
                    return {'full_name': 'owner/project', 'default_branch': 'main', 'permissions': {'admin': True}}
                if '/rulesets?' in path:
                    return [{'id': live['id'], 'name': live['name']}]
                return deepcopy(live)
        self.assertEqual(ruleset(c, github=Client())['blockers'], [])
        live['rules'].append({'type': 'required_signatures'})
        with self.assertRaisesRegex(ValueError, 'current diff'):
            ruleset(c, apply=True, github=Client())

    def test_protection_drift_during_remote_verification_never_overwrites_owner_changes(self):
        for creating in (False, True):
            with self.subTest(creating=creating):
                self.raw['review_bot']['ruleset'].pop('id', None)
                c = self.installed()
                c['_state'] = str(self.private / ('create-drift' if creating else 'update-drift'))
                live = None if creating else self.existing(c)
                if live:
                    c['review_bot']['ruleset']['id'] = live['id']
                    self.raw['review_bot']['ruleset']['id'] = live['id']
                    self.path.write_text(yaml.safe_dump(self.raw))
                class Client:
                    def request(self, method, path, body=None):
                        if method != 'GET':
                            raise AssertionError('concurrent owner protection must not be overwritten')
                        if path == '/repos/owner/project':
                            return {'full_name': c['repo'], 'default_branch': 'main',
                                    'permissions': {'admin': True}}
                        if '/rulesets?' in path:
                            return [{'id': live['id'], 'name': live['name']}] if live else []
                        return deepcopy(live)
                github = Client()
                ruleset(c, github=github)
                before = self.path.read_bytes()
                def owner_change(*args):
                    nonlocal live
                    if creating:
                        live = self.existing(c)
                    else:
                        live['rules'].append({'type': 'required_signatures'})
                        live['rules'][0]['parameters']['required_approving_review_count'] = 2
                with (patch('fleet.review_bot_ruleset.verify_pin', side_effect=owner_change),
                      patch('fleet.review_bot_ruleset.app_client'),
                      patch('fleet.review_bot_ruleset.verify_app'),
                      patch('fleet.review_bot_ruleset.verify_installation')):
                    with self.assertRaises(ValueError):
                        ruleset(c, apply=True, github=github)
                self.assertEqual(self.path.read_bytes(), before)

    def test_config_drift_during_remote_verification_preserves_owner_ci_and_never_mutates(self):
        c = self.installed()
        github = self.client(c, None)
        ruleset(c, github=github)
        extra = {'context': 'owner-security-scan', 'integration_id': 15368}
        def owner_change(*args):
            document = yaml.safe_load(self.path.read_text())
            document['review_bot']['ruleset']['required_checks'].append(extra)
            self.path.write_text(yaml.safe_dump(document))
        with (patch('fleet.review_bot_ruleset.verify_pin', side_effect=owner_change),
              patch('fleet.review_bot_ruleset.app_client'),
              patch('fleet.review_bot_ruleset.verify_app'),
              patch('fleet.review_bot_ruleset.verify_installation')):
            with self.assertRaises(ValueError):
                ruleset(c, apply=True, github=github)
        persisted = yaml.safe_load(self.path.read_text())['review_bot']['ruleset']
        self.assertIn(extra, persisted['required_checks'])
        self.assertNotIn('id', persisted)

    def test_init_rejects_config_drift_before_creation_or_activation(self):
        for during in ('pin', 'app'):
            with self.subTest(during=during):
                c = self.installed()
                before_publication = yaml.safe_load(self.path.read_text()).get('publication')
                def owner_change(*args):
                    document = yaml.safe_load(self.path.read_text())
                    document['review_bot']['apps']['review']['installation_id'] = 9876
                    self.path.write_text(yaml.safe_dump(document))
                with (patch('fleet.review_bot_apps.verify_pin', side_effect=owner_change if during == 'pin' else None),
                      patch('fleet.review_bot_apps.app_client'),
                      patch('fleet.review_bot_apps.verify_app', side_effect=owner_change if during == 'app' else None),
                      patch('fleet.review_bot_apps.verify_installation'),
                      patch('fleet.review_bot_apps.ManifestCallback', side_effect=AssertionError('creation forbidden'))):
                    with self.assertRaises(ValueError):
                        initialize(c, apply=True, github=self.client(c, None))
                persisted = yaml.safe_load(self.path.read_text())
                self.assertEqual(persisted['review_bot']['apps']['review']['installation_id'], 9876)
                self.assertEqual(persisted.get('publication'), before_publication)

    def installed(self):
        self.raw['review_bot']['source'] = self.source()
        for index, role in enumerate(('review', 'builder')):
            self.raw['review_bot']['apps'][role].update(
                id=1007 + index, slug='project-' + role, installation_id=1010 + index)
        return self.load()

    def client(self, c, live, *, capability=None):
        class Client:
            def request(self, method, path, body=None):
                if method != 'GET':
                    raise AssertionError('read-only protection verification must not mutate GitHub')
                if path == '/repos/owner/project':
                    return {'full_name': c['repo'], 'owner': {'type': 'Organization'},
                            'default_branch': 'main', 'allow_auto_merge': True,
                            'permissions': {'admin': True}}
                if '/rulesets?' in path:
                    if capability:
                        raise capability
                    return [{'id': live['id'], 'name': live['name']}] if live else []
                if live and path == f'/repos/owner/project/rulesets/{live["id"]}':
                    return deepcopy(live)
                raise AssertionError('unexpected GitHub read')
        return Client()

    def test_init_capability_failure_preserves_config_and_never_creates_apps(self):
        c = self.load()
        before = self.path.read_bytes()
        github = self.client(c, None, capability=APIError(403))
        with patch('fleet.review_bot_apps.ManifestCallback', side_effect=AssertionError('App creation forbidden')):
            plan = initialize(c, github=github)
            self.assertTrue(any('GitHub403' in reason for reason in plan['blockers']))
            with self.assertRaisesRegex(ValueError, 'GitHub403'):
                initialize(c, apply=True, github=github)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.private / 'review.env').exists())
        self.assertFalse((self.private / 'builder.env').exists())

    def test_init_preserves_publication_until_live_protection_then_reuses_apps(self):
        self.raw['publication'] = {'mode': 'manual', 'release': 'main', 'review_app': '',
                                   'hold_label': 'hold', 'merge_method': 'rebase'}
        c = self.installed()
        github = self.client(c, None)
        with (patch('fleet.review_bot_apps.verify_pin'), patch('fleet.review_bot_apps.app_client'),
              patch('fleet.review_bot_apps.verify_app'),
              patch('fleet.review_bot_apps.verify_installation', side_effect=lambda cfg, role, client: cfg['review_bot']['apps'][role]['installation_id']),
              patch('fleet.review_bot_apps.ManifestCallback', side_effect=AssertionError('existing Apps must be reused'))):
            pending = initialize(c, apply=True, release='none', github=github)
            self.assertTrue(pending['blockers'])
            self.assertFalse(pending['publication']['applied'])
            after = yaml.safe_load(self.path.read_text())
            self.assertEqual(after['publication'], self.raw['publication'])
            self.assertEqual(after['review_bot']['apps'], self.raw['review_bot']['apps'])
            c = config.load(self.path)
            c['_state'] = str(self.private / 'state')
            live = self.existing(c)
            c['review_bot']['ruleset']['id'] = live['id']
            document = yaml.safe_load(self.path.read_text())
            document['review_bot']['ruleset']['id'] = live['id']
            self.path.write_text(yaml.safe_dump(document))
            complete = initialize(c, apply=True, release='none', github=self.client(c, live))
        self.assertEqual(complete['blockers'], [])
        self.assertTrue(complete['publication']['applied'])
        publication = yaml.safe_load(self.path.read_text())['publication']
        self.assertEqual(publication, {'mode': 'auto_merge', 'release': 'none',
                                      'review_app': 'project-review', 'hold_label': 'hold',
                                      'merge_method': 'rebase'})

    def test_live_authority_rejects_invisible_bypass_wrong_app_and_weakened_approvals(self):
        c = self.installed()
        live = self.existing(c)
        c['review_bot']['ruleset']['id'] = live['id']
        receipt = verify_live_ruleset(c, self.client(c, live))
        self.assertEqual(receipt['ruleset_id'], live['id'])
        mutations = (
            lambda value: value.pop('bypass_actors'),
            lambda value: value.update(bypass_actors=[{'actor_type': 'RepositoryRole', 'actor_id': 5, 'bypass_mode': 'always'}]),
            lambda value: value.update(enforcement='evaluate'),
            lambda value: value['conditions']['ref_name']['include'].append('refs/heads/release'),
            lambda value: value['rules'][0]['parameters'].update(dismiss_stale_reviews_on_push=False),
            lambda value: value['rules'][0]['parameters'].update(require_last_push_approval=False),
            lambda value: value['rules'][0]['parameters'].update(required_approving_review_count=0),
            lambda value: value['rules'][1]['parameters']['required_status_checks'][-1].update(integration_id=1234),
            lambda value: value['rules'][1]['parameters'].update(strict_required_status_checks_policy=False),
        )
        for mutation in mutations:
            changed = deepcopy(live)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_live_ruleset(c, self.client(c, changed))


    def test_ruleset_creation_accepts_server_defaults_but_never_an_added_bypass(self):
        for unsafe in (False, True):
            with self.subTest(unsafe=unsafe):
                c = self.installed()
                c['_state'] = str(self.private / ('unsafe-state' if unsafe else 'safe-state'))
                class Client:
                    live = None

                    def request(self, method, path, body=None):
                        if method == 'POST' and path == '/repos/owner/project/rulesets':
                            self.live = {**deepcopy(body), 'id': 1009,
                                         'source_type': 'Repository', 'source': c['repo']}
                            self.live['rules'].reverse()
                            for rule in self.live['rules']:
                                if rule['type'] == 'pull_request':
                                    rule['parameters']['allowed_merge_methods'] = ['merge', 'squash', 'rebase']
                                if rule['type'] == 'required_status_checks':
                                    rule['parameters']['required_status_checks'].reverse()
                            if unsafe:
                                self.live['bypass_actors'] = [{'actor_type': 'RepositoryRole', 'actor_id': 5, 'bypass_mode': 'always'}]
                            return {'id': 1009}
                        if method == 'GET' and path == '/repos/owner/project':
                            return {'full_name': c['repo'], 'default_branch': 'main',
                                    'allow_auto_merge': True, 'permissions': {'admin': True}}
                        if method == 'GET' and '/rulesets?' in path:
                            return [] if self.live is None else [{'id': 1009, 'name': self.live['name']}]
                        if method == 'GET' and path == '/repos/owner/project/rulesets/1009':
                            return deepcopy(self.live)
                        raise AssertionError('unexpected ruleset operation')
                github = Client()
                ruleset(c, github=github)
                with (patch('fleet.review_bot_ruleset.verify_pin'),
                      patch('fleet.review_bot_ruleset.app_client'),
                      patch('fleet.review_bot_ruleset.verify_app'),
                      patch('fleet.review_bot_ruleset.verify_installation')):
                    if unsafe:
                        with self.assertRaises(ValueError):
                            ruleset(c, apply=True, github=github)
                        self.assertNotIn('id', yaml.safe_load(self.path.read_text())['review_bot']['ruleset'])
                    else:
                        ruleset(c, apply=True, github=github)
                        reloaded = config.load(self.path)
                        self.assertEqual(reloaded['review_bot']['ruleset']['id'], 1009)
                        self.assertTrue(verify_live_ruleset(reloaded, github)['verified'])


if __name__ == '__main__':
    unittest.main()

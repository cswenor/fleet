import json
import unittest
from unittest.mock import patch

from fleet.review_bot_status import _containers, _deliveries, status


class StatusTests(unittest.TestCase):
    def config(self):
        return {'root': '/checkout', 'repo': 'example/project', 'review_bot': {
            'source': {'repository': 'example/review-bot', 'version': 'v1.0.0', 'commit': 'a' * 40,
                       'image': 'ghcr.io/example/review-bot@sha256:' + 'b' * 64},
            'apps': {'review': {'name': 'project-review', 'slug': 'project-review', 'id': 1,
                                'installation_id': 11, 'env_file': '/private/review.env'},
                     'builder': {'name': 'project-builder', 'slug': 'project-builder', 'id': 2,
                                 'installation_id': 12, 'env_file': '/private/builder.env'}},
            'deployment': {'backend': 'compose', 'docker_context': 'owned-context',
                           'health_url': 'https://review.example/health'},
            'webhook_url': 'https://review.example/github'}}

    def daemon(self, *, revision=None, image=None, duplicate=False, missing=False, state='running', health='healthy'):
        c = self.config()
        services = ['control', 'runner', 'broker', 'gateway']
        if missing:
            services.pop()
        if duplicate:
            services.append('control')
        ids = {str(index + 1) * 12: role for index, role in enumerate(services)}
        def request(context, args, **kwargs):
            if args[:2] == ['container', 'ls']:
                return '\n'.join(ids)
            if args[:2] == ['container', 'inspect']:
                return ' '.join(json.dumps(value) for value in
                                (ids[args[-1]], 'sha256:' + 'c' * 64, state,
                                 c['review_bot']['source']['image'], health))
            return json.dumps({'digests': [image or c['review_bot']['source']['image']],
                               'revision': revision or 'a' * 40})
        return request

    def test_observed_digest_and_revision_both_must_match(self):
        c = self.config()
        for options, expected in (({}, True), ({'revision': 'd' * 40}, False),
                                  ({'image': 'ghcr.io/example/review-bot@sha256:' + 'e' * 64}, False),
                                  ({'duplicate': True}, False), ({'missing': True}, False),
                                  ({'state': 'exited'}, False), ({'health': 'starting'}, False),
                                  ({'health': 'unhealthy'}, False), ({'health': None}, False)):
            with self.subTest(options=options), patch('fleet.review_bot_status.docker', self.daemon(**options)):
                observed = _containers(c)
            self.assertEqual(all(item['healthy'] for item in observed.values()), expected)
        with patch('fleet.review_bot_status.docker', self.daemon(revision='d' * 40)):
            observed = _containers(c)
        self.assertEqual(observed['runner']['instances'][0]['revision'], 'd' * 40)
        self.assertEqual(observed['runner']['instances'][0]['digests'], [c['review_bot']['source']['image']])

    def test_delivery_metadata_never_exposes_payload_or_free_text(self):
        class Client:
            def request(self, method, path):
                if method != 'GET' or path != '/app/hook/deliveries?per_page=10':
                    raise AssertionError('payload endpoint is forbidden')
                return [{'id': 1, 'event': 'pull_request', 'status_code': 200,
                         'delivered_at': '2026-11-12T00:00:00Z', 'request': {'payload': 'secret'},
                         'response': {'body': 'secret'}, 'status': 'secret', 'duration': 'secret'}]
        result = _deliveries(Client())
        self.assertEqual(result[0]['id'], 1)
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result[0]['event'], 'pull_request')

    def test_unknown_runtime_never_inferred_from_configured_pin(self):
        with patch('fleet.review_bot_status.verify_pin'), \
                patch('fleet.review_bot_status.GitHub.owner', return_value=object()), \
                patch('fleet.review_bot_status.docker', side_effect=ValueError('secret daemon failure')), \
                patch('fleet.review_bot_status.health', side_effect=ValueError('secret health failure')), \
                patch('fleet.review_bot_status.app_client', side_effect=ValueError('secret credential failure')):
            result = status(self.config())
        self.assertFalse(result['healthy'])
        self.assertIsNone(result['containers'])
        self.assertIsNone(result['health'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(result['installations']['review']['verified'])

    def test_healthy_requires_live_all_service_and_advisory_health_evidence(self):
        c = self.config()
        class Client:
            def request(self, method, path):
                return []
        valid = {'ok': True, 'revision': 'a' * 40, 'mode': 'advisory', 'mode_degraded': None,
                 'mergeLaneEnabled': False, 'runner': 'up', 'gateway': 'up'}
        cases = [(200, valid, True), (503, {**valid, 'ok': False}, False),
                 (200, {**valid, 'revision': 'f' * 40}, False),
                 (200, {**valid, 'mode': 'approve'}, False),
                 (200, {**valid, 'mergeLaneEnabled': True}, False),
                 (200, {key: value for key, value in valid.items() if key != 'mode_degraded'}, False),
                 (200, {key: value for key, value in valid.items() if key != 'runner'}, False)]
        for code, value, healthy in cases:
            with self.subTest(value=value), \
                    patch('fleet.review_bot_status.verify_pin'), \
                    patch('fleet.review_bot_status.GitHub.owner', return_value=object()), \
                    patch('fleet.review_bot_status.docker', self.daemon()), \
                    patch('fleet.review_bot_status.health', return_value=(code, value)), \
                    patch('fleet.review_bot_status.app_client', return_value=Client()), \
                    patch('fleet.review_bot_status.verify_app'), \
                    patch('fleet.review_bot_status.verify_installation', return_value=11):
                result = status(c)
            self.assertEqual(result['healthy'], healthy)

    def test_unconfigured_status_has_no_external_effects(self):
        with patch('fleet.review_bot_status.docker', side_effect=AssertionError('Docker access')):
            result = status({'root': '/checkout', 'repo': 'example/project'})
        self.assertFalse(result['healthy'])

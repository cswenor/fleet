import unittest
import tempfile
from unittest.mock import patch
from fleet import snapshot


class MainStatusTests(unittest.TestCase):
    def test_coordinator_observation_is_visible(self):
        for state, reason, expected in (
                ('up', 'live omp UI', 'Main: up (live omp UI)'),
                ('down', 'terminal missing', 'Main: DOWN (terminal missing)'),
                ('suspect', 'bare shell prompt', 'Main: suspect (bare shell prompt)'),
                ('unknown', 'screen state unrecognized', 'Main: unknown (screen state unrecognized)')):
            with self.subTest(state=state):
                result = snapshot.render({'repo': 'owner/project', 'errors': [],
                                          'coordinator': {'terminal': 'main', 'state': state,
                                                          'reason': reason}})
                self.assertEqual([line for line in result.splitlines() if line.startswith('Main:')],
                                 [expected])

    def test_unconfigured_coordinator_is_omitted(self):
        for observation in (None, {}, {'terminal': None, 'state': 'unknown'}):
            with self.subTest(observation=observation):
                data = {'repo': 'owner/project', 'errors': []}
                if observation is not None:
                    data['coordinator'] = observation
                self.assertFalse(any(line.startswith('Main:')
                                     for line in snapshot.render(data).splitlines()))


class CollectionReasonTests(unittest.TestCase):
    def test_collection_failure_is_immediate_with_safe_reason(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        c = {'repo': 'owner/project', 'root': '.', 'quota': {}, '_state': directory.name,
             'disk': {'path': '/', 'min_free_gb': 0}}
        cases = [
            ('ghp_private', 'ghp_private'),
            ('Authorization: Bearer short-auth', 'short-auth'),
            ('Bearer short-auth', 'short-auth'),
            ('password=pass123', 'pass123'),
            ('custom-private-value', 'custom-private-value'),
            ('z' * 48, 'z' * 48),
            ('https://url-user:url-pass@example.test/path', 'url-pass'),
            ('ssh://url-user:url-pass@example.test/path', 'url-user'),
            ('PRIVATE_SETTING=assignment-value', 'assignment-value'),
            ('--pass\x1b[0mword "alpha beta"', 'beta'),
            ('pass\x1b[0mword="alpha beta"', 'beta'),
            ('--pass\x1b]0;title\x07word "alpha beta"', 'beta'),
            ('password="alpha beta"', 'beta'),
            ("secret: 'alpha beta'", 'beta'),
            ('api_key="alpha \\"beta\\" gamma"', 'gamma'),
            ('--password "alpha beta"', 'beta'),
            ("--password='alpha beta'", 'beta'),
            ('--password="alpha \\"beta\\" gamma"', 'gamma'),
            ('--password "alpha beta', 'beta'),
            ('--token argument-value', 'argument-value')]
        for message, secret in cases:
            with self.subTest(message=message), patch.dict(
                    'os.environ', {'CUSTOM_SETTING': 'custom-private-value'}), patch.object(
                    snapshot.health, 'collect', return_value={}), patch.object(
                    snapshot.health, 'coordinator', return_value={}), patch.object(
                    snapshot.quota, 'collect', return_value={}), patch.object(
                    snapshot.github, 'collect', side_effect=ValueError('failure: ' + message)):
                result = snapshot.collect(c)
            self.assertEqual(result['errors'], ['github collection unavailable'])
            reason = result['error_details']['github collection unavailable']
            self.assertTrue(reason.startswith('ValueError: failure:'))
            self.assertNotIn(secret, reason)

    def test_reasons_redact_before_bounding_and_keep_only_first_line(self):
        secrets = ['ghp_private', 'github_pat_private', 'short-auth', 'pass123',
                   'custom-private-value', 'z' * 48, 'url-user', 'url-pass',
                   'assignment-value', 'argument-value']
        message = ('request failed: ghp_private github_pat_private '
                   'Bearer short-auth password=pass123 custom-private-value ' + 'z' * 48 +
                   ' https://url-user:url-pass@example.test/private?key=hidden '
                   'PRIVATE_SETTING=assignment-value --token argument-value')
        with patch.dict('os.environ', {'CUSTOM_SETTING': 'custom-private-value'}):
            reason = snapshot._collection_reason(ValueError(message + '\nsecond line'))
        self.assertTrue(reason.startswith('ValueError: request failed:'))
        self.assertLessEqual(len(reason), 200)
        self.assertNotIn('second line', reason)
        for secret in secrets:
            self.assertNotIn(secret, reason)


if __name__ == '__main__':
    unittest.main()

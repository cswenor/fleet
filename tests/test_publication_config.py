from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import yaml

from fleet import config


class PublicationConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'fleet.yml'
        self.raw = {
            'version': 1, 'repo': 'owner/project', 'root': directory.name,
            'orca': '/orca', 'orca_repo_id': 'repo', 'pad_workspace': 'workspace',
            'builder': {'model': 'provider/model', 'resolved_model': 'provider/model',
                        'rules_file': 'RULES.md', 'slots': [directory.name]},
            'coordinator': {'run': 'run', 'terminal': 'main'},
        }

    def load(self, publication=None, *, omitted=False):
        raw = deepcopy(self.raw)
        if not omitted:
            raw['publication'] = publication
        self.path.write_text(yaml.safe_dump(raw))
        return config.load(self.path)['publication']

    def test_omitted_and_empty_policy_default_to_manual(self):
        expected = {'mode': 'manual', 'release': 'main', 'review_app': '',
                    'hold_label': 'hold', 'merge_method': 'squash'}
        self.assertEqual(self.load(omitted=True), expected)
        self.assertEqual(self.load({}), expected)
        self.assertEqual(self.load({'mode': 'manual'}), expected)

    def test_auto_merge_requires_app_and_preserves_project_settings(self):
        for method in ('merge', 'squash', 'rebase'):
            publication = {'mode': 'auto_merge', 'release': 'main',
                           'review_app': 'review-bot-2',
                           'hold_label': 'human hold', 'merge_method': method}
            with self.subTest(method=method):
                self.assertEqual(self.load(publication), publication)
        self.assertEqual(self.load({'mode': 'auto_merge', 'review_app': 'a'}),
                         {'mode': 'auto_merge', 'release': 'main', 'review_app': 'a',
                          'hold_label': 'hold', 'merge_method': 'squash'})

    def test_release_values_are_preserved_in_both_modes(self):
        for mode in ('manual', 'auto_merge'):
            for release in ('main', 'none'):
                with self.subTest(mode=mode, release=release):
                    policy = self.load({'mode': mode, 'release': release,
                                        'review_app': 'review-app'})
                    self.assertEqual(policy['mode'], mode)
                    self.assertEqual(policy['release'], release)

    def test_invalid_release_values_are_rejected_in_both_modes(self):
        for mode in ('manual', 'auto_merge'):
            for release in (None, False, True, 0, 1, [], {}, '', 'manual', 'MAIN', 'None'):
                with self.subTest(mode=mode, release=release), self.assertRaisesRegex(ValueError, 'publication.release'):
                    self.load({'mode': mode, 'release': release, 'review_app': 'review-app'})

    def test_malformed_publication_mapping_is_rejected(self):
        for publication in (None, False, [], 'auto_merge', {'unknown': True}):
            with self.subTest(publication=publication), self.assertRaisesRegex(ValueError, 'publication'):
                self.load(publication)

    def test_invalid_modes_labels_and_methods_are_rejected(self):
        for field, values in (
                ('mode', [None, False, [], {}, '', 'auto', 'AUTO_MERGE']),
                ('hold_label', [None, False, [], {}, '', '  ', '\n']),
                ('merge_method', [None, False, [], {}, '', 'auto', '--admin', 'Squash'])):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'publication.' + field):
                    self.load({field: value})

    def test_review_app_must_be_a_slug_and_is_required_in_auto_mode(self):
        for mode in ('manual', 'auto_merge'):
            for app in (None, False, 1, [], {}, ' ', 'Review-App', 'owner/app',
                        'app[bot]', '-app', 'app-', 'app_name', 'app\n'):
                with self.subTest(mode=mode, app=app), self.assertRaisesRegex(ValueError, 'publication.review_app'):
                    self.load({'mode': mode, 'review_app': app})
        for publication in ({'mode': 'auto_merge'}, {'mode': 'auto_merge', 'review_app': ''}):
            with self.subTest(publication=publication), self.assertRaisesRegex(ValueError, 'publication.review_app'):
                self.load(publication)
        self.assertEqual(self.load({'review_app': 'review-app'})['review_app'], 'review-app')


if __name__ == '__main__':
    unittest.main()

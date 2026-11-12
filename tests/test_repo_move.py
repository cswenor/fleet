import tempfile
import unittest
from unittest.mock import patch

from fleet import github, restart, snapshot
from fleet.commands import CommandError


class RepoMoveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'old/project', 'root': '.', '_state': directory.name,
                  'pad_workspace': 'project',
                  'disk': {'path': '/', 'min_free_gb': 0}}
        self.metadata = {'full_name': 'new/project', 'default_branch': 'main'}

    def api(self, c, *args):
        if args == ('api', 'repos/old/project'):
            if isinstance(self.metadata, Exception):
                raise self.metadata
            return self.metadata
        if '--slurp' in args:
            return ([{'workflow_runs': []}] if any('actions/runs?' in arg for arg in args)
                    else [[]])
        if 'graphql' in args:
            return {'data': {'repository': {'mergeQueue': None}}}
        return []

    def test_snapshot_identity_match_move_and_unavailable(self):
        for metadata, expected in (
                ({'full_name': 'OLD/Project', 'default_branch': 'main'}, []),
                (self.metadata, ['repo moved/renamed: config old/project, GitHub new/project; '
                                 'follow the MAIN.md repo-move procedure']),
                (CommandError('offline'), ['github collection unavailable'])):
            with self.subTest(metadata=metadata):
                self.metadata = metadata
                with patch.object(github, 'gh', side_effect=self.api), patch.object(
                        snapshot.health, 'collect', return_value={}), patch.object(
                        snapshot.health, 'coordinator', return_value={}), patch.object(
                        snapshot.health, 'add_pr_stalls'), patch.object(
                        snapshot.quota, 'collect', return_value={}):
                    result = snapshot.collect(self.c)
                self.assertEqual(result['errors'], expected)
                if expected:
                    self.assertIn(expected[0], snapshot.render(result))

    def test_restart_search_miss_diagnoses_move_without_retirement(self):
        for metadata, expected in (
                (self.metadata, 'repo moved/renamed: config old/project, GitHub new/project'),
                ({'full_name': 'OLD/Project'}, 'no merged PR'),
                (CommandError('offline'), 'no merged PR')):
            with self.subTest(metadata=metadata):
                self.metadata = metadata
                with patch.object(restart, '_merged_prs', return_value=[]), patch.object(
                        restart, 'json_run', return_value={'fields': {'status': 'done'}}), patch(
                        'fleet.parking.completion', return_value=None), patch.object(
                        github, 'gh', side_effect=self.api), patch.object(restart, 'orca') as orca:
                    with self.assertRaisesRegex(ValueError, expected):
                        restart._completion(self.c, 'TASK-2', 'TASK-1', {'at': 1})
                orca.assert_not_called()

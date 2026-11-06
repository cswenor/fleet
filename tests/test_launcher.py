from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import health, launcher, snapshot


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.slot = str(root / 'slot')
        Path(self.slot).mkdir()
        (Path(self.slot) / 'RULES.md').write_text('rules')
        self.c = {'root': self.directory.name, '_state': str(root / 'state'), 'repo': 'owner/project',
                  'orca': '/orca', 'orca_repo_id': 'repo', 'pad_workspace': 'workspace',
                  'coordinator': {'terminal': 'main', 'run': 'run'}, 'hooks': {},
                  'quota': {'reserve_percent': 10},
                  'builder': {'slots': [self.slot], 'max': 8, 'model': 'provider/model',
                              'resolved_model': 'provider/model', 'thinking': 'medium',
                              'rules_file': 'RULES.md', 'footer': '', 'idle_seconds': 2700}}
        self.s = {'errors': [], 'quota': {'may_start': True}, 'disk': {'low': False},
                  'inventory': {'errors': [], 'worktrees': [{'id': 'tree', 'path': self.slot, 'branch': 'work'}],
                                'builders': [dict(terminal='old-shell', path=self.slot, worktree='tree', connected=True, shell_ready=True)] +
                                            [dict(terminal=f'live-{i}', path=f'/other-{i}', worktree=f'other-{i}', connected=True) for i in range(7)]}}
        self.created = 0
        self.sent = []
        self.prompt = 'user@host:~/slot$ '

    def orca(self, config, *args):
        if args[:2] == ('terminal', 'show'):
            return {'terminal': {'connected': True, 'writable': True, 'worktreeId': 'tree'}}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': {'tail': ['old omp footer', self.prompt]}}
        if args[:2] == ('terminal', 'create'):
            self.created += 1
            return {'terminal': {'handle': 'new-terminal'}}
        if args[:2] == ('terminal', 'send'):
            handle = args[args.index('--terminal') + 1]
            self.sent.append(handle)
            self.prompt = '◑ model >'
            return {'send': {'handle': handle, 'accepted': True}}
        return {}

    def launch(self):
        with patch.object(launcher.snapshot, 'collect', return_value=self.s), patch.object(launcher, 'orca', side_effect=self.orca), patch.object(launcher, 'run'):
            return launcher.launch(self.c, 'TASK-123', self.slot)

    def test_eight_connected_terminals_with_one_exited_session_reuse_its_shell(self):
        self.assertTrue(snapshot.may_start(self.c, self.s)['may_start'])
        receipt = self.launch()
        self.assertEqual(receipt['receipt']['send']['handle'], 'old-shell')
        self.assertEqual(self.sent, ['old-shell'])
        self.assertEqual(self.created, 0)
        self.assertEqual(health.owner(self.s['inventory'], 'work'), None)

    def test_full_live_cap_and_live_target_are_still_withheld(self):
        self.s['inventory']['builders'][0]['shell_ready'] = False
        with self.assertRaisesRegex(ValueError, 'builder cap reached'):
            self.launch()
        self.s['inventory']['builders'].pop()
        with self.assertRaisesRegex(ValueError, 'live or unconfirmed'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_disconnected_session_never_frees_a_slot(self):
        self.s['inventory']['builders'].pop()
        self.s['inventory']['builders'][0].update(connected=False, shell_ready=False)
        with self.assertRaisesRegex(ValueError, 'live or unconfirmed'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))
        self.assertIsNone(health.owner(self.s['inventory'], 'work'))

    def test_raced_or_ambiguous_shell_never_receives_input(self):
        self.prompt = '◑ model >'
        with self.assertRaisesRegex(ValueError, 'no longer a ready shell'):
            self.launch()
        self.s['inventory']['builders'].append(dict(self.s['inventory']['builders'][0], terminal='second-shell'))
        with self.assertRaisesRegex(ValueError, 'multiple ready shells'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_reserve_gate_cannot_be_bypassed_by_replacing_a_session(self):
        self.s['quota'] = {'may_start': False, 'reason': 'quota reserve reached'}
        with self.assertRaisesRegex(ValueError, 'quota reserve reached'):
            self.launch()
        self.assertEqual((self.sent, self.created), ([], 0))

    def test_shell_prompt_requires_current_positive_evidence(self):
        for prompt in ('user@host:~/slot$ ', '\x1b[32muser@host:~/slot$\x1b[0m ', 'bash-5.2$ '):
            self.assertTrue(health.shell_ready('old session\n' + prompt))
        for prompt in ('user@host:~/slot$\n◑ GPT >', 'user@host:~/slot$ sleep 600 #', 'user@host:~/slot$ echo %', 'approval required', 'waiting for input', '', '$', 'price is $'):
            self.assertFalse(health.shell_ready(prompt))


if __name__ == '__main__':
    unittest.main()

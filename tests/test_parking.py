import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from fleet import parking, restart, state, threads


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.slot = os.path.realpath(self.temp.name)
        self.c = {'_state': self.slot, 'root': self.slot, 'pad_workspace': 'scratch',
                  'builder': {'slots': [self.slot]}, 'coordinator': {'run': 'main'}}
        self.launch = {'id': 'launch', 'ref': 'TASK-1', 'terminal': 'builder',
                       'at': 1767225600, 'terminal_incarnation_id': 'incarnation',
                       'worktree': {'id': 'tree', 'path': self.slot}}
        self.attempt = {'attempt_id': 'launch', 'terminal': 'builder', 'slot': self.slot,
                        'branch': 'task-1-wip', 'questions': {}, 'recorded': True, 'settled': False}
        self.save_attempt()
        state.write(self.c, 'launches', {self.slot: self.launch})
        self.terminal = {'handle': 'builder', 'incarnationId': 'incarnation', 'worktreeId': 'tree',
                         'connected': True, 'writable': True, 'orphaned': False}
        self.comments = [{'id': 'resume', 'body': 'Resume the remaining CLI changes from this WIP.',
                          'created_at': '2026-01-01T00:00:01Z'}]
        self.head = 'a' * 40
        self.branch = 'task-1-wip'
        self.dirty = ''
        self.untracked = ''
        self.remote_head = self.head
        self.sent = []
        self.pads = []
        self.commands = []
        patches = [patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'),
                   patch.object(threads, 'request', side_effect=self.native),
                   patch.object(threads, 'orca', side_effect=lambda *a: {'terminal': self.terminal}),
                   patch.object(threads, 'pad', side_effect=lambda *a: self.pads.append(a)),
                   patch.object(parking, 'request', side_effect=self.native),
                   patch.object(parking, 'run', side_effect=self.git),
                   patch.object(parking, 'json_run', side_effect=lambda *a: self.comments)]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def save_attempt(self):
        state.write(self.c, 'threads-TASK-1', {'TASK-1': {'id': 'thread',
                    'attempts': {'launch': self.attempt}}})

    def native(self, c, verb, *args):
        if verb == 'run-current':
            return {'run': None}
        self.assertEqual(verb, 'send')
        self.sent.append(args)
        return {'message': {'id': 'park-message'}}

    def git(self, argv, cwd, *, text=True):
        self.assertEqual(cwd, self.slot)
        self.commands.append(argv)
        if argv[1:] == ['rev-parse', '--show-toplevel']:
            return self.slot
        if argv[1:3] == ['status', '--porcelain']:
            return self.dirty
        if argv[1:] == ['ls-files', '--others', '--exclude-standard', '-z']:
            return os.fsencode(self.untracked)
        if argv[1:] == ['branch', '--show-current']:
            return self.branch
        if argv[1:] == ['rev-parse', 'HEAD']:
            return self.head
        if argv[1:5] == ['ls-remote', '--exit-code', '--heads', 'origin']:
            return self.remote_head + '\trefs/heads/' + self.branch + '\n'
        self.fail('unexpected git command: ' + repr(argv))

    def park(self):
        return parking.parked(self.c, 'TASK-1', 'resume')

    def test_pushed_wip_settles_without_merge_and_is_idempotent(self):
        receipt = self.park()
        self.assertEqual(self.park(), receipt)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.pads), 1)
        self.assertIn('--type', self.sent[0])
        self.assertIn('status', self.sent[0])
        self.assertTrue(any(a[1:5] == ['ls-remote', '--exit-code', '--heads', 'origin']
                            for a in self.commands))
        attempt = state.read(self.c, 'threads-TASK-1')['TASK-1']['attempts']['launch']
        self.assertTrue(attempt['settled'])
        self.assertEqual(attempt['result'], receipt['body'])
        self.assertEqual(attempt['settled_at'], receipt['at'])
        self.assertEqual(restart._finished_completion(self.c, 'TASK-1', self.launch)['park'], receipt)

    def test_dirty_and_unpushed_refuse_before_reporting(self):
        for dirty, remote, error in [(' M file', self.head, 'clean tracked worktree'),
                                     ('M  file', self.head, 'clean tracked worktree'),
                                     ('', 'b' * 40, 'actual origin branch')]:
            self.dirty, self.remote_head = dirty, remote
            with self.assertRaisesRegex(ValueError, error):
                self.park()
        self.assertEqual(self.sent, [])
        self.assertEqual(state.read(self.c, 'parks', {}), {})

    def test_changed_and_detached_branches_refuse(self):
        for branch in ('', 'main', 'task-10-other'):
            self.branch = branch
            with self.assertRaisesRegex(ValueError, 'task branch'):
                self.park()

    def test_opened_task_branch_need_not_embed_the_task_ref(self):
        self.branch = 'scratch/task-83-proof'
        self.attempt['branch'] = self.branch
        self.save_attempt()
        receipt = self.park()
        self.assertEqual(receipt['branch'], self.branch)
        self.assertEqual(parking.completion(self.c, 'TASK-1', self.launch)['park'], receipt)

    def test_invalid_resume_notes_refuse(self):
        for comments in ([], [{'id': 'wrong', 'body': 'Resume work', 'created_at': '2026-01-01T00:00:01Z'}],
                         [{'id': 'resume', 'body': '  ', 'created_at': '2026-01-01T00:00:01Z'}],
                         [{'id': 'resume', 'body': 'Resume work', 'created_at': '2026-01-01T00:00:00Z'}]):
            self.comments = comments
            with self.assertRaisesRegex(ValueError, 'resume-note'):
                self.park()
        self.assertEqual(self.sent, [])

    def test_bound_or_changed_identity_refuses(self):
        with patch.object(threads, 'request', return_value={'run': {'id': 'main'}}):
            with self.assertRaisesRegex(ValueError, 'Run-bound'):
                self.park()
        self.terminal['incarnationId'] = 'replacement'
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.park()

    def test_canonical_open_and_pending_decisions_required(self):
        self.attempt['recorded'] = False
        self.save_attempt()
        with self.assertRaisesRegex(ValueError, 'canonical opened'):
            self.park()
        self.attempt['recorded'] = True
        self.attempt['questions'] = {'q': {'reply_recorded': False}}
        self.save_attempt()
        with self.assertRaisesRegex(ValueError, 'pending decision'):
            self.park()

    def test_ambiguous_send_never_retries(self):
        with patch.object(parking, 'request', side_effect=ValueError('transport lost')):
            with self.assertRaisesRegex(ValueError, 'transport lost'):
                self.park()
        with self.assertRaisesRegex(ValueError, 'ambiguous park send'):
            self.park()
        self.assertEqual(self.sent, [])

    def test_pad_failure_retries_known_message_only(self):
        with patch.object(threads, 'pad', side_effect=ValueError('Pad unavailable')):
            with self.assertRaisesRegex(ValueError, 'Pad unavailable'):
                self.park()
        self.assertIsNone(parking.completion(self.c, 'TASK-1', self.launch))
        receipt = self.park()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(receipt['recorded'])

    def test_park_reassignment_needs_no_merge_or_next_handoff(self):
        receipt = self.park()
        with patch.object(restart, '_merged_prs', side_effect=AssertionError('no merge required')), \
                patch.object(restart, 'json_run', side_effect=AssertionError('no handoff required')):
            proof = restart._completion(self.c, 'TASK-2', 'TASK-1', self.launch)
        self.assertEqual(proof['park'], receipt)
        self.assertEqual(restart._completion(self.c, 'TASK-1', 'TASK-1', self.launch)['park'], receipt)
        for next_ref in ('TASK-1', 'TASK-2'):
            with self.assertRaisesRegex(ValueError, 'parked attempts cannot send FRESH-READY'):
                threads.ready(self.c, next_ref)

    def test_same_ref_without_park_or_merge_refuses(self):
        with patch.object(restart, '_merged_prs', return_value=[]), \
                patch.object(restart, 'json_run', return_value={'fields': {'status': 'done'}}):
            with self.assertRaisesRegex(ValueError, 'no merged PR'):
                restart._completion(self.c, 'TASK-1', 'TASK-1', self.launch)

    def untracked_file(self, name, mtime):
        from pathlib import Path
        path = Path(self.slot) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('user work')
        os.utime(path, (mtime, mtime))
        return path

    def test_untracked_is_preserved_and_reported(self):
        name = 'notes/space and\nnewline.txt'
        path = self.untracked_file(name, self.launch['at'] + 1)
        self.untracked = name + '\0'
        receipt = self.park()
        self.assertEqual(receipt['untracked_files'], [name])
        self.assertEqual(receipt['untracked_count'], 1)
        self.assertIn('notes/space and\\nnewline.txt', receipt['body'])
        self.assertEqual(path.read_text(), 'user work')
        self.assertIn(receipt['body'], self.sent[0])

    def test_untracked_listing_is_bounded_with_total_count(self):
        names = [f'old-{i:02}' for i in range(51)]
        for name in names:
            self.untracked_file(name, self.launch['at'] - 1)
        self.untracked = '\0'.join(names) + '\0'
        receipt = self.park()
        self.assertEqual(receipt['untracked_files'], names[:50])
        self.assertEqual(receipt['untracked_count'], 51)
        self.assertIn('51 total; listing 50', receipt['body'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX byte filenames')
    def test_real_git_preserves_byte_and_carriage_return_filenames(self):
        from pathlib import Path
        from fleet.commands import run
        root = Path(self.temp.name)
        repo, remote = root / 'repo', root / 'origin.git'
        repo.mkdir()
        run(['git', 'init', '--bare', str(remote)])
        run(['git', 'init', '-b', self.branch], repo)
        (repo / '.gitignore').write_text('ignored\n')
        run(['git', 'add', '.gitignore'], repo)
        run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', 'commit', '-m', 'Tracked WIP'], repo)
        run(['git', 'remote', 'add', 'origin', str(remote)], repo)
        run(['git', 'push', '-u', 'origin', self.branch], repo)
        names = [b'carriage\rreturn']
        # macOS filesystems reject invalid UTF-8 names with EILSEQ.
        if sys.platform.startswith('linux'):
            names.append(b'invalid-\xff')
        for name in names:
            with open(os.fsencode(repo) + b'/' + name, 'wb') as stream:
                stream.write(b'user work')
        (repo / 'ignored').write_text('ignored work')
        self.slot = str(repo)
        self.c.update(root=self.slot, builder={'slots': [self.slot]})
        self.launch['worktree']['path'] = self.slot
        self.attempt['slot'] = self.slot
        self.save_attempt()
        state.write(self.c, 'launches', {self.slot: self.launch})
        with patch.object(parking, 'run', side_effect=run):
            receipt = self.park()
        self.assertEqual({os.fsencode(name) for name in receipt['untracked_files']}, set(names))
        self.assertEqual(receipt['untracked_count'], len(names))
        self.assertIn('carriage\\rreturn', receipt['body'])
        if sys.platform.startswith('linux'):
            self.assertIn('invalid-\\udcff', receipt['body'])
        saved = state.read(self.c, 'parks')['launch']
        self.assertEqual(saved['untracked_files'], receipt['untracked_files'])
        for name in names:
            with open(os.fsencode(repo) + b'/' + name, 'rb') as stream:
                self.assertEqual(stream.read(), b'user work')

    def test_reopened_or_replaced_attempt_invalidates_park(self):
        self.park()
        ledger = state.read(self.c, 'threads-TASK-1')
        ledger['TASK-1']['attempts']['launch']['attempt_id'] = 'launch/continuation'
        state.write(self.c, 'threads-TASK-1', ledger)
        self.assertIsNone(parking.completion(self.c, 'TASK-1', self.launch))

    def test_finished_merge_proof_requires_exact_ref_and_new_merge(self):
        pr = {'title': 'TASK-10 is not TASK-1', 'body': '', 'url': 'pr',
              'mergedAt': '2026-01-01T00:00:01Z'}
        for title, merged in [('TASK-10', '2026-01-01T00:00:01Z'),
                              ('TASK-1', '2026-01-01T00:00:00Z')]:
            pr.update(title=title, mergedAt=merged)
            with patch.object(restart, '_merged_prs', return_value=[pr]):
                with self.assertRaisesRegex(ValueError, 'new merged PR'):
                    restart._finished_completion(self.c, 'TASK-1', self.launch)
        pr.update(title='Fix CLI', body='Pad-Ref: TASK-1', mergedAt='2026-01-01T00:00:01Z')
        with patch.object(restart, '_merged_prs', return_value=[pr]):
            self.assertEqual(restart._finished_completion(self.c, 'TASK-1', self.launch)['pr'], 'pr')

    def test_invalid_launch_timestamp_refuses(self):
        for value in (None, True, float('nan'), float('inf'), '2026-01-01'):
            with self.assertRaisesRegex(ValueError, 'valid launch timestamp'):
                restart._finished_completion(self.c, 'TASK-1', {**self.launch, 'at': value})

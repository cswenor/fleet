import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import dispatcher, state, threads


class CarryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.slot = str(Path(self.temp.name).resolve())
        self.c = {'root': self.slot, '_state': self.slot + '/state', 'pad_workspace': 'scratch',
                  'builder': {'slots': [self.slot]},
                  'coordinator': {'terminal': 'main', 'run': 'scratch'}}
        self.launch = {'id': 'one', 'ref': 'TASK-1', 'terminal': 'builder',
                       'terminal_incarnation_id': 'inc', 'worktree': {'path': self.slot, 'id': 'tree'}}
        state.write(self.c, 'launches', {self.slot: self.launch})
        self.inventory = {'errors': [], 'worktrees': [{'path': self.slot}], 'builders': [
            {'path': self.slot, 'terminal': 'builder', 'incarnation_id': 'inc',
             'shell_ready': False, 'connected': True}]}
        for p in (patch.dict(os.environ, ORCA_TERMINAL_HANDLE='main'),
                  patch.object(dispatcher.health, 'collect', return_value=self.inventory),
                  patch.object(dispatcher, 'run', return_value='main'),
                  patch.object(threads, 'run', return_value='task-1'),
                  patch.object(threads, 'pad'),
                  patch.object(threads, 'request', side_effect=lambda c, verb, *a:
                               {'run': None} if verb == 'run-current' else {'message': {'id': 'message'}})):
            p.start()
            self.addCleanup(p.stop)
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            threads.open_thread(self.c, 'TASK-1', self.slot)

    def carry(self, ref='TASK-2', launch='one', evidence='Main approved prerequisite'):
        return dispatcher.carry(self.c, self.slot, launch, ref, evidence)

    def test_refusals_do_not_create_approval(self):
        for ref, ident, evidence, error in (
                ('TASK-2', 'unknown', 'approval', 'exact current'),
                ('TASK-1', 'one', 'approval', 'primary ref'),
                ('task-2', 'one', 'approval', 'uppercase'),
                ('TASK-2', 'one', ' ', 'non-empty')):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                self.carry(ref, ident, evidence)
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            with self.assertRaisesRegex(ValueError, 'Main terminal'):
                self.carry()
        self.assertEqual(state.read(self.c, 'carries', {}), {})

    def test_settled_or_unconfirmed_launch_refuses(self):
        self.inventory['builders'][0]['shell_ready'] = True
        with self.assertRaisesRegex(ValueError, 'settled launch'):
            self.carry()
        self.inventory['builders'].clear()
        with self.assertRaisesRegex(ValueError, 'live launch health'):
            self.carry()
        state.write(self.c, 'threads-TASK-1', {'TASK-1': {'attempts': {'one': {'settled': True}}}})
        with self.assertRaisesRegex(ValueError, 'settled launch'):
            self.carry()

    def test_missing_or_wrong_primary_attempt_refuses_carry(self):
        for ledger in ({}, {'TASK-1': {'attempts': {'old': {'settled': False}}}},
                       {'TASK-1': {'attempts': {'one': {}}}}):
            with self.subTest(ledger=ledger):
                state.write(self.c, 'threads-TASK-1', ledger)
                with self.assertRaisesRegex(ValueError, 'unknown or settled'):
                    self.carry()

    def test_wrong_incarnation_disconnection_and_collection_errors_refuse(self):
        for changes in ({'incarnation_id': 'replacement'}, {'connected': False},
                        {'terminal': 'replacement'}):
            with self.subTest(changes=changes):
                original = dict(self.inventory['builders'][0])
                self.inventory['builders'][0].update(changes)
                with self.assertRaisesRegex(ValueError, 'live launch health'):
                    self.carry()
                self.inventory['builders'][0] = original
        self.inventory['errors'] = ['inventory incomplete']
        with self.assertRaisesRegex(ValueError, 'live launch health'):
            self.carry()

    def test_other_owner_and_carried_claim_block_launch_and_restart(self):
        self.inventory['builders'].append({'path': '/elsewhere', 'task': 'TASK-2',
                                           'shell_ready': False, 'connected': True})
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            self.carry()
        self.inventory['builders'].pop()
        approval = self.carry()
        self.assertEqual(self.carry(), approval)
        for retiring in (None, self.launch):
            claims = dispatcher.assignments(self.c, self.inventory, retiring=retiring)
            self.assertTrue(dispatcher.duplicate_reasons('TASK-2', claims, self.slot))
        self.inventory['builders'].clear()  # Missing health retains unresolved carried ownership.
        self.assertIn(('TASK-2', self.slot, 'carried launch receipt'),
                      dispatcher.assignments(self.c, self.inventory))
        state.write(self.c, 'reconciliations', {'one': {'launch_id': 'one', 'slot': self.slot}})
        self.assertEqual(dispatcher.assignments(self.c, self.inventory), [])

    def test_carried_result_settles_only_its_ref_and_never_transfers_attempts(self):
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            primary = threads.open_thread(self.c, 'TASK-1', self.slot)
            with self.assertRaisesRegex(ValueError, 'authoritative launch'):
                threads.open_thread(self.c, 'TASK-2', self.slot)
        self.carry()
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            carried = threads.open_thread(self.c, 'TASK-2', self.slot)
            self.assertEqual(carried['attempt'], primary['attempt'])
            self.assertNotEqual(carried['thread_id'], primary['thread_id'])
            threads.result(self.c, 'TASK-2', 'published prerequisite')
            self.assertFalse(threads.current(self.c, 'TASK-1', 'builder')[3]['settled'])
            with self.assertRaisesRegex(ValueError, 'settled'):
                threads.open_thread(self.c, 'TASK-2', self.slot)
        self.carry('TASK-3')
        with patch.dict(os.environ, ORCA_TERMINAL_HANDLE='builder'):
            threads.open_thread(self.c, 'TASK-3', self.slot)
            threads.result(self.c, 'TASK-1', 'primary published')
            with self.assertRaisesRegex(ValueError, 'no unique current'):
                threads.question(self.c, 'TASK-3', 'stale?')
            state.write(self.c, 'launches', {self.slot: dict(self.launch, id='two')})
            with self.assertRaisesRegex(ValueError, 'authoritative launch'):
                threads.open_thread(self.c, 'TASK-3', self.slot)

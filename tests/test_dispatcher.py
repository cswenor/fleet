from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import dispatcher, state
from fleet.commands import CommandError


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name
        self.c = {'root': self.path, '_state': self.path + '/state',
                  'builder': {'slots': [self.path], 'max': 2}}
        self.builder = {'path': self.path, 'terminal': 'term', 'connected': True,
                        'shell_ready': True, 'incarnation_id': 'inc'}
        self.inventory = {'worktrees': [{'path': self.path, 'id': 'tree', 'branch': 'stale-task-99'}],
                          'builders': [self.builder], 'errors': []}
        self.s = {'inventory': self.inventory, 'quota': {'may_start': True}, 'disk': {'low': False}, 'errors': []}
        self.tasks = [self.item('TASK-3', 'high', '2026-03-01'),
                      self.item('TASK-1', 'low', '2026-01-01'),
                      self.item('TASK-2', 'high', '2026-02-01')]
        self.edges = {}
        self.pad_patch = patch.object(dispatcher, 'pad', side_effect=self.pad)
        self.pad_patch.start()
        self.addCleanup(self.pad_patch.stop)
        self.git = patch.object(dispatcher, 'run', return_value='main')
        self.git.start()
        self.addCleanup(self.git.stop)

    def item(self, ref, priority, created):
        return {'ref': ref, 'title': ref, 'fields': {'status': 'open', 'priority': priority},
                'parent_ref': 'PLAN-1', 'created_at': created}

    def pad(self, c, *args):
        if args[:2] == ('item', 'deps'):
            value = self.edges.get(args[2], [])
            if isinstance(value, Exception):
                raise value
            return value
        if args[:3] == ('item', 'list', 'plans'):
            return [{'ref': 'PLAN-1', 'fields': {'status': 'active'}}]
        if args[:3] == ('item', 'list', 'tasks'):
            return self.tasks
        if args[:2] == ('item', 'show'):
            if args[2] == 'PLAN-1':
                return {'fields': {'status': 'active'}}
            return next(t for t in self.tasks if t['ref'] == args[2])
        raise AssertionError(args)

    def propose(self):
        with patch.object(dispatcher.snapshot, 'collect', return_value=self.s):
            return dispatcher.propose(self.c)

    def block(self, status):
        self.edges['TASK-2'] = [{'link_type': 'blocks', 'source_ref': 'TASK-9',
                                 'target_ref': 'TASK-2', 'source_status': status}]

    def test_priority_then_oldest_and_explicit_proposal(self):
        result = self.propose()
        self.assertEqual([r['ref'] for r in result['tasks']], ['TASK-2', 'TASK-3', 'TASK-1'])
        self.assertEqual(result['proposal'], {'ref': 'TASK-2', 'slot': self.path})
        self.assertFalse((Path(self.c['_state']) / 'launches.json').exists())

    def test_dependency_direction_terminal_states_and_unknown(self):
        for status in ('open', 'in-progress', None):
            self.block(status)
            self.assertEqual(self.propose()['proposal']['ref'], 'TASK-3')
        for status in ('done', 'cancelled'):
            self.block(status)
            self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
        self.edges['TASK-2'] = [{'link_type': 'blocks', 'source_ref': 'TASK-2',
                                'target_ref': 'TASK-9', 'source_status': 'open'}]
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
        self.edges['TASK-2'] = CommandError('unavailable')
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-3')

    def test_inactive_plan_nonopen_and_assignment_exclusions(self):
        self.tasks[0]['fields']['status'] = 'in-progress'
        self.tasks[1]['parent_ref'] = 'PLAN-closed'
        self.inventory['worktrees'][0]['comment'] = 'TASK-2: assigned'
        result = self.propose()
        self.assertIsNone(result['proposal'])
        self.assertTrue(all(r['reasons'] for r in result['tasks']))

    def test_live_disconnected_ambiguous_slots_and_quota_withhold(self):
        for connected in (True, False):
            self.builder.update(connected=connected, shell_ready=False)
            self.assertIsNone(self.propose()['proposal'])
        self.builder.update(connected=True, shell_ready=True)
        self.inventory['builders'].append(dict(self.builder, terminal='second'))
        self.assertIsNone(self.propose()['proposal'])
        self.inventory['builders'].pop()
        self.s['quota'] = {'may_start': False, 'reason': 'reserve'}
        self.assertIsNone(self.propose()['proposal'])

    def test_receipt_survives_absence_but_matching_shell_releases_it(self):
        receipt = {'id': 'launch', 'ref': 'TASK-2', 'worktree': {'path': self.path}, 'terminal': 'term',
                   'terminal_incarnation_id': 'inc'}
        state.write(self.c, 'last-launch', receipt)
        self.builder['incarnation_id'] = 'replacement'
        self.assertIsNone(self.propose()['proposal'])
        with self.assertRaisesRegex(ValueError, 'slot has unresolved'):
            dispatcher.check_launch(self.c, 'TASK-3', self.inventory, self.path)
        self.inventory['builders'].clear()
        with self.assertRaisesRegex(ValueError, 'slot has unresolved'):
            dispatcher.check_launch(self.c, 'TASK-3', self.inventory, self.path)
        self.inventory['builders'].append(self.builder)
        self.builder['incarnation_id'] = 'inc'
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')

    def test_launch_rechecks_dependency_and_other_slot_claim(self):
        dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
        self.block('open')
        with self.assertRaisesRegex(ValueError, 'blocked by TASK-9'):
            dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
        self.edges.clear()
        self.inventory['builders'].append(dict(self.builder, path='/other', task='TASK-2', shell_ready=False))
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)

    def test_retirement_must_match_launch_and_verified_shell_stage(self):
        receipt = {'id': 'launch', 'ref': 'TASK-2', 'worktree': {'path': self.path},
                   'terminal': 'old', 'terminal_incarnation_id': 'old-inc'}
        state.write(self.c, 'launches', {self.path: receipt})
        retirement = {'launch_id': 'launch', 'terminal': 'old',
                      'terminal_incarnation_id': 'old-inc', 'stage': 'exit-sent'}
        state.write(self.c, 'retirements', {self.path: retirement})
        self.assertIsNone(self.propose()['proposal'])
        retirement['stage'] = 'shell-verified'
        state.write(self.c, 'retirements', {self.path: retirement})
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
        retirement['launch_id'] = 'other-launch'
        state.write(self.c, 'retirements', {self.path: retirement})
        self.assertIsNone(self.propose()['proposal'])

    def test_main_reconciliation_refuses_live_unknown_wrong_id_or_actor(self):
        self.c['coordinator'] = {'terminal': 'main'}
        receipt = {'id': 'launch', 'ref': 'TASK-2', 'worktree': {'path': self.path},
                   'terminal': 'old', 'terminal_incarnation_id': 'old-inc'}
        state.write(self.c, 'launches', {self.path: receipt})
        with patch.dict('os.environ', {'ORCA_TERMINAL_HANDLE': 'main'}), patch.object(dispatcher.health, 'collect', return_value=self.inventory):
            for connected in (True, False):
                self.builder.update(connected=connected, shell_ready=False)
                with self.assertRaisesRegex(ValueError, 'live, unknown'):
                    dispatcher.reconcile(self.c, self.path, 'launch', 'positive exit proof')
            self.builder.update(connected=True, shell_ready=True)
            with self.assertRaisesRegex(ValueError, 'exact recorded'):
                dispatcher.reconcile(self.c, self.path, 'wrong', 'positive exit proof')
            recorded = dispatcher.reconcile(self.c, self.path, 'launch', 'final stop and shell observed')
            self.assertEqual(recorded['actor'], 'main')
            self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
        with patch.dict('os.environ', {'ORCA_TERMINAL_HANDLE': 'builder'}):
            with self.assertRaisesRegex(ValueError, 'Main terminal'):
                dispatcher.reconcile(self.c, self.path, 'launch', 'proof')


if __name__ == '__main__':
    unittest.main()

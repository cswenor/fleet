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
        self.path = str(Path(self.temp.name).resolve())
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
        self.parents = {'PLAN-1': {'ref': 'PLAN-1', 'fields': {'status': 'active'}}}
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
        if args == ('collection', 'list'):
            return [{'slug': 'tasks', 'schema': {'fields': [{
                'key': 'status', 'default': 'open',
                'options': ['open', 'in-progress', 'done', 'cancelled'],
                'terminal_options': ['done', 'cancelled']}]}}]
        if args[:2] == ('item', 'deps'):
            value = self.edges.get(args[2], [])
            if isinstance(value, Exception):
                raise value
            return value
        if args[:3] == ('item', 'list', 'plans'):
            return list(self.parents.values())
        if args[:3] == ('item', 'list', 'tasks'):
            return self.tasks
        if args[:2] == ('item', 'show'):
            if args[2] in self.parents:
                return self.parents[args[2]]
            for task in self.tasks:
                if task['ref'] == args[2]:
                    return task
            raise CommandError('missing item')
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

    def test_card_mentions_do_not_claim_related_tasks(self):
        other = self.path + '/inventory'
        self.c['builder']['slots'].append(other)
        self.inventory['worktrees'] = [
            {'path': other, 'comment': 'TASK-2751 ...; per Main exception skip tier-e until TASK-2753, retain local proof...'},
            {'path': self.path, 'comment': 'TASK-2750 done ... TASK-2753 handoff, then 2756/2739'},
        ]
        self.tasks.append(self.item('TASK-2753', 'high', '2026-03-02'))
        claims = dispatcher.assignments(self.c, self.inventory)
        self.assertEqual({ref for ref, _, _ in claims}, {'TASK-2751', 'TASK-2750'})
        self.assertEqual(dispatcher.check_launch(
            self.c, 'TASK-2753', self.inventory, self.path)['ref'], 'TASK-2753')

    def test_leading_card_claim_blocks_only_other_slots(self):
        other = self.path + '/other'
        self.c['builder']['slots'].append(other)
        self.inventory['worktrees'].append({'path': other})
        for prefix in ('', ' \n', '✅ ', 'in-progress: ', '🚧 working: ',
                       '\u23f3 ', '\u2b50 ', '\U0001f469\u200d\U0001f4bb ',
                       '\U0001f44d\U0001f3fd ', '1\ufe0f\u20e3 ',
                       '\u203c\ufe0f ', '\u2194\ufe0f ', ': ',
                       '\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f '):
            with self.subTest(prefix=prefix):
                self.inventory['worktrees'][1]['comment'] = prefix + 'TASK-2: assigned; TASK-3 next'
                with self.assertRaisesRegex(ValueError, 'TASK-2 already assigned'):
                    dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
                self.assertEqual(dispatcher.duplicate_reasons(
                    'TASK-2', dispatcher.assignments(self.c, self.inventory), other), [])

    def test_nonleading_card_mentions_claim_nothing(self):
        for comment in ('Next task is TASK-2', 'Waiting for TASK-2', 'See TASK-2',
                        'note: TASK-2', 'done with TASK-2', '', None):
            with self.subTest(comment=comment):
                self.inventory['worktrees'][0]['comment'] = comment
                self.assertEqual(dispatcher.assignments(self.c, self.inventory), [])


    def test_title_task_reference_does_not_block_another_task(self):
        other = self.path + '/other'
        self.c['builder']['slots'].append(other)
        self.inventory['worktrees'].append({'path': other})
        with patch.object(dispatcher, 'run', side_effect=lambda args, cwd:
                          'task-2-unblock-task-3' if cwd == other else 'main'):
            self.assertEqual(dispatcher.check_launch(
                self.c, 'TASK-3', self.inventory, self.path)['ref'], 'TASK-3')
            with self.assertRaisesRegex(ValueError, 'TASK-2 already assigned'):
                dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)

    def test_carried_ref_is_excluded_from_next_and_shared_launch_gate(self):
        receipt = {'id': 'launch', 'ref': 'TASK-1', 'worktree': {'path': self.path, 'id': 'tree'},
                   'terminal': 'term', 'terminal_incarnation_id': 'inc'}
        state.write(self.c, 'launches', {self.path: receipt})
        state.write(self.c, 'carries', {'launch': {'TASK-2': {
            'ref': 'TASK-2', 'slot': self.path, 'launch_id': 'launch'}}})
        self.builder['shell_ready'] = False
        row = next(row for row in self.propose()['tasks'] if row['ref'] == 'TASK-2')
        self.assertIn('carried launch receipt', '; '.join(row['reasons']))
        for retiring in (None, receipt):
            with self.assertRaisesRegex(ValueError, 'TASK-2 already assigned.*carried launch receipt'):
                dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path, retiring=retiring)
        self.builder['shell_ready'] = True
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')

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

    def test_nearest_plan_and_broken_ancestry(self):
        task = self.tasks[0]
        task['parent_ref'] = 'TASK-parent'
        self.parents['TASK-parent'] = {'ref': 'TASK-parent', 'parent_ref': 'PLAN-1'}
        self.assertEqual(dispatcher.plan_reasons(self.c, task), [])
        self.parents['PLAN-1']['fields']['status'] = 'paused'
        self.parents['PLAN-1']['parent_ref'] = 'PLAN-outer'
        self.parents['PLAN-outer'] = {'ref': 'PLAN-outer', 'fields': {'status': 'active'}}
        self.assertIn('not active', dispatcher.plan_reasons(self.c, task)[0])
        self.parents['TASK-parent']['parent_ref'] = task['ref']
        self.assertIn('cycle', dispatcher.plan_reasons(self.c, task)[0])
        self.parents['TASK-parent']['parent_ref'] = 'TASK-missing'
        self.assertIn('unreadable parent TASK-missing', dispatcher.plan_reasons(self.c, task)[0])
        self.parents['TASK-parent'].pop('parent_ref')
        self.assertIn('no PLAN', dispatcher.plan_reasons(self.c, task)[0])

    def test_ancestry_depth_boundary(self):
        task = self.tasks[0]
        task['parent_ref'] = 'TASK-parent-0'
        for n in range(19):
            self.parents[f'TASK-parent-{n}'] = {
                'ref': f'TASK-parent-{n}',
                'parent_ref': f'TASK-parent-{n + 1}' if n < 18 else 'PLAN-1'}
        self.assertEqual(dispatcher.plan_reasons(self.c, task), [])
        self.parents['TASK-parent-18']['parent_ref'] = 'TASK-parent-19'
        self.parents['TASK-parent-19'] = {'ref': 'TASK-parent-19', 'parent_ref': 'PLAN-1'}
        self.assertIn('exceeds 20', dispatcher.plan_reasons(self.c, task)[0])

    def test_workspace_status_schema_and_shared_launch_gate(self):
        collection = [{'slug': 'tasks', 'schema': {'fields': [{
            'key': 'status', 'default': 'todo',
            'options': ['todo', 'in-progress', 'blocked', 'done'],
            'terminal_options': ['done']}]}}]
        original = self.pad
        with patch.object(dispatcher, 'pad', side_effect=lambda c, *args:
                          collection if args == ('collection', 'list') else original(c, *args)):
            self.tasks[2]['fields']['status'] = 'todo'
            self.tasks[2]['parent_ref'] = self.tasks[1]['ref']
            self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
            dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
            for status in ('blocked', 'unrecognized', 'done', None):
                self.tasks[2]['fields']['status'] = status
                with self.assertRaisesRegex(ValueError, 'status is not eligible'):
                    dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
            self.tasks[2]['fields']['status'] = 'in-progress'
            self.assertIsNone(self.propose()['proposal'])
            dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
            self.block('cancelled')
            with self.assertRaisesRegex(ValueError, 'blocked by TASK-9'):
                dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
            self.block('done')
            dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)

    def test_explicit_status_mapping_and_validation(self):
        self.c['pad'] = {'statuses': {'not_started': ['queued'], 'in_progress': ['working'],
                                     'terminal': ['finished', 'abandoned']}}
        self.tasks[2]['fields']['status'] = 'queued'
        self.block('finished')
        self.assertEqual(self.propose()['proposal']['ref'], 'TASK-2')
        dispatcher.check_launch(self.c, 'TASK-2', self.inventory, self.path)
        self.c['pad']['statuses']['in_progress'] = ['queued']
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            self.propose()
        for bad in ([], 'working', [None]):
            self.c['pad']['statuses']['in_progress'] = bad
            with self.assertRaisesRegex(ValueError, 'non-empty lists'):
                self.propose()

    def test_schema_unavailable_defaults_but_malformed_refuses(self):
        with patch.object(dispatcher, 'pad', side_effect=CommandError('old CLI')):
            statuses = dispatcher.status_policy(self.c)
        self.assertEqual(dispatcher.eligibility(self.c, self.tasks[0], statuses,
                         cache=self.parents), [])
        with patch.object(dispatcher, 'pad', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'schema invalid'):
                dispatcher.status_policy(self.c)


if __name__ == '__main__':
    unittest.main()

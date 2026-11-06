import copy
import tempfile
import unittest
from unittest.mock import patch
from fleet import governor, state, supervisor, snapshot
from fleet.commands import CommandError


class GovernorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = {'_state': self.tmp.name, 'root': self.tmp.name, 'repo': 'owner/project',
                  'pad_workspace': 'workspace', 'quota': {'park_timeout_seconds': 10},
                  'coordinator': {'run': 'scratch', 'terminal': 'coordinator'},
                  'hooks': {}, 'status': {'url': '', 'token_env': ''}}
        self.builders = [dict(terminal=x, path='/' + x, task='TASK-' + str(i + 1),
                              branch=x, worktree=x, connected=True, shell_ready=False, flags=[], incarnation_id='inc-' + x)
                         for i, x in enumerate(('a', 'b'))]
        self.s = {'quota': {'known': True, 'may_start': False}, 'errors': [],
                  'inventory': {'builders': self.builders, 'errors': [], 'stray_worktrees': [],
                                'worktrees': [dict(id=x, branch=x) for x in ('a', 'b')]}}
        self.ledger = {}
        self.priorities = {'TASK-1': 'high', 'TASK-2': 'low'}
        self.patch = patch.object(governor, 'json_run', side_effect=lambda args, cwd:
                                  {'fields': '{"priority":"' + self.priorities[args[-3]] + '"}'})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def advance(self, now=0):
        return governor.advance(self.c, self.s, self.ledger, now)

    def history(self, a=1, b=2):
        state.write(self.c, 'launches', {row['path']: dict(id=row['terminal'], at=at,
                    terminal=row['terminal'], ref=row['task'], terminal_incarnation_id=row['incarnation_id'])
                    for row, at in zip(self.builders, (a, b))})

    def test_priority_alone_selects_lowest_without_history(self):
        events = self.advance()
        self.assertEqual(events[0]['recipients'], ['b', 'run:scratch', 'pad:TASK-2'])
        self.assertEqual(self.advance(), [])

    def test_ties_require_history_then_choose_newest(self):
        self.priorities['TASK-1'] = 'low'
        self.assertIn('history unresolved', self.advance()[0]['subject'])
        self.assertIsNone(self.ledger['governor']['active'])
        self.assertEqual(self.advance(), [])
        self.history(a=3, b=2)
        self.assertEqual(self.advance()[0]['recipients'][0], 'a')

    def test_reincarnation_before_selection_never_borrows_old_launch_time(self):
        for tied in (False, True):
            with self.subTest(tied=tied):
                self.ledger = {}
                self.builders[1]['incarnation_id'] = 'original'
                self.priorities['TASK-1'] = 'low' if tied else 'high'
                self.history()
                self.builders[1]['incarnation_id'] = 'replacement'
                events = self.advance()
                self.assertTrue(all(not e.get('park') for e in events))
                self.assertIsNone(self.ledger['governor']['active'])
                self.assertEqual(self.advance(), [])

    def test_unresolvable_priority_ranks_below_low(self):
        self.priorities['TASK-1'] = 'unrecognized'
        self.assertEqual(self.advance()[0]['recipients'][0], 'a')

    def test_unknown_never_parks_or_delivers_queued_park(self):
        event = self.advance()[0]
        self.s['quota'] = {'known': False, 'may_start': False}
        self.assertEqual(self.advance(), [])
        self.assertFalse(governor.deliverable(self.c, event, self.s, self.ledger))
        self.ledger = {}
        self.assertEqual(self.advance(), [])
        self.assertIsNone(self.ledger['governor']['active'])

    def test_disconnected_or_changed_owner_cannot_receive_pending_park(self):
        event = self.advance()[0]
        self.builders[1]['connected'] = False
        self.assertFalse(governor.deliverable(self.c, event, self.s, self.ledger))
        self.builders[1]['connected'] = True
        self.builders[1]['terminal'] = 'replacement'
        self.assertFalse(governor.deliverable(self.c, event, self.s, self.ledger))

    def test_same_terminal_relaunch_or_incarnation_change_never_receives_old_park(self):
        self.history()
        event = self.advance()[0]
        launches = state.read(self.c, 'launches')
        launches['/b']['id'] = 'new-session'
        state.write(self.c, 'launches', launches)
        self.assertFalse(governor.deliverable(self.c, event, self.s, self.ledger))
        events = self.advance(1)
        self.assertTrue(all(not e.get('park') for e in events))
        self.assertEqual(self.ledger['governor']['active']['launch_id'], 'b')
        launches['/b']['id'] = 'b'
        state.write(self.c, 'launches', launches)
        self.builders[1]['incarnation_id'] = 'replacement'
        self.assertFalse(governor.deliverable(self.c, event, self.s, self.ledger))

    def test_shell_exit_keeps_failed_run_and_pad_copies_retryable(self):
        received = []
        fail = True
        def enqueue(c, e, r):
            if e.get('park'):
                if fail and r != 'b':
                    raise CommandError('offline')
                received.append(r)
        with patch.object(supervisor.snapshot, 'collect', side_effect=lambda c: copy.deepcopy(self.s)), \
                patch.object(supervisor, 'enqueue', side_effect=enqueue):
            supervisor.cycle(self.c)
            self.assertEqual(received, ['b'])
            self.builders[1]['shell_ready'] = True
            self.s['quota']['may_start'] = True
            fail = False
            supervisor.cycle(self.c)
            supervisor.cycle(self.c)
        self.assertEqual(received, ['b', 'run:scratch', 'pad:TASK-2'])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_timeout_disconnect_and_missing_inventory_never_free_another_lane(self):
        self.advance()
        self.builders[1]['connected'] = False
        events = self.advance(11)
        self.assertIn('timeout', events[0]['subject'])
        self.assertEqual(self.advance(12), [])
        self.s.pop('inventory')
        self.assertTrue(all(not e.get('park') for e in self.advance(20)))
        self.assertEqual(self.ledger['governor']['active']['terminal'], 'b')

    def test_exit_rechecks_then_recovery_reports_parked_and_recurrence(self):
        self.advance()
        self.builders[1]['shell_ready'] = True
        events = self.advance(1)
        self.assertEqual([p['ref'] for p in self.ledger['governor']['parked']], ['TASK-2'])
        self.assertEqual(events[1]['recipients'][0], 'a')
        self.builders[0]['shell_ready'] = True
        self.advance(2)
        self.s['quota']['may_start'] = True
        recovery = self.advance(3)
        self.assertIn('TASK-1, TASK-2', recovery[0]['body'])
        self.assertEqual(self.advance(4), [])
        self.builders[1]['shell_ready'] = False
        self.s['quota']['may_start'] = False
        self.assertEqual(self.advance(5)[0]['recipients'][0], 'b')

    def test_partial_delivery_restarts_without_duplicate_builder_message(self):
        delivered = []
        def enqueue(c, e, r):
            if e.get('park'):
                if r == 'pad:TASK-2' and delivered.count(r) == 0:
                    delivered.append(r)
                    raise CommandError('offline')
                delivered.append(r)
        with patch.object(supervisor.snapshot, 'collect', side_effect=lambda c: copy.deepcopy(self.s)), \
                patch.object(supervisor, 'enqueue', side_effect=enqueue):
            supervisor.cycle(self.c)
            supervisor.cycle(self.c)
            supervisor.cycle(self.c)
        self.assertEqual(delivered, ['b', 'run:scratch', 'pad:TASK-2', 'pad:TASK-2'])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_partial_delivery_receipts_survive_seven_day_expiry(self):
        delivered = []
        pad_available = False
        def enqueue(c, event, recipient):
            if event.get('park'):
                if recipient.startswith('pad:') and not pad_available:
                    raise CommandError('long Pad outage')
                delivered.append(recipient)
        with patch.object(supervisor.snapshot, 'collect', side_effect=lambda c: copy.deepcopy(self.s)), \
                patch.object(supervisor, 'enqueue', side_effect=enqueue), \
                patch.object(supervisor.time, 'time', return_value=100) as clock:
            supervisor.cycle(self.c)
            clock.return_value = 8 * 86400
            supervisor.cycle(self.c)
            supervisor.cycle(self.c)
            pad_available = True
            supervisor.cycle(self.c)
        self.assertEqual(delivered, ['b', 'run:scratch', 'pad:TASK-2'])
        self.assertEqual(state.read(self.c, 'supervisor')['pending'], [])

    def test_drill_cannot_override_real_launch_gate(self):
        c = {**self.c, '_drill_usage_file': 'fixture', 'builder': {'max': 3}}
        s = {**self.s, 'quota': {'may_start': True}, 'real_quota': {'may_start': False, 'reason': 'usage unavailable or stale'}, 'disk': {'low': False}}
        admission = snapshot.may_start(c, s)
        self.assertFalse(admission['may_start'])
        self.assertIn('real quota: usage unavailable or stale', admission['reasons'])


if __name__ == '__main__':
    unittest.main()

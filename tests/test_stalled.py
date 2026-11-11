import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import health, state, supervisor


class StalledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.logs = root / 'sessions'
        self.logs.mkdir()
        self.c = {'_state': str(root / 'state'), 'root': '/root', 'orca_repo_id': 'repo',
                  'coordinator': {'terminal': 'main'},
                  'builder': {'slots': ['/slot'], 'resolved_model': 'provider/model',
                              'idle_seconds': 600, 'stall_minutes': 1, 'stall_after_ci_minutes': 0.5}}
        self.terminal = {'handle': 'lane', 'worktreeId': 'tree', 'incarnationId': 'inc',
                         'connected': True, 'lastOutputAt': 200_000}
        self.record = {'id': 'launch', 'ref': 'TASK-61', 'terminal': 'lane',
                       'terminal_incarnation_id': 'inc', 'worktree': {'path': '/slot'},
                       'session_dir': str(self.logs), 'resolved_model': 'provider/model'}
        state.write(self.c, 'launches', {'/slot': self.record})
        self.ui = 'working'
        self.log(100)

    def log(self, second):
        from datetime import datetime, timezone
        stamp = datetime.fromtimestamp(second, timezone.utc).isoformat()
        events = [{'type': 'session', 'id': 's', 'cwd': '/slot', 'timestamp': stamp},
                  {'type': 'model_change', 'model': 'provider/model',
                   'resolvedModelIsFallback': False, 'timestamp': stamp}]
        (self.logs / 'session.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))

    def native(self, c, *args):
        if args[:2] == ('worktree', 'list'):
            return {'worktrees': [{'id': 'tree', 'repoId': 'repo', 'path': '/slot', 'branch': 'task-61'}]}
        if args[:2] == ('terminal', 'list'):
            return {'terminals': [self.terminal]}
        if args[:2] == ('orchestration', 'run-current'):
            return {'run': None}
        glyph = 'π' if self.ui == 'idle' else '⠙ 30s'
        gear = ' ⚙ 1' if self.ui == 'background' else ''
        return {'terminal': {'tail': [f' {glyph} > ◑ model >{gear}', '╰─']}}

    def collect(self, now=200):
        with patch.object(health, 'orca', side_effect=self.native):
            return health.collect(self.c, now)

    def test_spinner_output_does_not_count_as_progress(self):
        b = self.collect()['builders'][0]
        self.assertIn('stalled', b['flags'])
        self.assertEqual(b['idle_seconds'], 0)
        self.assertEqual(b['progress']['event_age_seconds'], 100)

    def test_background_is_progress_even_after_ninety_minutes(self):
        self.ui = 'background'
        self.log(100)
        self.assertNotIn('stalled', self.collect(5500)['builders'][0]['flags'])
        self.ui = 'idle'
        self.assertNotIn('stalled', self.collect()['builders'][0]['flags'])

    def test_failed_owned_pr_requires_no_progress_since_failure(self):
        self.log(150)
        self.ui = 'idle'
        inventory = self.collect()
        check = {'name': 'CI', 'conclusion': 'failure', 'completed_at': '1970-01-01T00:02:40Z'}
        pr = {'number': 7, 'state': 'open', 'branch': 'task-61', 'checks': [check]}
        health.add_pr_stalls(self.c, inventory, {'prs': [pr]}, 200)
        self.assertIn('stalled', inventory['builders'][0]['flags'])
        self.assertEqual(inventory['builders'][0]['owned_prs'][0]['failed_checks'], ['CI'])
        for stamp, now, branch, status in [(170, 200, 'task-61', 'open'),
                                          (150, 180, 'task-61', 'open'),
                                          (150, 200, 'other', 'open'),
                                          (150, 200, 'task-61', 'closed')]:
            self.log(stamp)
            inventory = self.collect(now)
            health.add_pr_stalls(self.c, inventory, {'prs': [{**pr, 'branch': branch, 'state': status}]}, now)
            self.assertNotIn('stalled', inventory['builders'][0]['flags'])

    def test_background_failed_pr_never_stalls(self):
        self.ui = 'background'
        self.c['builder']['stall_minutes'] = 1000
        inventory = self.collect(5500)
        health.add_pr_stalls(self.c, inventory, {'prs': [{
            'number': 7, 'state': 'open', 'branch': 'task-61', 'checks': [
                {'name': 'CI', 'conclusion': 'cancelled', 'completed_at': '1970-01-01T00:02:40Z'}]}]}, 5500)
        self.assertNotIn('stalled', inventory['builders'][0]['flags'])

    def test_unknown_progress_never_silently_healthy(self):
        for content in ('', '{broken\n', json.dumps({'type': 'session', 'cwd': '/other', 'timestamp': '1970-01-01T00:01:00Z'})):
            (self.logs / 'session.jsonl').write_text(content)
            b = self.collect()['builders'][0]
            self.assertIn('progress_unknown', b['flags'])
            self.assertNotIn('stalled', b['flags'])
        self.log(100)
        self.terminal['incarnationId'] = 'replacement'
        self.assertIn('progress_unknown', self.collect()['builders'][0]['flags'])
        state.write(self.c, 'launches', {})
        self.assertIn('progress_unknown', self.collect()['builders'][0]['flags'])

    def test_title_preamble_and_exact_threshold(self):
        path = self.logs / 'session.jsonl'
        path.write_text(json.dumps({'type': 'title', 'title': 'scratch'}) + '\n' + path.read_text())
        self.assertNotIn('stalled', self.collect(159)['builders'][0]['flags'])
        self.assertIn('stalled', self.collect(160)['builders'][0]['flags'])
        path.unlink()
        self.assertIn('progress_unknown', self.collect()['builders'][0]['flags'])

    def test_unknown_progress_is_nonurgent(self):
        state.write(self.c, 'launches', {})
        event = supervisor.alerts({'inventory': self.collect(), 'quota': {'may_start': True}}, self.c)[0]
        self.assertNotIn('type', supervisor.urgent_event(self.c, event))

    def test_stall_alert_has_context_without_age_flapping(self):
        inventory = self.collect()
        snapshot = {'inventory': inventory, 'quota': {'may_start': True}}
        first = supervisor.alerts(snapshot, self.c)[0]
        self.assertIn('TASK-61', first['body'])
        self.assertIn('100', first['body'])
        self.assertIn('working', first['body'])
        inventory['builders'][0]['progress']['event_age_seconds'] = 101
        second = supervisor.alerts(snapshot, self.c)[0]
        self.assertEqual(supervisor.event_signature(first), supervisor.event_signature(second))
        self.assertEqual(supervisor.urgent_event(self.c, first)['type'], 'question')

    def test_event_during_inventory_is_recent_progress(self):
        self.log(201)
        with patch.object(health.time, 'time', return_value=202):
            b = self.collect(200)['builders'][0]
        self.assertNotIn('progress_unknown', b['flags'])
        self.assertEqual(b['progress']['event_age_seconds'], 0)

    def test_reused_lane_reads_replacement_receipt_next_cycle(self):
        old = self.collect()['builders'][0]
        self.assertEqual(old['progress']['launch_id'], 'launch')
        self.record.update(id='replacement', ref='TASK-62', at=190,
                           session_dir=str(self.logs / 'replacement'))
        state.write(self.c, 'launches', {'/slot': self.record})
        # Same terminal/incarnation, old JSONL still healthy: it cannot be reused.
        missing = self.collect()['builders'][0]
        self.assertIsNone(missing['progress'])
        self.assertEqual((missing['launch_id'], missing['launch_at']), ('replacement', 190))
        self.assertIn('model_unknown', missing['flags'])
        self.assertIn('progress_unknown', missing['flags'])
        self.logs = self.logs / 'replacement'
        self.logs.mkdir()
        self.log(195)
        fresh = self.collect()['builders'][0]
        self.assertEqual((fresh['progress']['launch_id'], fresh['progress']['task'],
                          fresh['progress']['last_event_at']),
                         ('replacement', 'TASK-62', 195))
        self.assertNotIn('progress_unknown', fresh['flags'])
        self.assertNotIn('model_unknown', fresh['flags'])

    def test_receipt_identity_never_graces_an_unrelated_launch(self):
        self.record['at'] = 195
        for field, value in [('terminal', 'other'), ('terminal_incarnation_id', 'other'),
                             ('worktree', {'path': '/other'}), ('id', None)]:
            with self.subTest(field=field):
                record = dict(self.record, **{field: value})
                state.write(self.c, 'launches', {'/slot': record})
                builder = self.collect()['builders'][0]
                self.assertIsNone(builder['launch_at'])
                self.assertIsNone(builder['launch_id'])
                self.assertIsNone(builder['progress'])
                self.assertIn('progress_unknown', builder['flags'])

    def test_receipt_time_is_not_inferred_from_session_or_first_observation(self):
        for at in (None, True, '195', -1, 0, float('nan'), float('inf'), 195):
            with self.subTest(at=at):
                self.record['at'] = at
                state.write(self.c, 'launches', {'/slot': self.record})
                builder = self.collect()['builders'][0]
                self.assertEqual(builder['launch_at'], 195 if at == 195 else None)
                self.assertEqual(builder['progress']['last_event_at'], 100)

    def test_partial_session_append_remains_unknown_then_recovers_next_cycle(self):
        self.record['at'] = 100
        state.write(self.c, 'launches', {'/slot': self.record})
        path = self.logs / 'session.jsonl'
        path.write_text(path.read_text() + '{"type":"model_change"')
        # Even a known launch cannot hide damaged/incomplete evidence in health.
        builder = self.collect(200)['builders'][0]
        self.assertEqual(builder['launch_at'], 100)
        self.assertIn('model_unknown', builder['flags'])
        self.assertIn('progress_unknown', builder['flags'])
        self.log(201)
        builder = self.collect(202)['builders'][0]
        self.assertEqual(builder['progress']['last_event_at'], 201)
        self.assertNotIn('progress_unknown', builder['flags'])

    def test_same_name_failure_rerun_is_new_alert_evidence(self):
        self.ui = 'idle'
        def alert(check_id):
            inventory = self.collect()
            health.add_pr_stalls(self.c, inventory, {'prs': [{
                'number': 7, 'head': 'sha', 'state': 'open', 'branch': 'task-61',
                'checks': [{'id': check_id, 'name': 'CI', 'conclusion': 'failure',
                            'completed_at': '1970-01-01T00:02:40Z'}]}]}, 200)
            return supervisor.alerts({'inventory': inventory, 'quota': {'may_start': True}}, self.c)[0]
        first, rerun = alert(1), alert(2)
        self.assertNotEqual(supervisor.event_signature(first), supervisor.event_signature(rerun))

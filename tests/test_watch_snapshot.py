import copy
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fleet import snapshot, state, watcher
from test_initial_events import history, pull


class SharedSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name, 'interval_seconds': 60}
        self.now = 1000
        self.current = history(pull(auto_merge={'enabled': True}))
        self.saved = dict(repo=self.c['repo'], generated_at=1000, github=self.current,
                          collection={'finished_at': 1000, 'period_seconds': 60})
        env = patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''})
        env.start()
        self.addCleanup(env.stop)

    def watch(self, timeout=0):
        return watcher.watch(self.c, 7, timeout=timeout, interval=60)

    def test_fresh_snapshot_avoids_network_and_reports_budget_metadata(self):
        self.saved['github_budget'] = {'blocked': True, 'reset': 2000}
        state.write(self.c, 'snapshot', self.saved)
        self.c['_github_debug'] = True
        stderr = io.StringIO()
        with patch.object(watcher.time, 'time', return_value=1075), patch.object(
                watcher.github, 'collect', side_effect=AssertionError('network')), patch('sys.stderr', stderr):
            self.assertEqual(self.watch()['event'], 'timeout')
        debug = json.loads(stderr.getvalue())
        self.assertEqual(debug['github_poll']['requests'], 0)
        self.assertEqual(debug['snapshot'], 'fresh')
        self.assertTrue(debug['snapshot_budget']['blocked'])

    def test_fallback_reasons_collect_requested_pr(self):
        auth = patch('fleet.github_auth.resolve', return_value=(None, {'kind': 'app_installation'}))
        auth.start()
        self.addCleanup(auth.stop)
        variants = [('missing', None), ('stale', {**self.saved,
                    'collection': {'finished_at': 924, 'period_seconds': 60}, 'generated_at': 924}),
                    ('repo_mismatch', {**self.saved, 'repo': 'other/repo'}),
                    ('unavailable', {**self.saved, 'github': None}),
                    ('pr_absent', {**self.saved, 'github': history(pull(number=8))})]
        for reason, saved in variants:
            with self.subTest(reason=reason):
                state.write(self.c, 'snapshot', saved)
                self.c['_github_debug'] = True
                stderr = io.StringIO()
                with patch.object(watcher.time, 'time', return_value=1000), patch.object(
                        watcher.github, 'collect', return_value=self.current) as collect, patch('sys.stderr', stderr):
                    self.watch()
                collect.assert_called_once_with(self.c, 7)
                self.assertEqual(json.loads(stderr.getvalue())['snapshot'], reason)

    def follow(self, samples):
        clock = [1000]
        samples = iter(samples)
        def publish():
            self.saved.update(github=next(samples), generated_at=clock[0],
                              collection={'finished_at': clock[0], 'period_seconds': 60})
            state.write(self.c, 'snapshot', self.saved)
        def sleep(seconds):
            clock[0] += seconds
            publish()
        publish()
        with patch.object(watcher.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'sleep', side_effect=sleep), \
                patch.object(watcher.github, 'collect', side_effect=AssertionError('network')):
            return self.watch(timeout=600)

    def queued(self, *, position=2, runs=(), state='open', queued=True):
        current = history(pull(state=state, auto_merge=None,
                               merge_queue_entry={'id': 'q'} if queued else None,
                               merge_queue_unknown=False), runs=runs)
        current['merge_queue'] = ([{
            'position': position, 'state': 'QUEUED', 'headCommit': None,
            'pullRequest': {'number': 7}, 'runs': []}] if queued else [])
        return current

    def test_missing_group_evidence_keeps_following_until_merged(self):
        failed = {'id': 1, 'head_sha': 'old', 'prs': [7],
                  'member_heads': {'7': 'sha'}, 'membership_complete': True,
                  'conclusion': 'failure', 'url': 'https://example/run/1'}
        unknown = {'id': 2, 'head_sha': 'new', 'prs': [],
                   'membership_complete': False, 'conclusion': None}
        missing = self.queued()
        missing.pop('merge_group_runs')
        result = self.follow([self.queued(runs=[failed, unknown]),
                              missing, self.queued(state='merged', queued=False)])
        self.assertEqual(result['event'], 'merged')
        self.assertFalse(any('group:' in e['key'] for e in result['events']))

    def test_queue_position_progress_then_merged(self):
        first = self.queued()
        progress = self.queued(position=1)
        result = self.follow([first, progress])
        self.assertEqual([e['subject'] for e in result['events']],
                         ['PR #7 merge_queue progress'])
        result = self.follow([progress, self.queued(state='merged', queued=False)])
        self.assertEqual(result['event'], 'merged')

    def test_queue_group_checks_progress(self):
        first = self.queued()
        first['merge_queue'][0]['runs'] = [{'id': 4, 'status': 'queued', 'conclusion': None}]
        progress = copy.deepcopy(first)
        progress['merge_queue'][0]['runs'][0]['status'] = 'in_progress'
        result = self.follow([first, progress])
        self.assertEqual([e['subject'] for e in result['events']],
                         ['PR #7 merge_queue progress'])

    def test_queue_failure_and_ejection_are_both_reported(self):
        failed = {'id': 3, 'head_sha': 'new', 'prs': [7],
                  'member_heads': {'7': 'sha'}, 'membership_complete': True,
                  'conclusion': 'failure', 'url': 'https://example/run/3'}
        result = self.follow([self.queued(), self.queued(runs=[failed], queued=False)])
        self.assertEqual({e['subject'] for e in result['events']},
                         {'PR #7 merge_queue exited', 'Merge group 3 failure'})

    def test_terminal_result_bypasses_seen_receipts_every_time(self):
        for terminal in ('merged', 'closed'):
            self.saved['github'] = history(pull(state=terminal))
            state.write(self.c, 'snapshot', self.saved)
            with patch.object(watcher.time, 'time', return_value=1000), patch.object(
                    watcher.github, 'collect', side_effect=AssertionError('network')), patch.object(
                    watcher.time, 'sleep', side_effect=AssertionError('sleep')):
                for _ in range(2):
                    result = watcher.watch(self.c, 7, terminal='same-caller')
                    self.assertEqual(result['event'], terminal)
                    self.assertEqual(result['pr'], 7)

    def test_unchanged_armed_backoff_caps_and_resets_on_non_waking_change(self):
        auth = patch('fleet.github_auth.resolve', return_value=(None, {'kind': 'app_installation'}))
        auth.start()
        self.addCleanup(auth.stop)
        clock = [0]
        sleeps = []
        samples = []
        for i in range(7):
            current = copy.deepcopy(self.current)
            current['since'] = f'rolling-window-{i}'
            current['prs'].append(pull(number=8, head=f'unrelated-head-{i}'))
            if i >= 5:
                current['prs'][0]['updated_at'] = 'later'
            samples.append(current)
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        with patch.object(watcher.github, 'collect', side_effect=samples), patch.object(
                watcher.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                watcher.time, 'sleep', side_effect=sleep):
            result = self.watch(timeout=1200)
        self.assertEqual(result['event'], 'timeout')
        self.assertEqual(sleeps, [60, 120, 240, 300, 300, 60, 120])

    def test_collection_duration_and_cadence_extend_freshness(self):
        self.saved.update(generated_at=800, collection={
            'finished_at': 865, 'period_seconds': 130, 'expected_next_at': 995})
        state.write(self.c, 'snapshot', self.saved)
        with patch.object(snapshot.time, 'time', return_value=1000):
            saved, info = snapshot.shared(self.c)
            self.assertEqual(saved['github'], self.current)
            self.assertEqual(info['source'], 'fresh')
            self.assertEqual(info['expected_next_at'], 995)
        with patch.object(snapshot.time, 'time', return_value=1011):
            self.assertEqual(snapshot.shared(self.c)[1]['source'], 'stale')

    def test_stale_stock_waits_for_publication_without_github_calls(self):
        clock = [1000]
        self.saved.update(generated_at=800, collection={'finished_at': 800, 'period_seconds': 130})
        state.write(self.c, 'snapshot', self.saved)
        def publish(seconds):
            clock[0] += seconds
            state.write(self.c, 'snapshot', {**self.saved, 'generated_at': clock[0],
                        'collection': {'finished_at': clock[0], 'period_seconds': 130},
                        'github': history(pull(state='merged'))})
        with patch.object(watcher.time, 'time', side_effect=lambda: clock[0]), patch.object(
                watcher.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
                watcher.time, 'sleep', side_effect=publish), patch.object(
                watcher.github, 'collect', side_effect=AssertionError('stock network')):
            self.assertEqual(self.watch(timeout=60)['event'], 'merged')

    def test_stale_stock_and_invalid_app_return_distinct_unavailable(self):
        self.saved.update(generated_at=800, collection={'finished_at': 800, 'period_seconds': 60})
        state.write(self.c, 'snapshot', self.saved)
        self.c['github'] = {'read_auth': {'configured': True}}
        clock = [900]
        def sleep(seconds):
            clock[0] += seconds
        with patch('fleet.github_auth.resolve', return_value=(None, {'kind': 'stock', 'fallback': 'invalid'})), \
                patch.object(watcher.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'sleep', side_effect=sleep), \
                patch.object(watcher.github, 'collect', side_effect=AssertionError('stock network')):
            self.assertEqual(self.watch(timeout=180)['event'], 'snapshot_unavailable')
            self.assertEqual(clock[0], 1005)

    def test_status_reuses_github_but_keeps_local_health_current(self):
        self.c.update(builder={'resolved_model': 'default/model'},
                      disk={'path': self.c['_state'], 'min_free_gb': 0})
        self.current['repo_move_error'] = 'Repository moved; update configuration'
        state.write(self.c, 'snapshot', self.saved)
        with patch.object(snapshot.time, 'time', return_value=1000), \
                patch.object(snapshot, '_github', side_effect=AssertionError('network')), \
                patch.object(snapshot.health, 'collect', return_value={'builders': []}), \
                patch.object(snapshot.health, 'coordinator', return_value={'state': 'current'}), \
                patch.object(snapshot.health, 'add_pr_stalls'), \
                patch.object(snapshot.quota, 'collect', return_value={'known': True}):
            result = snapshot.collect(self.c, shared_github=True)
        self.assertEqual(result['github'], self.current)
        self.assertEqual(result['github_requests']['requests'], 0)
        self.assertEqual(result['coordinator']['state'], 'current')
        self.assertEqual(result['errors'], [self.current['repo_move_error']])

    def test_legacy_live_capture_uses_publication_not_collection_start(self):
        # TASK-145 real supervisor sample: collection took 61.023115s.
        self.saved.pop('collection')
        self.saved['generated_at'] = 1794706485.6789837
        state.write(self.c, 'snapshot', self.saved)
        published = 1794706546.7020988
        os.utime(state.directory(self.c) / 'snapshot.json', (published, published))
        with patch.object(snapshot.time, 'time', return_value=published + 120):
            self.assertEqual(snapshot.shared(self.c)[1]['source'], 'fresh')
        with patch.object(snapshot.time, 'time', return_value=published + 137):
            self.assertEqual(snapshot.shared(self.c)[1]['source'], 'stale')

    def test_missing_snapshot_wait_is_bounded_without_network(self):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'sleep', side_effect=sleep), \
                patch.object(watcher.github, 'collect', side_effect=AssertionError('network')):
            result = self.watch(timeout=300)
        self.assertEqual(result['event'], 'snapshot_unavailable')
        self.assertLessEqual(clock[0], 210)

    def test_fresh_but_unusable_publications_do_not_extend_wait(self):
        clock = [1000]
        def publish(seconds=0):
            clock[0] += seconds
            state.write(self.c, 'snapshot', {**self.saved, 'generated_at': clock[0],
                        'collection': {'finished_at': clock[0], 'period_seconds': 60},
                        'github': None})
        publish()
        with patch.object(watcher.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'sleep', side_effect=publish), \
                patch.object(watcher.github, 'collect', side_effect=AssertionError('network')):
            result = self.watch(timeout=600)
        self.assertEqual(result, {'pr': 7, 'event': 'snapshot_unavailable', 'reason': 'unavailable'})
        self.assertLessEqual(clock[0], 1210)

    def test_app_identity_loss_waits_for_shared_evidence_without_stock_reads(self):
        self.c.update(root=self.c['_state'], github={'read_auth': {'configured': True}})
        self.saved.update(generated_at=900, collection={'finished_at': 900, 'period_seconds': 60})
        state.write(self.c, 'snapshot', self.saved)
        clock = [1000]
        resolutions = [0]
        def resolve(c):
            resolutions[0] += 1
            if resolutions[0] <= 2:
                return {'GH_TOKEN': 'fixture-app-token'}, {'kind': 'app_installation'}
            return None, {'kind': 'stock', 'fallback': 'invalid'}
        def publish(seconds):
            clock[0] += seconds
            state.write(self.c, 'snapshot', {**self.saved, 'generated_at': clock[0],
                        'collection': {'finished_at': clock[0], 'period_seconds': 60},
                        'github': history(pull(state='merged'))})
        with patch('fleet.github_auth.resolve', side_effect=resolve), \
                patch('fleet.commands._execute', side_effect=AssertionError('stock network')), \
                patch.object(watcher.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(watcher.time, 'sleep', side_effect=publish):
            result = self.watch(timeout=60)
        self.assertEqual(result['event'], 'merged')
        self.assertEqual(clock[0], 1015)


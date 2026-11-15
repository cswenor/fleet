import copy
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fleet import state, watcher
from test_initial_events import history, pull


class SharedSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'repo': 'owner/repo', '_state': directory.name, 'interval_seconds': 60}
        self.now = 1000
        self.current = history(pull(auto_merge={'enabled': True}))
        self.saved = dict(repo=self.c['repo'], generated_at=1000, github=self.current)
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
        variants = [('missing', None), ('stale', {**self.saved, 'generated_at': 924}),
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
        clock = [0]
        sleeps = []
        samples = []
        for i in range(7):
            current = copy.deepcopy(self.current)
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


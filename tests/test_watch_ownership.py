"""Newest watch-pr invocation owns a repo/PR/caller without process signals."""
from contextlib import ExitStack
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fleet import github, github_api, state, watcher
from test_initial_events import history, pull


class WatchOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = {'repo': 'owner/repo', '_state': self.temp.name}
        self.now = 100.0
        self.sleep_hook = None
        self.current = history(pull())
        patches = [
            patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': ''}),
            patch.object(watcher.time, 'monotonic', side_effect=lambda: self.now),
            patch.object(watcher.time, 'time', side_effect=lambda: self.now),
            patch.object(watcher.time, 'sleep', side_effect=self.sleep),
            patch.object(github, 'collect', side_effect=lambda *args: self.current),
            patch.object(github_api, 'Session'),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.collect = mocks[-2]
        mocks[-1].return_value.__enter__.return_value = SimpleNamespace(stats={}, blocked_until=0)

    def sleep(self, seconds):
        self.now += seconds
        if self.sleep_hook:
            hook, self.sleep_hook = self.sleep_hook, None
            hook()

    def replace(self, *, config=None, number=7, terminal=None):
        return watcher.watch(config or self.c, number, timeout=0, terminal=terminal)

    def test_same_key_replacement_exits_before_next_collect(self):
        self.sleep_hook = lambda: self.assertEqual(
            self.replace(terminal='lane-a')['event'], 'timeout')
        result = watcher.watch(self.c, 7, timeout=30, interval=1, terminal='lane-a')
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})
        self.assertEqual(self.collect.call_count, 2)
        self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

    def test_none_caller_is_a_replaceable_key(self):
        self.sleep_hook = lambda: self.replace()
        result = watcher.watch(self.c, 7, timeout=30, interval=1)
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})
        self.assertEqual(self.collect.call_count, 2)

    def test_other_repo_pr_and_caller_do_not_replace(self):
        variants = [
            {'config': {**self.c, 'repo': 'other/repo'}, 'terminal': 'lane-a'},
            {'number': 8, 'terminal': 'lane-a'},
            {'terminal': 'lane-b'},
            {'terminal': None},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.sleep_hook = lambda: self.replace(**variant)
                result = watcher.watch(self.c, 7, timeout=2, interval=1, terminal='lane-a')
                self.assertEqual(result, {'pr': 7, 'event': 'timeout', 'seconds': 2})
                self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

    def test_environment_caller_wins_over_terminal_argument(self):
        with patch.dict(os.environ, {'ORCA_TERMINAL_HANDLE': 'environment'}):
            self.sleep_hook = lambda: self.replace(terminal='different-fallback')
            result = watcher.watch(self.c, 7, timeout=30, interval=1, terminal='fallback')
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})

    def test_initial_in_flight_collect_cannot_return_stale_event_or_receipt(self):
        stale = history(pull(merge_state='DIRTY'))

        def collect(*args):
            self.collect.side_effect = lambda *args: self.current
            self.replace(terminal='lane-a')
            return stale

        self.collect.side_effect = collect
        result = watcher.watch(self.c, 7, timeout=0, terminal='lane-a')
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})
        self.assertEqual(state.read(self.c, 'watch-pr', {}), {})

    def test_changed_in_flight_collect_cannot_return_stale_event_or_receipt(self):
        stale = history(pull(merge_state='DIRTY'))
        calls = 0

        def collect(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.replace(terminal='lane-a')
                return stale
            return self.current

        self.collect.side_effect = collect
        result = watcher.watch(self.c, 7, timeout=30, interval=1, terminal='lane-a')
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})
        self.assertEqual(state.read(self.c, 'watch-pr', {}), {})

    def test_replacement_at_deadline_returns_superseded_not_timeout(self):
        self.sleep_hook = lambda: self.replace()
        result = watcher.watch(self.c, 7, timeout=1, interval=60)
        self.assertEqual(result, {'pr': 7, 'event': 'superseded'})

    def test_old_cleanup_does_not_remove_active_replacement(self):
        with ExitStack() as old:
            old_key, old_generation = old.enter_context(watcher._ownership(self.c, None, 7))
            with watcher._ownership(self.c, None, 7) as (new_key, new_generation):
                self.assertEqual(old_key, new_key)
                self.assertNotEqual(old_generation, new_generation)
                old.close()
                self.assertEqual(state.read(self.c, 'watch-pr-owners'),
                                 {json.dumps(['owner/repo', 7, None]): new_generation})
        self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

    def test_ordinary_timeout_preserves_unknown_diagnostics(self):
        self.current = {**self.current, 'unavailable': ['checks unavailable']}
        result = watcher.watch(self.c, 7, timeout=2, interval=1)
        self.assertEqual(result, {'pr': 7, 'event': 'timeout', 'seconds': 2,
                                  'github_unknown': ['checks unavailable']})
        self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

    def test_initial_and_changed_events_keep_their_existing_shape(self):
        self.current = history(pull(merge_state='DIRTY'))
        result = watcher.watch(self.c, 7, timeout=0)
        self.assertEqual(set(result), {'pr', 'events'})
        self.assertIn('DIRTY', result['events'][0]['body'])
        self.current = history(pull())
        self.sleep_hook = lambda: setattr(self, 'current', history(pull(merge_state='DIRTY')))
        result = watcher.watch(self.c, 7, timeout=30, interval=1)
        self.assertEqual(set(result), {'pr', 'events'})
        self.assertIn('DIRTY', result['events'][0]['body'])
        self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

    def test_exception_releases_ownership(self):
        self.collect.side_effect = RuntimeError('collect failed')
        with self.assertRaisesRegex(RuntimeError, 'collect failed'):
            watcher.watch(self.c, 7, timeout=0)
        self.assertEqual(state.read(self.c, 'watch-pr-owners'), {})

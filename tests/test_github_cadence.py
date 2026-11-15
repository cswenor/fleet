from contextlib import ExitStack
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from fleet import github_idle, snapshot, state
from fleet.commands import CommandError


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = 1000
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(github_idle, 'time', SimpleNamespace(
            time=lambda: self.clock, monotonic=lambda: self.clock)))
        self.c = {'repo': 'owner/repo', 'interval_seconds': 60}

    def test_full_backoff_caps_without_backing_off_probes_and_resets_on_change(self):
        scheduler = github_idle.Scheduler(60)
        collector = Mock(return_value={'prs': [], 'idle': True, 'full_collection': True})
        for expected in (120, 240, 480, 600, 600):
            result = {}
            scheduler.collect(self.c, result, collector)
            self.assertEqual(result['github_collection']['full_interval_seconds'], expected)
            self.assertEqual(result['github_collection']['interval_seconds'], 60)
            due = scheduler.due
            self.clock += 60
            collector.return_value = {'prs': [], 'idle': True, 'full_collection': False}
            scheduler.collect(self.c, {}, collector)
            self.assertEqual(scheduler.due, due)
            self.clock = due
            collector.return_value = {'prs': [], 'idle': True, 'full_collection': True}
        collector.return_value = {'prs': [], 'idle': False, 'full_collection': True}
        scheduler.collect(self.c, {}, collector)
        self.assertEqual(scheduler.interval, 60)
        collector.side_effect = CommandError('github failed')
        with self.assertRaises(CommandError):
            scheduler.collect(self.c, {}, collector)
        self.assertEqual(scheduler.previous, {})
        self.assertFalse(scheduler.timing['successful'])

    def test_base_larger_than_cap_and_unknown_never_back_off(self):
        for base, data in ((900, {'idle': True, 'full_collection': True}),
                           (60, {'merge_queue_unknown': True})):
            scheduler = github_idle.Scheduler(base)
            scheduler.collect(self.c, {}, Mock(return_value=data))
            self.assertEqual(scheduler.interval, base)

    def test_probe_refreshes_freshness_without_refreshing_full_collection_timestamp(self):
        scheduler = github_idle.Scheduler(60)
        def collect(c, result, **kwargs):
            self.clock += 5
            return {'prs': [], 'idle': True, 'full_collection': kwargs['idle_full']}
        scheduler.collect(self.c, {}, collect)
        full_finished = scheduler.timing['full_finished_at']
        due = scheduler.due
        self.clock += 60
        result = {}
        scheduler.collect(self.c, result, collect)
        self.assertEqual(result['github_collection']['full_finished_at'], full_finished)
        self.assertEqual(scheduler.due, due)
        with tempfile.TemporaryDirectory() as directory, patch.object(snapshot.time, 'time', return_value=self.clock):
            self.c['_state'] = directory
            state.write(self.c, 'snapshot', {**result, 'repo': self.c['repo'],
                        'generated_at': self.clock, 'github': scheduler.previous})
            _, info = snapshot.shared(self.c)
            self.assertEqual(info['age_seconds'], 0)
            self.assertEqual(info['period_seconds'], 65)
        with patch.object(snapshot.time, 'time', return_value=self.clock + 81):
            # A new local publication cannot extend the last successful probe.
            state.write(self.c, 'snapshot', {**result, 'repo': self.c['repo'],
                        'generated_at': self.clock + 81, 'github': scheduler.previous})
            self.assertEqual(snapshot.shared(self.c)[1]['source'], 'stale')

    def test_failure_and_restart_do_not_launder_success_or_inherit_backoff(self):
        scheduler = github_idle.Scheduler(60)
        collector = Mock(side_effect=ValueError('unavailable'))
        result = {}
        with self.assertRaises(ValueError):
            scheduler.collect(self.c, result, collector)
        with tempfile.TemporaryDirectory() as directory:
            self.c['_state'] = directory
            state.write(self.c, 'snapshot', {**result, 'repo': self.c['repo'],
                        'generated_at': self.clock, 'github': {'prs': []}})
            self.assertEqual(snapshot.shared(self.c), (None, {'source': 'unavailable'}))
        restarted = github_idle.Scheduler(60)
        self.assertIsNone(restarted.due)
        self.assertEqual(restarted.interval, 60)
        self.assertEqual(restarted.previous, {})

    def test_local_health_and_quota_remain_current_during_idle_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.c.update(_state=directory, disk={'path': directory, 'min_free_gb': 0})
            scheduler = github_idle.Scheduler(60)
            with patch.object(snapshot.health, 'collect', side_effect=[{'builders': [], 'state': 'busy'}, {'builders': [], 'state': 'idle'}]), \
                    patch.object(snapshot.health, 'coordinator', side_effect=[{'state': 'idle'}, {'state': 'down'}]), \
                    patch.object(snapshot.health, 'add_pr_stalls'), \
                    patch.object(snapshot.quota, 'collect', side_effect=[{'known': True}, {'known': False}]), \
                    patch.object(snapshot, '_github', side_effect=[{'prs': [], 'idle': True, 'full_collection': True}, {'prs': [], 'idle': True, 'full_collection': False}]):
                first = snapshot.collect(self.c, github_scheduler=scheduler)
                self.clock += 60
                second = snapshot.collect(self.c, github_scheduler=scheduler)
                self.assertEqual(first['inventory']['state'], 'busy')
                self.assertEqual(second['inventory']['state'], 'idle')
                self.assertFalse(second['quota']['known'])
                self.assertEqual(second['coordinator']['state'], 'down')
                self.assertEqual(second['github_collection']['full_finished_at'], first['github_collection']['full_finished_at'])

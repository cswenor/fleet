from contextlib import ExitStack
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
        for name in ('time', 'monotonic'):
            self.stack.enter_context(patch.object(github_idle.time, name,
                                                 side_effect=lambda: self.clock))
        self.c = {'repo': 'owner/repo', 'interval_seconds': 60}

    def test_backoff_caps_and_resets_on_change_and_error(self):
        scheduler = github_idle.Scheduler(60)
        collector = Mock(return_value={'prs': [], 'idle': True})
        for expected in (120, 240, 480, 600, 600):
            result = {}
            scheduler.collect(self.c, result, collector)
            self.assertEqual(result['github_collection']['interval_seconds'], expected)
            self.clock += expected
        collector.return_value = {'prs': [], 'idle': False}
        scheduler.collect(self.c, {}, collector)
        self.assertEqual(scheduler.interval, 60)
        self.clock += 60
        collector.return_value = {'prs': [], 'idle': True}
        scheduler.collect(self.c, {}, collector)
        self.assertEqual(scheduler.interval, 120)
        self.clock += 120
        collector.side_effect = CommandError('github failed')
        result = {}
        with self.assertRaises(CommandError):
            scheduler.collect(self.c, result, collector)
        self.assertEqual(scheduler.interval, 60)
        self.assertFalse(result['github_collection']['successful'])
        self.assertEqual(scheduler.previous, {})

    def test_base_larger_than_cap_and_unknown_never_back_off(self):
        for base, data in ((900, {'idle': True}), (60, {'merge_queue_unknown': True})):
            scheduler = github_idle.Scheduler(base)
            scheduler.collect(self.c, {}, Mock(return_value=data))
            self.assertEqual(scheduler.interval, base)

    def test_republication_preserves_evidence_timing_and_counts_no_requests(self):
        scheduler = github_idle.Scheduler(60)
        previous = {'prs': [], 'idle': True}
        def collect(c, result, *, idle_previous):
            self.assertEqual(idle_previous, {})
            self.clock += 5
            result['github_requests'] = {'requests': 6, 'not_modified': 5}
            result['github_auth'] = {'kind': 'app_installation'}
            return previous
        collector = Mock(side_effect=collect)
        first = {}
        scheduler.collect(self.c, first, collector)
        self.assertEqual(first['github_collection']['period_seconds'], 125)
        self.clock += 60
        second = {}
        self.assertEqual(scheduler.collect(self.c, second, collector), previous)
        self.assertEqual(collector.call_count, 1)
        self.assertEqual(second['github_requests']['requests'], 0)
        self.assertEqual(second['github_collection'], first['github_collection'])
        self.assertEqual(second['github_auth'], first['github_auth'])
        with tempfile.TemporaryDirectory() as directory:
            self.c['_state'] = directory
            state.write(self.c, 'snapshot', {**second, 'repo': self.c['repo'],
                        'generated_at': self.clock, 'github': previous,
                        'collection': {'finished_at': self.clock, 'period_seconds': 60}})
            _, info = snapshot.shared(self.c)
            self.assertEqual(info['age_seconds'], 60)
            self.assertEqual(info['collection_seconds'], 5)
            self.assertEqual(info['period_seconds'], 125)
            self.clock = 1146
            self.assertEqual(snapshot.shared(self.c)[1]['source'], 'stale')

    def test_failure_republications_and_restart_do_not_launder_success(self):
        scheduler = github_idle.Scheduler(60)
        collector = Mock(side_effect=ValueError('unavailable'))
        first = {}
        with self.assertRaises(ValueError):
            scheduler.collect(self.c, first, collector)
        self.clock += 30
        second = {}
        with self.assertRaises(ValueError):
            scheduler.collect(self.c, second, collector)
        self.assertEqual(collector.call_count, 1)
        self.assertEqual(second['github_collection'], first['github_collection'])
        with tempfile.TemporaryDirectory() as directory:
            self.c['_state'] = directory
            state.write(self.c, 'snapshot', {**second, 'repo': self.c['repo'],
                        'generated_at': self.clock, 'github': {'prs': []}})
            self.assertEqual(snapshot.shared(self.c), (None, {'source': 'unavailable'}))
        collector.side_effect = None
        collector.return_value = {'prs': [], 'idle': True}
        restarted = github_idle.Scheduler(60)
        restarted.collect(self.c, {}, collector)
        self.assertEqual(collector.call_count, 2)
        self.assertEqual(collector.call_args.kwargs['idle_previous'], {})
        self.assertEqual(restarted.interval, 120)

    def test_local_health_quota_and_coordinator_continue_without_github_session(self):
        with tempfile.TemporaryDirectory() as directory:
            self.c.update(_state=directory, disk={'path': directory, 'min_free_gb': 0})
            scheduler = github_idle.Scheduler(60)
            with patch.object(snapshot.health, 'collect', return_value={'builders': []}) as health, \
                    patch.object(snapshot.health, 'coordinator', return_value={'state': 'idle'}) as coordinator, \
                    patch.object(snapshot.health, 'add_pr_stalls'), \
                    patch.object(snapshot.quota, 'collect', return_value={'known': True}) as quota, \
                    patch.object(snapshot.github_api, 'Session') as session, \
                    patch.object(snapshot.github, 'collect', return_value={'prs': [], 'idle': True}) as github:
                session.return_value.__enter__.return_value.blocked_until = 0
                first = snapshot.collect(self.c, github_scheduler=scheduler)
                self.clock += 60
                second = snapshot.collect(self.c, github_scheduler=scheduler)
                self.assertEqual(github.call_count, 1)
                github.assert_called_once_with(self.c, idle_previous={})
                self.assertEqual(session.call_count, 1)
                self.assertEqual(health.call_count, 2)
                self.assertEqual(quota.call_count, 2)
                self.assertEqual(coordinator.call_count, 2)
                self.assertEqual(second['github_collection'], first['github_collection'])



if __name__ == '__main__':
    unittest.main()

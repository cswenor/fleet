"""Budget deferral must not disable local health or repeatedly notify Main."""
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fleet import github_api, snapshot, supervisor, watcher
from test_supervisor import sample, pr


class BudgetFlowTests(unittest.TestCase):
    def test_supervisor_budget_window_relays_once_and_keeps_health(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {},
                 'status': {'url': '', 'token_env': ''}}
            current = sample(pr())
            del current['github']
            current['github_budget'] = {'blocked': True, 'reset': 1000}
            current['disk'] = {'low': True, 'free_gb': 1}
            delivered = []
            with patch.object(supervisor.snapshot, 'collect', return_value=current), patch.object(
                    supervisor, 'enqueue', side_effect=lambda c, e, r: delivered.append(e) or {}):
                supervisor.cycle(c)
                supervisor.cycle(c)
                current['github_budget']['reset'] = 2000
                supervisor.cycle(c)
            budget = [e for e in delivered if e['subject'] == 'github budget low']
            self.assertEqual(len(budget), 2)
            self.assertTrue(all(not e.get('urgent') for e in budget))
            self.assertTrue(any('disk' in e['key'] for e in delivered))
            self.assertFalse(any('collection error' in e['subject'] for e in delivered))

    def test_budget_deferred_snapshot_withholds_launch_without_collection_error(self):
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'repo': 'o/r', 'root': directory,
                 'disk': {'path': directory, 'min_free_gb': 0}, 'builder': {'max': 2}}
            context = MagicMock()
            session = context.__enter__.return_value
            session.stats = {'requests': 0, 'not_modified': 0}
            session.budget = {'reset': 1000}
            session.blocked_until = 1000
            with patch.object(github_api, 'Session', return_value=context), patch.object(
                    snapshot.github, 'collect', side_effect=github_api.BudgetLow(1000)), patch.object(
                    snapshot.health, 'collect', return_value={'builders': [], 'errors': []}), patch.object(
                    snapshot.health, 'coordinator', return_value={'terminal': 'main', 'state': 'up'}), patch.object(
                    snapshot.quota, 'collect', return_value={'may_start': True}), patch.object(
                    snapshot.time, 'time', return_value=100):
                result = snapshot.collect(c)
            self.assertEqual(result['errors'], [])
            self.assertIn('inventory', result)
            self.assertFalse(snapshot.may_start(c, result)['may_start'])

    def test_watcher_budget_sleep_is_bounded_by_deadline(self):
        context = MagicMock()
        session = context.__enter__.return_value
        session.stats = {'requests': 0, 'not_modified': 0}
        session.blocked_until = 1000
        with patch.object(github_api, 'Session', return_value=context), patch.object(
                watcher.github, 'collect', side_effect=github_api.BudgetLow(1000)) as collect, patch.object(
                watcher.time, 'monotonic', side_effect=[0, 0, 20]), patch.object(
                watcher.time, 'time', return_value=100), patch.object(watcher.time, 'sleep') as sleep:
            result = watcher.watch({'repo': 'o/r'}, 1, timeout=20, interval=1)
        self.assertEqual(result['event'], 'timeout')
        collect.assert_called_once()
        sleep.assert_called_once_with(20)

    def test_supervisor_retains_group_failures_across_optional_history_outage(self):
        import copy
        from fleet import state
        with tempfile.TemporaryDirectory() as directory:
            c = {'_state': directory, 'coordinator': {'run': 'r'}, 'hooks': {},
                 'status': {'url': '', 'token_env': ''}}
            known = sample(pr())
            run = {'id': 5, 'conclusion': 'failure', 'prs': [1],
                   'membership_complete': True, 'url': 'https://example/run/5'}
            known['github']['merge_group_runs'] = [run]
            unavailable = copy.deepcopy(known)
            unavailable['github']['merge_group_runs'] = []
            unavailable['github']['merge_group_unknown'] = True
            unavailable['github']['unavailable'] = ['Actions history HTTP 403']
            delivered = []
            with patch.object(supervisor, 'enqueue',
                              side_effect=lambda c, e, r: delivered.append(e) or {}):
                for current in (known, unavailable, copy.deepcopy(unavailable), known):
                    with patch.object(supervisor.snapshot, 'collect', return_value=current):
                        supervisor.cycle(c)
                    self.assertEqual(state.read(c, 'supervisor')['github']['merge_group_runs'], [run])
            group_reports = [e for e in delivered if e['key'].startswith('group:')]
            self.assertEqual(len(group_reports), 2)  # coordinator and owning builder, once each

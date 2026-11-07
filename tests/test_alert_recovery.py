import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import config, state, supervisor


class HealthRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'_state': directory.name, 'coordinator': {'run': 'scratch'},
                  'hooks': {}, 'status': {'url': '', 'token_env': ''}}
        self.builder = {'terminal': 'builder', 'incarnation_id': 'one', 'worktree': 'w',
                        'path': '/slot', 'model': 'provider/model', 'task': 'TASK-59',
                        'title': 'Builder', 'flags': ['wrong_model']}
        self.s = {'errors': [], 'inventory': {'errors': [], 'builders': [self.builder]},
                  'quota': {'known': True, 'may_start': True}, 'disk': {'low': False}}
        self.calls = []
        collect = patch.object(supervisor.snapshot, 'collect', side_effect=lambda c: copy.deepcopy(self.s))
        collect.start()
        self.addCleanup(collect.stop)
        enqueue = patch.object(supervisor, 'enqueue',
                               side_effect=lambda c, e, r: self.calls.append(copy.deepcopy(e)))
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def cycle(self):
        supervisor.cycle(self.c)

    def test_two_terminals_alternate_without_resending(self):
        scratch = {**self.builder, 'terminal': 'scratch', 'incarnation_id': 'two'}
        for terminal in [self.builder, scratch] * 3:
            self.s['inventory']['builders'] = [terminal]
            self.cycle()
        self.assertEqual([e['body'].split()[0] for e in self.calls], ['builder', 'scratch'])

    def test_flicker_and_configured_recovery_survive_restart(self):
        self.c['builder'] = {'alert_recovery_cycles': 3}
        self.cycle()
        first_key = self.calls[0]['key']
        for _ in range(2):
            self.builder['flags'] = []
            self.cycle()
        self.builder['flags'] = ['wrong_model']
        self.cycle()
        self.assertEqual(len(self.calls), 1)
        self.builder['flags'] = []
        for _ in range(3):
            self.cycle()
        self.builder['flags'] = ['wrong_model']
        self.cycle()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first_key, self.calls[1]['key'])

    def test_absences_must_be_consecutive_and_one_cycle_can_be_configured(self):
        self.cycle()
        for _ in range(3):
            self.builder['flags'] = []
            self.cycle()
            self.builder['flags'] = ['wrong_model']
            self.cycle()
        self.assertEqual(len(self.calls), 1)
        self.c['builder'] = {'alert_recovery_cycles': 1}
        self.builder['flags'] = []
        self.cycle()
        self.builder['flags'] = ['wrong_model']
        self.cycle()
        self.assertEqual(len(self.calls), 2)

    def test_new_incarnation_and_changed_content_alert_immediately(self):
        self.cycle()
        self.builder['incarnation_id'] = 'replacement'
        self.cycle()
        self.builder['model'] = 'other/model'
        self.cycle()
        self.cycle()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len({e['key'] for e in self.calls}), 3)

    def test_legacy_signature_migrates_only_matching_terminal(self):
        old = supervisor.urgent_event(self.c, supervisor.alerts(self.s, self.c)[0])
        state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                    'active_alerts': {'health:w:wrong_model': supervisor.event_signature(old)}})
        scratch = {**self.builder, 'terminal': 'scratch', 'incarnation_id': 'two'}
        self.s['inventory']['builders'].append(scratch)
        self.cycle()
        self.cycle()
        self.assertEqual([e['body'].split()[0] for e in self.calls], ['scratch'])
        self.assertNotIn('health:w:wrong_model', state.read(self.c, 'supervisor')['active_alerts'])

    def test_legacy_changed_content_is_not_suppressed(self):
        old = supervisor.urgent_event(self.c, supervisor.alerts(self.s, self.c)[0])
        state.write(self.c, 'supervisor', {'github': {}, 'pending': [], 'sent': {},
                    'active_alerts': {'health:w:wrong_model': supervisor.event_signature(old)}})
        self.builder['model'] = 'new/model'
        self.cycle()
        self.assertEqual([e['body'] for e in self.calls],
                         ['builder /slot; model=new/model; flag=wrong_model'])


class RecoveryConfigTests(unittest.TestCase):
    def test_positive_integer_recovery_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.yml'
            c = {'version': 1, 'repo': 'owner/project', 'root': directory,
                 'orca_repo_id': 'repo', 'pad_workspace': 'workspace', 'orca': '/orca',
                 'builder': {'model': 'model', 'resolved_model': 'provider/model',
                             'rules_file': 'rules', 'slots': [directory]},
                 'coordinator': {'run': 'r', 'terminal': 'main'}}
            for value in (0, -1, True, False, 1.5, '2', None):
                with self.subTest(value=value):
                    c['builder']['alert_recovery_cycles'] = value
                    path.write_text(yaml.safe_dump(c))
                    with self.assertRaisesRegex(ValueError, 'alert_recovery_cycles'):
                        config.load(path)

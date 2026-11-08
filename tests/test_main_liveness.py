import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import config, health, state, supervisor
from fleet.commands import CommandError


class MainObservationTests(unittest.TestCase):
    def observe(self, text='', terminal=None, truncated=False, current=None, omitted_hosts=None):
        terminal = terminal or {'handle': 'main', 'connected': True, 'incarnationId': 'one'}
        def native(c, *args):
            if args[:2] == ('terminal', 'list'):
                return {'terminals': [] if terminal == 'missing' else [terminal], 'truncated': truncated,
                        'hostScope': {'omittedHostIds': omitted_hosts or []}}
            if args[:2] == ('terminal', 'read'):
                return {'terminal': {'tail': text.splitlines()}}
            if args[:2] == ('terminal', 'show'):
                return {'terminal': current or terminal}
            self.fail(args)
        with patch.object(health, 'orca', side_effect=native):
            return health.coordinator({'coordinator': {'terminal': 'main'}})

    def test_shell_crash_and_healthy_ui(self):
        self.assertEqual(self.observe('dev@host:~/project$')['state'], 'suspect')
        crash = '[Uncaught Exception] Error: Native shell returned an invalid execution trace'
        result = self.observe(crash + '\nRun omp --resume to recover\ndev@host:~/project$')
        self.assertEqual(result['reason'], 'omp crash')
        self.assertEqual(result['evidence'], crash)
        self.assertEqual(result['recovery_hint'], 'Run omp --resume to recover')
        for footer in [' π > ◑ GPT-6.1 Sol>', ' ⠋ Working > ◑ GPT-6.1 Sol>']:
            self.assertEqual(self.observe(crash + '\n' + footer + '\n╰─')['state'], 'up')
        self.assertEqual(self.observe('[Recovery] retry required')['state'], 'suspect')

    def test_retained_crash_above_unknown_application_is_not_current(self):
        for tail in ['custom prompt>', 'other application running', 'user@host %']:
            with self.subTest(tail=tail):
                self.assertEqual(self.observe('[Recovery] old error\n' + tail)['state'], 'unknown')
        self.assertEqual(self.observe('[Uncaught Exception] Error: failed\n'
                                      '    at run (/app/main.js:1:2)\n'
                                      'Resume this session with\nomp --resume session-id')['state'],
                         'suspect')

    def test_missing_disconnected_and_unknown_are_distinct(self):
        self.assertEqual(self.observe(terminal='missing')['state'], 'down')
        for fields in [{'connected': False}, {'orphaned': True}]:
            terminal = {'handle': 'main', 'incarnationId': 'one', 'connected': True, **fields}
            self.assertEqual(self.observe(terminal=terminal)['state'], 'down')
        self.assertEqual(self.observe('custom prompt>')['state'], 'unknown')
        self.assertEqual(self.observe(terminal='missing', truncated=True)['state'], 'unknown')
        self.assertEqual(self.observe(terminal={'handle': 'main', 'connected': True})['state'], 'unknown')
        self.assertEqual(self.observe('dev@host:~$', current={'incarnationId': 'two', 'connected': True})['state'], 'unknown')
        with patch.object(health, 'orca', side_effect=CommandError('private diagnostic')):
            result = health.coordinator({'coordinator': {'terminal': 'main'}})
        self.assertEqual(result['state'], 'unknown')
        self.assertNotIn('private diagnostic', str(result))

    def test_omitted_host_is_unknown_not_a_missing_main(self):
        result = self.observe(terminal='missing', omitted_hosts=['remote-host'])
        self.assertEqual(result['state'], 'unknown')

    def test_evidence_is_bounded_and_credentials_are_removed(self):
        result = self.observe('[Uncaught Exception] token=superprivate password=hidden ' + 'x' * 400)
        self.assertNotIn('superprivate', result['evidence'])
        self.assertNotIn('hidden', result['evidence'])
        self.assertLessEqual(len(result['evidence']), 200)


class MainDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.c = {'_state': directory.name, 'coordinator': {'run': 'project', 'terminal': 'main',
                  'escalation_run': 'run_safety', 'liveness_minutes': 5},
                  'hooks': {}, 'status': {'url': '', 'token_env': ''}}
        self.s = {'errors': [], 'inventory': {'builders': []},
                  'quota': {'known': True, 'may_start': True}, 'disk': {'low': False},
                  'coordinator': {'terminal': 'main', 'incarnation_id': 'one', 'state': 'suspect',
                                  'reason': 'bare shell prompt', 'evidence': 'supported bare shell prompt'}}
        self.calls = []

    def cycle(self, now):
        with patch.object(supervisor.snapshot, 'collect', return_value=copy.deepcopy(self.s)), \
                patch.object(supervisor.time, 'time', return_value=now), \
                patch.object(supervisor, 'enqueue', side_effect=lambda c, e, r: self.calls.append((e, r))):
            supervisor.cycle(self.c)

    def test_threshold_dedupe_recovery_and_recurrence(self):
        self.cycle(100)
        self.cycle(399)
        self.assertEqual(self.calls, [])
        self.cycle(400)
        self.cycle(999)
        self.assertEqual([r for _, r in self.calls], ['run:project', 'run:run_safety'])
        self.assertEqual(self.calls[0][0]['type'], 'question')
        self.assertIn('minutes down=5.00', self.calls[0][0]['body'])
        self.s['coordinator']['state'] = 'up'
        self.cycle(1000)
        self.s['coordinator']['state'] = 'suspect'
        self.cycle(1001)
        self.cycle(1301)
        self.assertEqual([r for _, r in self.calls], ['run:project', 'run:run_safety'] * 2)

    def test_crash_threshold_and_new_incarnation_reset(self):
        self.s['coordinator'].update(reason='omp crash', evidence='[Recovery] failure')
        self.cycle(0)
        self.s['coordinator']['incarnation_id'] = 'two'
        self.cycle(299)
        self.cycle(300)
        self.assertEqual(self.calls, [])
        self.cycle(599)
        self.assertIn('[Recovery] failure', self.calls[0][0]['body'])

    def test_missing_is_immediate_unknown_is_nonurgent(self):
        self.s['coordinator'].update(state='down', reason='terminal missing', incarnation_id=None)
        self.cycle(0)
        self.cycle(600)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0]['type'], 'question')
        self.s['coordinator'].update(state='unknown', reason='terminal observation unavailable')
        self.cycle(601)
        self.cycle(602)
        self.assertEqual(len(self.calls), 4)
        self.assertNotIn('type', self.calls[-1][0])

    def test_healthy_main_and_same_run_do_not_duplicate(self):
        self.c['coordinator']['escalation_run'] = 'project'
        self.s['coordinator']['state'] = 'up'
        self.cycle(0)
        self.assertEqual(self.calls, [])
        self.s['coordinator']['state'] = 'down'
        self.cycle(1)
        self.assertEqual([r for _, r in self.calls], ['run:project'])


    def test_unknown_is_not_recovery_and_does_not_repeat(self):
        self.s['coordinator']['state'] = 'down'
        self.cycle(0)
        self.s['coordinator']['state'] = 'unknown'
        self.cycle(10)
        self.cycle(100)
        self.s['coordinator']['state'] = 'down'
        self.cycle(200)
        self.assertEqual([e['subject'] for e, r in self.calls if r == 'run:project'],
                         ['[urgent] Main main_down', 'Main main_unknown'])

    def test_disconnected_reconnect_to_shell_is_one_outage_until_live_ui(self):
        self.s['coordinator'].update(state='down', reason='terminal disconnected or orphaned')
        self.cycle(0)
        self.s['coordinator'].update(state='suspect', reason='bare shell prompt')
        self.cycle(1)
        self.cycle(300)
        self.assertEqual([r for _, r in self.calls], ['run:project', 'run:run_safety'])
        self.s['coordinator']['state'] = 'up'
        self.cycle(301)
        self.s['coordinator']['state'] = 'suspect'
        self.cycle(302)
        self.cycle(602)
        self.assertEqual([r for _, r in self.calls], ['run:project', 'run:run_safety'] * 2)

    def test_unknown_before_threshold_requires_new_observation_window(self):
        self.cycle(0)
        self.s['coordinator']['state'] = 'unknown'
        self.cycle(299)
        self.cycle(600)
        self.s['coordinator']['state'] = 'suspect'
        self.cycle(601)
        self.assertFalse(any(e.get('type') == 'question' for e, _ in self.calls))
        self.cycle(901)
        self.assertEqual(sum(e.get('type') == 'question' for e, _ in self.calls), 2)

class MainConfigTests(unittest.TestCase):
    def test_invalid_liveness_and_escalation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.yml'
            c = {'version': 1, 'repo': 'owner/project', 'root': directory,
                 'orca_repo_id': 'repo', 'pad_workspace': 'workspace', 'orca': '/orca',
                 'builder': {'model': 'model', 'resolved_model': 'provider/model',
                             'rules_file': 'rules', 'slots': [directory]},
                 'coordinator': {'run': 'r', 'terminal': 'main'}}
            for key, values in [('liveness_minutes', [0, -1, True, '5', None, float('inf'), float('nan')]),
                                ('escalation_run', ['', True, 'run:run_bad', 'terminal'])]:
                for value in values:
                    with self.subTest(key=key, value=value):
                        current = copy.deepcopy(c)
                        current['coordinator'][key] = value
                        path.write_text(yaml.safe_dump(current))
                        with self.assertRaisesRegex(ValueError, 'coordinator.' + key):
                            config.load(path)

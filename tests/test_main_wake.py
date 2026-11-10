import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fleet import config, health, state, supervisor


class MainWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = {'_state': self.tmp.name, 'coordinator': {
            'terminal': 'main', 'run': 'run_test', 'escalation_run': 'run_help'}}
        self.terminal = {'handle': 'main', 'incarnationId': 'one', 'worktreeId': 'tree',
                         'tabId': 'tab', 'leafId': 'leaf', 'connected': True,
                         'writable': True, 'agentIdentity': 'omp'}
        captured = json.loads((Path(__file__).parent / 'fixtures/orca-main-ui.json').read_text())
        self.screen = {**captured['email'], 'handle': 'main', 'source': 'screen'}
        self.agent = {'paneKey': 'tab:leaf', 'agentType': 'omp', 'state': 'idle',
                      'programStatus': {'state': 'idle'}}
        self.messages = [{'id': 'msg_one', 'run_id': 'run_test',
                          'to_handle': 'run:run_test', 'delivery_contract': 'current_delivery',
                          'read': 0, 'type': 'question', 'created_at': '2026-11-10T12:00:00Z'}]
        self.now = datetime(2026, 11, 10, 12, 3, tzinfo=timezone.utc).timestamp()
        self.sends = []
        self.ledger = {}
        self.binding = 'run_test'
        self.current = None
        self.truncated = False
        self.fail_send = False
        native = patch.object(supervisor, 'orca', side_effect=self.native)
        native.start()
        self.addCleanup(native.stop)
        native_health = patch.object(health, 'orca', side_effect=self.native)
        native_health.start()
        self.addCleanup(native_health.stop)

    def native(self, c, *args):
        if args[:2] == ('orchestration', 'run-show'):
            return {'run': {'id': 'run_test', 'coordinator_handle': 'main'}}
        if args[:2] == ('orchestration', 'inbox'):
            return {'messages': copy.deepcopy(self.messages)}
        if args[:2] == ('orchestration', 'check'):
            self.assertIn('--peek', args)
            self.assertNotIn('--ack', args)
            return {'messages': copy.deepcopy(self.messages), 'count': len(self.messages),
                    'runId': 'run_test'}
        if args[:2] == ('orchestration', 'run-current'):
            return {'run': {'id': self.binding}}
        if args[:2] == ('terminal', 'show'):
            return {'terminal': self.current or self.terminal}
        if args[:2] == ('terminal', 'read'):
            return {'terminal': self.screen}
        if args[:2] == ('worktree', 'ps'):
            return {'truncated': self.truncated, 'worktrees': [
                {'worktreeId': 'tree', 'agents': [self.agent]}]}
        if args[:2] == ('terminal', 'send'):
            # The durable attempt must exist before any input, even if transport fails.
            self.assertEqual(state.read(c, 'supervisor')['main_wakes']['attempts'][-1]['status'],
                             'attempted')
            self.sends.append(args)
            if self.fail_send:
                raise RuntimeError('ambiguous')
            return {'send': {'accepted': True}}
        self.fail(args)

    def wake(self, seconds=0, deliver=True):
        return supervisor.wake_main(self.c, self.ledger, self.now + seconds, deliver)

    def test_idle_wakes_once_then_cooldown_and_consumption(self):
        self.assertEqual(self.wake(), [])
        self.wake(599)
        self.assertEqual(len(self.sends), 1)
        self.assertIn('check your inbox', self.sends[0][self.sends[0].index('--text') + 1])
        self.wake(600)
        self.assertEqual(len(self.sends), 2)
        self.messages = []
        self.wake(1200)
        self.assertEqual(len(self.sends), 2)
        journal = self.ledger['main_wakes']['attempts']
        self.assertEqual(journal[0]['oldest_unread_id'], 'msg_one')
        self.assertEqual(journal[0]['proof']['composer'], 'empty')

    def test_unsafe_surfaces_relay_once_per_set_never_input(self):
        variants = [
            {**self.screen, 'tail': [' π > ◒ Sol>', '╰─ draft']},
            {**self.screen, 'tail': [' ⠋ Working > ◒ Sol>', '╰─']},
            {**self.screen, 'tail': ['╭─ Ask ─╮', '│ Question │', '╰──────╯']},
            {**self.screen, 'source': 'screen-unavailable'},
            {**self.screen, 'tail': []},
        ]
        for screen in variants:
            with self.subTest(screen=screen):
                self.ledger = {}
                self.screen = screen
                event, = self.wake(720)
                self.assertEqual(event['recipients'], ['run:run_help'])
                self.assertEqual(event['type'], 'status')
                self.assertIn('Main ', event['body'])
                self.assertEqual(self.wake(900), [])
                self.assertEqual(self.sends, [])

    def test_identity_and_pane_fail_closed(self):
        for change in ('binding', 'incarnation', 'busy', 'truncated', 'not-omp', 'not-writable'):
            with self.subTest(change=change):
                self.ledger = {}
                with patch.dict(self.terminal), patch.dict(self.agent):
                    self.binding = 'run_other' if change == 'binding' else 'run_test'
                    self.truncated = change == 'truncated'
                    if change == 'incarnation':
                        self.terminal['incarnationId'] = None
                    if change == 'busy':
                        self.agent['state'] = 'working'
                    if change == 'not-omp':
                        self.terminal['agentIdentity'] = 'bash'
                    if change == 'not-writable':
                        self.terminal['writable'] = False
                    self.assertEqual(len(self.wake(720)), 1)
                    self.assertEqual(self.sends, [])

    def test_dry_run_disabled_young_and_incomplete_mail_do_not_wake(self):
        self.wake(deliver=False)
        self.assertEqual(self.ledger['main_wakes']['attempts'], [])
        self.c['coordinator']['wake'] = False
        self.wake()
        self.c['coordinator']['wake'] = True
        self.wake(-100)
        self.messages[0]['run_id'] = 'run_other'
        self.wake()
        self.assertEqual(self.sends, [])

    def test_restored_pane_null_status_requires_idle_state_and_real_empty_screen(self):
        # Reported post-restart Orca shape: state=done, programStatus=null.
        self.agent.update(state='done', programStatus=None)
        self.wake()
        self.assertEqual(len(self.sends), 1)
        for change in ('busy', 'interrupted', 'draft', 'unavailable'):
            with self.subTest(change=change), patch.dict(self.agent), patch.dict(self.screen):
                self.ledger = {}
                if change == 'busy':
                    self.agent['state'] = 'working'
                elif change == 'interrupted':
                    self.agent['interrupted'] = True
                elif change == 'draft':
                    self.screen['tail'] = [' π > ◑ Sol>', '╰─ user draft']
                else:
                    self.screen['source'] = 'screen-unavailable'
                self.assertEqual(len(self.wake(720)), 1)
                self.assertEqual(len(self.sends), 1)

    def test_capped_unread_backlog_still_wakes_or_escalates(self):
        self.messages = [{**self.messages[0], 'id': f'msg_{i}'} for i in range(100)]
        self.wake()
        self.wake(1)
        self.assertEqual(len(self.sends), 1)
        self.screen['source'] = 'screen-unavailable'
        self.assertEqual(len(self.wake(720)), 1)
        self.assertEqual(self.wake(721), [])
        self.assertEqual(len(self.sends), 1)

    def test_journal_keeps_recent_attempts_and_unwakeable_sets_bounded(self):
        self.ledger['main_wakes'] = {
            'attempts': [{'at': self.now - 1000, 'messages': str(i)} for i in range(200)],
            'unwakeable': [str(i) for i in range(200)]}
        self.wake()
        journal = self.ledger['main_wakes']
        self.assertEqual(len(journal['attempts']), 200)
        self.assertEqual(journal['attempts'][0]['messages'], '1')
        self.assertEqual(journal['attempts'][-1]['oldest_unread_id'], 'msg_one')
        self.screen['source'] = 'screen-unavailable'
        self.wake(720)
        self.assertEqual(len(journal['unwakeable']), 200)
        self.assertEqual(journal['unwakeable'][0], '1')
        self.assertNotEqual(journal['unwakeable'][-1], '199')

    def test_ambiguous_send_is_journaled_and_not_immediately_retried(self):
        self.fail_send = True
        self.wake()
        self.wake(1)
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.ledger['main_wakes']['attempts'][0]['status'], 'unknown')

    def test_changed_set_wakes_but_return_to_prior_set_keeps_cooldown(self):
        self.wake()
        original = copy.deepcopy(self.messages)
        self.messages.append({**self.messages[0], 'id': 'msg_two'})
        self.wake(1)
        self.messages = original
        self.wake(2)
        self.assertEqual(len(self.sends), 2)

    def test_status_only_backlog_never_wakes_or_relays(self):
        self.messages[0]['type'] = 'status'
        for busy in (False, True):
            with self.subTest(busy=busy):
                self.agent['state'] = 'working' if busy else 'idle'
                self.assertEqual(self.wake(3600), [])
                self.assertEqual(self.sends, [])
                self.assertEqual(self.ledger, {})

    def test_old_status_does_not_age_young_question(self):
        self.messages.append({**self.messages[0], 'id': 'status', 'type': 'status'})
        self.messages[0]['created_at'] = '2026-11-10T12:02:00Z'
        self.wake()
        self.assertEqual(self.sends, [])
        self.wake(60)
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.ledger['main_wakes']['attempts'][0]['oldest_unread_id'], 'msg_one')

    def test_busy_relay_waits_for_actionable_age_and_ignores_status_arrivals(self):
        self.agent['state'] = 'working'
        self.assertEqual(self.wake(), [])
        self.assertEqual(self.wake(719), [])
        event, = self.wake(720)
        self.assertIn('msg_one', event['body'])
        self.messages.append({**self.messages[0], 'id': 'status', 'type': 'status'})
        self.assertEqual(self.wake(721), [])
        self.messages.append({**self.messages[0], 'id': 'decision', 'type': 'escalation'})
        self.assertEqual(len(self.wake(722)), 1)
        self.assertEqual(self.sends, [])

    def test_status_arrivals_do_not_bypass_wake_cooldown(self):
        self.wake()
        self.messages.append({**self.messages[0], 'id': 'status', 'type': 'status'})
        self.wake(1)
        self.assertEqual(len(self.sends), 1)

    def test_custom_unwakeable_age(self):
        self.c['coordinator']['unwakeable_after_seconds'] = 300
        self.agent['state'] = 'working'
        self.assertEqual(self.wake(119), [])
        self.assertEqual(len(self.wake(120)), 1)


class WakeConfigTests(unittest.TestCase):
    def test_wake_controls_validate(self):
        raw = yaml.safe_load(Path('fleet.example.yml').read_text())
        for key, bad in [('wake', 'yes'), ('wake_after_seconds', 0),
                         ('wake_after_seconds', True), ('wake_cooldown_seconds', float('inf')),
                         ('wake_cooldown_seconds', -1), ('unwakeable_after_seconds', 0),
                         ('unwakeable_after_seconds', True),
                         ('unwakeable_after_seconds', float('nan'))]:
            with self.subTest(key=key, bad=bad), tempfile.TemporaryDirectory() as directory:
                candidate = copy.deepcopy(raw)
                candidate['root'] = directory
                candidate['coordinator'][key] = bad
                path = Path(directory) / 'config.yml'
                path.write_text(yaml.safe_dump(candidate))
                with self.assertRaisesRegex(ValueError, 'coordinator.' + key):
                    config.load(path)

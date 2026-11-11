import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml
from fleet import health, state


# Model metadata captured from project-gamma TASK-33 launch 1794371506079746465.
FABLE = [
    {'type': 'model_change', 'timestamp': '2026-11-11T04:31:48.010Z',
     'model': 'anthropic/claude-fable-5-2', 'resolvedModelIsFallback': False},
    {'type': 'thinking_level_change', 'timestamp': '2026-11-11T04:31:48.010Z',
     'thinkingLevel': 'xhigh'},
]


class SessionModelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.slot = temp.name
        self.log = Path(self.slot) / 'session.jsonl'
        self.c = {'root': self.slot, '_state': self.slot + '/state', 'repo': 'owner/project'}
        self.record = {'id': '123', 'terminal': 'lane', 'terminal_incarnation_id': 'one',
                       'worktree': {'path': self.slot}, 'session_dir': self.slot,
                       'resolved_model': 'anthropic/claude-fable-5-2', 'thinking': 'xhigh'}
        self.terminal = {'handle': 'lane', 'incarnationId': 'one', 'connected': True}

    def read(self, events):
        state.write(self.c, 'launches', {self.slot: self.record})
        header = {'type': 'session', 'cwd': self.slot, 'timestamp': '2026-11-11T04:31:47Z'}
        self.log.write_text(''.join(json.dumps(e) + '\n' for e in [header, *events]))
        return health._progress(self.c, self.terminal, self.slot, time.time())

    def flags(self, evidence):
        return health.classify(self.terminal, ' π > ◕ Fable 5.2 >\n╰─', evidence, 600, time.time())[1]

    def test_real_anthropic_events_and_sol_use_exact_ids(self):
        for model, thinking in [('anthropic/claude-fable-5-2', 'xhigh'),
                                ('openai-codex/gpt-6.1-sol', 'medium')]:
            with self.subTest(model=model):
                self.record.update(resolved_model=model, thinking=thinking)
                events = [dict(FABLE[0], model=model), dict(FABLE[1], thinkingLevel=thinking)]
                evidence = self.read(events)
                self.assertEqual((evidence['model'], evidence['thinking'], self.flags(evidence)),
                                 (model, thinking, []))

    def test_latest_model_thinking_and_fallback_violate_launch_pin(self):
        for change in (dict(FABLE[0], model='anthropic/other'),
                       dict(FABLE[0], resolvedModelIsFallback=True),
                       dict(FABLE[1], thinkingLevel='low')):
            with self.subTest(change=change):
                self.assertEqual(self.flags(self.read([*FABLE, change])), ['wrong_model'])

    def test_missing_unreadable_or_replaced_session_never_uses_footer(self):
        self.assertEqual(self.flags(self.read([])), ['model_unknown'])
        self.read(FABLE)
        self.log.write_text('{partial')
        self.assertIsNone(health._progress(self.c, self.terminal, self.slot, time.time()))
        self.read(FABLE)
        self.terminal['incarnationId'] = 'replacement'
        evidence = health._progress(self.c, self.terminal, self.slot, time.time())
        self.assertEqual(self.flags(evidence), ['model_unknown'])

    def test_legacy_overlay_not_current_config_sets_expectation(self):
        self.record.pop('resolved_model')
        self.record.pop('thinking')
        overlay = Path(self.slot) / 'overlay.yml'
        self.record['overlay'] = str(overlay)
        self.c['builder'] = {'resolved_model': 'openai-codex/gpt-6.1-sol', 'thinking': 'low'}
        for models, flags in [({'anthropic/claude-fable-5-2': 'f200000'}, []),
                              ({}, ['wrong_model']),
                              ({'one/model': '', 'two/model': ''}, ['wrong_model'])]:
            with self.subTest(models=models):
                overlay.write_text(yaml.safe_dump({'compaction': {'modelThresholds': models}}))
                self.assertEqual(self.flags(self.read(FABLE)), flags)

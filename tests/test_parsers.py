import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from fleet import health
from fleet.health import branch, classify, owner
from fleet.quota import evaluate


class HealthTests(unittest.TestCase):
    def test_expected_scratch_matching_excludes_lanes_root_and_other_projects(self):
        paths = ['/root', '/scratch/slot', '/scratch/TASK-1/wt',
                 '/scratch/TASK-1/wt2', '/other/wt']
        c = {'root': '/root', 'orca_repo_id': 'repo',
             'coordinator': {'terminal': 'main', 'expected_worktree_globs': ['/scratch/*']},
             'builder': {'slots': ['/scratch/slot']}}
        trees = [{'id': path, 'repoId': 'repo', 'path': path} for path in paths]
        trees += [{'id': 'foreign', 'repoId': 'other', 'path': '/scratch/foreign'},
                  {'id': 'archived', 'repoId': 'repo', 'path': '/scratch/archived', 'isArchived': True},
                  {'id': 'main', 'repoId': 'repo', 'path': '/scratch/main', 'isMainWorktree': True}]
        with patch.object(health, 'orca', side_effect=[
                {'worktrees': trees}, {'terminals': []}]):
            inventory = health.collect(c)
        self.assertEqual(inventory['stray_worktrees'], paths[2:])
        self.assertEqual(inventory['expected_worktrees'], paths[2:4])

    def test_model_requires_session_evidence_not_projection_or_footer(self):
        t = {'connected': True, 'lastOutputAt': 100_000,
             'projection': {'provider': {'model': 'openai-codex/gpt-6.1-sol'}}}
        model, flags, idle = classify(t, '◑ GPT-6.1 Sol >\nRetry failed after 3 attempts', None, 60, 200)
        self.assertIsNone(model)
        self.assertEqual(flags, ['model_unknown', 'provider_stalled', 'idle'])
        self.assertEqual(idle, 100)

    def test_thinking_levels_preserve_working_idle_and_background_states(self):
        for indicator in '⦸○◔◐◑◒◓◕◉⟳':
            for prefix, suffix, expected in (
                    ('⠹ 39h', '', 'working'),
                    ('π', '', 'idle'),
                    ('π', ' > ⚙ 1', 'background')):
                with self.subTest(indicator=indicator, state=expected):
                    footer = (f' {prefix} > {indicator} Opus 5.5 > '
                              f'📁 ~/dev/cswenor/fleet > ⑂ main{suffix}')
                    self.assertEqual(health.omp_ui(footer + '\n╰─'), expected)
        self.assertEqual(health.omp_ui(' ⠙ 30s > ◑ GPT-6.1 Sol >\n╰─'), 'working')

    def test_thinking_indicator_does_not_replace_footer_structure(self):
        for text in (
                ' prose > ◒ Opus 5.5 >\n╰─',
                ' π ◒ Opus 5.5 >\n╰─',
                ' π > ◒ Opus 5.5 >',
                ' π > ◒ Opus 5.5 >\nnot a border',
                ' π > ◒ Opus 5.5 >\n╰─\nnew transcript'):
            with self.subTest(text=text):
                self.assertEqual(health.omp_ui(text), 'unknown')

    def test_captured_dialogs_and_background_are_distinct(self):
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        terminal = {'connected': True, 'lastOutputAt': 100_000}
        for name, screen in screens.items():
            with self.subTest(surface=name):
                _, flags, _ = classify(terminal, '\n'.join(screen),
                                       {'model': 'p/m', 'expected_model': 'p/m'}, 60, 200)
                self.assertEqual('waiting_dialog' in flags, name in ('permission', 'ask'))
                self.assertEqual('idle' in flags, name != 'background')
                if name == 'background':
                    self.assertEqual(health.omp_ui('\n'.join(screen)), 'background')

    def test_permission_takes_precedence_over_concurrent_background_job(self):
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        screen = screens['permission'][:-2] + screens['background'][-2:]
        _, flags, _ = classify({'connected': True, 'lastOutputAt': 100_000},
                               '\n'.join(screen), {'model': 'p/m', 'expected_model': 'p/m'}, 60, 200)
        self.assertEqual(flags, ['waiting_dialog', 'idle'])

    def test_job_status_not_wait_prose_controls_background_and_idle(self):
        terminal = {'connected': True, 'lastOutputAt': 100_000}
        for glyph in ('π', '⠙ 30s'):
            text = ('⎋ Waiting for live drill completion\n'
                    f' {glyph} > ◑ GPT-6.1 Sol > ⚙ 1\n╰─')
            _, flags, _ = classify(terminal, text, {'model': 'p/m', 'expected_model': 'p/m'}, 60, 200)
            self.assertEqual(flags, [])
            self.assertEqual(health.omp_ui(text), 'background')
        # A transcript gear or stale footer cannot suppress the stale-output alert.
        for text in ('⚙ 1\n π > ◑ GPT-6.1 Sol >\n╰─',
                     ' ⠙ 30s > ◑ GPT-6.1 Sol > ⚙ 1\nold screen, no composer'):
            _, flags, _ = classify(terminal, text, {'model': 'p/m', 'expected_model': 'p/m'}, 60, 200)
            self.assertEqual(flags, ['idle'])

    def test_ask_replaces_old_composer_and_partial_widget_stays_unknown(self):
        screens = json.loads((Path(__file__).parent / 'fixtures' /
                              'omp-18.17-retirement.json').read_text())
        self.assertEqual(health.omp_ui('\n'.join(screens['idle'] + screens['ask'])), 'dialog')
        self.assertEqual(health.omp_ui('\n'.join(screens['ask'][:-1])), 'unknown')

    def test_transcript_prose_and_widget_quotes_do_not_become_dialogs(self):
        footer = ' π > ◑ GPT-6.1 Sol >'
        for prose in ('Waiting for input; permission required; approve or deny.',
                      'Ask 2 questions; Main approved merge.',
                      '╭─ Ask ───╮\n│ quoted prompt │\n╰─────────╯'):
            with self.subTest(prose=prose):
                _, flags, _ = classify({'connected': True},
                                       prose + '\n' + footer + '\n╰─',
                                       {'model': 'p/m', 'expected_model': 'p/m'}, 60, 200)
                self.assertNotIn('waiting_dialog', flags)

    def test_unknown_terminal_identity_keeps_slot_occupied(self):
        c = {'root': '/root', 'orca_repo_id': 'repo', 'coordinator': {'terminal': 'main'},
             'builder': {'slots': ['/slot'], 'resolved_model': 'provider/model', 'idle_seconds': 600}}
        for terminal in ({'connected': True}, {'connected': False, 'agentIdentity': 'omp'},
                         {'connected': True, 'orphaned': True, 'agentIdentity': 'omp'}):
            with self.subTest(terminal=terminal):
                def orca(config, *args):
                    if args[:2] == ('worktree', 'list'):
                        return {'worktrees': [{'id': 'tree', 'repoId': 'repo', 'path': '/slot'}]}
                    if args[:2] == ('terminal', 'list'):
                        return {'terminals': [{'handle': 'lane', 'worktreeId': 'tree', **terminal}]}
                    return {'terminal': {'tail': ['process still unresolved']}}
                with patch.object(health, 'orca', side_effect=orca):
                    inventory = health.collect(c, now=100)
                self.assertEqual([b['path'] for b in inventory['builders'] if health.active(b)], ['/slot'])

    def test_binding_requires_explicit_native_null_or_run_id(self):
        cases = [
            ({'run': None}, {'state': 'none', 'run_id': None}),
            ({'run': {'id': 'run_owner'}}, {'state': 'bound', 'run_id': 'run_owner'}),
            ({}, {'state': 'unknown', 'run_id': None}),
            ({'run': {}}, {'state': 'unknown', 'run_id': None}),
            ({'run': {'id': ''}}, {'state': 'unknown', 'run_id': None}),
            ({'run': {'id': 123}}, {'state': 'unknown', 'run_id': None}),
            ({'run': 'run_owner'}, {'state': 'unknown', 'run_id': None}),
            (None, {'state': 'unknown', 'run_id': None}),
        ]
        for response, expected in cases:
            with self.subTest(response=response), patch.object(health, 'orca', return_value=response) as native:
                self.assertEqual(health.binding({}, 'lane'), expected)
                native.assert_called_once_with({}, 'orchestration', 'run-current', '--from', 'lane')
        with patch.object(health, 'orca', side_effect=RuntimeError('unavailable')):
            self.assertEqual(health.binding({}, 'lane'), {'state': 'unknown', 'run_id': None})

    def test_each_slot_terminal_exposes_binding_even_at_ready_shell(self):
        c = {'root': '/root', 'orca_repo_id': 'repo', 'coordinator': {'terminal': 'main'},
             'builder': {'slots': ['/slot'], 'resolved_model': 'provider/model', 'idle_seconds': 600}}
        queries = []
        bindings = {'unbound': {'run': None}, 'bound': {'run': {'id': 'run_owner'}}}
        def orca(config, *args):
            if args[:2] == ('worktree', 'list'):
                return {'worktrees': [
                    {'id': 'tree', 'repoId': 'repo', 'path': '/slot'},
                    {'id': 'other', 'repoId': 'repo', 'path': '/unconfigured'}]}
            if args[:2] == ('terminal', 'list'):
                return {'terminals': [
                    {'handle': handle, 'worktreeId': 'tree', 'connected': True, 'writable': True}
                    for handle in ('unbound', 'bound', 'unknown', 'main')] +
                    [{'handle': 'outside', 'worktreeId': 'other', 'connected': True}]}
            if args[:2] == ('terminal', 'read'):
                return {'terminal': {'tail': ['user@host:~/slot$ ']}}
            if args[:2] == ('orchestration', 'run-current'):
                handle = args[args.index('--from') + 1]
                queries.append(handle)
                if handle == 'unknown':
                    raise RuntimeError('unavailable')
                return bindings[handle]
            self.fail('unexpected native call')
        with patch.object(health, 'orca', side_effect=orca):
            inventory = health.collect(c, now=100)
        self.assertEqual(queries, ['unbound', 'bound', 'unknown'])
        builders = {b['terminal']: b for b in inventory['builders']}
        self.assertEqual(builders['unbound']['binding'], {'state': 'none', 'run_id': None})
        self.assertEqual(builders['unbound']['flags'], [])
        self.assertEqual(builders['unbound']['state'], 'shell')
        self.assertEqual(builders['bound']['binding'], {'state': 'bound', 'run_id': 'run_owner'})
        self.assertEqual(builders['bound']['flags'], ['owner_delivery_broken'])
        self.assertEqual(builders['bound']['state'], 'owner_delivery_broken')
        self.assertEqual(builders['unknown']['binding'], {'state': 'unknown', 'run_id': None})
        self.assertEqual(builders['unknown']['flags'], ['binding_unknown'])
        self.assertTrue(all(b['shell_ready'] for b in builders.values()))
        self.assertTrue(all(not health.active(b) for b in builders.values()))
        self.assertEqual(inventory['errors'], [])

    def test_ambiguous_owner_never_gets_message(self):
        inv = {'worktrees': [{'id': 'w', 'branch': 'feature'}], 'builders': [{'worktree': 'w', 'connected': True, 'terminal': 'a'}]}
        self.assertEqual(owner(inv, 'refs/heads/feature'), 'a')
        inv['builders'].append({'worktree': 'w', 'connected': True, 'terminal': 'b'})
        self.assertIsNone(owner(inv, 'feature'))
        inv['builders'].pop()
        inv['worktrees'].append({'id': 'x', 'branch': 'feature'})
        self.assertIsNone(owner(inv, 'feature'))


class QuotaTests(unittest.TestCase):
    def test_pooled_accounts_reserve_and_staleness(self):
        config = {'provider': 'p', 'reserve_percent': 10, 'max_age_seconds': 60}
        report = lambda value: {'provider': 'p', 'fetchedAt': 100_000, 'limits': [{'id': 'weekly', 'amount': {'remainingFraction': value}}]}
        data = {'reports': [report(0), report(.3)]}
        self.assertTrue(evaluate(data, config, 100)['may_start'])
        config['reserve_percent'] = 15
        self.assertFalse(evaluate(data, config, 100)['may_start'])
        self.assertFalse(evaluate(data, config, 161)['known'])
        self.assertFalse(evaluate({}, config, 100)['may_start'])




if __name__ == '__main__':
    unittest.main()

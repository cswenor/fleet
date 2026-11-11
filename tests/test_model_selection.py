import copy
import unittest
from unittest.mock import patch

from fleet import config, dispatcher, launcher, quota
from fleet.commands import CommandError


DEFAULT_MODEL = 'openai-codex/gpt-6.1-sol'
PLAN_MODEL = 'anthropic/claude-fable-5-2'
OVERRIDE_MODEL = 'other/model'


def builder_policy():
    return {
        'model': '@builder',
        'resolved_model': DEFAULT_MODEL,
        'thinking': 'medium',
        'allowed_models': {PLAN_MODEL: ['medium', 'high'], OVERRIDE_MODEL: ['low']},
        'plan_models': {'PLAN-1': {'model': PLAN_MODEL, 'thinking': 'high'}},
    }


class ModelPolicyTests(unittest.TestCase):
    def test_allowlist_matches_both_resolved_model_and_thinking(self):
        builder = builder_policy()
        config.validate_model_policy(builder)
        for model, thinking, allowed in (
                (DEFAULT_MODEL, 'medium', True),
                (DEFAULT_MODEL, 'high', False),
                ('@builder', 'medium', False),
                (PLAN_MODEL, 'medium', True),
                (PLAN_MODEL, 'high', True),
                (PLAN_MODEL, 'low', False),
                (OVERRIDE_MODEL, 'low', True),
                ('unknown/model', 'medium', False)):
            with self.subTest(model=model, thinking=thinking):
                self.assertEqual(config.model_allowed(builder, model, thinking), allowed)

    def test_default_pair_is_implicitly_allowed_without_allowlist(self):
        builder = builder_policy()
        builder.pop('allowed_models')
        builder['plan_models'] = {
            'PLAN-1': {'model': DEFAULT_MODEL, 'thinking': 'medium'}}
        config.validate_model_policy(builder)
        self.assertTrue(config.model_allowed(builder, DEFAULT_MODEL, 'medium'))
        self.assertFalse(config.model_allowed(builder, DEFAULT_MODEL, 'high'))

    def test_malformed_allowlists_are_rejected(self):
        invalid = (
            None, [], 'provider/model',
            {'@builder': ['medium']},
            {'model': ['medium']},
            {'provider/model/extra': ['medium']},
            {'provider/white space': ['medium']},
            {1: ['medium']},
            {PLAN_MODEL: []},
            {PLAN_MODEL: 'high'},
            {PLAN_MODEL: None},
            {PLAN_MODEL: ['unknown']},
            {PLAN_MODEL: [None]},
            {PLAN_MODEL: ['high', 'high']},
        )
        for allowed in invalid:
            with self.subTest(allowed=allowed):
                builder = builder_policy()
                builder['allowed_models'] = allowed
                with self.assertRaises(ValueError):
                    config.validate_model_policy(builder)

    def test_malformed_plan_policies_are_rejected(self):
        valid = {'model': PLAN_MODEL, 'thinking': 'high'}
        invalid = (
            None, [], 'PLAN-1',
            {'TASK-1': valid},
            {'PLAN-no': valid},
            {1: valid},
            {'PLAN-1': None},
            {'PLAN-1': PLAN_MODEL},
            {'PLAN-1': {'model': PLAN_MODEL}},
            {'PLAN-1': {'thinking': 'high'}},
            {'PLAN-1': {**valid, 'extra': True}},
            {'PLAN-1': {'model': None, 'thinking': 'high'}},
            {'PLAN-1': {'model': PLAN_MODEL, 'thinking': None}},
            {'PLAN-1': {'model': '@builder', 'thinking': 'medium'}},
            {'PLAN-1': {'model': 'unknown/model', 'thinking': 'high'}},
            {'PLAN-1': {'model': PLAN_MODEL, 'thinking': 'low'}},
        )
        for plans in invalid:
            with self.subTest(plans=plans):
                builder = builder_policy()
                builder['plan_models'] = plans
                with self.assertRaises(ValueError):
                    config.validate_model_policy(builder)


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.c = {
            'root': '/unused',
            'pad_workspace': 'test',
            'builder': builder_policy(),
            'quota': {'provider': 'reviewer', 'reserve_percent': 10,
                      'max_age_seconds': 900},
        }
        config.validate_model_policy(self.c['builder'])
        self.items = {
            'TASK-1': {'ref': 'TASK-1', 'parent_ref': 'TASK-2'},
            'TASK-2': {'ref': 'TASK-2', 'parent_ref': 'PLAN-1'},
            'PLAN-1': {'ref': 'PLAN-1', 'parent_ref': 'PLAN-2'},
            'PLAN-2': {'ref': 'PLAN-2'},
        }
        pad_patch = patch.object(dispatcher, 'pad', side_effect=self.pad)
        self.pad_mock = pad_patch.start()
        self.addCleanup(pad_patch.stop)

    def pad(self, c, *args):
        if len(args) != 3 or args[:2] != ('item', 'show'):
            raise AssertionError('Unexpected Pad operation: ' + repr(args))
        if args[2] not in self.items:
            raise CommandError('item unavailable')
        # Transport returns independent decoded JSON objects.
        return copy.deepcopy(self.items[args[2]])

    def assert_selection(self, selected, model, thinking, source, argv_model=None):
        self.assertIsNot(selected, self.c)
        self.assertIsNot(selected['builder'], self.c['builder'])
        self.assertEqual(selected['builder']['resolved_model'], model)
        self.assertEqual(selected['builder']['model'], argv_model or model)
        self.assertEqual(selected['builder']['thinking'], thinking)
        receipt = selected['_model_selection']
        self.assertEqual(receipt['model'], argv_model or model)
        self.assertEqual(receipt['resolved_model'], model)
        self.assertEqual(receipt['thinking'], thinking)
        self.assertEqual(receipt['model_source'], source)
        self.assertIsInstance(receipt['model_description'], str)
        self.assertTrue(receipt['model_description'].strip())
        self.assertEqual(receipt['plan_ref'], 'PLAN-1')

    def test_default_alias_and_configuration_remain_unchanged_across_calls(self):
        self.c['builder']['plan_models'] = {}
        before = copy.deepcopy(self.c)
        first = launcher.resolve_model(self.c, 'TASK-1')
        self.assert_selection(first, DEFAULT_MODEL, 'medium', 'default', '@builder')
        first['builder']['model'] = 'caller-local/change'
        first['builder']['thinking'] = 'high'
        second = launcher.resolve_model(self.c, 'TASK-1')
        self.assert_selection(second, DEFAULT_MODEL, 'medium', 'default', '@builder')
        self.assertEqual(self.c, before)

    def test_plan_mapping_selects_nearest_plan_through_task_ancestry(self):
        self.c['builder']['plan_models']['PLAN-2'] = {
            'model': OVERRIDE_MODEL, 'thinking': 'low'}
        before = copy.deepcopy(self.c)
        selected = launcher.resolve_model(self.c, 'TASK-1')
        self.assert_selection(selected, PLAN_MODEL, 'high', 'plan:PLAN-1')
        self.assertEqual(selected['_model_selection']['plan_ref'], 'PLAN-1')
        self.assertEqual(self.c, before)

    def test_unmapped_nearest_plan_does_not_inherit_outer_plan_mapping(self):
        self.c['builder']['plan_models'] = {
            'PLAN-2': {'model': PLAN_MODEL, 'thinking': 'high'}}
        selected = launcher.resolve_model(self.c, 'TASK-1')
        self.assert_selection(selected, DEFAULT_MODEL, 'medium', 'default', '@builder')

    def test_explicit_pair_overrides_plan_mapping_without_mutating_defaults(self):
        before = copy.deepcopy(self.c)
        selected = launcher.resolve_model(
            self.c, 'TASK-1', model=OVERRIDE_MODEL, thinking='low')
        self.assert_selection(selected, OVERRIDE_MODEL, 'low', 'override')
        self.assertEqual(self.c, before)
        following = launcher.resolve_model(self.c, 'TASK-1')
        self.assert_selection(following, PLAN_MODEL, 'high', 'plan:PLAN-1')

    def test_explicit_default_pair_is_implicitly_allowed(self):
        self.c['builder']['allowed_models'] = {}
        self.c['builder']['plan_models'] = {}
        selected = launcher.resolve_model(
            self.c, 'TASK-1', model=DEFAULT_MODEL, thinking='medium')
        self.assert_selection(selected, DEFAULT_MODEL, 'medium', 'override')

    def test_supplied_item_is_used_without_modifying_it(self):
        item = copy.deepcopy(self.items['TASK-1'])
        before = copy.deepcopy(item)
        del self.items['TASK-1']
        selected = launcher.resolve_model(self.c, 'TASK-1', item=item)
        self.assert_selection(selected, PLAN_MODEL, 'high', 'plan:PLAN-1')
        self.assertEqual(item, before)

    def test_partial_overrides_are_refused_without_config_mutation(self):
        for override in ({'model': PLAN_MODEL}, {'thinking': 'high'}):
            with self.subTest(override=override):
                before = copy.deepcopy(self.c)
                with self.assertRaises(ValueError):
                    launcher.resolve_model(self.c, 'TASK-1', **override)
                self.assertEqual(self.c, before)

    def test_disallowed_model_thinking_pairs_are_refused(self):
        for model, thinking in (
                ('unknown/model', 'high'), (PLAN_MODEL, 'low'),
                (DEFAULT_MODEL, 'high'), ('@builder', 'medium')):
            with self.subTest(model=model, thinking=thinking):
                before = copy.deepcopy(self.c)
                with self.assertRaises(ValueError):
                    launcher.resolve_model(self.c, 'TASK-1', model=model, thinking=thinking)
                self.assertEqual(self.c, before)

    def test_unknown_ancestry_is_refused_instead_of_falling_back(self):
        for missing in ('TASK-1', 'TASK-2', 'PLAN-1'):
            with self.subTest(missing=missing):
                items = copy.deepcopy(self.items)
                del items[missing]
                before = copy.deepcopy(self.c)
                with patch.object(self, 'items', items):
                    with self.assertRaises((ValueError, CommandError)):
                        launcher.resolve_model(self.c, 'TASK-1')
                self.assertEqual(self.c, before)

    def test_mismatched_parent_response_is_refused(self):
        self.items['TASK-2'] = {'ref': 'TASK-999', 'parent_ref': 'PLAN-1'}
        with self.assertRaises(ValueError):
            launcher.resolve_model(self.c, 'TASK-1')

    def test_cyclic_ancestry_is_refused_with_or_without_override(self):
        self.items['TASK-2']['parent_ref'] = 'TASK-1'
        for override in ({}, {'model': OVERRIDE_MODEL, 'thinking': 'low'}):
            with self.subTest(override=override):
                before = copy.deepcopy(self.c)
                with self.assertRaises(ValueError):
                    launcher.resolve_model(self.c, 'TASK-1', **override)
                self.assertEqual(self.c, before)

    def test_unreadable_ancestry_is_refused_even_with_explicit_pair(self):
        del self.items['PLAN-1']
        before = copy.deepcopy(self.c)
        with self.assertRaises((ValueError, CommandError)):
            launcher.resolve_model(
                self.c, 'TASK-1', model=OVERRIDE_MODEL, thinking='low')
        self.assertEqual(self.c, before)

    def test_quota_queries_and_evaluates_selected_provider_not_default_or_reviewer(self):
        for source, override, provider in (
                ('plan', {}, 'anthropic'),
                ('override', {'model': OVERRIDE_MODEL, 'thinking': 'low'}, 'other')):
            for remaining, allowed in ((.81, True), (.05, False)):
                with self.subTest(source=source, remaining=remaining):
                    selected = launcher.resolve_model(self.c, 'TASK-1', **override)
                    # Conflicting capacity ensures the real quota evaluator must
                    # select the chosen provider, not merely echo command args.
                    reports = {'reports': [
                        {'provider': 'openai-codex', 'fetchedAt': 1001000,
                         'limits': [{'id': 'weekly', 'amount': {
                             'remainingFraction': 0 if allowed else 1}}]},
                        {'provider': 'reviewer', 'fetchedAt': 1001000,
                         'limits': [{'id': 'weekly', 'amount': {
                             'remainingFraction': 0 if allowed else 1}}]},
                        {'provider': provider, 'fetchedAt': 1001000,
                         'limits': [{'id': 'weekly', 'amount': {
                             'remainingFraction': remaining}}]},
                    ]}
                    with patch.object(quota, 'json_run', return_value=reports) as usage, \
                            patch.object(quota.time, 'time', return_value=1002):
                        result = quota.collect(selected)
                    usage.assert_called_once_with(
                        ['omp', 'usage', '--json', '--provider', provider], '/unused')
                    self.assertEqual(result['provider'], provider)
                    self.assertTrue(result['known'])
                    self.assertEqual(result['may_start'], allowed)
                    self.assertEqual(result['windows'], [{
                        'window': 'weekly', 'accounts': 1,
                        'remaining_percent': round(remaining * 100, 2)}])

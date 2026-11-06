import time
import unittest
from fleet.health import branch, classify, owner
from fleet.quota import evaluate
from fleet.github import events


class HealthTests(unittest.TestCase):
    def test_wrong_model_and_live_prompt(self):
        t = {'connected': True, 'lastOutputAt': 100_000}
        model, flags, idle = classify(t, '◑ Opus 5.5 > 🌳 project\nRetry failed after 3 attempts', 'openai-codex/gpt-6.1-sol', 60, 200)
        self.assertEqual(model, 'Opus 5.5')
        self.assertEqual(flags, ['wrong_model', 'provider_stalled', 'idle'])
        self.assertEqual(idle, 100)
        _, flags, _ = classify(t, '◑ GPT-6.1 Sol > 🌳 project', 'openai-codex/gpt-6.1-sol', 600, 200)
        self.assertEqual(flags, [])

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


class DiffTests(unittest.TestCase):
    def test_comments_checks_and_close_are_distinct_events(self):
        p = {'number': 1, 'url': 'https://example/pr/1', 'branch': 'x', 'head': 'sha', 'state': 'open', 'checks': [], 'comments': [], 'reviews': []}
        before = {'prs': [p], 'merge_group_runs': []}
        after = {'prs': [{**p, 'checks': [{'id': 2, 'status': 'failure'}], 'comments': [{'id': 3}]}], 'merge_group_runs': []}
        result = events(before, after)
        self.assertEqual({x['subject'].split()[2] for x in result}, {'checks', 'comments'})
        closed = {'prs': [{**p, 'state': 'closed'}], 'merge_group_runs': []}
        self.assertEqual([e['subject'] for e in events(after, closed)], ['PR #1 state changed: closed'])
        self.assertEqual(events(after, after), [])
        self.assertEqual(events({}, before), [])


if __name__ == '__main__':
    unittest.main()

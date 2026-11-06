import unittest
from unittest.mock import patch
from fleet import quota
from fleet.commands import CommandError


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.c = {'root': '/unused', 'quota': {
            'provider': 'p', 'reserve_percent': 10, 'max_age_seconds': 900}}

    def report(self, at, remaining=.5, **extra):
        return {'reports': [{'provider': 'p', 'fetchedAt': at * 1000,
                             'limits': [{'id': 'weekly', 'amount': {'remainingFraction': remaining}}],
                             **extra}]}

    def test_new_report_uses_completion_time_not_collection_start(self):
        # A real fetch during a slow snapshot is newer than its start timestamp.
        with patch.object(quota, 'json_run', return_value=self.report(101)), \
                patch.object(quota.time, 'time', return_value=102):
            self.assertTrue(quota.collect(self.c)['may_start'])

    def test_stale_account_requires_refresh_and_rechecks_every_account(self):
        stale = self.report(1000)
        stale['reports'] += self.report(1)['reports']
        for refreshed, known, allowed in (
                (self.report(1001), True, True),
                (stale, False, False),
                (self.report(1001, .1), True, False),
                (self.report(1001, error='unavailable'), False, False)):
            with self.subTest(known=known, allowed=allowed), \
                    patch.object(quota, 'json_run', side_effect=[stale, refreshed]), \
                    patch.object(quota, 'run'), \
                    patch.object(quota.time, 'time', return_value=1002):
                result = quota.collect(self.c)
                self.assertEqual((result['known'], result['may_start']), (known, allowed))

    def test_refresh_failure_does_not_reuse_capacity_from_older_report(self):
        with patch.object(quota, 'json_run', return_value=self.report(1)), \
                patch.object(quota, 'run', side_effect=CommandError('unavailable')), \
                patch.object(quota.time, 'time', return_value=1002):
            with self.assertRaises(CommandError):
                quota.collect(self.c)

    def test_known_reserve_does_not_retry_until_capacity_appears(self):
        with patch.object(quota, 'json_run', side_effect=[self.report(1001, .1), self.report(1001)]), \
                patch.object(quota.time, 'time', return_value=1002):
            result = quota.collect(self.c)
            self.assertTrue(result['known'])
            self.assertFalse(result['may_start'])

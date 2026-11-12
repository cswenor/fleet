import copy
import tempfile
import unittest
from unittest.mock import patch

from fleet import snapshot, state
from fleet.commands import CommandError


class ProviderQuotaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.c = {'_state': tmp.name, 'root': tmp.name, 'repo': 'owner/repo',
                  'builder': {'resolved_model': 'default/model', 'max': 3},
                  'quota': {}, 'disk': {'path': tmp.name, 'min_free_gb': 0}}
        self.builders = [{'path': '/lane', 'terminal': 'builder', 'task': 'TASK-1',
                          'incarnation_id': 'one', 'connected': True, 'shell_ready': False}]
        self.receipt = {'terminal': 'builder', 'ref': 'TASK-1',
                        'terminal_incarnation_id': 'one', 'resolved_model': 'alternate/model'}
        state.write(self.c, 'launches', {'/lane': self.receipt})
        self.pools = {'default': {'provider': 'default', 'known': True, 'may_start': True},
                      'alternate': {'provider': 'alternate', 'known': False, 'may_start': False}}

    def collect(self):
        def quota(config):
            pool = self.pools[config['builder']['resolved_model'].split('/')[0]]
            if isinstance(pool, Exception):
                raise pool
            return copy.deepcopy(pool)
        with patch.object(snapshot.health, 'collect', return_value={
                'builders': self.builders, 'errors': []}), patch.object(
                snapshot.health, 'coordinator', return_value={}), patch.object(
                snapshot.health, 'add_pr_stalls'), patch.object(
                snapshot, '_github', return_value={}), patch.object(
                snapshot.quota, 'collect', side_effect=quota):
            return snapshot.collect(self.c)

    def test_unknown_alternate_does_not_block_default_admission(self):
        result = self.collect()
        self.assertEqual(result['quota_by_provider'], self.pools)
        self.assertTrue(snapshot.may_start(self.c, result)['may_start'])
        self.c['builder']['resolved_model'] = 'alternate/model'
        result = self.collect()
        self.assertFalse(snapshot.may_start(self.c, result)['may_start'])

    def test_failed_alternate_collection_is_unknown_without_global_error(self):
        self.pools['alternate'] = CommandError('unavailable')
        result = self.collect()
        self.assertFalse(result['quota_by_provider']['alternate']['known'])
        self.assertTrue(snapshot.may_start(self.c, result)['may_start'])
        self.assertEqual(result['errors'], [])

    def test_no_alternate_preserves_single_pool_snapshot(self):
        self.receipt['resolved_model'] = 'default/model'
        state.write(self.c, 'launches', {'/lane': self.receipt})
        result = self.collect()
        self.assertNotIn('quota_by_provider', result)
        self.assertEqual(result['quota'], self.pools['default'])

    def test_replaced_receipt_never_binds_provider_to_new_incarnation(self):
        self.builders[0]['incarnation_id'] = 'replacement'
        self.assertNotIn('quota_by_provider', self.collect())

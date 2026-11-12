import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from fleet.metrics import MeasurementError, merged_prs, tally, utc


class MetricsTests(unittest.TestCase):
    def test_merge_pagination_uses_updated_time_and_exact_boundaries(self):
        pages = [
            {'nodes': [
                {'number': 1, 'mergedAt': '2026-11-02T00:00:00Z',
                 'updatedAt': '2026-11-03T00:00:00Z'},
                {'number': 2, 'mergedAt': '2026-10-01T00:00:00Z',
                 'updatedAt': '2026-11-02T00:00:00Z'}],
             'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}},
            {'nodes': [
                {'number': 3, 'mergedAt': '2026-11-01T00:00:00Z',
                 'updatedAt': '2026-11-01T00:00:00Z'}],
             'pageInfo': {'hasNextPage': False, 'endCursor': None}}]
        responses = [subprocess.CompletedProcess([], 0, json.dumps({
            'data': {'repository': {'pullRequests': page}}})) for page in pages]
        with patch('fleet.metrics.subprocess.run', side_effect=responses):
            self.assertEqual(merged_prs('example/repo', utc('2026-11-01T00:00:00Z'),
                                       utc('2026-11-02T00:00:00Z')), 1)

    def test_copy_union_boundaries_and_auxiliary_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = {'type': 'session', 'id': 'same', 'cwd': '/project/lane'}
            def event(identity, stamp, kind='message'):
                message = dict(role='assistant', provider='openai-codex', model='sol',
                               usage=dict(input=3, output=2, cacheRead=5, cacheWrite=1,
                                          cost={'total': 0.5}))
                return dict(type=kind, id=identity, timestamp=stamp,
                            **({'message': message} if kind == 'message' else message))
            first = event('a', '2026-11-01T00:00:00Z')
            auxiliary = event('b', '2026-11-01T12:00:00Z', 'model_usage')
            excluded = event('c', '2026-11-02T00:00:00Z')
            files = [root / 'a.jsonl', root / 'b.jsonl']
            for path, events in zip(files, [[first], [first, auxiliary, excluded]]):
                path.write_text('\n'.join(json.dumps(e) for e in [
                    {'type': 'title', 'title': 'private'}, header, *events]) + '\n')
            result = tally(files, [('p', Path('/project'))],
                           utc('2026-11-01T00:00:00Z'), utc('2026-11-02T00:00:00Z'))
            self.assertEqual(result['coverage']['sessions'], 1)
            self.assertEqual(result['coverage']['duplicate_events'], 1)
            self.assertEqual(sum(r['calls'] for r in result['usage']), 2)
            self.assertEqual(sum(r['cache_read'] for r in result['usage']), 10)
            self.assertEqual(sum(r['recorded_list_usd'] for r in result['usage']), 1)
            self.assertEqual({r['event_type'] for r in result['usage']}, {'message', 'model_usage'})

    def test_fork_and_compressed_copy_do_not_inflate_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            files = [Path(directory) / 'a.jsonl', Path(directory) / 'b.jsonl.gz']
            event = dict(type='message', id='inherited', timestamp='2026-11-01T00:00:00Z',
                         message=dict(role='assistant', provider='p', model='m',
                                      usage=dict(input=7, output=2, cacheRead=11, cacheWrite=0)))
            for path, sid in zip(files, ['original', 'fork']):
                header = dict(type='session', id=sid, cwd='/p')
                opener = gzip.open if path.suffix == '.gz' else open
                with opener(path, 'wt') as stream:
                    stream.write('\n'.join(map(json.dumps, [header, event])))
            result = tally(files, [('p', Path('/p'))], utc('2026-11-01T00:00:00Z'),
                           utc('2026-11-02T00:00:00Z'))
            self.assertEqual(result['coverage']['sessions'], 2)
            self.assertEqual(result['usage'][0]['calls'], 1)
            self.assertEqual(result['usage'][0]['input'], 7)

    def test_conflicting_copies_refuse_instead_of_choosing_one(self):
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for n in (1, 2):
                path = Path(directory) / f'{n}.jsonl'
                events = [dict(type='session', id='same', cwd='/p'),
                          dict(type='message', id='same', timestamp='2026-11-01T00:00:00Z',
                               message=dict(role='assistant', usage={'input': n}))]
                path.write_text('\n'.join(map(json.dumps, events)))
                files.append(path)
            # Missing token fields are independently invalid; complete them before comparing copies.
            for path in files:
                events = [json.loads(line) for line in path.read_text().splitlines()]
                events[1]['message'].update(provider='p', model='m')
                events[1]['message']['usage'].update(output=0, cacheRead=0, cacheWrite=0)
                path.write_text('\n'.join(map(json.dumps, events)))
            with self.assertRaisesRegex(MeasurementError, 'conflicting duplicate'):
                tally(files, [('p', Path('/p'))], utc('2026-11-01T00:00:00Z'),
                      utc('2026-11-02T00:00:00Z'))

    def test_unknown_cost_and_usage_are_not_complete_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.jsonl'
            events = [dict(type='session', id='s', cwd='/p')]
            for n, usage in enumerate([None, dict(input=0, output=0, cacheRead=0, cacheWrite=0)]):
                events.append(dict(type='message', id=str(n), timestamp='2026-11-01T00:00:00Z',
                                   message=dict(role='assistant', provider='p', model='m', usage=usage)))
            path.write_text('\n'.join(map(json.dumps, events)))
            result = tally([path], [('p', Path('/p'))], utc('2026-11-01T00:00:00Z'),
                           utc('2026-11-02T00:00:00Z'))
            self.assertEqual(result['coverage']['missing_usage'], 1)
            self.assertEqual(result['usage'][0]['unpriced_calls'], 1)


if __name__ == '__main__':
    unittest.main()

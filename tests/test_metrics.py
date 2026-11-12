import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from fleet.metrics import MeasurementError, merged_prs, tally, utc


class MetricsTests(unittest.TestCase):
    def test_isolated_helpers_follow_inventoried_ancestry_but_explicit_cwd_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ('main.jsonl', 'helper.jsonl', 'nested.jsonl.gz',
                                             'other.jsonl')]
            for index, (path, cwd) in enumerate(zip(files, ['/alpha', '/isolated/one',
                                                          '/isolated/two', '/beta'])):
                header = dict(type='session', id=str(index), cwd=cwd)
                if index:
                    header['parentSession'] = str(files[1 if index == 2 else 0])
                events = [header]
                if index:
                    events.append(dict(type='message', id=str(index),
                                       timestamp='2026-11-01T00:00:00Z',
                                       message=dict(role='assistant', provider='p', model='m',
                                                    usage=dict(input=index, output=0,
                                                               cacheRead=10, cacheWrite=0,
                                                               cost={'total': 0.25}))))
                opener = gzip.open if path.suffix == '.gz' else open
                with opener(path, 'wt') as stream:
                    stream.write('\n'.join(map(json.dumps, events)))
            result = tally(list(reversed(files)), [('alpha', Path('/alpha')),
                                                   ('beta', Path('/beta'))],
                           utc('2026-11-01T00:00:00Z'), utc('2026-11-02T00:00:00Z'))
            rows = {row['project']: row for row in result['usage']}
            self.assertEqual(rows['alpha']['calls'], 2)
            self.assertEqual(rows['alpha']['input'], 3)
            self.assertEqual(rows['alpha']['cache_read'], 20)
            self.assertEqual(rows['beta']['calls'], 1)
            self.assertEqual(rows['beta']['input'], 3)
            self.assertEqual(result['coverage']['unattributed_calls'], 0)

    def test_uninventoried_ancestor_is_not_silently_read(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'parent.jsonl'
            child = Path(directory) / 'child.jsonl'
            parent.write_text(json.dumps(dict(type='session', id='parent', cwd='/alpha')))
            child.write_text('\n'.join(map(json.dumps, [
                dict(type='session', id='child', cwd='/isolated', parentSession=str(parent)),
                dict(type='message', id='call', timestamp='2026-11-01T00:00:00Z',
                     message=dict(role='assistant', provider='p', model='m',
                                  usage=dict(input=1, output=1, cacheRead=1, cacheWrite=0)))])))
            result = tally([child], [('alpha', Path('/alpha'))],
                           utc('2026-11-01T00:00:00Z'), utc('2026-11-02T00:00:00Z'))
            self.assertEqual(result['usage'], [])
            self.assertEqual(result['coverage']['unattributed_calls'], 1)

    def test_cyclic_unknown_ancestry_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a.jsonl', 'b.jsonl')]
            for index, path in enumerate(paths):
                path.write_text(json.dumps(dict(type='session', id=str(index), cwd='/isolated',
                                               parentSession=str(paths[1 - index]))))
            with self.assertRaisesRegex(MeasurementError, 'cyclic session ancestry'):
                tally(paths, [('alpha', Path('/alpha'))], utc('2026-11-01T00:00:00Z'),
                      utc('2026-11-02T00:00:00Z'))

    def test_malformed_header_refuses_instead_of_dropping_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            for text in ('', '{invalid', '{"type":"title"}\n{invalid'):
                path.write_text(text)
                with self.assertRaisesRegex(MeasurementError, 'unreadable session header'):
                    tally([path], [('p', Path('/p'))], utc('2026-11-01T00:00:00Z'),
                          utc('2026-11-02T00:00:00Z'))

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
                                   message=dict(role='assistant', provider='p', model='m',
                                                stopReason='error' if usage is None else 'stop', usage=usage)))
            path.write_text('\n'.join(map(json.dumps, events)))
            result = tally([path], [('p', Path('/p'))], utc('2026-11-01T00:00:00Z'),
                           utc('2026-11-02T00:00:00Z'))
            self.assertEqual(result['coverage']['missing_usage'], 1)
            self.assertEqual(result['usage'][0]['calls'], 2)
            self.assertEqual(result['usage'][0]['errors'], 1)
            self.assertEqual(result['usage'][0]['missing_usage'], 1)
            self.assertEqual(result['usage'][0]['unpriced_calls'], 2)


if __name__ == '__main__':
    unittest.main()

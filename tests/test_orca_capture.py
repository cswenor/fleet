import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fleet import orca_capture as capture


class CaptureTests(unittest.TestCase):
    def test_latest_resolved_events_and_partial_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.jsonl'
            events = [
                {'type': 'session', 'id': 's', 'cwd': '/slot', 'secret': 'private'},
                {'type': 'model_change', 'model': 'provider/old'},
                {'type': 'thinking_level_change', 'thinkingLevel': 'low'},
                {'type': 'message', 'body': 'private'},
                {'type': 'model_change', 'model': 'provider/current', 'resolvedModelIsFallback': True},
                {'type': 'thinking_level_change', 'thinkingLevel': 'medium'},
            ]
            path.write_text(''.join(json.dumps(e) + '\n' for e in events))
            result = capture._session(path)
            self.assertEqual(result['model'], {'model': 'provider/current', 'resolvedModelIsFallback': True})
            self.assertEqual(result['thinking'], {'thinkingLevel': 'medium'})
            self.assertTrue(result['complete'])
            self.assertNotIn('private', json.dumps(result))
            with path.open('a') as stream:
                stream.write('{"type":')
            self.assertFalse(capture._session(path)['complete'])

    def test_private_text_excluded_and_runtime_change_not_success(self):
        calls = []
        def native(config, *args):
            calls.append(args)
            if args == ('status',):
                return {'runtime': {'runtimeId': 'before' if len(calls) == 1 else 'after',
                                    'appVersion': '1', 'credential': 'private'},
                        'app': {'pid': 123, 'secret': 'private'}}
            if args[:2] == ('terminal', 'list'):
                return {'terminals': [{'handle': 'term', 'worktreePath': '/slot',
                                       'title': 'private', 'preview': 'private'}]}
            if args[1] in ('run-current', 'run-show'):
                return {'run': {'id': 'run', 'objective': 'private'}}
            if args[1] == 'inbox':
                return {'messages': [{'id': 'msg', 'read': 0, 'sequence': 7,
                                      'body': 'private', 'subject': 'private', 'payload': 'private'}]}
            self.fail('mutation or unsupported read')
        c = {'root': '/root', 'builder': {'slots': ['/slot']}, 'coordinator': {'run': 'run'}}
        with patch.object(capture, 'orca', side_effect=native), \
                patch.object(capture, '_processes', return_value=([], [])), \
                patch.object(capture, 'run', return_value='ActiveState=active\nMainPID=9\n'):
            result = capture.collect(c, ['fleet-test.service'])
        self.assertIn('runtime_identity_unstable', result['errors'])
        self.assertEqual(result['mailbox']['messages'], [{'id': 'msg', 'read': 0, 'sequence': 7}])
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['services'][0]['ActiveState'], 'active')

    def test_no_overwrite_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'before'
            result = {'runtime': {}, 'terminals': [], 'processes': [], 'errors': []}
            argv = ['capture', '--config', 'config', '--out', str(directory)]
            with patch('sys.argv', argv), patch.object(capture, 'load', return_value={'repo': 'a/b'}), \
                    patch.object(capture, 'collect', return_value=result) as collect:
                self.assertEqual(capture.main(), 0)
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                path = directory / 'snapshot.json'
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(capture.main(), 2)
                self.assertEqual(collect.call_count, 1)
                self.assertEqual(json.loads(path.read_text()), result)


if __name__ == '__main__':
    unittest.main()

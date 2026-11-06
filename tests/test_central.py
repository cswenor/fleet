import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from fleet import central


class CentralTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = {'host': '127.0.0.1', 'port': 0, 'database': str(Path(self.directory.name) / 'status.db'),
                  'projects': {'owner/first': 'FIRST', 'owner/second': 'SECOND'},
                  '_tokens': {'owner/first': 'a' * 32, 'owner/second': 'b' * 32},
                  'history_limit': 3, 'history_hours': 48, 'stale_seconds': 600}
        self.server = central.Server(self.c)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.now = time.time()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, method, path, payload=None, token='a' * 32, **headers):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        try:
            conn.request(method, path, body, {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **headers})
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def snapshot(self, **values):
        return {'version': 1, 'repo': 'owner/first', 'generated_at': self.now, 'errors': [], **values}

    def test_credentials_cannot_write_another_project_or_create_projects(self):
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(), token='wrong')[0], 401)
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(repo='owner/second'))[0], 400)
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(repo='owner/unknown'))[0], 400)
        self.assertEqual(self.server.store.latest('owner/first'), (None, None))
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot())[0], 204)
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(repo='owner/second'), token='b' * 32)[0], 204)
        self.assertEqual(set(self.server.store.latest('owner/second')[1]), {'version', 'repo', 'generated_at', 'errors'})

    def test_malformed_and_oversized_payload_does_not_poison_status(self):
        for fields in ({'generated_at': float('nan')}, {'generated_at': self.now + 300},
                       {'github': []}, {'inventory': {'builders': [None]}}, {'errors': 'bad'},
                       {'errors': ['\ud800']}):
            with self.subTest(fields=fields):
                self.assertEqual(self.request('POST', '/snapshots', self.snapshot(**fields))[0], 400)
        # Oversized declarations are rejected before the server reads any body.
        self.assertEqual(self.request('POST', '/snapshots', **{'Content-Length': str(2 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(), **{'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.server.store.latest('owner/first'), (None, None))
        self.assertEqual(self.request('GET', '/')[0], 200)

    def test_latest_survives_restart_late_delivery_and_history_eviction(self):
        for offset in (0, 10, -10, -20, -30):
            self.assertEqual(self.request('POST', '/snapshots', self.snapshot(generated_at=self.now + offset))[0], 204)
        restarted = central.Store(self.c)
        self.assertEqual(restarted.latest('owner/first')[1]['generated_at'], self.now + 10)
        self.assertEqual(len(restarted.history('owner/first')), 3)
        restarted.save(self.snapshot(generated_at=self.now - 1), self.now + 49 * 3600)
        self.assertEqual(restarted.latest('owner/first')[1]['generated_at'], self.now + 10)
        self.assertEqual(len(restarted.history('owner/first')), 2)

    def test_missing_stale_collector_failure_and_untrusted_html_remain_distinct(self):
        status, html = self.request('GET', '/')
        self.assertIn('NEVER RECEIVED', html)
        self.assertEqual(self.request('POST', '/snapshots', self.snapshot(
            generated_at=self.now - 601, errors=['<script>alert(1)</script>'],
            api_token='should-not-persist', github={'prs': [{'number': 7, 'state': 'open',
                'url': 'javascript:alert(1)', 'branch': 'work', 'title': '<b>Feature name</b>'}]},
            inventory={'builders': [{'task': '<img src=x>', 'branch': 'work', 'model': 'provider/model', 'state': 'active'}]}))[0], 204)
        _, html = self.request('GET', '/project/owner%2Ffirst')
        self.assertIn('STALE / UNREACHABLE', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img src=x&gt;', html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn('#7', html)
        self.assertIn('&lt;b&gt;Feature name&lt;/b&gt;', html)
        self.assertNotIn('api_token', json.dumps(self.server.store.latest('owner/first')))
        self.assertNotIn('NEVER RECEIVED', html)
        self.assertEqual(self.request('GET', '/project/owner%2Funknown')[0], 404)

    def test_activity_window_excludes_expired_events_even_on_stale_snapshot(self):
        iso = lambda seconds: central.datetime.fromtimestamp(seconds, central.timezone.utc).isoformat()
        self.request('POST', '/snapshots', self.snapshot(github={'prs': [
            {'number': 1, 'created_at': iso(self.now - 90000), 'merged_at': iso(self.now - 20), 'closed_at': iso(self.now - 20)},
            {'number': 2, 'created_at': iso(self.now - 20)}], 'commits': [{'date': iso(self.now - 90000)}, {'date': iso(self.now - 20)}]}))
        _, html = self.request('GET', '/')
        self.assertIn('<td>1</td><td>1</td><td>1</td><td>1</td>', html)
        self.assertFalse(central.recent(iso(self.now + 3600), self.now))


class BindConfigTests(unittest.TestCase):
    def test_wildcard_binding_requires_explicit_proxy_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'central.yml'
            for host, proxy, allowed in (('127.0.0.1', False, True), ('100.64.0.1', False, True),
                                         ('0.0.0.0', False, False), ('0.0.0.0', 'true', False),
                                         ('0.0.0.0', True, True), ('198.51.100.1', True, False)):
                with self.subTest(host=host, proxy=proxy):
                    path.write_text(central.yaml.safe_dump({'version': 1, 'host': host,
                        'behind_proxy': proxy, 'projects': {'owner/project': 'PROJECT_TOKEN'}}))
                    if allowed:
                        self.assertEqual(central.load(path, require_tokens=False)['host'], host)
                    else:
                        with self.assertRaises(ValueError):
                            central.load(path, require_tokens=False)


if __name__ == '__main__':
    unittest.main()

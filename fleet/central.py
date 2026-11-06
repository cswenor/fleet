"""Tailnet-only, read-only status UI and authenticated snapshot ingestion."""
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
import hmac
import ipaddress
import json
import math
import os
import re
import sqlite3
import time
import yaml


# Persist only the public snapshot contract, never arbitrary extra payload fields.
TEXT = (str, type(None))
NUMBER = (int, float)
PR = {'number': int, 'url': str, 'branch': str, 'state': str, 'created_at': TEXT,
      'closed_at': TEXT, 'merged_at': TEXT, 'time_to_merge_seconds': (int, type(None)),
      'merge_state': str, 'checks': [{'name': str, 'status': TEXT, 'conclusion': TEXT}]}
SCHEMA = {'version': int, 'repo': str, 'generated_at': NUMBER, 'errors': [str],
          'github': {'prs': [PR], 'since': str, 'default_branch': str,
                     'commits': [{'sha': str, 'url': str, 'date': str}],
                     'merge_queue': [{'state': str, 'pullRequest': {'number': int}}],
                     'merge_group_runs': [{'id': int, 'status': str, 'conclusion': TEXT,
                                           'url': str, 'prs': [int], 'membership_complete': bool}]},
          'inventory': {'builders': [{'task': TEXT, 'title': TEXT, 'branch': str, 'model': TEXT,
                                      'state': str, 'connected': bool, 'flags': [str]}],
                        'errors': [str], 'stray_worktrees': [str]},
          'quota': {'provider': str, 'reserve_percent': NUMBER, 'known': bool, 'may_start': bool,
                    'reason': str, 'windows': [{'window': TEXT, 'accounts': int, 'remaining_percent': NUMBER}]},
          'disk': {'free_gb': NUMBER, 'min_free_gb': NUMBER, 'low': bool}}


def project(value, schema):
    """Validate known fields and discard unrecognized/private collector metadata."""
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ValueError('expected object')
        return {k: project(v, schema[k]) for k, v in value.items() if k in schema}
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError('expected list')
        return [project(v, schema[0]) for v in value]
    if not isinstance(value, schema) or (isinstance(value, bool) and schema is not bool):
        raise ValueError('invalid field type')
    if isinstance(value, (float, int)) and not math.isfinite(value):
        raise ValueError('invalid number')
    if isinstance(value, str):
        value.encode('utf-8')  # Reject lone surrogates before they can poison HTML rendering.
    return value


def load(path, require_tokens=True):
    path = Path(path).expanduser().resolve()
    c = yaml.safe_load(path.read_text())
    if not isinstance(c, dict) or c.get('version') != 1:
        raise ValueError('central config requires version: 1')
    host = c.get('host', '127.0.0.1')
    address = ipaddress.ip_address(host)
    if not (address.is_loopback or address in ipaddress.ip_network('100.64.0.0/10') or
            address in ipaddress.ip_network('fd7a:115c:a1e0::/48')):
        raise ValueError('central host must be loopback or a Tailscale address')
    if address.version != 4:
        raise ValueError('central host currently requires IPv4')
    c['host'] = host
    c.setdefault('port', 8787)
    c.setdefault('stale_seconds', 600)
    c.setdefault('history_hours', 48)
    c.setdefault('history_limit', 1000)
    for key in ('port', 'stale_seconds', 'history_hours', 'history_limit'):
        if type(c[key]) is not int or c[key] < 1:
            raise ValueError('central limits must be positive integers')
    if c['port'] > 65535:
        raise ValueError('invalid central port')
    repos = c.get('projects')
    if not isinstance(repos, dict) or not repos:
        raise ValueError('central projects must map repo to token environment name')
    tokens = {}
    for repo, variable in repos.items():
        if not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('central project must be owner/name')
        if not isinstance(variable, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', variable):
            raise ValueError('invalid token environment name')
        token = os.environ.get(variable, '')
        if require_tokens and (len(token) < 32 or not token.isascii() or any(ch.isspace() for ch in token)):
            raise ValueError('each project needs a private ASCII token of at least 32 characters')
        if require_tokens and token in tokens.values():
            raise ValueError('project tokens must be distinct')
        tokens[repo] = token
    c['_tokens'] = tokens
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    c['database'] = str(Path(c.get('database', base / 'fleet/central/snapshots.sqlite3')).expanduser().resolve())
    c['_path'] = str(path)
    return c


class Store:
    def __init__(self, c):
        self.c = c
        path = Path(c['database'])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, repo TEXT NOT NULL, received REAL NOT NULL, generated REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS snapshots_repo ON snapshots(repo, generated DESC, id DESC)')
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.c['database'], timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, s, now):
        with self.connect() as db:
            db.execute('INSERT INTO snapshots(repo, received, generated, payload) VALUES (?, ?, ?, ?)',
                       (s['repo'], now, s['generated_at'], json.dumps(s, allow_nan=False)))
            # Never let late/replayed snapshots replace newer collection state.
            for repo in self.c['projects']:
                db.execute('DELETE FROM snapshots WHERE repo=? AND id NOT IN (SELECT id FROM snapshots WHERE repo=? ORDER BY generated DESC, id DESC LIMIT ?)',
                           (repo, repo, self.c['history_limit']))
                db.execute('DELETE FROM snapshots WHERE repo=? AND received<? AND id != (SELECT id FROM snapshots WHERE repo=? ORDER BY generated DESC, id DESC LIMIT 1)',
                           (repo, now - self.c['history_hours'] * 3600, repo))

    def latest(self, repo):
        with self.connect() as db:
            row = db.execute('SELECT received, payload FROM snapshots WHERE repo=? ORDER BY generated DESC, id DESC LIMIT 1', (repo,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def history(self, repo):
        with self.connect() as db:
            return db.execute('SELECT received, generated FROM snapshots WHERE repo=? ORDER BY generated DESC, id DESC LIMIT 50', (repo,)).fetchall()


def text(value):
    return escape(str(value)) if value is not None else 'unknown'


def link(url, label):
    try:
        parts = urlsplit(url or '')
    except ValueError:
        return text(label)
    if parts.scheme not in ('https', 'http') or not parts.netloc:
        return text(label)
    return f'<a href="{escape(url, quote=True)}" rel="noreferrer">{text(label)}</a>'


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join('<th>' + text(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def recent(value, now):
    try:
        return now - 86400 <= datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() <= now
    except (ValueError, TypeError, AttributeError):
        return False


def render_project(c, store, repo, now, detail=False):
    received, s = store.latest(repo)
    title = f'<a href="/project/{quote(repo, safe="")}">{text(repo)}</a>'
    out = f'<section><h2>{title}</h2>'
    if s is None:
        return out + '<p class="warning">UNREACHABLE / NEVER RECEIVED — no snapshot</p></section>'
    age = max(0, now - s['generated_at'])
    stale = age > c['stale_seconds'] or now - received > c['stale_seconds']
    out += f'<p class="{"warning" if stale else "fresh"}">{"STALE / UNREACHABLE" if stale else "LIVE"} — snapshot age {int(age)}s; received {text(stamp(received))}; collected {text(stamp(s["generated_at"]))}</p>'
    errors = s.get('errors', []) + s.get('inventory', {}).get('errors', [])
    if errors:
        out += '<p class="warning">Collector errors: ' + text('; '.join(errors)) + '</p>'
    g = s.get('github')
    if g is None:
        out += '<p class="warning">GitHub unavailable — activity unknown</p>'
        g = {}
    else:
        prs = g.get('prs', [])
        counts = [sum(recent(p.get(field), now) for p in prs) for field in ('created_at', 'merged_at', 'closed_at')]
        commits = [x for x in g.get('commits', []) if recent(x.get('date'), now)]
        out += table(['Last 24h: opened', 'Merged', 'Closed (includes merged)', 'Default-branch commits'], [[text(v) for v in counts + [len(commits)]]])
        out += table(['PR', 'State', 'Opened', 'Merged / closed', 'Time to merge'],
                     [[link(p.get('url'), '#' + str(p.get('number', '?'))), text(p.get('state')), text(p.get('created_at')),
                       text(p.get('merged_at') or p.get('closed_at')), text(str(p['time_to_merge_seconds']) + 's' if p.get('time_to_merge_seconds') is not None else None)]
                      for p in prs if p.get('state') == 'open' or any(recent(p.get(f), now) for f in ('created_at', 'merged_at', 'closed_at'))])
        out += '<h3>Merge queue</h3>'
        queue = g.get('merge_queue', [])
        out += table(['PR', 'State'], [[text(x.get('pullRequest', {}).get('number')), text(x.get('state'))] for x in queue]) if queue else '<p>Queue empty</p>'
        failures = [x for x in g.get('merge_group_runs', []) if x.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required')]
        out += '<h3>Recent merge-group failures (collector 24h window)</h3>'
        out += table(['Run', 'Result', 'PRs'], [[link(x.get('url'), x.get('id')), text(x.get('conclusion')), text(', '.join(map(str, x.get('prs', []))) or 'membership unknown')] for x in failures]) if failures else '<p>No reported failures</p>'
        if detail:
            out += '<h3>Default-branch commits (24h)</h3>' + table(['Commit', 'Time'], [[link(x.get('url'), x.get('sha', '')[:12]), text(x.get('date'))] for x in commits])
    out += '<h3>Builder sessions</h3>'
    inventory = s.get('inventory')
    if inventory is None:
        out += '<p class="warning">Inventory unavailable</p>'
    else:
        rows = []
        for b in inventory.get('builders', []):
            prs = [p for p in g.get('prs', []) if p.get('branch') == b.get('branch')]
            rows.append([text(b.get('task') or b.get('title')), ', '.join(link(p.get('url'), '#' + str(p.get('number'))) for p in prs) or '—', text(b.get('model')), text(b.get('state')), text(', '.join(b.get('flags', [])))])
        out += table(['Task', 'PR', 'Model', 'State', 'Flags'], rows) if rows else '<p>No builder sessions</p>'
        if inventory.get('stray_worktrees'):
            out += '<p class="warning">Stray worktrees: ' + text(', '.join(inventory['stray_worktrees'])) + '</p>'
    q, d = s.get('quota', {}), s.get('disk', {})
    out += '<h3>Quota and disk</h3><p>' + text(q.get('provider')) + ': ' + text(q.get('reason')) + '; disk free ' + text(d.get('free_gb')) + ' GB / reserve ' + text(d.get('min_free_gb')) + ' GB' + (' — LOW' if d.get('low') else '') + '</p>'
    out += table(['Quota window', 'Accounts', 'Remaining', 'Reserve'], [[text(w.get('window')), text(w.get('accounts')), text(w.get('remaining_percent')) + '%', text(q.get('reserve_percent')) + '%'] for w in q.get('windows', [])])
    if detail:
        out += '<h3>Recent snapshot history (up to 50)</h3>' + table(['Received', 'Collected'], [[text(stamp(r)), text(stamp(g))] for r, g in store.history(repo)])
    return out + '</section>'


def page(c, store, repo=None):
    now = time.time()
    body = ''.join(render_project(c, store, name, now, repo is not None) for name in ([repo] if repo else sorted(c['projects'])))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>Fleet status</title><style>body{font:16px system-ui;margin:2rem auto;padding:0 1rem;max-width:1100px;color:#202124;background:#fafafa}a{color:#174ea6}section{border:1px solid #bbb;border-radius:8px;padding:1rem;margin:1rem 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:.5rem;border-bottom:1px solid #ddd;overflow-wrap:anywhere}.scroll{overflow-x:auto}.warning{color:#9b2316;font-weight:600}.fresh{color:#17612e}h3{margin-bottom:.5rem} @media(prefers-color-scheme:dark){body{background:#17191c;color:#eee}a{color:#9dc1ff}.warning{color:#ffad99}.fresh{color:#8ed6a0}th,td{border-color:#444}}</style><header><h1><a href="/">Fleet status</a></h1><p>Read-only · last 24 hours · refresh every 60s · ' + text(stamp(now)) + '</p></header><main>' + body + '</main></html>').encode()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, c):
        self.config = c
        self.store = Store(c)
        super().__init__((c['host'], c['port']), Handler)

    def handle_error(self, request, client_address):
        # No tracebacks containing request payloads or headers in service logs.
        print('fleet central: request failed', flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Fleet'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def respond(self, status, body=b'', content_type='text/plain; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        repo = unquote(path[len('/project/'):]) if path.startswith('/project/') else None
        if path != '/' and (repo is None or repo not in self.server.config['projects']):
            return self.respond(404)
        self.respond(200, page(self.server.config, self.server.store, repo), 'text/html; charset=utf-8')

    def do_POST(self):
        if self.path != '/snapshots':
            return self.respond(404)
        auth = self.headers.get('Authorization', '')
        matches = [repo for repo, token in self.server.config['_tokens'].items()
                   if hmac.compare_digest(auth.encode(), ('Bearer ' + token).encode())]
        if len(matches) != 1:
            return self.respond(401)
        try:
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return self.respond(400)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2 * 1024 * 1024:
                return self.respond(413)
            if self.headers.get_content_type() != 'application/json':
                return self.respond(415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self.respond(400)
            s = project(json.loads(raw), SCHEMA)
            now = time.time()
            if s.get('version') != 1 or s.get('repo') != matches[0] or 'errors' not in s:
                return self.respond(400)
            generated = s.get('generated_at')
            if generated is None or not 0 <= generated <= now + 60:
                return self.respond(400)
            self.server.store.save(s, now)
        except (ValueError, TypeError, RecursionError, OverflowError):
            return self.respond(400)
        except sqlite3.Error:
            return self.respond(503)
        self.respond(204)


def serve(c):
    os.umask(0o077)
    with Server(c) as server:
        print('fleet central: listening', flush=True)
        server.serve_forever()

"""Owner-operated App creation; only live App APIs establish installation authority."""
import base64
import html
import json
import re
import secrets
import select
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import state
from .review_bot_api import APIError, GitHub, app_client, verify_pin
from .review_bot_files import create_environment, private_parent, read_config, save_config


PERMISSIONS = {
    'review': {'contents': 'read', 'pull_requests': 'write', 'checks': 'write',
               'actions': 'read', 'issues': 'read', 'statuses': 'read',
               'merge_queues': 'read'},
    'builder': {'contents': 'write', 'pull_requests': 'write'},
}
EVENTS = {'review': ['pull_request', 'pull_request_review', 'pull_request_review_comment',
                     'issue_comment', 'check_run', 'check_suite', 'status', 'push',
                     'workflow_run', 'merge_group'], 'builder': []}


def prerequisites(c):
    bot = c.get('review_bot', {})
    missing = []
    for field in ('apps.owner', 'apps.owner_type', 'apps.review.name', 'apps.review.env_file',
                  'apps.builder.name', 'apps.builder.env_file', 'webhook_url'):
        value = bot
        for part in field.split('.'):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        if not value:
            missing.append('review_bot.' + field)
    if missing:
        raise ValueError('configure ' + ', '.join(missing))
    return bot


def manifest(app, role, webhook_url, callback):
    return {'name': app['name'], 'url': 'https://github.com/cswenor/fleet-review-bot',
            'public': False, 'redirect_url': callback,
            'hook_attributes': {'url': webhook_url, 'active': role == 'review'},
            'default_permissions': PERMISSIONS[role], 'default_events': EVENTS[role]}


class ManifestCallback:
    """Loopback-only, nonce-bound callback. Request paths/codes are never logged."""
    def __init__(self, app, role, owner, owner_type, webhook_url):
        self.nonce = secrets.token_urlsafe(32)
        self.code = None
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.headers.get('Host') != f'127.0.0.1:{flow.server.server_port}':
                    self.send_error(400)
                    return
                parsed = urlsplit(self.path)
                if parsed.path == '/start/' + flow.nonce and flow.code is None:
                    destination = (f'https://github.com/organizations/{owner}/settings/apps/new'
                                   if owner_type == 'organization' else 'https://github.com/settings/apps/new')
                    destination += '?state=' + flow.nonce
                    payload = json.dumps(manifest(app, role, webhook_url, flow.callback_url))
                    page = ('<!doctype html><meta charset="utf-8"><title>Create project App</title>'
                            '<p>Review the GitHub manifest and create this private project App.</p>'
                            f'<form method="post" action="{html.escape(destination, quote=True)}">'
                            f'<input type="hidden" name="manifest" value="{html.escape(payload, quote=True)}">'
                            '<button type="submit">Continue to GitHub</button></form>')
                    self.respond(200, page)
                    return
                if parsed.path == '/created/' + flow.nonce and flow.code is None:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if (query.get('state') == [flow.nonce] and len(query.get('code', [])) == 1
                            and re.fullmatch(r'[A-Za-z0-9_-]{1,512}', query['code'][0])):
                        flow.code = query['code'][0]
                        # Scrub the one-use code from the browser's current URL.
                        self.send_response(303)
                        self.send_header('Location', f'https://github.com/organizations/{owner}/settings/apps'
                                         if owner_type == 'organization' else 'https://github.com/settings/apps')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Referrer-Policy', 'no-referrer')
                        self.end_headers()
                        return
                    self.respond(403, 'Creation callback state rejected. Return to the terminal.')
                    return
                self.respond(404, 'Not found')

            def respond(self, status, text):
                encoded = text.encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(encoded)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; form-action https://github.com; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(encoded)

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = 1
        self.start_url = f'http://127.0.0.1:{self.server.server_port}/start/{self.nonce}'
        self.callback_url = f'http://127.0.0.1:{self.server.server_port}/created/{self.nonce}'

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        try:
            while self.code is None and time.monotonic() < deadline:
                self.server.handle_request()
            if self.code is None:
                raise ValueError('App creation callback timed out; reconcile the pending creation before retrying')
            return self.code
        finally:
            self.server.server_close()


def _confirm_install(prompt, timeout):
    print(prompt, flush=True)
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready or not sys.stdin.readline():
            raise ValueError()
    except (OSError, ValueError):
        raise ValueError('installation confirmation unavailable; saved App receipts are preserved for resume') from None


def verify_app(c, role, client):
    bot = prerequisites(c)
    expected = bot['apps'][role]
    remote = client.request('GET', '/app')
    if not isinstance(remote, dict):
        raise ValueError('invalid App identity response')
    permissions = {**PERMISSIONS[role], 'metadata': 'read'}
    actual = remote.get('permissions', {})
    if (remote.get('id') != expected['id'] or remote.get('slug') != expected['slug']
            or remote.get('name') != expected['name']
            or remote.get('owner', {}).get('login', '').lower() != bot['apps']['owner'].lower()
            or remote.get('owner', {}).get('type') != {'user': 'User', 'organization': 'Organization'}[bot['apps']['owner_type']]
            or not isinstance(actual, dict) or {**actual, 'metadata': actual.get('metadata', 'read')} != permissions
            or sorted(remote.get('events', [])) != sorted(EVENTS[role])):
        raise ValueError(f'{role} App identity, owner, permissions or events differ from the private project manifest')
    if role == 'review':
        hook = client.request('GET', '/app/hook/config')
        if not isinstance(hook, dict) or hook.get('url') != bot['webhook_url']:
            raise ValueError('Review App webhook does not target this project instance')
    return remote


def verify_installation(c, role, client):
    app = c['review_bot']['apps'][role]
    installation = client.request('GET', f'/repos/{c["repo"]}/installation')
    if (not isinstance(installation, dict) or type(installation.get('id')) is not int
            or installation['id'] < 1 or installation.get('app_id') != app['id']
            or installation.get('repository_selection') != 'selected'
            or installation.get('account', {}).get('login', '').lower() != c['repo'].split('/')[0].lower()
            or installation.get('suspended_at') is not None):
        raise ValueError(f'{role} App requires an active selected-repository installation for this project owner')
    if app.get('installation_id') is not None and app['installation_id'] != installation['id']:
        raise ValueError(f'{role} App installation receipt differs from live installation')
    # Deliberately do not restrict the minted token: that would conceal extra
    # repositories selected on a broad installation.
    issued = client.request('POST', f'/app/installations/{installation["id"]}/access_tokens', {})
    token = issued.get('token') if isinstance(issued, dict) else None
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ValueError('App installation token unavailable')
    scoped = GitHub(token)
    try:
        repositories = scoped.request('GET', '/installation/repositories?per_page=100')
        rows = repositories.get('repositories', []) if isinstance(repositories, dict) else []
        if (not isinstance(repositories, dict) or repositories.get('total_count') != 1 or len(rows) != 1
                or rows[0].get('full_name', '').lower() != c['repo'].lower()):
            raise ValueError(f'{role} App installation must select ONLY this repository')
    finally:
        scoped.request('DELETE', '/installation/token')
    return installation['id']


def initialize(c, *, apply=False, release='main', timeout=600, github=None,
               notify=print, confirm=_confirm_install):
    bot = prerequisites(c)
    github = github or GitHub.owner()
    repository = github.request('GET', f'/repos/{c["repo"]}')
    owner_type = {'user': 'User', 'organization': 'Organization'}[bot['apps']['owner_type']]
    if (not isinstance(repository, dict) or repository.get('full_name', '').lower() != c['repo'].lower()
            or repository.get('owner', {}).get('type') != owner_type):
        raise ValueError('repository identity/owner type differs from the private project config')
    from .review_bot_ruleset import verify_capability, verify_live_ruleset
    plan = {'operation': 'init', 'apply': apply, 'repository': c['repo'],
            'webhook_url': bot['webhook_url'],
            'create_url': (f'https://github.com/organizations/{bot["apps"]["owner"]}/settings/apps/new'
                           if bot['apps']['owner_type'] == 'organization' else 'https://github.com/settings/apps/new'),
            'apps': {role: {'name': bot['apps'][role]['name'], 'private': True,
                            'permissions': PERMISSIONS[role], 'events': EVENTS[role],
                            'install_url': ('https://github.com/apps/' + bot['apps'][role]['slug'] + '/installations/new')
                            if bot['apps'][role].get('slug') else None,
                            'repository_selection': [c['repo']]}
                     for role in ('review', 'builder')},
            'publication': {'mode': 'auto_merge', 'release': release, 'hold_label': 'hold',
                            'applied': False,
                            'activation_prerequisite': 'Explicit live-verified App-bound ruleset; run ruleset --dry-run, ruleset --apply, then rerun init --apply'},
            'repository_auto_merge_enabled': repository.get('allow_auto_merge') is True,
            'pin': bot.get('source'),
            'owner_steps': ['Create each private App through the local manifest form',
                            'Install each App selecting ONLY this repository',
                            'Review and apply the App-bound ruleset, enable repository auto-merge, then rerun init --apply to activate publication'],
            'blockers': [] if bot.get('source') else ['An explicit verified version/commit/image pin is required before --apply']}
    try:
        verify_capability(c, github)
    except ValueError as exc:
        plan['blockers'].append(str(exc))
    if not apply:
        return plan
    if release not in ('main', 'none') or type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('invalid release policy or owner-step timeout (1–600 seconds)')
    if plan['blockers']:
        raise ValueError('; '.join(plan['blockers']))
    if not shutil.which('openssl'):
        raise ValueError('openssl is required for GitHub App authentication')
    verify_pin(bot['source'], github)
    with state.lock(c, 'review-bot-install', operation='Review Bot installation'):
        document, encoded = read_config(c)
        if document.get('review_bot') != bot:
            raise ValueError('Review Bot configuration changed during pin verification; reload before init')
        def unchanged():
            if read_config(c)[1] != encoded:
                raise ValueError('configuration changed during initialization; reconcile preserved Apps before retrying')
        # Resolve all known prerequisites before creating either new App.
        for role in ('review', 'builder'):
            app = bot['apps'][role]
            private_parent(app['env_file'])
            if not app.get('id'):
                if (state.read(c, 'review-bot-create-' + role) or Path(app['env_file']).exists()
                        or Path(app['env_file']).is_symlink()):
                    raise ValueError(f'{role} App creation is pending or credentials exist; reconcile before retrying')
            else:
                client = app_client(app, role)
                verify_app(c, role, client)
                try:
                    verify_installation(c, role, client)
                except APIError as exc:
                    if exc.status != 404:
                        raise
        for role in ('review', 'builder'):
            app = bot['apps'][role]
            unchanged()
            private_parent(app['env_file'])
            receipt = state.read(c, 'review-bot-create-' + role)
            if not app.get('id'):
                if receipt or Path(app['env_file']).exists() or Path(app['env_file']).is_symlink():
                    raise ValueError(f'{role} App creation is pending or credentials already exist; recover the App or revoke it before retrying')
                flow = ManifestCallback(app, role, bot['apps']['owner'], bot['apps']['owner_type'], bot['webhook_url'])
                state.write(c, 'review-bot-create-' + role, {'repo': c['repo'], 'name': app['name'], 'stage': 'owner_creation_pending'})
                notify(f'{role} App: open {flow.start_url} in your own browser on this host (forward its loopback port if remote).')
                code = flow.wait(timeout)
                unchanged()
                converted = GitHub().request('POST', '/app-manifests/' + code + '/conversions')
                if (not isinstance(converted, dict) or type(converted.get('id')) is not int or converted['id'] < 1
                        or not all(isinstance(converted.get(key), str) and converted[key]
                                   for key in ('slug', 'client_id', 'pem', 'webhook_secret'))):
                    raise ValueError('App conversion unconfirmed; reconcile the pending creation, never retry blindly')
                if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', converted['slug']):
                    raise ValueError('App conversion returned an invalid slug')
                prefix = 'BUILDER_' if role == 'builder' else ''
                values = {prefix + 'GITHUB_APP_ID': converted['id'],
                          prefix + 'GITHUB_APP_CLIENT_ID': converted['client_id'],
                          prefix + 'GITHUB_APP_PRIVATE_KEY_B64': base64.b64encode(converted['pem'].encode()).decode()}
                if role == 'review':
                    values['GITHUB_WEBHOOK_SECRET'] = converted['webhook_secret']
                app.update(id=converted['id'], slug=converted['slug'])
                client = app_client(app, role, values=values)
                verify_app(c, role, client)
                unchanged()
                create_environment(app['env_file'], values)
                document['review_bot']['apps'][role].update(id=app['id'], slug=app['slug'])
                encoded = save_config(c, document, encoded)
                state.write(c, 'review-bot-create-' + role, {'repo': c['repo'], 'name': app['name'], 'id': app['id'], 'stage': 'credentials_saved'})
            client = app_client(app, role)
            verify_app(c, role, client)
            try:
                installation_id = verify_installation(c, role, client)
            except APIError as exc:
                if exc.status != 404:
                    raise
                notify(f'{role} App installation: https://github.com/apps/{app["slug"]}/installations/new')
                confirm(f'Select ONLY {c["repo"]}, install, then press Enter. No other repository is permitted.', timeout)
                installation_id = verify_installation(c, role, app_client(app, role))
            app['installation_id'] = installation_id
            document['review_bot']['apps'][role]['installation_id'] = installation_id
            encoded = save_config(c, document, encoded)
            (state.directory(c) / ('review-bot-create-' + role + '.json')).unlink(missing_ok=True)
        try:
            plan['protection'] = verify_live_ruleset(c, github)
        except ValueError as exc:
            plan['blockers'] = [str(exc) + '; Apps are preserved, publication is unchanged. Run ruleset --dry-run, ruleset --apply, then rerun init --apply']
            return plan
        publication = document.setdefault('publication', {})
        unchanged()
        publication.update(mode='auto_merge', release=release, hold_label='hold', review_app=bot['apps']['review']['slug'])
        save_config(c, document, encoded)
        plan['publication']['applied'] = True
    plan['publication']['review_app'] = bot['apps']['review']['slug']
    plan['blockers'] = []
    return plan

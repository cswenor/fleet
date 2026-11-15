"""Process-local installation credentials for GitHub reads; never persist tokens."""
from collections import OrderedDict
from datetime import datetime
import hashlib
import os
import time

from .review_bot_api import APIError, GitHub, app_client
from .service_env import read_environment


_TOKENS = OrderedDict()
READ_PERMISSIONS = {'contents': 'read', 'pull_requests': 'read', 'checks': 'read',
                    'actions': 'read', 'statuses': 'read', 'merge_queues': 'read',
                    'issues': 'read', 'metadata': 'read'}


class MissingPermissions(ValueError):
    pass


def resolve(c):
    auth = c.get('github', {}).get('read_auth')
    if not auth:
        return None, {'kind': 'stock'}
    try:
        if os.environ.get('GH_HOST', 'github.com') not in ('github.com', 'api.github.com'):
            raise ValueError()
        values = read_environment(auth['app_env_file'])
        key = (c['repo'].lower(), auth['installation_id'], auth['app_env_file'],
               hashlib.sha256(repr(sorted(values.items())).encode()).hexdigest())
        cached = _TOKENS.get(key)
        if cached and cached[1] > time.time() + 60:
            _TOKENS.move_to_end(key)
            token = cached[0]
        else:
            app_id = int(values['BUILDER_GITHUB_APP_ID'])
            client = app_client({'id': app_id, 'env_file': auth['app_env_file']},
                                'builder', values=values)
            installation = client.request('GET', f'/repos/{c["repo"]}/installation')
            if (not isinstance(installation, dict) or installation.get('id') != auth['installation_id']
                    or installation.get('app_id') != app_id
                    or installation.get('repository_selection') != 'selected'
                    or not isinstance(installation.get('account'), dict)
                    or not isinstance(installation['account'].get('login'), str)
                    or installation.get('account', {}).get('login', '').lower() != c['repo'].split('/')[0].lower()
                    or installation.get('suspended_at') is not None):
                raise ValueError()
            permissions = installation.get('permissions', {})
            if not isinstance(permissions, dict):
                raise ValueError()
            missing = [name + ':read' for name in sorted(READ_PERMISSIONS)
                       if permissions.get(name) not in ('read', 'write')]
            if missing:
                raise MissingPermissions('read_auth App lacks ' + ', '.join(missing))
            # Inspect an unrestricted token so repository narrowing cannot hide a
            # broad installation. Revoke this verification token, as onboarding does.
            issued = client.request('POST', f'/app/installations/{auth["installation_id"]}/access_tokens', {})
            probe = GitHub(_token(issued))
            try:
                from .review_bot_apps import verify_repository_scope
                verify_repository_scope(probe, c['repo'])
            finally:
                probe.request('DELETE', '/installation/token')
            requested = dict(READ_PERMISSIONS)
            issued = client.request('POST', f'/app/installations/{auth["installation_id"]}/access_tokens',
                                    {'permissions': requested})
            token = _token(issued)
            if not isinstance(issued.get('expires_at'), str):
                raise ValueError()
            expires = datetime.fromisoformat(issued['expires_at'].replace('Z', '+00:00'))
            if expires.tzinfo is None or expires.timestamp() <= time.time() + 60:
                raise ValueError()
            _TOKENS[key] = (token, expires.timestamp())
            _TOKENS.move_to_end(key)
            while len(_TOKENS) > 32:
                _TOKENS.popitem(last=False)
        return {**os.environ, 'GH_TOKEN': token, 'GITHUB_TOKEN': token}, {
            'kind': 'app_installation', 'installation_id': auth['installation_id']}
    except MissingPermissions as error:
        return None, {'kind': 'stock', 'fallback': str(error)}
    except (APIError, ValueError, KeyError, TypeError, OverflowError, OSError):
        # Credential/API response details can contain secrets. Never expose them.
        return None, {'kind': 'stock', 'fallback': 'App read credentials unavailable or invalid'}


def _token(issued):
    token = issued.get('token') if isinstance(issued, dict) else None
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ValueError('App installation token unavailable')
    return token

"""GitHub transport and short-lived App authentication without secret argv/logs."""
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .commands import CommandError, run
from .service_env import read_environment
from .review_bot_files import private_parent


class APIError(ValueError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f'GitHub API refused the operation (HTTP {status})' if status else
                         'GitHub API unavailable or returned an invalid response')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class GitHub:
    def __init__(self, token=None):
        self.token = token
        self.opener = build_opener(_NoRedirect())

    @classmethod
    def owner(cls):
        try:
            token = run(['gh', 'auth', 'token'], timeout=15).strip()
        except CommandError:
            raise ValueError('GitHub owner authentication unavailable; authenticate gh first') from None
        if not token or any(char.isspace() for char in token):
            raise ValueError('GitHub owner authentication unavailable')
        return cls(token)

    def request(self, method, path, body=None):
        if not path.startswith('/') or path.startswith('//') or any(ord(char) < 32 for char in path):
            raise ValueError('invalid GitHub API path')
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'fleet-review-bot-installer'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request('https://api.github.com' + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise APIError()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            # Never include response bodies, request URLs, headers or conversion codes.
            raise APIError(exc.code) from None
        except (OSError, URLError, ValueError):
            raise APIError() from None


def _encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


def app_client(app, role, *, values=None):
    env = read_environment(app['env_file']) if values is None else values
    prefix = 'BUILDER_' if role == 'builder' else ''
    try:
        allowed = {prefix + 'GITHUB_APP_' + key for key in ('ID', 'CLIENT_ID', 'PRIVATE_KEY_B64')}
        if role == 'review':
            allowed.add('GITHUB_WEBHOOK_SECRET')
        if set(env) != allowed:
            raise ValueError()
        client_id = env[prefix + 'GITHUB_APP_CLIENT_ID']
        if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,128}', client_id):
            raise ValueError()
        if int(env[prefix + 'GITHUB_APP_ID']) != app['id']:
            raise ValueError()
        key = base64.b64decode(env[prefix + 'GITHUB_APP_PRIVATE_KEY_B64'], validate=True)
        now = int(time.time())
        payload = _encoded(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': client_id},
                                      separators=(',', ':')).encode())
        message = _encoded(b'{"alg":"RS256","typ":"JWT"}') + b'.' + payload
        with tempfile.TemporaryDirectory(prefix='.fleet-app-jwt-', dir=private_parent(app['env_file']).parent) as directory:
            path = Path(directory) / 'key.pem'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(key)
            signed = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(path)],
                                    input=message, capture_output=True, timeout=15)
            if signed.returncode or not signed.stdout:
                raise ValueError()
        return GitHub((message + b'.' + _encoded(signed.stdout)).decode())
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired):
        raise ValueError(f'{role} App credentials are unavailable or do not match the receipt') from None


def verify_pin(source, github):
    """Match the explicit pin to an annotated release and its immutable receipt."""
    repository, version = source['repository'], source['version']
    root = f'/repos/{repository}'
    reference = github.request('GET', f'{root}/git/ref/tags/{version}')
    obj = reference.get('object', {}) if isinstance(reference, dict) else {}
    if obj.get('type') != 'tag' or not re.fullmatch(r'[0-9a-f]{40}', str(obj.get('sha', ''))):
        raise ValueError('Review Bot pin requires an annotated release tag')
    tag = github.request('GET', f'{root}/git/tags/{obj["sha"]}')
    if (tag.get('tag') != version or tag.get('object', {}).get('type') != 'commit'
            or tag.get('object', {}).get('sha') != source['commit']):
        raise ValueError('Review Bot version tag does not match the pinned commit')
    ancestry = github.request('GET', f'{root}/compare/{source["commit"]}...main')
    if ancestry.get('status') not in ('ahead', 'identical'):
        raise ValueError('Review Bot release commit is not in main history')
    release = github.request('GET', f'{root}/releases/tags/{version}')
    assets = [asset for asset in release.get('assets', []) if asset.get('name') == 'release.json']
    expected = f'https://github.com/{repository}/releases/download/{version}/release.json'
    if (release.get('draft') or release.get('prerelease') or release.get('tag_name') != version
            or len(assets) != 1 or assets[0].get('browser_download_url') != expected):
        raise ValueError('Review Bot release has no authoritative release.json receipt')
    try:
        # Public download only: a redirect can never forward an owner/App token.
        with urlopen(Request(expected, headers={'User-Agent': 'fleet-review-bot-installer'}), timeout=30) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        receipt = json.loads(raw)
    except (OSError, URLError, ValueError):
        raise ValueError('Review Bot release receipt unavailable or invalid') from None
    if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in source.items()):
        raise ValueError('Review Bot release receipt differs from the explicit project pin')
    match = re.fullmatch(re.escape(f'https://github.com/{repository}/actions/runs/') + r'([1-9][0-9]*)',
                         str(receipt.get('run', '')))
    if not match or receipt.get('platform') != 'linux/amd64':
        raise ValueError('Review Bot release receipt lacks supported platform/build provenance')
    verification = github.request('GET', f'{root}/actions/runs/{match[1]}')
    if (verification.get('head_sha') != source['commit'] or verification.get('conclusion') != 'success'
            or verification.get('event') != 'push' or verification.get('path') != '.github/workflows/release.yml'):
        raise ValueError('Review Bot release workflow has not successfully verified the pinned commit')
    return receipt

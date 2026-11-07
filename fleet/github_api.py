"""Conditional REST reads through stock gh authentication, with private bounded state."""
from collections import OrderedDict
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import os
import time
from urllib.parse import urljoin, urlsplit

from . import commands, state
from .commands import CommandError


_ACTIVE = ContextVar('fleet_github_api', default=None)
MAX_ENTRIES = 256
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_BACKOFF = 3600
_STATE_NAME = 'github-api'


class BudgetLow(CommandError):
    def __init__(self, reset):
        super().__init__('GitHub API budget unavailable until reset', code='github_budget_low')
        self.reset = reset


def active_session():
    return _ACTIVE.get()


def _arguments(args):
    """Only intercept untransformed REST GETs; other gh contracts stay untouched."""
    if not args or args[0] != 'api':
        return None
    endpoint = None
    headers = []
    paginate = slurp = False
    method = 'GET'
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in ('--paginate', '--slurp'):
            paginate |= arg == '--paginate'
            slurp |= arg == '--slurp'
        elif arg in ('-X', '--method', '-H', '--header'):
            index += 1
            if index == len(args):
                return None
            if arg in ('-X', '--method'):
                method = args[index].upper()
            else:
                headers.append(args[index])
        elif arg.startswith('--method='):
            method = arg.split('=', 1)[1].upper()
        elif arg.startswith('--header='):
            headers.append(arg.split('=', 1)[1])
        elif arg.startswith('-') or endpoint is not None:
            return None
        else:
            endpoint = arg
        index += 1
    if (method != 'GET' or not endpoint or
            endpoint.strip('/').split('?', 1)[0] == 'graphql' or
            urlsplit(endpoint).path.rstrip('/').endswith('/graphql')):
        return None
    # Explicit credentials and validators remain owned by the stock gh caller.
    if any(h.partition(':')[0].strip().lower() in
           ('if-none-match', 'if-modified-since', 'authorization', 'proxy-authorization',
            'x-auth-token', 'x-github-token')
           for h in headers):
        return None
    return endpoint, headers, paginate, slurp


def _response(stdout):
    text = stdout.replace('\r\n', '\n')
    headers = {}
    status = None
    while text.startswith('HTTP/'):
        block, separator, text = text.partition('\n\n')
        if not separator:
            raise CommandError('invalid GitHub HTTP response')
        lines = block.splitlines()
        match = re.fullmatch(r'HTTP/\S+\s+(\d{3})(?:\s+.*)?', lines[0])
        if not match:
            raise CommandError('invalid GitHub HTTP response')
        status = int(match[1])
        headers = {}
        for line in lines[1:]:
            key, colon, value = line.partition(':')
            if colon:
                key = key.strip().lower()
                headers[key] = headers.get(key, '') + (', ' if key in headers else '') + value.strip()
    if status is None:
        raise CommandError('invalid GitHub HTTP response')
    return status, headers, text


def _next(headers, endpoint):
    for link in headers.get('link', '').split(','):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"', link)
        if match and 'next' in match[2].split():
            host = os.environ.get('GH_HOST', 'github.com')
            api = 'https://api.github.com/' if host in ('github.com', 'api.github.com') else f'https://{host}/api/v3/'
            base = endpoint if urlsplit(endpoint).scheme else api + endpoint.lstrip('/')
            result = urljoin(base, match[1])
            # Never forward headers/authentication to an unrelated Link host.
            if urlsplit(result).scheme != 'https' or urlsplit(result).netloc != urlsplit(base).netloc:
                raise CommandError('invalid GitHub pagination link')
            return result
    return None


class Session:
    """Activate gh REST GET interception; retain this object across polling cycles.

    stats counts outbound API attempts (including uncached GraphQL) and 304s.
    budget is the last observed reset/remaining/limit mapping. blocked_until and
    BudgetLow.reset are Unix times. Persistent state contains only hashed auth
    identity, validators, cached bodies, next-page links, and budget metadata.
    """
    def __init__(self, c, persistent=False):
        self.c = c
        self.persistent = persistent
        self.stats = {'requests': 0, 'not_modified': 0}
        self.blocked_until = 0
        self.budget = None
        self._cache = OrderedDict()
        self._size = 0
        self._tokens = []
        self._dirty = False
        self._scope = self._identity()
        if persistent and self._scope:
            self._load()

    def __enter__(self):
        self._refresh_identity()
        self._tokens.append(_ACTIVE.set(self))
        return self

    def __exit__(self, *exc):
        try:
            if len(self._tokens) == 1:
                self._save()
        finally:
            _ACTIVE.reset(self._tokens.pop())

    def handles(self, c, args):
        return c == self.c and _arguments(args) is not None

    def _identity(self):
        host = os.environ.get('GH_HOST', 'github.com')
        names = (('GH_TOKEN', 'GITHUB_TOKEN') if host in ('github.com', 'api.github.com')
                 else ('GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'))
        token = next((os.environ[n] for n in names if os.environ.get(n)), None)
        if token is None and self.persistent:
            try:
                process = commands._execute(['gh', 'auth', 'token', '--hostname', host], self.c['root'])
                token = process.stdout.strip() if process.returncode == 0 else None
            except CommandError:
                token = None
        if not token:
            return None
        return hashlib.sha256((host + '\0' + token).encode()).hexdigest()

    def _refresh_identity(self):
        scope = self._identity()
        if scope != self._scope:
            self._scope = scope
            self._cache.clear()
            self._size = 0
            self.blocked_until = 0
            self.budget = None
            if self.persistent and scope:
                self._load()

    def observe(self, c, args):
        if c == self.c and args[:1] == ('api',):
            self.stats['requests'] += 1
            if any(urlsplit(a).path.rstrip('/').endswith('graphql') for a in args[1:]):
                self.stats['graphql'] = self.stats.get('graphql', 0) + 1

    def _key(self, c, endpoint, headers):
        # gh resolves relative endpoints against its stock configured host.
        identity = [self._scope, c['root'], c.get('repo'), endpoint, headers]
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def _put(self, key, entry):
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._size -= len(json.dumps([key, previous]).encode())
        size = len(json.dumps([key, entry]).encode())
        if size <= MAX_CACHE_BYTES:
            self._cache[key] = entry
            self._size += size
        while self._cache and (len(self._cache) > MAX_ENTRIES or self._size > MAX_CACHE_BYTES):
            old_key, old = self._cache.popitem(last=False)
            self._size -= len(json.dumps([old_key, old]).encode())

    def _load(self):
        try:
            saved = state.read(self.c, _STATE_NAME, {})
            if not isinstance(saved, dict) or saved.get('scope') != self._scope:
                return
            reset = saved.get('blocked_until', 0)
            if isinstance(reset, (int, float)):
                self.blocked_until = min(max(0, reset), time.time() + MAX_BACKOFF)
            budget = saved.get('budget')
            if (isinstance(budget, dict) and set(budget) == {'reset', 'remaining', 'limit'}
                    and all(type(v) is int and v >= 0 for v in budget.values())):
                self.budget = budget
            entries = saved.get('entries', [])
            if not isinstance(entries, list):
                return
            for item in entries[-MAX_ENTRIES:]:
                if not isinstance(item, list) or len(item) != 2:
                    continue
                key, entry = item
                if (not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key)
                        or not isinstance(entry, dict) or set(entry) != {'etag', 'body', 'next'}
                        or not isinstance(entry['etag'], str) or '\n' in entry['etag'] or '\r' in entry['etag']
                        or not isinstance(entry['body'], str)
                        or not (entry['next'] is None or isinstance(entry['next'], str))):
                    continue
                json.loads(entry['body'])
                self._put(key, entry)
        except (OSError, ValueError, TypeError):
            # A damaged cache is disposable; gh remains the source of truth.
            self._cache.clear()
            self._size = 0

    def _save(self):
        if self.persistent and self._scope and self._dirty:
            state.write(self.c, _STATE_NAME, {'scope': self._scope, 'entries': list(self._cache.items()),
                        'budget': self.budget, 'blocked_until': self.blocked_until})
            self._dirty = False

    def _rate(self, status, headers, body):
        now = time.time()
        try:
            budget = {key: int(headers['x-ratelimit-' + key])
                      for key in ('reset', 'remaining', 'limit')}
            if all(value >= 0 for value in budget.values()):
                self.budget = budget
        except (KeyError, ValueError):
            pass
        low = (self.budget is not None and self.budget['limit'] > 0 and
               self.budget['remaining'] < self.budget['limit'] * 0.1 and
               self.budget['reset'] > now)
        limited = (status == 429 or (status == 403 and
                   ('retry-after' in headers or
                    headers.get('x-ratelimit-remaining') == '0' or
                    bool(re.search(r'(?:rate limit|secondary rate|abuse detection)', body, re.I)))))
        if limited or low:
            reset = self.budget['reset'] if self.budget and self.budget['reset'] > now else now + 60
            retry = headers.get('retry-after')
            if retry:
                try:
                    retry_at = now + max(0, int(retry))
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(retry).timestamp()
                    except (ValueError, TypeError, OverflowError):
                        retry_at = now + 60
                reset = max(reset, retry_at) if low else retry_at
            self.blocked_until = max(self.blocked_until, min(now + MAX_BACKOFF, max(now + 1, reset)))
        return limited

    def request(self, c, args):
        endpoint, headers, paginate, slurp = _arguments(args)
        pages = []
        visited = set()
        while endpoint:
            if endpoint in visited:
                raise CommandError('invalid GitHub pagination cycle')
            visited.add(endpoint)
            page, following = self._page(c, endpoint, headers)
            pages.append(page)
            endpoint = following if paginate else None
        if slurp:
            return pages
        if len(pages) == 1:
            return pages[0]
        # gh emits adjacent page documents without --slurp. Preserve json_run's
        # existing rejection rather than silently changing its return shape.
        raise CommandError('invalid command JSON')

    def _page(self, c, endpoint, headers):
        if self.blocked_until > time.time():
            raise BudgetLow(self.blocked_until)
        self.blocked_until = 0
        self._dirty = True
        key = self._key(c, endpoint, headers)
        cached = self._cache.get(key)
        argv = ['gh', 'api', '-i', '-X', 'GET', endpoint]
        for header in headers:
            argv.extend(['-H', header])
        if cached:
            argv.extend(['-H', 'If-None-Match: ' + cached['etag']])
        self.stats['requests'] += 1
        process = commands._execute(argv, c['root'])
        try:
            status, response_headers, body = _response(process.stdout)
        except CommandError:
            if process.returncode:
                raise commands.GhError(endpoint, stderr=process.stderr) from None
            raise
        limited = self._rate(status, response_headers, body)
        if limited:
            self._save()
            raise BudgetLow(self.blocked_until)
        if status == 304:
            self.stats['not_modified'] += 1
            if not cached:
                self._save()
                raise CommandError('GitHub returned an uncached conditional response')
            self._cache.move_to_end(key)
            return json.loads(cached['body']), cached['next']
        if process.returncode or not 200 <= status < 300:
            self._save()
            raise commands.GhError(endpoint, status, process.stderr)
        try:
            parsed = json.loads(body)
        except ValueError as error:
            self._save()
            raise CommandError('invalid GitHub JSON') from error
        following = _next(response_headers, endpoint)
        etag = response_headers.get('etag')
        if etag and '\n' not in etag and '\r' not in etag:
            self._put(key, {'etag': etag, 'body': body, 'next': following})
        elif cached:
            self._size -= len(json.dumps([key, self._cache.pop(key)]).encode())
        return parsed, following

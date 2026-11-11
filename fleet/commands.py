"""Bounded subprocess calls; only gh emits credential-sanitized diagnostics."""
import json
import os
import re
import subprocess
import time
from urllib.parse import urlsplit


class CommandError(RuntimeError):
    def __init__(self, message, *, code='command_failed'):
        super().__init__(message)
        self.code = code


def _gh_reason(text):
    """Bound diagnostics without exposing credentials, auth headers, or URL queries."""
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text or '')
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
        token = os.environ.get(name)
        if token:
            text = text.replace(token, '[redacted]')
    text = re.sub(r'(?i)(?:proxy-authorization|authorization|x-auth-token|x-github-token)\s*[:=][^\r\n]*',
                  '[redacted auth header]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b', '[redacted]', text)
    text = re.sub(r'\b[0-9a-fA-F]{40}\b', '[redacted]', text)
    text = re.sub(r'(?i)\b(?:bearer|token|basic)\s+[A-Za-z0-9_./+=-]+', '[redacted credential]', text)
    text = re.sub(r'https?://[^\s<>]+', lambda m:
                  '/' + m[0].split('://', 1)[1].partition('/')[2].split('?', 1)[0].split('#', 1)[0], text)
    return ' '.join(text.split())[:300]


def _gh_endpoint(endpoint):
    path = urlsplit(endpoint).path
    return re.sub(r'[^A-Za-z0-9/._\[\]-]', '_', _gh_reason(path))[:180] or 'GitHub'


def _gh_target(args):
    skip = False
    for arg in args[1:]:
        if skip:
            skip = False
        elif arg in ('-X', '--method', '-H', '--header', '-f', '--field', '-F', '--raw-field',
                     '--hostname', '--jq', '-q', '--template', '-t', '--input', '--cache'):
            skip = True
        elif not arg.startswith('-'):
            return arg
    return 'GitHub'


class GhError(CommandError):
    def __init__(self, endpoint, status=None, stderr=''):
        if status is None:
            match = re.search(r'\bHTTP (\d{3})\b', stderr or '')
            status = int(match[1]) if match else None
        self.endpoint = _gh_endpoint(endpoint)
        self.status = status
        reason = _gh_reason(stderr)
        detail = f'HTTP {status}' if status is not None else 'request failed'
        super().__init__(f'GitHub {self.endpoint}: {detail}' + (f': {reason}' if reason else ''),
                         code='github_request_failed')

def _execute(argv, cwd=None, timeout=60, *, text=True):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=text, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CommandError(f'{argv[0].split("/")[-1]} unavailable or timed out') from e
    return p


def run(argv, cwd=None, timeout=60, *, text=True):
    p = _execute(argv, cwd, timeout, text=text)
    if p.returncode:
        raise CommandError(f'{argv[0].split("/")[-1]} exited {p.returncode}')
    return p.stdout


def json_run(argv, cwd=None):
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as e:
        raise CommandError('invalid command JSON') from e

_ORCHESTRATION_CAPABILITIES = {}


def _orchestration_full(executable, verb, timeout=60):
    # Discover before sending any mutation: retrying after a concise response
    # could send twice. Older Orca rejects --full even with --json.
    key = (executable, verb)
    if key in _ORCHESTRATION_CAPABILITIES:
        return _ORCHESTRATION_CAPABILITIES[key]
    try:
        help_text = run([executable, 'orchestration', verb, '--help'], timeout=timeout)
    except CommandError as error:
        if isinstance(error.__cause__, subprocess.TimeoutExpired):
            raise CommandError('Orca capability discovery timed out',
                               code='orca_capability_timeout') from error
        raise
    _, separator, options = help_text.partition('\nOptions:\n')
    full = re.findall(r'(?m)^\s+--full(?:\s|$)', options)
    if (not separator or f'Usage: orca orchestration {verb}' not in help_text
            or len(full) > 1 or ('--full' in help_text and not full)):
        raise CommandError('ambiguous Orca orchestration capabilities')
    supported = bool(full)
    if len(_ORCHESTRATION_CAPABILITIES) >= 64:
        _ORCHESTRATION_CAPABILITIES.pop(next(iter(_ORCHESTRATION_CAPABILITIES)))
    _ORCHESTRATION_CAPABILITIES[key] = supported
    return supported


def orchestration_args(executable, args, timeout=60):
    if args and '--full' not in args and _orchestration_full(executable, args[0], timeout=timeout):
        return (*args, '--full')
    return args



def orca(c, *args, timeout=60):
    deadline = time.monotonic() + timeout
    if args[:1] == ('orchestration',):
        args = ('orchestration', *orchestration_args(c['orca'], args[1:], timeout=timeout))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CommandError('Orca command deadline expired')
    p = _execute([c['orca'], *args, '--json'], c['root'], timeout=remaining)
    try:
        data = json.loads(p.stdout)
    except (ValueError, TypeError) as e:
        raise CommandError('invalid Orca JSON') from e
    if not isinstance(data, dict):
        raise CommandError('invalid Orca response')
    if data.get('ok') is False:
        error = data.get('error')
        code = error.get('code') if isinstance(error, dict) else None
        code = code if isinstance(code, str) and re.fullmatch('[a-z][a-z0-9_]{0,79}', code) else 'orca_rejected'
        raise CommandError(code, code=code)
    if p.returncode or data.get('ok') is not True or 'result' not in data:
        raise CommandError('invalid Orca response')
    return data['result']


def gh(c, *args):
    from .github_api import active_session
    session = active_session()
    if session is not None:
        if session.handles(c, args):
            return session.request(c, args)
        session.observe(c, args)
    process = _execute(['gh', *args], c['root'])
    if process.returncode:
        raise GhError(_gh_target(args), stderr=process.stderr)
    try:
        return json.loads(process.stdout)
    except (ValueError, TypeError) as error:
        raise CommandError('invalid command JSON') from error

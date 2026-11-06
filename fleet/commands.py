"""Bounded subprocess calls. Never include stderr/argv in errors: they can contain secrets."""
import json
import re
import subprocess


class CommandError(RuntimeError):
    def __init__(self, message, *, code='command_failed'):
        super().__init__(message)
        self.code = code


def _execute(argv, cwd=None, timeout=60):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CommandError(f'{argv[0].split("/")[-1]} unavailable or timed out') from e
    return p


def run(argv, cwd=None, timeout=60):
    p = _execute(argv, cwd, timeout)
    if p.returncode:
        raise CommandError(f'{argv[0].split("/")[-1]} exited {p.returncode}')
    return p.stdout


def json_run(argv, cwd=None):
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as e:
        raise CommandError('invalid command JSON') from e


def orca(c, *args):
    p = _execute([c['orca'], *args, '--json'], c['root'])
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
    return json_run(['gh', *args], c['root'])

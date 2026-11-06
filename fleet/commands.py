"""Bounded subprocess calls. Never include stderr/argv in errors: they can contain secrets."""
import json
import subprocess


class CommandError(RuntimeError):
    pass


def run(argv, cwd=None, timeout=60):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CommandError(f'{argv[0].split("/")[-1]} unavailable or timed out') from e
    if p.returncode:
        raise CommandError(f'{argv[0].split("/")[-1]} exited {p.returncode}')
    return p.stdout


def json_run(argv, cwd=None):
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as e:
        raise CommandError('invalid command JSON') from e


def orca(c, *args):
    data = json_run([c['orca'], *args, '--json'], c['root'])
    if not data.get('ok'):
        raise CommandError('Orca request rejected')
    return data['result']


def gh(c, *args):
    return json_run(['gh', *args], c['root'])

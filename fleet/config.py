"""Validated, project-local configuration. No provider credentials belong here."""
from pathlib import Path
import os
import re
import shutil
import yaml


def load(path):
    path = Path(path).expanduser().resolve()
    c = yaml.safe_load(path.read_text())
    if not isinstance(c, dict) or c.get('version') != 1:
        raise ValueError('fleet.yml requires version: 1')
    for key in ('repo', 'root', 'orca_repo_id', 'pad_workspace', 'builder', 'coordinator'):
        if not c.get(key):
            raise ValueError(f'missing config: {key}')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', c['repo']):
        raise ValueError('repo must be owner/name')
    c['root'] = str(Path(c['root']).expanduser().resolve())
    if not Path(c['root']).is_dir():
        raise ValueError('root must be an existing checkout')
    b = c['builder']
    for key in ('model', 'resolved_model', 'rules_file', 'slots'):
        if not b.get(key):
            raise ValueError(f'missing builder.{key}')
    if '/' not in b['resolved_model']:
        raise ValueError('builder.resolved_model must include provider/model')
    b.setdefault('thinking', 'medium')
    b.setdefault('max', len(b['slots']))
    b.setdefault('footer', '')
    b.setdefault('idle_seconds', 2700)
    if b['thinking'] not in ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'auto'):
        raise ValueError('invalid builder.thinking')
    if not isinstance(b['max'], int) or b['max'] < 1:
        raise ValueError('builder.max must be positive')
    if not isinstance(b['slots'], list) or not all(isinstance(s, str) for s in b['slots']):
        raise ValueError('builder.slots must be paths')
    b['slots'] = [str(Path(s).expanduser().resolve()) for s in b['slots']]
    q = c.setdefault('quota', {})
    q.setdefault('provider', b['resolved_model'].split('/')[0])
    q.setdefault('reserve_percent', 10)
    q.setdefault('max_age_seconds', 900)
    if not 0 <= q['reserve_percent'] <= 100 or q['max_age_seconds'] <= 0:
        raise ValueError('invalid quota thresholds')
    d = c.setdefault('disk', {})
    d.setdefault('path', c['root'])
    d.setdefault('min_free_gb', 10)
    if d['min_free_gb'] < 0:
        raise ValueError('disk.min_free_gb must be nonnegative')
    s = c.setdefault('status', {})
    s.setdefault('url', '')
    s.setdefault('token_env', '')
    s.setdefault('push_seconds', 300)
    c.setdefault('interval_seconds', 60)
    if c['interval_seconds'] < 1 or s['push_seconds'] < 1:
        raise ValueError('intervals must be positive')
    for key in ('terminal', 'run'):
        if not c['coordinator'].get(key):
            raise ValueError(f'missing coordinator.{key}')
    c.setdefault('hooks', {})
    for name, argv in c['hooks'].items():
        if name not in ('before_launch', 'after_launch', 'event') or not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError('hooks must be argv lists: before_launch, after_launch, event')
    c['orca'] = c.get('orca') or os.environ.get('ORCA_CLI_COMMAND') or shutil.which('orca-ide')
    if not c['orca']:
        raise ValueError('set orca to the absolute Orca CLI path')
    c['_path'] = str(path)
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    c['_state'] = str(base / 'fleet' / c['repo'].replace('/', '--'))
    return c

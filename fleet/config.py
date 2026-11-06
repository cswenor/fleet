"""Validated, project-local configuration. No provider credentials belong here."""
from pathlib import Path
import os
import re
import shutil
import sys
import yaml

STATUS_DEFAULTS = {'not_started': ['open'], 'in_progress': ['in-progress'],
                   'terminal': ['done', 'cancelled']}


def validate_statuses(statuses, *, allow_no_progress=False):
    if not isinstance(statuses, dict) or set(statuses) != set(STATUS_DEFAULTS):
        raise ValueError('pad.statuses requires not_started, in_progress and terminal')
    seen = set()
    for category, names in statuses.items():
        if (not isinstance(names, list) or
                (not names and not (allow_no_progress and category == 'in_progress')) or
                any(not isinstance(n, str) or not n.strip() for n in names)):
            raise ValueError('pad.statuses categories must be non-empty lists of status names')
        if seen.intersection(names) or len(set(names)) != len(names):
            raise ValueError('pad.statuses categories must be disjoint without duplicates')
        seen.update(names)
    return statuses


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
    pad = c.setdefault('pad', {})
    if not isinstance(pad, dict):
        raise ValueError('pad must be a mapping')
    if 'statuses' in pad:
        validate_statuses(pad['statuses'])
    b = c['builder']
    for key in ('model', 'resolved_model', 'rules_file', 'slots'):
        if not b.get(key):
            raise ValueError(f'missing builder.{key}')
    if '/' not in b['resolved_model']:
        raise ValueError('builder.resolved_model must include provider/model')
    b.setdefault('thinking', 'medium')
    b.setdefault('max', len(b['slots']))
    b.setdefault('orca_status_extension', os.environ.get('ORCA_OMP_STATUS_EXTENSION'))
    extension = b['orca_status_extension']
    if extension is not None and (not isinstance(extension, str) or not Path(extension).is_absolute()):
        raise ValueError('builder.orca_status_extension must be an absolute path or null')
    b.setdefault('footer', 'This session is already fresh for the assigned task. Do not signal FRESH-READY for an inherited handoff or boundary. Only after publishing your next piece of work, choose a different next task, write its handoff, report FRESH-READY with that different task ref to the coordinator, and stop. Never resume context or silently switch provider/model.')
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
    q.setdefault('park_timeout_seconds', 1800)
    q.setdefault('unknown_alert_cycles', 3)
    if type(q['unknown_alert_cycles']) is not int or q['unknown_alert_cycles'] < 1:
        raise ValueError('quota.unknown_alert_cycles must be a positive integer')
    if not isinstance(q['park_timeout_seconds'], (int, float)) or not 0 < q['park_timeout_seconds'] <= 86400:
        raise ValueError('quota.park_timeout_seconds must be positive and at most 86400')
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
    c['orca'] = (c.get('orca') or os.environ.get('ORCA_CLI_COMMAND') or
                 shutil.which('orca' if sys.platform == 'darwin' else 'orca-ide'))
    if not c['orca']:
        raise ValueError('set orca to the absolute Orca CLI path')
    c['_path'] = str(path)
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    c['_state'] = str(base / 'fleet' / c['repo'].replace('/', '--'))
    return c

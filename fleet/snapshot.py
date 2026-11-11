"""One snapshot contract for CLI, daemon, and central status."""
import json
import os
import re
from pathlib import Path
import shutil
import time
from . import github, github_api, health, quota
from .commands import CommandError
from .display import duration


def _github(c, result):
    with github_api.Session(c, persistent=True) as session:
        try:
            return github.collect(c)
        finally:
            result['github_requests'] = dict(session.stats)
            if session.budget:
                result['github_budget'] = dict(session.budget)
            if session.blocked_until > time.time():
                result['github_budget'] = {**result.get('github_budget', {}),
                                           'blocked': True, 'reset': session.blocked_until}


def _collection_reason(error):
    lines = str(error).splitlines()
    message = lines[0] if lines else ''
    message = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', message)
    message = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', message)
    # Exception messages may contain shell assignments or private configured values.
    for name, value in os.environ.items():
        if value and (len(value) >= 8 or re.search(
                r'(?i)token|secret|password|credential|auth|api_?key', name)):
            message = message.replace(value, '[redacted]')
    message = re.sub(
        r'(?i)\b(?:[A-Z_][A-Z_0-9]*\s*=|(?:password|secret|api[_-]?key|token)\s*:)\s*'
        r'(?:"(?:\\.|[^"\\])*"?|\'(?:\\.|[^\'\\])*\'?|\S+)',
        '[redacted assignment]', message)
    message = re.sub(
        r'(?i)(--?(?:password|secret|token|api[_-]?key|authorization))(?:=|\s+)'
        r'(?:"(?:\\.|[^"\\])*"?|\'(?:\\.|[^\'\\])*\'?|\S+)',
        r'\1 [redacted]', message)
    message = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/]*@[^\s]*',
                     '[redacted URL]', message)
    return (type(error).__name__ + ': ' + health._screen_evidence(message))[:200]


def collect(c):
    now = time.time()
    result = {'version': 1, 'repo': c['repo'], 'generated_at': now, 'errors': []}
    for key, fn in (('inventory', lambda: health.collect(c, now)), ('github', lambda: _github(c, result)),
                    ('quota', lambda: quota.collect(c)), ('coordinator', lambda: health.coordinator(c))):
        try:
            result[key] = fn()
        except github_api.BudgetLow as error:
            result['github_budget'] = {**result.get('github_budget', {}), 'reset': error.reset,
                                       'blocked': True}
        except (CommandError, ValueError, KeyError, TypeError) as error:
            label = key + ' collection unavailable'
            result['errors'].append(label)
            result.setdefault('error_details', {})[label] = _collection_reason(error)
    if result.get('inventory') is not None and result.get('github') is not None:
        health.add_pr_stalls(c, result['inventory'], result['github'], now)
    if c.get('_drill_usage_file'):
        result['drill'] = True
        result['real_quota'] = result.get('quota', {})
        result['quota'] = quota.evaluate(json.loads(Path(c['_drill_usage_file']).read_text()), c['quota'], time.time())
    try:
        free = shutil.disk_usage(c['disk']['path']).free / 1_000_000_000
        result['disk'] = {'path': c['disk']['path'], 'free_gb': round(free, 2), 'min_free_gb': c['disk']['min_free_gb'], 'low': free < c['disk']['min_free_gb']}
    except OSError as error:
        label = 'disk collection unavailable'
        result['errors'].append(label)
        result.setdefault('error_details', {})[label] = _collection_reason(error)
    return result


def may_start(c, s):
    reasons = []
    if s.get('errors'):
        reasons.extend(s['errors'])
    if s.get('github_budget', {}).get('blocked'):
        reasons.append('github budget low; visibility deferred until reset')
    if not s.get('quota', {}).get('may_start'):
        reasons.append(s.get('quota', {}).get('reason', 'quota unavailable'))
    if c.get('_drill_usage_file') and not s.get('real_quota', {}).get('may_start'):
        reasons.append('real quota: ' + s.get('real_quota', {}).get('reason', 'quota unavailable'))
    if s.get('disk', {}).get('low', True):
        reasons.append('disk reserve reached or unavailable')
    inventory = s.get('inventory', {})
    if inventory.get('errors'):
        reasons.append('terminal health incomplete')
    if len([b for b in inventory.get('builders', []) if health.active(b)]) >= c['builder']['max']:
        reasons.append('builder cap reached')
    return {'may_start': not reasons, 'reasons': reasons}


def render(s):
    lines = [f"Fleet {s['repo']}"]
    main = s.get('coordinator', {})
    if main.get('terminal'):
        state = main.get('state', 'unknown')
        label = 'DOWN' if state == 'down' else state
        lines.append(f"Main: {label} ({main.get('reason', 'terminal observation unavailable')})")
    g = s.get('github', {})
    since = g.get('since', '')
    prs = g.get('prs', [])
    lines.append('Last 24h: ' + ', '.join(f'{label}={sum(bool(p.get(field)) and p[field] >= since for p in prs)}' for label, field in [('opened', 'created_at'), ('merged', 'merged_at'), ('closed', 'closed_at')]) + f", main commits={len(g.get('commits', []))}")
    for p in prs:
        if p.get('merged_at') and p['merged_at'] >= since:
            lines.append(f"  #{p['number']} {p.get('title') or 'title unavailable'} merged in {duration(p['time_to_merge_seconds'])}")
    lines.append('Merge queue: ' + ', '.join(f"#{x['pullRequest']['number']}:{x['state']}" for x in g.get('merge_queue', [])))
    for r in g.get('merge_group_runs', []):
        if r['conclusion'] in ('failure', 'cancelled', 'timed_out'):
            lines.append(f"  group {r['id']} {r['conclusion']} {r['url']}")
    for b in s.get('inventory', {}).get('builders', []):
        lines.append(f"  {b['task'] or '-'} {b['terminal']} {b['model'] or 'UNKNOWN MODEL'} {','.join(b['flags']) or b['state']}")
    for path in s.get('inventory', {}).get('stray_worktrees', []):
        lines.append('Stray worktree: ' + path)
    q = s.get('quota', {})
    lines.append(f"Quota: {q.get('provider', '?')} {q.get('reason', 'unknown')} {q.get('windows', [])}")
    d = s.get('disk', {})
    lines.append(f"Disk: {d.get('free_gb', '?')} GB free; reserve {d.get('min_free_gb', '?')} GB")
    lines.extend('ERROR: ' + x for x in s['errors'])
    return '\n'.join(lines)

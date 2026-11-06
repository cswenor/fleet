"""One snapshot contract for CLI, daemon, and central status."""
import json
from pathlib import Path
import shutil
import time
from . import github, health, quota
from .commands import CommandError
from .display import duration


def collect(c):
    now = time.time()
    result = {'version': 1, 'repo': c['repo'], 'generated_at': now, 'errors': []}
    for key, fn in (('inventory', lambda: health.collect(c, now)), ('github', lambda: github.collect(c)),
                    ('quota', lambda: quota.collect(c))):
        try:
            result[key] = fn()
        except (CommandError, ValueError, KeyError, TypeError):
            result['errors'].append(key + ' collection unavailable')
    if c.get('_drill_usage_file'):
        result['drill'] = True
        result['real_quota'] = result.get('quota', {})
        result['quota'] = quota.evaluate(json.loads(Path(c['_drill_usage_file']).read_text()), c['quota'], time.time())
    try:
        free = shutil.disk_usage(c['disk']['path']).free / 1_000_000_000
        result['disk'] = {'path': c['disk']['path'], 'free_gb': round(free, 2), 'min_free_gb': c['disk']['min_free_gb'], 'low': free < c['disk']['min_free_gb']}
    except OSError:
        result['errors'].append('disk collection unavailable')
    return result


def may_start(c, s):
    reasons = []
    if s.get('errors'):
        reasons.extend(s['errors'])
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

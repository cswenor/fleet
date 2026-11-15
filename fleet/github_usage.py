"""Bounded request metadata only; never persist URLs, tokens or response bodies."""
from contextvars import ContextVar
import os
import re
import time
import sys
import uuid

from . import state

_ACTIVE = ContextVar('fleet_github_usage', default=None)
_PROCESS = uuid.uuid4().hex
MAX_ROWS = 2048


def configure(c, kind, pr=None, ref=None):
    _ACTIVE.set((c, kind, pr, ref))
    record()


def enabled():
    return _ACTIVE.get() is not None


def record(rest=0, graphql=0):
    active = _ACTIVE.get()
    if active is None:
        return
    c, kind, pr, ref = active
    now = time.time()
    minute = int(now // 60) * 60
    try:
        _record(c, kind, pr, ref, now, minute, rest, graphql)
    except (OSError, ValueError, KeyError, TypeError):
        # A completed GitHub mutation must never look failed because accounting
        # is unavailable. Report the gap without exposing stored data.
        print('fleet: GitHub request attribution unavailable', file=sys.stderr)


def _record(c, kind, pr, ref, now, minute, rest, graphql):
    with state.lock(c, 'github-usage', wait_seconds=5):
        saved = state.read(c, 'github-usage', {})
        rows = [r for r in saved.get('rows', []) if r['at'] >= now - 3600]
        row = next((r for r in rows if r['process'] == _PROCESS and r['at'] == minute
                    and (r['kind'], r['pr'], r['ref']) == (kind, pr, ref)), None)
        if row is None:
            row = dict(at=minute, process=_PROCESS, pid=os.getpid(), kind=kind,
                       project=c['repo'], pr=pr, ref=ref, rest=0, graphql=0)
            rows.append(row)
        row['rest'] += rest
        row['graphql'] += graphql
        truncated_until = saved.get('truncated_until', 0)
        if len(rows) > MAX_ROWS:
            rows.sort(key=lambda r: r['at'])
            truncated_until = now + 3600
        state.write(c, 'github-usage', dict(rows=rows[-MAX_ROWS:],
                    truncated_until=truncated_until))


def trace(stderr):
    """Count gh's actual HTTP attempts, including pagination and native commands.

    GH_DEBUG=api is captured in memory, never printed or persisted. Discard the
    complete trace before existing bounded error diagnostics can see it.
    """
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors='replace')
    paths = re.findall(r'^> [A-Z]+ (\S+) HTTP/\S+', stderr or '', re.M)
    graphql = sum(path.split('?', 1)[0].rstrip('/').endswith('/graphql') for path in paths)
    record(len(paths) - graphql, graphql)


def consumers(c):
    saved = state.read(c, 'github-usage', {})
    now = time.time()
    totals = {}
    for row in saved.get('rows', []):
        if row['at'] < now - 3600:
            continue
        key = (row['process'], row['kind'], row['pr'], row['ref'])
        if key not in totals:
            totals[key] = {k: row[k] for k in ('pid', 'kind', 'project', 'pr', 'ref')}
            totals[key].update(rest=0, graphql=0)
        totals[key]['rest'] += row['rest']
        totals[key]['graphql'] += row['graphql']
    return dict(consumers=sorted(totals.values(), key=lambda r: r['rest'] + r['graphql'],
                                reverse=True)[:10],
                truncated=saved.get('truncated_until', 0) > now)

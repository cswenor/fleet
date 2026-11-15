"""Bounded request counts only; REST charges exclude confirmed 304 responses."""
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
    return _ACTIVE.set((c, kind, pr, ref))


def reset(token):
    _ACTIVE.reset(token)


def enabled():
    return _ACTIVE.get() is not None


def record(rest=0, graphql=0, *, not_modified=0, identity='stock'):
    """Record conservative REST charges, GraphQL attempts and REST 304 counts."""
    active = _ACTIVE.get()
    if active is None:
        return
    c, kind, pr, ref = active
    now = time.time()
    minute = int(now // 60) * 60
    try:
        _record(c, kind, pr, ref, now, minute, rest, graphql, not_modified, identity)
    except (OSError, ValueError, KeyError, TypeError):
        # A completed GitHub mutation must never look failed because accounting
        # is unavailable. Report the gap without exposing stored data.
        print('fleet: GitHub request attribution unavailable', file=sys.stderr)


def _record(c, kind, pr, ref, now, minute, rest, graphql, not_modified, identity):
    with state.lock(c, 'github-usage', wait_seconds=5):
        saved = state.read(c, 'github-usage', {})
        rows = [r for r in saved.get('rows', []) if r['at'] >= now - 3600]
        row = next((r for r in rows if r['process'] == _PROCESS and r['at'] == minute
                    and (r['kind'], r['pr'], r['ref'], r.get('identity', 'unknown')) ==
                    (kind, pr, ref, identity)), None)
        if row is None:
            row = dict(at=minute, process=_PROCESS, pid=os.getpid(), kind=kind, identity=identity,
                       project=c['repo'], pr=pr, ref=ref, rest=0, graphql=0, not_modified=0)
            rows.append(row)
        row['rest'] += rest
        row['graphql'] += graphql
        row['not_modified'] = row.get('not_modified', 0) + not_modified
        truncated_until = saved.get('truncated_until', 0)
        if len(rows) > MAX_ROWS:
            rows.sort(key=lambda r: r['at'])
            truncated_until = now + 3600
        state.write(c, 'github-usage', dict(rows=rows[-MAX_ROWS:],
                    truncated_until=truncated_until))


def trace(stderr, *, identity='stock'):
    """Count gh attempts, excluding confirmed REST 304s from charged requests.

    GH_DEBUG=api is captured in memory, never printed or persisted. Discard the
    complete trace before existing bounded error diagnostics can see it.
    """
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors='replace')
    rest = graphql = not_modified = outstanding = 0
    pending = None
    for line in (stderr or '').splitlines():
        request = re.match(r'^> [A-Z]+ (\S+) HTTP/\S+', line)
        if request:
            path = request[1].split('?', 1)[0].rstrip('/')
            is_graphql = path.endswith('/graphql')
            graphql += is_graphql
            rest += not is_graphql
            # gh can interleave concurrent traces without response IDs. Only
            # discount a 304 when exactly one unambiguous request is pending.
            pending = is_graphql if outstanding == 0 else None
            outstanding += 1
            continue
        response = re.match(r'^< HTTP/\S+ (\d{3})(?:\s|$)', line)
        if response and int(response[1]) < 200:
            continue
        if response or line.startswith('< error:'):
            if outstanding == 1 and pending is False and response and response[1] == '304':
                rest -= 1
                not_modified += 1
            outstanding = max(0, outstanding - 1)
            if not outstanding:
                pending = None
    record(rest, graphql, not_modified=not_modified, identity=identity)


def consumers(c):
    saved = state.read(c, 'github-usage', {})
    now = time.time()
    totals = {}
    for row in saved.get('rows', []):
        if row['at'] < now - 3600:
            continue
        key = (row['process'], row['kind'], row['pr'], row['ref'], row.get('identity', 'unknown'))
        if key not in totals:
            totals[key] = {k: row[k] for k in ('pid', 'kind', 'project', 'pr', 'ref')}
            totals[key]['identity'] = row.get('identity', 'unknown')
            totals[key].update(rest=0, graphql=0, not_modified=0)
        totals[key]['rest'] += row['rest']
        totals[key]['graphql'] += row['graphql']
        totals[key]['not_modified'] += row.get('not_modified', 0)
    return dict(consumers=sorted(totals.values(), key=lambda r: r['rest'] + r['graphql'],
                                reverse=True)[:10],
                truncated=saved.get('truncated_until', 0) > now)

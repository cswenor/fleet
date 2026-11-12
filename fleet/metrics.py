"""Read-only local session usage measurement: python -m fleet.metrics --help."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import sys


class MeasurementError(ValueError):
    """A source cannot be measured without silently losing coverage."""


def utc(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise MeasurementError('timestamps require explicit UTC Z')
    try:
        result = dt.datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        raise MeasurementError('invalid timestamp') from None
    return result


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise MeasurementError('invalid numeric usage')
    return value


def discover(roots, index=None):
    files = set()
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            raise MeasurementError('session root missing')
        for pattern in ('*.jsonl', '*.jsonl.gz'):
            files.update(p.resolve() for p in root.rglob(pattern) if p.is_file())
    if index:
        uri = Path(index).expanduser().resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as db:
            for (filename,) in db.execute('SELECT DISTINCT session_file FROM messages'):
                path = Path(filename)
                if not path.is_file():
                    raise MeasurementError('indexed session file missing')
                files.add(path.resolve())
    return sorted(files)


def attribution(cwd, projects):
    matches = [(len(str(prefix)), project) for project, prefix in projects
               if cwd == prefix or prefix in cwd.parents]
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    names = {name for length, name in matches if length == longest}
    if len(names) != 1:
        raise MeasurementError('ambiguous project attribution')
    return names.pop()


def tally(files, projects, start, end):
    seen, inherited, sessions, groups = {}, {}, {}, {}
    counts = dict(files=len(files), sessions=0, duplicate_events=0,
                  unattributed_calls=0, missing_usage=0, non_session_files=0)
    versions = set()
    for path in files:
        opener = gzip.open if path.name.endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as stream:
            try:
                header = json.loads(next(stream))
                while isinstance(header, dict) and header.get('type') == 'title':
                    header = json.loads(next(stream))
            except (ValueError, StopIteration):
                counts['non_session_files'] += 1
                continue
            if not isinstance(header, dict) or header.get('type') != 'session':
                counts['non_session_files'] += 1
                continue
            if not header.get('id') or not header.get('cwd'):
                raise MeasurementError('invalid session header')
            sid, cwd = header['id'], Path(header['cwd'])
            project = attribution(cwd, projects)
            if sid in sessions and sessions[sid] != project:
                raise MeasurementError('session copy attribution conflict')
            sessions[sid] = project
            for line in stream:
                event = json.loads(line)
                kind = event.get('type')
                if kind == 'message':
                    message = event.get('message', {})
                    if message.get('role') != 'assistant':
                        continue
                elif kind == 'model_usage':
                    message = event
                else:
                    continue
                stamp = utc(event.get('timestamp'))
                if not start <= stamp < end:
                    continue
                eid = event.get('id')
                if not eid:
                    raise MeasurementError('usage event lacks identity')
                usage = message.get('usage')
                # Hash only measurement fields; prompts/content are never retained.
                fields = [event['timestamp'], kind, message.get('provider'),
                          message.get('model'), message.get('stopReason'), usage]
                digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).digest()
                key = (sid, eid)
                if key in seen:
                    if seen[key] != digest:
                        raise MeasurementError('conflicting duplicate usage event')
                    counts['duplicate_events'] += 1
                    continue
                seen[key] = digest
                fingerprint = (eid, digest)
                if fingerprint in inherited:
                    # Forks get a new session id but retain the original usage entries.
                    if inherited[fingerprint] != project:
                        raise MeasurementError('fork attribution conflict')
                    counts['duplicate_events'] += 1
                    continue
                inherited[fingerprint] = project
                if not project:
                    counts['unattributed_calls'] += 1
                    continue
                if not isinstance(usage, dict):
                    counts['missing_usage'] += 1
                    continue
                provider, model = message.get('provider'), message.get('model')
                if not isinstance(provider, str) or not isinstance(model, str):
                    raise MeasurementError('usage lacks provider/model')
                group = groups.setdefault((project, provider, model, kind), dict(
                    calls=0, errors=0, input=0, output=0, cache_read=0,
                    cache_write=0, recorded_list_usd=0, unpriced_calls=0))
                group['calls'] += 1
                group['errors'] += message.get('stopReason') == 'error'
                for source, target in [('input', 'input'), ('output', 'output'),
                                       ('cacheRead', 'cache_read'), ('cacheWrite', 'cache_write')]:
                    if source not in usage:
                        raise MeasurementError('usage token field missing')
                    group[target] += number(usage[source])
                cost = usage.get('cost')
                if not isinstance(cost, dict) or cost.get('total') is None or usage.get('unpriced') or cost.get('unpriced'):
                    group['unpriced_calls'] += 1
                else:
                    group['recorded_list_usd'] += number(cost['total'])
                version = event.get('runtime', {}).get('version')
                if isinstance(version, str):
                    versions.add(version)
    counts['sessions'] = len(sessions)
    rows = [dict(project=p, provider=v, model=m, event_type=k, **totals)
            for (p, v, m, k), totals in sorted(groups.items())]
    return dict(coverage=counts, runtime_versions=sorted(versions), usage=rows)


def merged_prs(repo, start, end):
    query = '''query($owner:String!,$name:String!,$cursor:String) {
      repository(owner:$owner,name:$name) {
        pullRequests(first:100,after:$cursor,states:MERGED,
                     orderBy:{field:UPDATED_AT,direction:DESC}) {
          nodes { number mergedAt updatedAt }
          pageInfo { hasNextPage endCursor }
        }
      }
    }'''
    owner, name = repo.split('/')
    cursor, numbers = None, set()
    while True:
        args = ['gh', 'api', 'graphql', '-f', 'query=' + query,
                '-f', 'owner=' + owner, '-f', 'name=' + name]
        if cursor:
            args += ['-f', 'cursor=' + cursor]
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise MeasurementError('GitHub query failed; no partial result')
        payload = json.loads(result.stdout)
        if payload.get('errors'):
            raise MeasurementError('GitHub returned incomplete data')
        page = payload['data']['repository']['pullRequests']
        nodes = page['nodes']
        numbers.update(n['number'] for n in nodes if start <= utc(n['mergedAt']) < end)
        if not page['pageInfo']['hasNextPage'] or any(utc(n['updatedAt']) < start for n in nodes):
            return len(numbers)
        following = page['pageInfo']['endCursor']
        if not following or following == cursor:
            raise MeasurementError('GitHub pagination incomplete')
        cursor = following


def pairs(values):
    result = []
    for value in values:
        name, separator, target = value.partition('=')
        if not name or not separator or not target:
            raise MeasurementError('expected NAME=VALUE')
        result.append((name, target))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', default=[], help='session tree; repeat for every default/custom root')
    parser.add_argument('--index', help='optional read-only stock stats.db: include every referenced session file')
    parser.add_argument('--project', action='append', default=[], help='NAME=absolute cwd prefix; repeat for lanes/aliases')
    parser.add_argument('--repo', action='append', default=[], help='NAME=owner/repository; query exact merged PR count')
    parser.add_argument('--complete-project', action='append', default=[],
                        help='project with independently verified source coverage; enables per-PR rates')
    parser.add_argument('--manifest', help='private JSON roots/projects/repos/index inventory; no contents printed')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args(argv)
    try:
        if args.manifest:
            inventory = json.loads(Path(args.manifest).read_text())
            args.root += inventory['roots']
            args.project += [name + '=' + prefix for name, prefixes in inventory['projects'].items()
                             for prefix in prefixes]
            args.repo += [name + '=' + repo for name, repo in inventory.get('repos', {}).items()]
            args.index = args.index or inventory.get('index')
        if not args.root or not args.project:
            raise MeasurementError('session roots and project bindings required')
        start, end = utc(args.start), utc(args.end)
        if not start < end <= dt.datetime.now(dt.timezone.utc):
            raise MeasurementError('require completed increasing interval')
        projects = [(name, Path(prefix)) for name, prefix in pairs(args.project)]
        if any(not prefix.is_absolute() for _, prefix in projects):
            raise MeasurementError('project prefixes must be absolute')
        report = tally(discover(args.root, args.index), projects, start, end)
        merges = {name: merged_prs(repo, start, end) for name, repo in pairs(args.repo)}
        report.update(start=args.start, end_exclusive=args.end,
                      collected_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                      status='LOCAL OBSERVED USAGE; completeness requires external inventory',
                      merged_prs=merges)
        for row in report['usage']:
            denominator = (merges.get(row['project'])
                           if row['project'] in args.complete_project and not report['coverage']['missing_usage']
                           else None)
            row['calls_per_pr'] = row['calls'] / denominator if denominator else None
            row['cache_read_per_pr'] = row['cache_read'] / denominator if denominator else None
            row['list_usd_per_pr'] = (row['recorded_list_usd'] / denominator
                                      if denominator and not row['unpriced_calls'] else None)
        print(json.dumps(report, indent=2))
        return 0
    except MeasurementError as error:
        print('Measurement refused: ' + str(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError,
            AttributeError, EOFError, OverflowError, StopIteration, subprocess.TimeoutExpired):
        # No exception text: paths, malformed JSON and subprocess output may contain secrets.
        print('Measurement refused: invalid/incomplete source, arguments or GitHub response; inspect privately.', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())

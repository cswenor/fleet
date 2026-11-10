"""GitHub reads, pagination, and semantic event identities."""
from datetime import datetime, timezone, timedelta
import json
from urllib.parse import urlsplit
from .commands import CommandError, gh
from . import groups

_workflow_check_cache = {}
_pr_group_cache = {}


def _permission_unknown(error, unavailable):
    if (getattr(error, 'status', None) in (403, 404) or
            'Resource not accessible by integration' in str(error)):
        unavailable.append(str(error))
        return True
    return False


def _queued(c, number):
    owner, name = c['repo'].split('/')
    query = 'query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){mergeQueueEntry{id}}}}'
    data = gh(c, 'api', 'graphql', '-f', 'query=' + query,
              '-f', 'owner=' + owner, '-f', 'name=' + name, '-F', f'number={number}')
    if data.get('errors'):
        raise ValueError('GitHub PR queue query failed')
    return data['data']['repository']['pullRequest']['mergeQueueEntry'] is not None


def _recent(timestamp, cutoff):
    return not timestamp or datetime.fromisoformat(timestamp.replace('Z', '+00:00')) >= cutoff


def pages(c, endpoint):
    # --slurp preserves page boundaries for array endpoints.
    data = gh(c, 'api', '--paginate', '--slurp', endpoint)
    return [row for page in data for row in page]


def _workflow_checks(c, sha, checks, unavailable=None):
    if not any(r.get('conclusion') == 'cancelled' or
               (r.get('status') == 'completed' and
                r.get('conclusion') not in (None, 'success')) for r in checks):
        return checks
    signature = json.dumps(sorted(
        ({k: r.get(k) for k in ('id', 'check_suite', 'workflow_run_id',
                                'details_url', 'status', 'conclusion')}
         for r in checks), key=lambda r: r['id']), sort_keys=True)
    key = (c['repo'], sha)
    cached = _workflow_check_cache.get(key)
    if cached and cached[0] == signature:
        removed = cached[1]
        return [r for r in checks if r['id'] not in removed]
    retained, complete = _uncached_workflow_checks(c, sha, checks, unavailable)
    removed = {r['id'] for r in checks} - {r['id'] for r in retained}
    # One signature per head, bounded across long-running supervisor lifetimes.
    _workflow_check_cache.pop(key, None)
    if complete:
        if len(_workflow_check_cache) >= 512:
            _workflow_check_cache.pop(next(iter(_workflow_check_cache)))
        _workflow_check_cache[key] = (signature, removed)
    return retained


def _uncached_workflow_checks(c, sha, checks, unavailable=None):
    runs = []
    complete = False
    # Bounded enrichment: missing history or unavailable Actions permissions must
    # retain checks, never turn a failed check into an apparently healthy PR.
    for page in range(1, 4):
        try:
            data = gh(c, 'api', f'repos/{c["repo"]}/actions/runs?head_sha={sha}&per_page=100&page={page}')
        except CommandError as error:
            if unavailable is not None:
                _permission_unknown(error, unavailable)
            return checks, False
        rows = data.get('workflow_runs', [])
        runs.extend(r for r in rows if r.get('head_sha') == sha)
        if len(rows) < 100:
            complete = True
            break

    def newer(candidate, old):
        if candidate.get('id') == old.get('id'):
            return False
        for field in ('run_number', 'created_at', 'id'):
            if candidate.get(field) is not None and old.get(field) is not None:
                return candidate[field] > old[field]
        return False

    superseded = {
        r['id'] for r in runs
        if r.get('id') is not None and r.get('workflow_id') is not None
        and (r.get('conclusion') == 'cancelled'
             or (r.get('status') == 'completed'
                 and r.get('conclusion') not in (None, 'success')))
        and any(n.get('workflow_id') == r['workflow_id'] and newer(n, r) for n in runs)
    }
    by_suite = {}
    for run in runs:
        if run.get('check_suite_id') is not None and run.get('id') is not None:
            by_suite.setdefault(run['check_suite_id'], set()).add(run['id'])

    by_id = {r['id']: r for r in runs if r.get('id') is not None}

    def obsolete(check):
        suite = check.get('check_suite') or {}
        run_id = check.get('workflow_run_id')
        if run_id is None:
            url = urlsplit(check.get('details_url') or '')
            prefix = f'/{c["repo"]}/actions/runs/'
            if url.scheme == 'https' and url.netloc == 'github.com' and url.path.startswith(prefix):
                value = url.path[len(prefix):].split('/')[0]
                if value.isdigit():
                    run_id = int(value)
        if run_id is not None:
            run = by_id.get(run_id, {})
            if suite.get('id') is not None and suite['id'] != run.get('check_suite_id'):
                return False
            return run_id in superseded
        linked = by_suite.get(suite.get('id'), set())
        # Ambiguous suite linkage (for example a rerun sharing a suite) is not
        # proof that this particular check belongs to the obsolete attempt.
        return bool(linked) and linked <= superseded

    return [check for check in checks if not obsolete(check)], complete


def _queue_progress(c, entries, unavailable):
    """Enrich only queued synthetic heads; never confuse a lead PR with all members."""
    by_head = {}
    for entry in entries:
        head = (entry.get('headCommit') or {}).get('oid')
        if not head:
            entry['runs'] = []
            continue
        if head not in by_head:
            try:
                data = gh(c, 'api', '--paginate', '--slurp',
                          f'repos/{c["repo"]}/actions/runs?event=merge_group&head_sha={head}&per_page=100')
                runs = []
                for page in data:
                    for run in page['workflow_runs']:
                        if run['head_sha'] != head:
                            continue
                        job_pages = gh(c, 'api', '--paginate', '--slurp',
                                       f'repos/{c["repo"]}/actions/runs/{run["id"]}/jobs?filter=latest&per_page=100')
                        jobs = [job for page in job_pages for job in page['jobs']]
                        runs.append({
                            'id': run['id'], 'status': run['status'],
                            'conclusion': run['conclusion'], 'updated_at': run.get('updated_at'),
                            'jobs': sorted(
                                ({field: job.get(field) for field in
                                  ('id', 'status', 'conclusion', 'started_at', 'completed_at', 'steps')}
                                 for job in jobs), key=lambda job: job['id'])})
                by_head[head] = sorted(runs, key=lambda run: run['id'])
            except (CommandError, ValueError, KeyError) as error:
                unavailable.append(f'Merge queue progress unavailable: {error}')
                by_head[head] = None
        if by_head[head] is None:
            entry['progress_unknown'] = True
        else:
            entry['runs'] = by_head[head]


def collect(c, pr_number=None):
    repo = c['repo']
    unavailable = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    since = cutoff.isoformat().replace('+00:00', 'Z')
    # Stable URLs within each hour let the REST session revalidate with ETags.
    # The broader fetched window is trimmed to the exact cutoff below.
    query_since = cutoff.replace(minute=0, second=0, microsecond=0).isoformat().replace('+00:00', 'Z')
    if pr_number is not None:
        p = gh(c, 'api', f'repos/{repo}/pulls/{pr_number}')
        by_number = {pr_number: p}
    else:
        prs = []
        page = 1
        while True:
            rows = gh(c, 'api', f'repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page={page}')
            prs.extend(p for p in rows if p['state'] == 'open' or p['updated_at'] >= since)
            if len(rows) < 100 or all(p['updated_at'] < since for p in rows):
                break
            page += 1
        # Old open PRs can be below the updated cutoff.
        open_prs = pages(c, f'repos/{repo}/pulls?state=open&per_page=100')
        by_number = {p['number']: p for p in prs}
        by_number.update({p['number']: p for p in open_prs})
    result = []
    check_signatures = {}
    for p in by_number.values():
        number = p['number']
        checks = []
        comments = []
        reviews = []
        if p['state'] == 'open':
            if 'mergeable_state' not in p:
                p = gh(c, 'api', f'repos/{repo}/pulls/{number}')
            sha = p['head']['sha']
            check_pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{repo}/commits/{sha}/check-runs?per_page=100')
            raw_checks = [r for page in check_pages for r in page['check_runs']]
            checks = [{'id': r['id'], 'name': r['name'], 'status': r['status'],
                       'conclusion': r['conclusion'], 'completed_at': r.get('completed_at')}
                      for r in _workflow_checks(c, sha, raw_checks, unavailable)]
            statuses = pages(c, f'repos/{repo}/commits/{sha}/statuses?per_page=100')
            latest = {}
            for s in statuses:
                latest.setdefault(s['context'], s)
            checks += [{'id': s['id'], 'name': s['context'], 'status': s['state'],
                        'conclusion': s['state'], 'completed_at': s.get('updated_at')}
                       for s in latest.values()]
            check_signatures[number] = {'raw': raw_checks, 'statuses': latest}
            comments = pages(c, f'repos/{repo}/pulls/{number}/comments?per_page=100')
            comments += pages(c, f'repos/{repo}/issues/{number}/comments?per_page=100')
            reviews = pages(c, f'repos/{repo}/pulls/{number}/reviews?per_page=100')
        merged = p.get('merged_at')
        result.append({'number': number, 'title': p['title'], 'url': p['html_url'], 'branch': p['head']['ref'], 'head': p['head']['sha'],
                       'state': 'merged' if merged else p['state'], 'created_at': p['created_at'],
                       'draft': p.get('draft', False), 'labels': [label['name'] for label in p.get('labels', [])],
                       'updated_at': p['updated_at'], 'merge_state': (p.get('mergeable_state') or 'unknown').upper(),
                       'closed_at': p['closed_at'], 'merged_at': merged,
                       'time_to_merge_seconds': int((datetime.fromisoformat(merged.replace('Z', '+00:00')) - datetime.fromisoformat(p['created_at'].replace('Z', '+00:00'))).total_seconds()) if merged else None,
                       'checks': sorted(checks, key=lambda x: (x['name'], x['id'])),
                       'comments': sorted([{'id': x['id'], 'updated_at': x.get('updated_at', x.get('submitted_at')), 'url': x.get('html_url')} for x in comments], key=lambda x: (x['id'], x['url'] or '')),
                       'reviews': [{'id': x['id'], 'state': x['state'], 'submitted_at': x.get('submitted_at')} for x in reviews]})
    cached = None
    signature = None
    merge_group_unknown = False
    if pr_number is not None:
        signature = json.dumps({'prs': [
            {k: p[k] for k in ('head', 'state', 'updated_at', 'merge_state', 'checks')}
            for p in result], 'checks': check_signatures}, sort_keys=True)
        cached = _pr_group_cache.get((repo, pr_number))
    # A cold read is necessary even for a healthy, nonqueued PR: a previous
    # same-head group may have failed after leaving the queue. Once discovered,
    # own-group evidence keeps history live for failure and supersession changes.
    try:
        refresh = (cached is None or cached[0] != signature or
                   any(pr_number in r.get('prs', []) or
                       (not r.get('membership_complete') and
                        (r.get('status') != 'completed' or
                         r.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required')))
                       for r in cached[1]) or
                   _queued(c, pr_number))
    except CommandError as error:
        if not _permission_unknown(error, unavailable):
            raise
        refresh = True
    if refresh:
        try:
            run_pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{repo}/actions/runs?event=merge_group&created=%3E{query_since}&per_page=100')
            runs = groups.resolve(c, [{'id': r['id'], 'branch': r['head_branch'], 'head_sha': r['head_sha'],
                                     'associated_prs': [p['number'] for p in r.get('pull_requests', [])],
                                     'created_at': r.get('created_at'),
                                     'status': r['status'], 'conclusion': r['conclusion'], 'url': r['html_url']}
                                    for page in run_pages for r in page['workflow_runs']
                                    if _recent(r.get('created_at'), cutoff)], pr_number=pr_number)
        except CommandError as error:
            if not _permission_unknown(error, unavailable):
                raise
            runs = cached[1] if cached else []
            merge_group_unknown = True
    else:
        runs = [r for r in cached[1] if _recent(r.get('created_at'), cutoff)]
    if pr_number is not None and not unavailable:
        key = (repo, pr_number)
        _pr_group_cache.pop(key, None)
        if len(_pr_group_cache) >= 512:
            _pr_group_cache.pop(next(iter(_pr_group_cache)))
        _pr_group_cache[key] = (signature, runs)
    if pr_number is not None:
        return {'prs': result, 'merge_group_runs': runs, 'unavailable': unavailable,
                'merge_group_unknown': merge_group_unknown}
    owner, name = repo.split('/')
    default_branch = gh(c, 'api', f'repos/{repo}')['default_branch']
    query = 'query($owner:String!,$name:String!,$branch:String!){repository(owner:$owner,name:$name){mergeQueue(branch:$branch){entries(first:100){nodes{position state headCommit{oid} pullRequest{number}} pageInfo{hasNextPage}}}}}'
    queue_unknown = False
    try:
        queue = gh(c, 'api', 'graphql', '-f', 'query=' + query, '-f', 'owner=' + owner, '-f', 'name=' + name, '-f', 'branch=' + default_branch)
        if queue.get('errors'):
            raise ValueError('GitHub queue query failed')
        repository = queue['data']['repository']
        mq = repository.get('mergeQueue')
        if mq and mq['entries']['pageInfo']['hasNextPage']:
            raise ValueError('merge queue exceeds 100 entries')
        entries = sorted(mq['entries']['nodes'], key=lambda entry: entry['position']) if mq else []
    except (CommandError, ValueError, KeyError) as error:
        unavailable.append(f'Merge queue unavailable: {error}')
        entries = []
        queue_unknown = True
    _queue_progress(c, entries, unavailable)
    commits = pages(c, f'repos/{repo}/commits?sha={default_branch}&since={query_since}&per_page=100')
    commits = [x for x in commits if _recent(x['commit']['committer']['date'], cutoff)]
    return {'prs': sorted(result, key=lambda x: x['number']), 'merge_queue': entries,
            'merge_queue_unknown': queue_unknown,
            'merge_group_runs': runs, 'merge_group_unknown': merge_group_unknown,
            'default_branch': default_branch, 'unavailable': unavailable,
            'commits': [{'sha': x['sha'], 'url': x['html_url'], 'date': x['commit']['committer']['date']} for x in commits], 'since': since}


_FAILED_CONCLUSIONS = frozenset((
    'failure', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale'))


def _failing_checks(p):
    return sorted({x['name'] for x in p['checks'] if x.get('conclusion') in _FAILED_CONCLUSIONS})


def initial_event(p):
    reasons = []
    if p['state'] in ('merged', 'closed'):
        reasons.append(p['state'])
    else:
        if p.get('merge_state') == 'DIRTY':
            reasons.append('DIRTY')
        failed = _failing_checks(p)
        if failed:
            reasons.append('failing checks: ' + ', '.join(failed))
    if not reasons:
        return None
    detail = '; '.join(reasons)
    failures = sorted(
        ((x.get('id'), x['name'], x.get('conclusion'), x.get('completed_at'))
         for x in p.get('checks', []) if x.get('conclusion') in _FAILED_CONCLUSIONS),
        key=lambda x: (x[1], str(x[0]), x[2] or '', x[3] or ''))
    evidence = json.dumps(failures, separators=(',', ':'))
    return {'key': f'pr:{p["number"]}:initial:{p.get("updated_at", p["head"])}:{p["head"]}:{detail}:{evidence}',
            'subject': f'PR #{p["number"]} initial state: {detail}', 'pr': p['number'],
            'branch': p['branch'], 'body': p['url'] + '\n' + detail}


def events(previous, current):
    old = {p['number']: p for p in previous.get('prs', [])}
    events = []
    for p in current['prs']:
        before = old.get(p['number'])
        if before is None:
            initial = initial_event(p)
            if initial:
                events.append(initial)
            elif previous:
                events.append({'key': f'pr:{p["number"]}:opened:{p["head"]}', 'subject': f'PR #{p["number"]} {p["state"]}', 'pr': p['number'], 'branch': p['branch'], 'body': p['url']})
            continue
        fields = ('state', 'head', 'merge_state', 'checks', 'comments', 'reviews') if p['state'] == 'open' else ('state',)
        for field in fields:
            if field not in before and field == 'merge_state' and p.get(field) != 'DIRTY':
                continue
            if before.get(field) != p.get(field):
                value = json.dumps(p.get(field), sort_keys=True)
                occurrence = p.get('updated_at', p['head'])
                detail = f'{field} changed: {p["state"]}'
                if field == 'checks':
                    failed = _failing_checks(p)
                    if failed:
                        detail += '; failing checks: ' + ', '.join(failed)
                events.append({'key': f'pr:{p["number"]}:{field}:{occurrence}:{value}', 'subject': f'PR #{p["number"]} {detail}', 'pr': p['number'], 'branch': p['branch'], 'body': p['url'] + '\n' + field + ': ' + value})
    old_runs = {(r['id'], r['conclusion']): set(r.get('prs', []) or [None]) for r in previous.get('merge_group_runs', [])}
    for r in current['merge_group_runs']:
        if r['conclusion'] in ('failure', 'cancelled', 'timed_out', 'action_required'):
            already = old_runs.get((r['id'], r['conclusion']), set())
            for number in r.get('prs', []) or [None]:
                if number in already:
                    continue
                p = next((p for p in current['prs'] if p['number'] == number), None)
                detail = r['url'] + ('\nGroup membership unresolved; notify coordinator for reconciliation.' if not r.get('membership_complete') else '')
                events.append({'key': f'group:{r["id"]}:{r["conclusion"]}:{number}', 'subject': f'Merge group {r["id"]} {r["conclusion"]}', 'pr': number, 'branch': p['branch'] if p else None, 'body': detail})
    return events

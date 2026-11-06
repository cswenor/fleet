"""GitHub reads, pagination, and semantic event identities."""
from datetime import datetime, timezone, timedelta
import json
from .commands import gh
from . import groups


def pages(c, endpoint):
    # --slurp preserves page boundaries for array endpoints.
    data = gh(c, 'api', '--paginate', '--slurp', endpoint)
    return [row for page in data for row in page]


def collect(c, pr_number=None):
    repo = c['repo']
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
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
            checks = [{'id': r['id'], 'name': r['name'], 'status': r['status'], 'conclusion': r['conclusion']} for page in check_pages for r in page['check_runs']]
            statuses = pages(c, f'repos/{repo}/commits/{sha}/statuses?per_page=100')
            latest = {}
            for s in statuses:
                latest.setdefault(s['context'], s)
            checks += [{'id': s['id'], 'name': s['context'], 'status': s['state'], 'conclusion': s['state']} for s in latest.values()]
            comments = pages(c, f'repos/{repo}/pulls/{number}/comments?per_page=100')
            comments += pages(c, f'repos/{repo}/issues/{number}/comments?per_page=100')
            reviews = pages(c, f'repos/{repo}/pulls/{number}/reviews?per_page=100')
        merged = p.get('merged_at')
        result.append({'number': number, 'url': p['html_url'], 'branch': p['head']['ref'], 'head': p['head']['sha'],
                       'state': 'merged' if merged else p['state'], 'created_at': p['created_at'],
                       'updated_at': p['updated_at'], 'merge_state': (p.get('mergeable_state') or 'unknown').upper(),
                       'closed_at': p['closed_at'], 'merged_at': merged,
                       'time_to_merge_seconds': int((datetime.fromisoformat(merged.replace('Z', '+00:00')) - datetime.fromisoformat(p['created_at'].replace('Z', '+00:00'))).total_seconds()) if merged else None,
                       'checks': sorted(checks, key=lambda x: (x['name'], x['id'])),
                       'comments': sorted([{'id': x['id'], 'updated_at': x.get('updated_at', x.get('submitted_at')), 'url': x.get('html_url')} for x in comments], key=lambda x: (x['id'], x['url'] or '')),
                       'reviews': [{'id': x['id'], 'state': x['state'], 'submitted_at': x.get('submitted_at')} for x in reviews]})
    run_pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{repo}/actions/runs?event=merge_group&created=%3E{since}&per_page=100')
    runs = groups.resolve(c, [{'id': r['id'], 'branch': r['head_branch'], 'head_sha': r['head_sha'],
                             'associated_prs': [p['number'] for p in r.get('pull_requests', [])],
                             'status': r['status'], 'conclusion': r['conclusion'], 'url': r['html_url']}
                            for page in run_pages for r in page['workflow_runs']])
    if pr_number is not None:
        return {'prs': result, 'merge_group_runs': runs}
    owner, name = repo.split('/')
    default_branch = gh(c, 'api', f'repos/{repo}')['default_branch']
    query = 'query($owner:String!,$name:String!,$branch:String!){repository(owner:$owner,name:$name){mergeQueue(branch:$branch){entries(first:100){nodes{state pullRequest{number}} pageInfo{hasNextPage}}}}}'
    queue = gh(c, 'api', 'graphql', '-f', 'query=' + query, '-f', 'owner=' + owner, '-f', 'name=' + name, '-f', 'branch=' + default_branch)
    if queue.get('errors'):
        raise ValueError('GitHub queue query failed')
    repository = queue['data']['repository']
    mq = repository.get('mergeQueue')
    if mq and mq['entries']['pageInfo']['hasNextPage']:
        raise ValueError('merge queue exceeds 100 entries')
    commits = pages(c, f'repos/{repo}/commits?sha={default_branch}&since={since}&per_page=100')
    return {'prs': sorted(result, key=lambda x: x['number']), 'merge_queue': mq['entries']['nodes'] if mq else [],
            'merge_group_runs': runs, 'default_branch': default_branch,
            'commits': [{'sha': x['sha'], 'url': x['html_url'], 'date': x['commit']['committer']['date']} for x in commits], 'since': since}


def initial_event(p):
    reasons = []
    if p['state'] in ('merged', 'closed'):
        reasons.append(p['state'])
    else:
        if p.get('merge_state') == 'DIRTY':
            reasons.append('DIRTY')
        failed = [x['name'] for x in p['checks'] if x.get('conclusion') in
                  ('failure', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale')]
        if failed:
            reasons.append('failing checks: ' + ', '.join(failed))
    if not reasons:
        return None
    detail = '; '.join(reasons)
    return {'key': f'pr:{p["number"]}:initial:{p.get("updated_at", p["head"])}:{detail}',
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
                events.append({'key': f'pr:{p["number"]}:{field}:{occurrence}:{value}', 'subject': f'PR #{p["number"]} {field} changed: {p["state"]}', 'pr': p['number'], 'branch': p['branch'], 'body': p['url'] + '\n' + field + ': ' + value})
    old_runs = {(r['id'], r['conclusion']): set(r.get('prs', []) or [None]) for r in previous.get('merge_group_runs', [])}
    for r in current['merge_group_runs']:
        if previous and r['conclusion'] in ('failure', 'cancelled', 'timed_out', 'action_required'):
            already = old_runs.get((r['id'], r['conclusion']), set())
            for number in r.get('prs', []) or [None]:
                if number in already:
                    continue
                p = next((p for p in current['prs'] if p['number'] == number), None)
                detail = r['url'] + ('\nGroup membership unresolved; notify coordinator for reconciliation.' if not r.get('membership_complete') else '')
                events.append({'key': f'group:{r["id"]}:{r["conclusion"]}:{number}', 'subject': f'Merge group {r["id"]} {r["conclusion"]}', 'pr': number, 'branch': p['branch'] if p else None, 'body': detail})
    return events

"""GitHub reads, pagination, and semantic event identities."""
from datetime import datetime, timezone, timedelta
import json
import re
from .commands import gh


def pages(c, endpoint):
    # --slurp preserves page boundaries for array endpoints.
    data = gh(c, 'api', '--paginate', '--slurp', endpoint)
    return [row for page in data for row in page]


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def collect(c):
    repo = c['repo']
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    # Updated ordering includes every open PR; closed PRs older than the window stop pagination relevance.
    prs = []
    page = 1
    while True:
        rows = gh(c, 'api', f'repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page={page}')
        for p in rows:
            if p['state'] == 'open' or p['updated_at'] >= since:
                prs.append(p)
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
                       'closed_at': p['closed_at'], 'merged_at': merged,
                       'time_to_merge_seconds': int((datetime.fromisoformat(merged.replace('Z', '+00:00')) - datetime.fromisoformat(p['created_at'].replace('Z', '+00:00'))).total_seconds()) if merged else None,
                       'checks': sorted(checks, key=lambda x: (x['name'], x['id'])),
                       'comments': sorted([{'id': x['id'], 'updated_at': x.get('updated_at', x.get('submitted_at')), 'url': x.get('html_url')} for x in comments], key=lambda x: (x['id'], x['url'] or '')),
                       'reviews': [{'id': x['id'], 'state': x['state'], 'submitted_at': x.get('submitted_at')} for x in reviews]})
    run_pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{repo}/actions/runs?event=merge_group&created=%3E{since}&per_page=100')
    runs = [{'id': r['id'], 'branch': r['head_branch'], 'status': r['status'], 'conclusion': r['conclusion'], 'url': r['html_url']} for page in run_pages for r in page['workflow_runs']]
    owner, name = repo.split('/')
    query = 'query($owner:String!,$name:String!){repository(owner:$owner,name:$name){defaultBranchRef{name} mergeQueue(branch:"main"){entries(first:100){nodes{state pullRequest{number}} pageInfo{hasNextPage}}}}}'
    queue = gh(c, 'api', 'graphql', '-f', 'query=' + query, '-f', 'owner=' + owner, '-f', 'name=' + name)
    if queue.get('errors'):
        raise ValueError('GitHub queue query failed')
    repository = queue['data']['repository']
    mq = repository.get('mergeQueue')
    if mq and mq['entries']['pageInfo']['hasNextPage']:
        raise ValueError('merge queue exceeds 100 entries')
    default_branch = repository['defaultBranchRef']['name']
    commits = pages(c, f'repos/{repo}/commits?sha={default_branch}&since={since}&per_page=100')
    return {'prs': sorted(result, key=lambda x: x['number']), 'merge_queue': mq['entries']['nodes'] if mq else [],
            'merge_group_runs': runs, 'default_branch': default_branch,
            'commits': [{'sha': x['sha'], 'url': x['html_url'], 'date': x['commit']['committer']['date']} for x in commits], 'since': since}


def pr_signature(pr):
    return {k: pr[k] for k in ('state', 'head', 'checks', 'comments', 'reviews')}


def events(previous, current):
    old = {p['number']: p for p in previous.get('prs', [])}
    events = []
    for p in current['prs']:
        before = old.get(p['number'])
        if before is None:
            if previous:
                events.append({'key': f'pr:{p["number"]}:opened:{p["head"]}', 'subject': f'PR #{p["number"]} {p["state"]}', 'pr': p['number'], 'branch': p['branch'], 'body': p['url']})
            continue
        for field in ('state', 'head', 'checks', 'comments', 'reviews'):
            if before[field] != p[field]:
                value = json.dumps(p[field], sort_keys=True)
                events.append({'key': f'pr:{p["number"]}:{field}:{value}', 'subject': f'PR #{p["number"]} {field} changed: {p["state"]}', 'pr': p['number'], 'branch': p['branch'], 'body': p['url'] + '\n' + field + ': ' + value})
    old_runs = {(r['id'], r['conclusion']) for r in previous.get('merge_group_runs', [])}
    for r in current['merge_group_runs']:
        if previous and r['conclusion'] in ('failure', 'cancelled', 'timed_out', 'action_required') and (r['id'], r['conclusion']) not in old_runs:
            matches = re.findall(r'(?:^|/)pr-(\d+)-', r['branch'] or '')
            for number in matches or [None]:
                p = next((p for p in current['prs'] if p['number'] == int(number)), None) if number else None
                events.append({'key': f'group:{r["id"]}:{r["conclusion"]}:{number}', 'subject': f'Merge group {r["id"]} {r["conclusion"]}', 'pr': int(number) if number else None, 'branch': p['branch'] if p else None, 'body': r['url']})
    return events

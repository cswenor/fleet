"""GitHub reads, pagination, and semantic event identities."""
from datetime import datetime, timezone, timedelta
import json
from urllib.parse import urlsplit
from .commands import CommandError, gh
from . import groups

_workflow_check_cache = {}
_pr_group_cache = {}


def repo_move_error(c, repository=None):
    """Diagnose redirects without treating unavailable identity as a move."""
    try:
        live = (repository if repository is not None else
                gh(c, 'api', f'repos/{c["repo"]}'))['full_name']
        if isinstance(live, str) and live.casefold() != c['repo'].casefold():
            return (f'repo moved/renamed: config {c["repo"]}, GitHub {live}; '
                    'follow the MAIN.md repo-move procedure')
    except (CommandError, ValueError, KeyError, TypeError):
        pass
    return None


def _permission_unknown(error, unavailable):
    if (getattr(error, 'status', None) in (403, 404) or
            'Resource not accessible by integration' in str(error)):
        unavailable.append(str(error))
        return True
    return False


def _queued(c, number, *, details=False):
    owner, name = c['repo'].split('/')
    query = 'query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){mergeQueueEntry{id}}}}'
    data = gh(c, 'api', 'graphql', '-f', 'query=' + query,
              '-f', 'owner=' + owner, '-f', 'name=' + name, '-F', f'number={number}')
    if data.get('errors'):
        raise ValueError('GitHub PR queue query failed')
    entry = data['data']['repository']['pullRequest']['mergeQueueEntry']
    return entry if details else entry is not None


def _review_snapshot(c, reviews, head, carrier=None):
    """Keep review evidence separate from current-head and producer identity."""
    configured = c.get('publication', {}).get('review_app')
    result = []
    for review in reviews:
        user = review.get('user') or {}
        app = review.get('performed_via_github_app')
        source = 'performed_via_github_app' if app else None
        # GitHub's REST review author is a canonical App profile for App bots,
        # even when performed_via_github_app is omitted. A human's login alone
        # is never App evidence; require the GitHub-owned App URL and Bot type.
        if not app and user.get('type') == 'Bot':
            try:
                profile = urlsplit(user.get('html_url') or '')
                parts = profile.path.split('/')
                if (profile.scheme == 'https' and profile.netloc.casefold() == 'github.com' and
                        not profile.query and not profile.fragment and
                        len(parts) == 3 and parts[1] == 'apps' and parts[2] and
                        user.get('login', '').casefold() == (parts[2] + '[bot]').casefold()):
                    app = {'slug': parts[2]}
                    source = 'github_bot_profile'
            except ValueError:
                pass
        slug = (app or {}).get('slug')
        current_head = (review['commit_id'] == head
                        if review.get('commit_id') is not None else None)
        match = (slug.casefold() == configured.casefold()
                 if slug and configured else None)
        carried = bool(carrier and review.get('commit_id') == carrier['reviewed_head']
                       and review['state'] == 'APPROVED' and current_head is False)
        result.append({
            'id': review['id'], 'state': review['state'],
            'submitted_at': review.get('submitted_at'),
            'commit_id': review.get('commit_id'), 'current_head': current_head,
            'head_display': ('current head' if current_head is True else
                             f"unverified carrier of reviewed head {carrier['reviewed_head']}"
                             if carried else 'stale head' if current_head is False else 'unknown head'),
            'carrier': carrier if carried else None,
            'user': {field: user.get(field) for field in ('id', 'login', 'type', 'html_url')},
            'performed_via_github_app': review.get('performed_via_github_app'),
            'app_id': (app or {}).get('id'), 'app_slug': slug,
            'app_identity_source': source, 'review_app_match': match,
            'classification': ('carrier_of_reviewed_head' if carried else
                               'stale' if current_head is False else
                               'unknown_head' if current_head is None else
                               'wrong_app' if match is False else
                               'current_review_app' if match is True else
                               'unknown_app')})
    return result


def _review_carrier(c, p, reviews, unavailable):
    """Recognize ancestry for display only; never claim a clean remerge or approval."""
    approved = {r.get('commit_id') for r in reviews
                if r['state'] == 'APPROVED' and r.get('commit_id')
                and r['commit_id'] != p['head']['sha']}
    base = (p.get('base') or {}).get('sha')
    if not approved or not base:
        return None
    try:
        commit = gh(c, 'api', f'repos/{c["repo"]}/git/commits/{p["head"]["sha"]}')
        parents = commit['parents']
        if len(parents) != 2 or parents[0]['sha'] not in approved:
            return None
        second = parents[1]['sha']
        if second != base:
            comparison = gh(c, 'api', f'repos/{c["repo"]}/compare/{second}...{base}?per_page=1')
            if (comparison.get('status') != 'ahead' or
                    (comparison.get('merge_base_commit') or {}).get('sha') != second):
                return None
        return {'reviewed_head': parents[0]['sha'], 'head': p['head']['sha'],
                'base_parent': second, 'base': base, 'cleanliness': 'unverified'}
    except (CommandError, ValueError, KeyError, TypeError) as error:
        unavailable.append(f'PR #{p["number"]} carrier evidence unavailable: {error}')
        return None


def _recent(timestamp, cutoff):
    return not timestamp or datetime.fromisoformat(timestamp.replace('Z', '+00:00')) >= cutoff


def pages(c, endpoint):
    # --slurp preserves page boundaries for array endpoints.
    data = gh(c, 'api', '--paginate', '--slurp', endpoint)
    return [row for page in data for row in page]


def _latest_checks(checks):
    """Supersede only attempts with a proven common check producer."""
    latest = {}
    unversioned = []
    for check in checks:
        if check.get('id') is None:
            unversioned.append(check)
            continue
        is_status = check.get('status') in ('pending', 'success', 'failure', 'error')
        app = check.get('app_id', (check.get('app') or {}).get('id'))
        suite = check.get('check_suite_id', (check.get('check_suite') or {}).get('id'))
        if not is_status and (app is None or suite is None):
            # Legacy snapshots and unlinked providers cannot prove supersession.
            unversioned.append(check)
            continue
        key = (check['name'], check.get('head_sha'), is_status,
               None if is_status else (app, suite))
        previous = latest.get(key)
        if previous is None or check['id'] > previous['id']:
            latest[key] = check
    return list(latest.values()) + unversioned


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
        queue_entry = None
        queue_unknown = False
        if p['state'] == 'open':
            if 'mergeable_state' not in p:
                p = gh(c, 'api', f'repos/{repo}/pulls/{number}')
            sha = p['head']['sha']
            check_pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{repo}/commits/{sha}/check-runs?per_page=100')
            raw_checks = [r for page in check_pages for r in page['check_runs']]
            checks = [{'id': r['id'], 'name': r['name'], 'status': r['status'],
                       'conclusion': r['conclusion'], 'completed_at': r.get('completed_at'),
                       'app_id': (r.get('app') or {}).get('id'),
                       'app_slug': (r.get('app') or {}).get('slug'),
                       'check_suite_id': (r.get('check_suite') or {}).get('id')}
                      for r in _latest_checks(_workflow_checks(c, sha, raw_checks, unavailable))]
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
            try:
                queue_entry = _queued(c, number, details=True)
            except (CommandError, ValueError, KeyError, TypeError) as error:
                unavailable.append(f'PR #{number} merge queue unavailable: {error}')
                queue_unknown = True
        merged = p.get('merged_at')
        result.append({'number': number, 'title': p['title'], 'url': p['html_url'], 'branch': p['head']['ref'], 'head': p['head']['sha'],
                       'state': 'merged' if merged else p['state'], 'created_at': p['created_at'],
                       'draft': p.get('draft', False), 'labels': [label['name'] for label in p.get('labels', [])],
                       'hold_label': c.get('publication', {}).get('hold_label', 'hold'),
                       'auto_merge': p.get('auto_merge'),
                       'auto_merge_unknown': 'auto_merge' not in p,
                       'merge_queue_entry': queue_entry, 'merge_queue_unknown': queue_unknown,
                       'updated_at': p['updated_at'], 'merge_state': (p.get('mergeable_state') or 'unknown').upper(),
                       'closed_at': p['closed_at'], 'merged_at': merged,
                       'time_to_merge_seconds': int((datetime.fromisoformat(merged.replace('Z', '+00:00')) - datetime.fromisoformat(p['created_at'].replace('Z', '+00:00'))).total_seconds()) if merged else None,
                       'checks': sorted(checks, key=lambda x: (x['name'], x['id'])),
                       'comments': sorted([{'id': x['id'], 'updated_at': x.get('updated_at', x.get('submitted_at')), 'url': x.get('html_url')} for x in comments], key=lambda x: (x['id'], x['url'] or '')),
                       'reviews': _review_snapshot(
                           c, reviews, p['head']['sha'], _review_carrier(c, p, reviews, unavailable))})
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
    refresh = (cached is None or cached[0] != signature or
               any(pr_number in r.get('prs', []) or
                   (not r.get('membership_complete') and
                    (r.get('status') != 'completed' or
                     r.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required')))
                   for r in cached[1]) or
               any(p.get('merge_queue_unknown') or p.get('merge_queue_entry') is not None
                   for p in result))
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
    repo_metadata = gh(c, 'api', f'repos/{repo}')
    default_branch = repo_metadata['default_branch']
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
            'repo_move_error': repo_move_error(c, repo_metadata),
            'commits': [{'sha': x['sha'], 'url': x['html_url'], 'date': x['commit']['committer']['date']} for x in commits], 'since': since}


_FAILED_CONCLUSIONS = frozenset((
    'failure', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale'))


def _failing_checks(p):
    return sorted({x['name'] for x in _latest_checks(p.get('checks', []))
                   if x.get('conclusion') in _FAILED_CONCLUSIONS})


def checks_conclusion(p):
    """Return the overall pending/success/failure state of effective PR checks."""
    checks = _latest_checks(p.get('checks', []))
    if any(x.get('conclusion') in _FAILED_CONCLUSIONS for x in checks):
        return 'failure'
    if not checks or any(
            x.get('status') not in ('completed', 'success') or
            x.get('conclusion') not in ('success', 'neutral', 'skipped')
            for x in checks):
        return 'pending'
    return 'success'


def _publication_state(p):
    hold_label = p.get('hold_label', 'hold').casefold()
    return {
        'hold': (any(label.casefold() == hold_label for label in p['labels'])
                 if 'labels' in p else None),
        'draft': p.get('draft'),
        'auto_merge': (p['auto_merge'] is not None
                       if 'auto_merge' in p and not p.get('auto_merge_unknown') else None),
        'merge_queue': (p['merge_queue_entry'] is not None
                        if 'merge_queue_entry' in p and not p.get('merge_queue_unknown') else None),
    }


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
        publication = _publication_state(p)
        reasons.extend(detail for field, detail in (
            ('hold', 'hold active'), ('draft', 'draft'))
            if publication[field] is True)
    if not reasons:
        return None
    detail = '; '.join(reasons)
    failures = sorted(
        ((x.get('id'), x['name'], x.get('conclusion'), x.get('completed_at'))
         for x in _latest_checks(p.get('checks', [])) if x.get('conclusion') in _FAILED_CONCLUSIONS),
        key=lambda x: (x[1], str(x[0]), x[2] or '', x[3] or ''))
    evidence = json.dumps(failures, separators=(',', ':'))
    return {'key': f'pr:{p["number"]}:initial:{p.get("updated_at", p["head"])}:{p["head"]}:{detail}:failing={evidence}',
            'subject': f'PR #{p["number"]} initial state: {detail}', 'pr': p['number'],
            'branch': p['branch'], 'body': p['url'] + '\n' + detail}


def events(previous, current, *, aggregate_checks=False):
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
        old_publication = _publication_state(before)
        publication = _publication_state(p)
        auto_merge_cleared = (old_publication['auto_merge'] is True and
                              publication['auto_merge'] is False)
        for field, descriptions in (
                ('hold', ('hold removed', 'hold added')),
                ('draft', ('draft ready', 'draft converted')),
                ('auto_merge', ('auto_merge disarmed', 'auto_merge armed')),
                ('merge_queue', ('merge_queue exited', 'merge_queue entered'))):
            value = publication[field]
            # GitHub consumes the auto-merge request on enqueue or merge.
            if p['state'] == 'merged' and field in ('auto_merge', 'merge_queue'):
                continue
            if field == 'auto_merge' and auto_merge_cleared and publication['merge_queue'] is True:
                continue
            if (value is not None and old_publication[field] is not None and
                    value != old_publication[field]):
                detail = descriptions[int(value)]
                if field == 'auto_merge' and auto_merge_cleared and publication['merge_queue'] is None:
                    detail = 'auto_merge cleared; queue state unknown'
                elif field == 'merge_queue' and value and auto_merge_cleared:
                    detail = 'merge_queue entered (auto-merge consumed)'
                occurrence = p.get('updated_at', p['head'])
                evidence_field = {'auto_merge': 'auto_merge',
                                  'merge_queue': 'merge_queue_entry'}.get(field)
                evidence = json.dumps(
                    (p if value else before).get(evidence_field)
                    if evidence_field else value, sort_keys=True)
                events.append({
                    'key': f'pr:{p["number"]}:{field}:{occurrence}:{value}:{evidence}',
                    'subject': f'PR #{p["number"]} {detail}', 'pr': p['number'],
                    'branch': p['branch'], 'body': p['url'] + '\n' + detail})
        if p['state'] == 'open':
            # Hold has its own event; ignore label ordering and hold spelling.
            if 'labels' in before and 'labels' in p:
                hold_label = p.get('hold_label', 'hold').casefold()
                old_labels = set(before['labels'])
                labels = set(p['labels'])
                added = sorted(label for label in labels - old_labels
                               if label.casefold() != hold_label)
                removed = sorted(label for label in old_labels - labels
                                 if label.casefold() != hold_label)
                if added or removed:
                    value = json.dumps({'added': added, 'removed': removed}, sort_keys=True)
                    occurrence = p.get('updated_at', p['head'])
                    events.append({
                        'key': f'pr:{p["number"]}:labels:{occurrence}:{value}',
                        'subject': f'PR #{p["number"]} labels changed', 'pr': p['number'],
                        'branch': p['branch'], 'body': p['url'] + '\nlabels: ' + value})
        fields = ('state', 'head', 'merge_state', 'checks', 'comments', 'reviews') if p['state'] == 'open' else ('state',)
        for field in fields:
            if field not in before and field == 'merge_state' and p.get(field) != 'DIRTY':
                continue
            changed = before.get(field) != p.get(field)
            if field == 'checks' and aggregate_checks:
                conclusion = checks_conclusion(p)
                changed = (before['head'], checks_conclusion(before)) != (p['head'], conclusion)
                value = json.dumps({'head': p['head'], 'conclusion': conclusion}, sort_keys=True)
            else:
                value = json.dumps(p.get(field), sort_keys=True)
            if changed:
                occurrence = p.get('updated_at', p['head'])
                detail = (f'checks changed: {conclusion}' if field == 'checks' and aggregate_checks
                          else f'{field} changed: {p["state"]}')
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

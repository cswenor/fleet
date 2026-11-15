"""Resolve GitHub queue members from the synthetic first-parent chain, not its lead PR."""
from datetime import datetime
from functools import cmp_to_key
import re
from .commands import gh, CommandError
from . import state


def members(commits, base, head):
    by_sha = {c['sha']: c for c in commits}
    result = set()
    heads = {}
    visited = set()
    while head != base:
        if head in visited or head not in by_sha:
            return sorted(result), False, heads
        visited.add(head)
        commit = by_sha[head]
        parents = commit.get('parents', [])
        subject = commit.get('commit', {}).get('message', '').split('\n')[0]
        match = re.search(r'(?:Merge pull request #(\d+)\b|\(#(\d+)\)$)', subject)
        if len(parents) != 2 or match is None:
            return sorted(result), False, heads
        result.add(int(match[1] or match[2]))
        heads[str(int(match[1] or match[2]))] = parents[1]['sha']
        head = parents[0]['sha']
    return sorted(result), True, heads


def newer(candidate, older):
    """Unknown/equal ordering is not evidence of supersession."""
    a, b = candidate.get('created_at'), older.get('created_at')
    try:
        if a and b:
            return datetime.fromisoformat(a.replace('Z', '+00:00')) > datetime.fromisoformat(b.replace('Z', '+00:00'))
        return int(candidate['id']) > int(older['id'])
    except (ValueError, TypeError, KeyError):
        return False


def newer_group(candidate, older):
    return bool(candidate.get('head_sha') and older.get('head_sha') and
                candidate['head_sha'] != older['head_sha'] and newer(candidate, older))


def resolve(c, runs, pr_number=None, *, queued_heads=()):
    """Resolve failures, queued heads, and bounded successors in one memoized pass."""
    cache = state.read(c, 'group-members', {})
    memo = {}

    def resolve_run(r):
        sha = r['head_sha']
        if sha in memo:
            r.update(memo[sha])
            return
        if sha in cache and 'member_heads' in cache[sha]:
            r.update(cache[sha])
            memo[sha] = cache[sha]
            return
        match = re.search(r'/pr-(\d+)-([0-9a-f]{40})$', r.get('branch') or '')
        associated = set(r.get('associated_prs', []))
        heads = {}
        if match:
            associated.add(int(match[1]))
            try:
                pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{c["repo"]}/compare/{match[2]}...{sha}?per_page=100')
                found, complete, heads = members([row for page in pages for row in page['commits']], match[2], sha)
                associated = set(found) if complete else associated.union(found)
            except (CommandError, KeyError, ValueError):
                complete = False
        else:
            complete = False
        r['prs'] = sorted(associated)
        r['membership_complete'] = complete
        r['member_heads'] = heads
        memo[sha] = {'prs': r['prs'], 'membership_complete': complete, 'member_heads': heads}
        if complete:
            cache[sha] = memo[sha]
    queued_heads = set(queued_heads)
    for r in runs:
        if (r.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required') or
                r.get('head_sha') in queued_heads):
            resolve_run(r)
        else:
            r['prs'] = []
            r['membership_complete'] = False
    failed = [r for r in runs if
              r.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required')]
    targets = ({pr_number} if pr_number is not None else
               {number for r in failed for number in r['prs']})
    failures = {number: [r for r in failed if number in r['prs']] for number in targets}
    # A single newest-first search serves all members, stopping independently
    # once each has a proven successor. Shared synthetic heads are fetched once.
    def order(a, b):
        return -1 if newer(a, b) else (1 if newer(b, a) else 0)
    for r in sorted(runs, key=cmp_to_key(order)):
        candidates = {number for number, older in failures.items()
                      if any(newer_group(r, f) for f in older)}
        if not candidates:
            continue
        resolve_run(r)
        if r['membership_complete']:
            for number in candidates.intersection(r['prs']):
                del failures[number]
    # Different workflows on the same immutable head share the proof, including
    # runs encountered before a newest-first successor lookup resolved it.
    for r in runs:
        if r.get('head_sha') in memo:
            r.update(memo[r['head_sha']])
    # Cache only proven immutable commit membership, never a transient lookup failure.
    state.write(c, 'group-members', dict(list(cache.items())[-1024:]))
    return runs

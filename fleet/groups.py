"""Resolve GitHub queue members from the synthetic first-parent chain, not its lead PR."""
import re
from .commands import gh, CommandError
from . import state


def members(commits, base, head):
    by_sha = {c['sha']: c for c in commits}
    result = set()
    visited = set()
    while head != base:
        if head in visited or head not in by_sha:
            return sorted(result), False
        visited.add(head)
        commit = by_sha[head]
        parents = commit.get('parents', [])
        subject = commit.get('commit', {}).get('message', '').split('\n')[0]
        match = re.search(r'(?:Merge pull request #(\d+)\b|\(#(\d+)\)$)', subject)
        if len(parents) != 2 or match is None:
            return sorted(result), False
        result.add(int(match[1] or match[2]))
        head = parents[0]['sha']
    return sorted(result), True


def resolve(c, runs):
    cache = state.read(c, 'group-members', {})
    for r in runs:
        if r.get('conclusion') not in ('failure', 'cancelled', 'timed_out', 'action_required'):
            r['prs'] = []
            r['membership_complete'] = False
            continue
        sha = r['head_sha']
        if sha in cache:
            r.update(cache[sha])
            continue
        match = re.search(r'/pr-(\d+)-([0-9a-f]{40})$', r.get('branch') or '')
        associated = set(r.pop('associated_prs', []))
        if match:
            associated.add(int(match[1]))
            try:
                pages = gh(c, 'api', '--paginate', '--slurp', f'repos/{c["repo"]}/compare/{match[2]}...{sha}?per_page=100')
                found, complete = members([row for page in pages for row in page['commits']], match[2], sha)
                associated.update(found)
            except (CommandError, KeyError, ValueError):
                complete = False
        else:
            complete = False
        r['prs'] = sorted(associated)
        r['membership_complete'] = complete
        if complete:
            cache[sha] = {'prs': r['prs'], 'membership_complete': True}
    # Cache only proven immutable commit membership, never a transient lookup failure.
    state.write(c, 'group-members', dict(list(cache.items())[-1024:]))
    return runs

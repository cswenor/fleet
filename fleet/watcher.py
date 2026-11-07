"""Blocking process for async shell completion; no model-turn polling."""
import time
from . import github, groups


def _initial_view(current, number):
    runs = current['merge_group_runs']
    pr = next((p for p in current['prs'] if p['number'] == number), {})

    def superseded(run):
        if number not in run.get('prs', []):
            return False
        member_head = run.get('member_heads', {}).get(str(number))
        if member_head and pr.get('head') and member_head != pr['head']:
            return True
        return any(other.get('membership_complete') and number in other.get('prs', [])
                   and groups.newer_group(other, run) for other in runs)

    return {**current, 'merge_group_runs': [r for r in runs if not superseded(r)]}


def watch(c, number, timeout=14400, interval=60):
    def view():
        return github.collect(c, number)
    before = view()
    initial = [e for e in github.events({}, _initial_view(before, number)) if e.get('pr') == number]
    if initial:
        return {'pr': number, 'events': initial}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        after = view()
        changes = [e for e in github.events(before, after) if e.get('pr') == number]
        if changes:
            return {'pr': number, 'events': changes}
    return {'pr': number, 'event': 'timeout', 'seconds': timeout}

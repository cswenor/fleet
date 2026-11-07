"""Blocking process for async shell completion; no model-turn polling."""
import json
import os
import time
from . import github, groups, state


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


def _initial_events(current, number):
    return [e for e in github.events({}, _initial_view(current, number)) if e.get('pr') == number]


def _receipt(c, caller, number, initial, include_seen=False, record_only=False):
    if not caller:
        return initial
    key = json.dumps([caller, c['repo'], number])
    keys = [e['key'] for e in initial]
    with state.lock(c, 'watch-pr'):
        receipts = state.read(c, 'watch-pr', {})
        seen = receipts.get(key, [])
        unseen = [e for e in initial if include_seen or e['key'] not in seen]
        if unseen or record_only:
            receipts[key] = keys
            state.write(c, 'watch-pr', receipts)
    return unseen


def watch(c, number, timeout=14400, interval=60, terminal=None, include_seen=False):
    caller = os.environ.get('ORCA_TERMINAL_HANDLE') or terminal

    def view():
        return github.collect(c, number)
    before = view()
    initial = _receipt(c, caller, number, _initial_events(before, number), include_seen)
    if initial:
        return {'pr': number, 'events': initial}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        after = view()
        changes = [e for e in github.events(before, after) if e.get('pr') == number]
        if changes:
            _receipt(c, caller, number, _initial_events(after, number), record_only=True)
            return {'pr': number, 'events': changes}
        before = after
    return {'pr': number, 'event': 'timeout', 'seconds': timeout}

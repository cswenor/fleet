"""Blocking process for async shell completion; no model-turn polling."""
from contextlib import contextmanager
import json
import os
import sys
import time
import uuid
from . import github, github_api, groups, state


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


@contextmanager
def _ownership(c, caller, number):
    key = json.dumps([c['repo'], number, caller])
    generation = uuid.uuid4().hex
    with state.lock(c, 'watch-pr-owners', wait_seconds=5):
        owners = state.read(c, 'watch-pr-owners', {})
        owners[key] = generation
        state.write(c, 'watch-pr-owners', owners)
    try:
        yield key, generation
    finally:
        with state.lock(c, 'watch-pr-owners', wait_seconds=5):
            owners = state.read(c, 'watch-pr-owners', {})
            if owners.get(key) == generation:
                del owners[key]
                state.write(c, 'watch-pr-owners', owners)


def _owns(c, key, generation):
    return state.read(c, 'watch-pr-owners', {}).get(key) == generation


def watch(c, number, timeout=14400, interval=60, terminal=None, include_seen=False):
    caller = os.environ.get('ORCA_TERMINAL_HANDLE') or terminal
    with _ownership(c, caller, number) as (key, generation):
        return _watch(c, number, timeout, interval, caller, include_seen, key, generation)


def _watch(c, number, timeout, interval, caller, include_seen, key, generation):
    deadline = time.monotonic() + timeout
    before = None
    unknown = []
    first = True
    superseded = {'pr': number, 'event': 'superseded'}
    with github_api.Session(c) as session:
        while first or time.monotonic() < deadline:
            first = False
            if not _owns(c, key, generation):
                return superseded
            started = dict(session.stats)
            try:
                after = github.collect(c, number)
            except github_api.BudgetLow:
                after = None
            if c.get('_github_debug'):
                print(json.dumps({'github_poll': {key: value - started.get(key, 0)
                                                  for key, value in session.stats.items()},
                                  'blocked_until': session.blocked_until}), file=sys.stderr, flush=True)
            # Serialize the ownership check and event/receipt commit with registration.
            # A replacement may have started while collect was in flight.
            with state.lock(c, 'watch-pr-owners', wait_seconds=5):
                if not _owns(c, key, generation):
                    return superseded
                if after is not None:
                    if after.get('unavailable', []) != unknown:
                        unknown = after.get('unavailable', [])
                        if unknown:
                            print(json.dumps({'pr': number, 'github_unknown': unknown}),
                                  file=sys.stderr, flush=True)
                    if before is None:
                        initial = _receipt(c, caller, number, _initial_events(after, number), include_seen)
                        if initial:
                            return {'pr': number, 'events': initial}
                    else:
                        changes = [e for e in github.events(before, after) if e.get('pr') == number]
                        if changes:
                            _receipt(c, caller, number, _initial_events(after, number), record_only=True)
                            return {'pr': number, 'events': changes}
                    before = after
            delay = max(interval, session.blocked_until - time.time())
            remaining = max(0, deadline - time.monotonic())
            if remaining:
                time.sleep(min(delay, remaining))
    with state.lock(c, 'watch-pr-owners', wait_seconds=5):
        if not _owns(c, key, generation):
            return superseded
        result = {'pr': number, 'event': 'timeout', 'seconds': timeout}
        if unknown:
            result['github_unknown'] = unknown
        return result

"""Blocking process for async shell completion; no model-turn polling."""
from contextlib import contextmanager
import json
import os
import sys
import time
import uuid
from . import github, github_api, github_usage, groups, snapshot, state
from .commands import CommandError


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


def _check_baseline(c, caller, number, pr):
    """Retain check observations across bounded watch invocations, not cold starts."""
    if not caller:
        return None
    key = json.dumps([caller, c['repo'], number])
    now = time.time()
    with state.lock(c, 'watch-pr-checks'):
        saved = state.read(c, 'watch-pr-checks', {})
        saved = {k: v for k, v in saved.items()
                 if 0 <= now - v['observed_at'] < 86400}
        previous = saved.pop(key, None)
        if pr.get('state') == 'open':
            saved[key] = {'observed_at': now, 'head': pr['head'],
                          'checks': pr.get('checks', [])}
        saved = dict(sorted(saved.items(), key=lambda item: item[1]['observed_at'])[-128:])
        state.write(c, 'watch-pr-checks', saved)
    if previous is None or previous['head'] != pr.get('head'):
        return None
    return {**pr, 'checks': previous['checks']}


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


def _shared_snapshot(c, number):
    try:
        saved, info = snapshot.shared(c)
        if saved is None or info['source'] != 'fresh':
            return None, info['source'], saved.get('github_budget') if saved else None
        current = saved.get('github')
        if not isinstance(current, dict) or not isinstance(current.get('prs'), list):
            return None, 'unavailable', saved.get('github_budget')
        if not any(p.get('number') == number for p in current['prs']):
            return None, 'pr_absent', saved.get('github_budget')
        if not isinstance(current.get('merge_group_runs'), list):
            current = {**current, 'merge_group_runs': []}
        # Missing group membership must not hide fresh queue or terminal state.
        # Defer only failures whose supersession is still unproven this cycle.
        runs = _initial_view(current, number)['merge_group_runs']
        current = {**current, 'merge_group_runs': [
            r for r in runs if not (
                number in r.get('prs', []) and
                r.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required') and
                any(not other.get('membership_complete') and groups.newer_group(other, r)
                    for other in runs))]}
        return current, 'fresh', saved.get('github_budget')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, 'unavailable', None


def _queue_progress(current, number):
    if current.get('merge_queue_unknown'):
        return None
    entry = next((e for e in current.get('merge_queue', [])
                  if e.get('pullRequest', {}).get('number') == number), None)
    if entry is None or entry.get('progress_unknown'):
        return None
    return {k: entry.get(k) for k in ('position', 'state', 'headCommit', 'runs')}


def _queue_events(before, after, number):
    old = _queue_progress(before, number)
    progress = _queue_progress(after, number)
    pr = next((p for p in after['prs'] if p['number'] == number), {})
    if old is None or progress is None or old == progress or pr.get('state') != 'open':
        return []
    evidence = json.dumps(progress, sort_keys=True)
    return [{'key': f'pr:{number}:queue_progress:{evidence}', 'pr': number,
             'subject': f'PR #{number} merge_queue progress',
             'branch': pr.get('branch'), 'body': pr.get('url', '') + '\n' + evidence}]


def watch(c, number, timeout=14400, interval=60, terminal=None, include_seen=False):
    caller = os.environ.get('ORCA_TERMINAL_HANDLE') or terminal
    with _ownership(c, caller, number) as (key, generation):
        return _watch(c, number, timeout, interval, caller, include_seen, key, generation)


def _watch(c, number, timeout, interval, caller, include_seen, key, generation):
    deadline = time.monotonic() + timeout
    github_usage.record()
    before = None
    previous_progress = None
    unknown = []
    first = True
    superseded = {'pr': number, 'event': 'superseded'}
    delay = interval
    evidence_deadline = None
    waiting_snapshot = False
    with github_api.Session(c) as session:
        session.require_app = True
        while first or time.monotonic() < deadline:
            first = False
            if not _owns(c, key, generation):
                return superseded
            started = dict(session.stats)
            after, source, budget = _shared_snapshot(c, number)
            deferred = None
            unavailable = False
            waiting_snapshot = after is None
            if after is None:
                # App configuration can resolve to stock; require actual identity.
                if session.auth.get('kind') == 'app_installation':
                    try:
                        after = github.collect(c, number)
                        waiting_snapshot = False
                    except github_api.BudgetLow as error:
                        deferred = error.reset
                        waiting_snapshot = False
                    except CommandError as error:
                        if error.code != 'app_read_unavailable':
                            raise
                if waiting_snapshot:
                    _, info = snapshot.shared(c)
                    if evidence_deadline is None:
                        period = info.get('period_seconds', c.get('interval_seconds', 60))
                        evidence_deadline = time.monotonic() + 3 * period + 15
                    if info.get('unavailable') or time.monotonic() > evidence_deadline:
                        unavailable = True
                    delay = min(interval, 15)
            if after is not None:
                evidence_deadline = None
            if c.get('_github_debug'):
                print(json.dumps({'github_poll': {key: value - started.get(key, 0)
                                                  for key, value in session.stats.items()},
                                  'snapshot': source, 'snapshot_budget': budget,
                                  'snapshot_timing': snapshot.shared(c)[1],
                                  'blocked_until': deferred or session.blocked_until}), file=sys.stderr, flush=True)
            # Serialize the ownership check and event/receipt commit with registration.
            # A replacement may have started while collect was in flight.
            with state.lock(c, 'watch-pr-owners', wait_seconds=5):
                if not _owns(c, key, generation):
                    return superseded
                if unavailable:
                    return {'pr': number, 'event': 'snapshot_unavailable', 'reason': source}
                if deferred is not None:
                    return {'pr': number, 'event': 'github_budget_deferred', 'until': deferred}
                if after is not None:
                    if after.get('unavailable', []) != unknown:
                        unknown = after.get('unavailable', [])
                        if unknown:
                            print(json.dumps({'pr': number, 'github_unknown': unknown}),
                                  file=sys.stderr, flush=True)
                    pr = next((p for p in after['prs'] if p['number'] == number), {})
                    baseline = _check_baseline(c, caller, number, pr)
                    if pr.get('state') in ('merged', 'closed'):
                        events = (_initial_events(after, number) if before is None else
                                  [e for e in github.events(before, after) if e.get('pr') == number])
                        _receipt(c, caller, number, _initial_events(after, number), record_only=True)
                        return {'pr': number, 'event': pr['state'], 'events': events}
                    if before is None:
                        initial = _receipt(c, caller, number, _initial_events(after, number), include_seen)
                        checks = ([e for e in github.events(
                            {'prs': [baseline]}, after, aggregate_checks=True)
                            if e.get('pr') == number and ':checks:' in e['key']]
                            if baseline is not None else [])
                        if initial or checks:
                            return {'pr': number, 'events': initial + checks}
                    else:
                        changes = [e for e in github.events(before, after, aggregate_checks=True)
                                   if e.get('pr') == number]
                        changes += _queue_events(before, after, number)
                        if changes:
                            _receipt(c, caller, number, _initial_events(after, number), record_only=True)
                            return {'pr': number, 'events': changes}
                    # Ignore rolling collection windows and unrelated repository
                    # activity; retain all selected-PR progress, even non-waking updates.
                    progress = (pr, [r for r in after['merge_group_runs'] if number in r.get('prs', [])],
                                after.get('unavailable', []))
                    if previous_progress == progress and pr.get('auto_merge') is not None:
                        delay = min(delay * 2, max(interval, 300))
                    else:
                        delay = interval
                    previous_progress = progress
                    before = after
            delay = max(delay, session.blocked_until - time.time())
            remaining = max(0, deadline - time.monotonic())
            if remaining:
                time.sleep(min(delay, remaining))
    with state.lock(c, 'watch-pr-owners', wait_seconds=5):
        if not _owns(c, key, generation):
            return superseded
        if waiting_snapshot:
            return {'pr': number, 'event': 'snapshot_unavailable', 'reason': source}
        result = {'pr': number, 'event': 'timeout', 'seconds': timeout}
        if unknown:
            result['github_unknown'] = unknown
        return result

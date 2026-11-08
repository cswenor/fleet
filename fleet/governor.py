"""Cooperative reserve enforcement. Never consumes coordinator mail or stops a process."""
import json
import math
import os
import time
import uuid
from . import health, state
from .commands import json_run, CommandError

CONSENT = 'Saved resets require explicit owner approval recorded as a Pad DECISION comment BEFORE spend; Fleet never spends resets.'
PRIORITY = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}


def priority(c, ref):
    if not ref:
        return 0
    try:
        item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', ref, '--format', 'json'], c['root'])
        fields = item.get('fields', {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        return PRIORITY.get(fields.get('priority', item.get('priority')), 0)
    except (CommandError, ValueError, TypeError, AttributeError):
        return 0


def same_attempt(c, active, builders):
    launch = state.read(c, 'launches', {}).get(active['path'], {})
    return (launch.get('id') == active['launch_record_id'] and bool(active['incarnation_id'])
            and any(b['terminal'] == active['terminal']
                    and b.get('incarnation_id') == active['incarnation_id']
                    and b.get('path') == active['path'] and b.get('task') == active['ref']
                    for b in builders))


def _superseding_launch(c, active):
    """Only a newer guarded launch plus exact verified retirement proves replacement."""
    launch = state.read(c, 'launches', {}).get(active['path'], {})
    retirement = state.read(c, 'retirements', {}).get(active['path'], {})
    times = (active['at'], retirement.get('at'), launch.get('at'))
    if (not active.get('launch_record_id') or not launch.get('id')
            or launch['id'] == active['launch_record_id']
            or retirement.get('launch_id') != active['launch_record_id']
            or retirement.get('terminal') != active['terminal']
            or not active.get('incarnation_id')
            or retirement.get('terminal_incarnation_id') != active['incarnation_id']
            or retirement.get('stage') not in ('shell-verified', 'launch-attempted', 'launched')
            or not all(isinstance(t, (int, float)) and not isinstance(t, bool)
                       and math.isfinite(t) for t in times)
            or not times[0] <= times[1] < times[2]
            or (retirement.get('next_launch_id') is not None
                and retirement['next_launch_id'] != launch['id'])):
        return None
    return {'next_launch_id': launch['id'], 'launch_at': launch['at'],
            'retirement': retirement}


def _settled(c, launch, slot):
    ref = launch['ref']
    task = state.read(c, 'threads-' + ref, {}).get(ref, {})
    attempt = task.get('attempts', {}).get(launch['id'], {})
    return (bool(task.get('id')) and attempt.get('settled') is True
            and attempt.get('terminal') == launch['terminal'] and attempt.get('slot') == slot)


def reconcile(c, launch_id, evidence):
    """Queue Main's evidence without racing the service-owned supervisor ledger."""
    actor = os.environ.get('ORCA_TERMINAL_HANDLE')
    if actor != c['coordinator']['terminal']:
        raise ValueError('park-reconcile requires the configured Main terminal identity')
    if not evidence.strip():
        raise ValueError('park-reconcile requires non-empty evidence')
    with state.lock(c, 'launcher'):
        active = state.read(c, 'supervisor', {}).get('governor', {}).get('active')
        if not active or active.get('launch_record_id') != launch_id:
            raise ValueError('park-reconcile requires the exact active park launch ID')
        receipts = state.read(c, 'park-reconciliations', {})
        receipt = next((r for r in receipts.values() if r['park_key'] == active['key']), None)
        if receipt is None:
            receipt = {'id': str(uuid.uuid4()), 'launch_id': launch_id, 'park_key': active['key'],
                       'at': time.time(), 'actor': actor, 'evidence': evidence.strip()}
            receipts[receipt['id']] = receipt
            state.write(c, 'park-reconciliations', receipts)
        return {**receipt, 'notice': 'takes effect on the next supervisor cycle'}


def _reconciliations(c, g, events, now):
    # Outcomes live in the supervisor transaction, not the immutable request ledger.
    outcomes = g.setdefault('reconciliations', {})
    for receipt_id, receipt in state.read(c, 'park-reconciliations', {}).items():
        if receipt_id in outcomes:
            continue
        active = g['active']
        matches = (active and receipt.get('park_key') == active['key']
                   and receipt.get('launch_id') == active.get('launch_record_id'))
        outcomes[receipt_id] = {'status': 'consumed' if matches else 'ignored', 'at': now}
        if matches:
            active.update(status='reconciled', resolved_at=now, resolution=receipt)
            g['parked'].append(active)
            g['active'] = None
        events.append({'key': 'park-reconciliation:' + receipt_id, 'branch': None,
                       'subject': 'Fleet park reconciliation ' + outcomes[receipt_id]['status'],
                       'body': f"Receipt {receipt_id} for launch {receipt['launch_id']}: "
                               + ('Main evidence accepted; active park cleared.' if matches else
                                  'No matching active park; receipt ignored, no later park authorized.')})


def advance(c, s, ledger, now=None):
    now = time.time() if now is None else now
    g = ledger.setdefault('governor', {'episode': None, 'active': None, 'parked': []})
    events = []
    q = s.get('quota', {})
    inventory = s.get('inventory')
    builders = inventory.get('builders', []) if inventory else []
    def report(key, subject, body):
        events.append({'key': key, 'subject': subject, 'body': body, 'branch': None})
    _reconciliations(c, g, events, now)
    active = g['active']
    if active:
        replacement = _superseding_launch(c, active)
        # Absence/disconnection is never evidence of exit. Exact terminal + shell only.
        matched = same_attempt(c, active, builders)
        exited = matched and any(b['terminal'] == active['terminal'] and not health.active(b) for b in builders)
        if not replacement and not matched and not active.get('identity_alerted'):
            active['identity_alerted'] = True
            report(active['key'] + ':identity', 'Fleet park identity unresolved: Main reconciliation required',
                   f"{active['ref']} launch/incarnation is changed or unavailable; obsolete builder delivery withheld. No second park or stop authorized.")
        if replacement:
            active.update(status='superseded', resolved_at=now, resolution=replacement)
            g['parked'].append(active)
            g['active'] = None
            report(active['key'] + ':superseded', 'Fleet park superseded: ' + active['ref'],
                   f"{active['ref']} retired before guarded replacement launch {replacement['next_launch_id']}; obsolete park resolved.")
        elif exited:
            active['status'] = 'parked'
            g['parked'].append(active)
            g['active'] = None
            report(active['key'] + ':exited', 'Fleet lane parked: ' + active['ref'],
                   f"{active['ref']} has a verified shell exit. WIP/handoff must be reconciled by Main before fresh relaunch.")
        elif now - active['at'] >= c.get('quota', {}).get('park_timeout_seconds', 1800) and not active.get('escalated'):
            active['escalated'] = True
            report(active['key'] + ':timeout', 'Fleet park timeout: Main reconciliation required',
                   f"{active['ref']} has not positively exited; no second park or stop is authorized.")
    if not q.get('known'):
        return events
    if q.get('may_start'):
        if g['episode'] is not None:
            tasks = sorted({p['ref'] for p in g['parked']})
            outstanding = g['active']['ref'] if g['active'] else 'none'
            report(g['episode'] + ':recovery', 'Fleet quota capacity recovered',
                   f"Parked tasks: {', '.join(tasks) or 'none'}. Outstanding park: {outstanding}. New starts pass the quota gate; relaunch requires Main confirmation and a fresh session. {CONSENT}")
            g['episode'] = None
        return events
    if g['episode'] is None:
        g['episode'] = 'quota:' + str(time.time_ns())
    if g['active'] and g['active']['episode'] != g['episode'] and g.get('blocked_episode') != g['episode']:
        g['blocked_episode'] = g['episode']
        report(g['episode'] + ':blocked', 'Fleet park blocked by unresolved ' + g['active']['ref'],
               f"Launch {g['active']['launch_record_id']} still has an unresolved park; Main reconciliation required. No second park authorized.")
    if g['active'] or not inventory or inventory.get('errors'):
        return events
    launches = state.read(c, 'launches', {})
    candidates = []
    parked = {p['launch_id'] for p in g['parked'] if p['episode'] == g['episode']}
    for b in builders:
        if not health.active(b):
            continue
        launch = launches.get(b.get('path'), {})
        matching = (launch.get('terminal') == b['terminal'] and launch.get('ref') == b.get('task')
                    and bool(b.get('incarnation_id'))
                    and launch.get('terminal_incarnation_id') == b['incarnation_id'])
        launch_id = launch['id'] if matching else b['terminal'] + ':' + str(b.get('task'))
        if matching and _settled(c, launch, b['path']):
            continue
        if launch_id not in parked:
            candidates.append((priority(c, b.get('task')), b, launch if matching else {}, launch_id))
    if not candidates:
        return events
    lowest = min(row[0] for row in candidates)
    candidates = [row for row in candidates if row[0] == lowest]
    for _, b, _, _ in candidates:
        recorded = launches.get(b['path'], {})
        if (recorded.get('terminal') == b['terminal'] and recorded.get('ref') == b.get('task')
                and recorded.get('terminal_incarnation_id') != b.get('incarnation_id')):
            key = g['episode'] + ':incarnation'
            if g.get('incarnation_alert') != key:
                report(key, 'Fleet park withheld: recorded launch incarnation changed',
                       'A lowest-priority lane no longer matches its launch incarnation. Main must reconcile; old launch timestamps cannot rank a replacement.')
                g['incarnation_alert'] = key
            return events
    if len(candidates) > 1 and any(not isinstance(row[2].get('at'), (int, float)) for row in candidates):
        key = g['episode'] + ':history'
        if g.get('history_alert') != key:
            report(key, 'Fleet park withheld: launch history unresolved',
                   'Lowest-priority lanes tie without matching timestamped launch evidence; Main must reconcile. No tie guesses or stops.')
            g['history_alert'] = key
        return events
    _, b, launch, launch_id = max(candidates, key=lambda row: (row[2].get('at', 0), row[3]))
    if (not b.get('task') or not b['connected'] or not b.get('incarnation_id')
            or health.owner(inventory, b['branch']) != b['terminal']):
        key = g['episode'] + ':owner'
        if g.get('owner_alert') != key:
            report(key, 'Fleet park withheld: builder identity unresolved',
                   'Lowest-priority owner/incarnation is unavailable or ambiguous. Main must reconcile; no higher-priority substitute or stop.')
            g['owner_alert'] = key
        return events
    key = g['episode'] + ':' + launch_id
    g['active'] = {'key': key, 'episode': g['episode'], 'launch_id': launch_id, 'ref': b['task'],
                   'terminal': b['terminal'], 'branch': b['branch'], 'at': now, 'status': 'requested'}
    g['active'].update(path=b['path'], incarnation_id=b['incarnation_id'], worktree=b['worktree'],
                       launch_record_id=launches.get(b['path'], {}).get('id'))
    body = (f"PARK {b['task']} (request {key}). Finish your current step, commit and push WIP to your task branch, "
            f"write a Pad handoff comment in workspace {c['pad_workspace']}, report PARKED with WIP/handoff evidence "
            f"to run:{c['coordinator']['run']}. After reporting PARKED, end your turn with a final message and do nothing else: "
            "no further tool calls, no background jobs, and never a job that waits on your own terminal/idle state "
            "or sends input to your own terminal. Main (or the owner) retires the idle session via the documented "
            "retirement path (fleet restart for eligible finished work, or manual idle-proof /exit + fleet reconcile). "
            "Do not start another task or signal FRESH-READY. "
            f"Resume only as a Main-confirmed fresh session. Fleet never sends exit/interrupt/kill input. {CONSENT}")
    events.append({'key': key, 'subject': 'Fleet cooperative PARK: ' + b['task'], 'body': body,
                   'branch': None, 'park': key, 'attempt': dict(g['active']),
                   'recipients': [b['terminal'], 'run:' + c['coordinator']['run'], 'pad:' + b['task']]})
    return events


def deliverable(c, event, s, ledger):
    if not event.get('park'):
        return True
    active = ledger['governor']['active']
    inventory = s.get('inventory')
    return bool(active and active['key'] == event['park'] and inventory and not inventory.get('errors')
                and health.owner(inventory, active['branch']) == active['terminal']
                and same_attempt(c, active, inventory['builders'])
                and s.get('quota', {}).get('known') and not s['quota'].get('may_start'))

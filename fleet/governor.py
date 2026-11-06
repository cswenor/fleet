"""Cooperative reserve enforcement. Never consumes coordinator mail or stops a process."""
import json
import time
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


def advance(c, s, ledger, now=None):
    now = time.time() if now is None else now
    g = ledger.setdefault('governor', {'episode': None, 'active': None, 'parked': []})
    events = []
    q = s.get('quota', {})
    inventory = s.get('inventory')
    builders = inventory.get('builders', []) if inventory else []
    def report(key, subject, body):
        events.append({'key': key, 'subject': subject, 'body': body, 'branch': None})
    active = g['active']
    if active:
        # Absence/disconnection is never evidence of exit. Exact terminal + shell only.
        matched = same_attempt(c, active, builders)
        exited = matched and any(b['terminal'] == active['terminal'] and not health.active(b) for b in builders)
        if not matched and not active.get('identity_alerted'):
            active['identity_alerted'] = True
            report(active['key'] + ':identity', 'Fleet park identity unresolved: Main reconciliation required',
                   f"{active['ref']} launch/incarnation is changed or unavailable; obsolete builder delivery withheld. No second park or stop authorized.")
        if exited:
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
            f"to run:{c['coordinator']['run']}, then stop this session yourself. Do not start another task or signal FRESH-READY. "
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

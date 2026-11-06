"""Read-only Main-confirmed proposals and shared launch eligibility checks."""
import json
import os
import time
from pathlib import Path
from . import health, snapshot, state
from .commands import CommandError, json_run, run

PRIORITY = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}


def pad(c, *args):
    return json_run(['pad', '--workspace', c['pad_workspace'], *args, '--format', 'json'], c['root'])


def fields(item):
    value = item.get('fields', {})
    return json.loads(value) if isinstance(value, str) else value


def dependencies(c, ref):
    reasons = []
    try:
        edges = pad(c, 'item', 'deps', ref)
    except (CommandError, ValueError, TypeError):
        return ['dependency state unavailable']
    for edge in edges:
        if edge['link_type'] == 'blocks' and edge['target_ref'] == ref:
            if edge.get('source_status') not in ('done', 'cancelled'):
                reasons.append('blocked by ' + edge['source_ref'] + ' (' + str(edge.get('source_status', 'unknown')) + ')')
    return reasons


def unresolved(c, inventory):
    receipts = list(state.read(c, 'launches', {}).values())
    last = state.read(c, 'last-launch', {})
    if last:
        receipts.append(last)
    retirements = state.read(c, 'retirements', {})
    reconciled = state.read(c, 'reconciliations', {})
    pending = {}
    for receipt in receipts:
        path = receipt['worktree']['path']
        incarnation = receipt.get('terminal_incarnation_id')
        exited = bool(incarnation) and any(
            b['path'] == path and b['terminal'] == receipt['terminal']
            and b.get('incarnation_id') == incarnation and not health.active(b)
            for b in inventory.get('builders', []))
        retirement = retirements.get(path, {})
        retired = (bool(receipt.get('id')) and retirement.get('launch_id') == receipt['id']
                   and retirement.get('terminal') == receipt['terminal']
                   and bool(incarnation) and retirement.get('terminal_incarnation_id') == incarnation
                   and retirement.get('stage') in ('shell-verified', 'launch-attempted', 'launched'))
        reconciliation = reconciled.get(receipt.get('id'), {})
        acknowledged = (bool(receipt.get('id')) and reconciliation.get('slot') == path
                        and reconciliation.get('launch_id') == receipt['id'])
        if not (exited or retired or acknowledged):
            pending[(path, receipt.get('id'))] = receipt
    return list(pending.values())


def receipt_reasons(c, inventory, path, *, retiring=None):
    return sorted({f"slot has unresolved launch receipt {r.get('id', 'unknown')} ({r['ref']})"
                   for r in unresolved(c, inventory) if r['worktree']['path'] == path and r != retiring})


def reconcile(c, slot, launch_id, evidence):
    slot = str(Path(slot).expanduser().resolve())
    actor = os.environ.get('ORCA_TERMINAL_HANDLE')
    if actor != c['coordinator']['terminal']:
        raise ValueError('reconcile requires the configured Main terminal identity')
    if slot not in c['builder']['slots'] or not evidence.strip():
        raise ValueError('reconcile requires a configured slot and non-empty exit evidence')
    with state.lock(c, 'launcher'):
        inventory = health.collect(c)
        if inventory.get('errors') or any(b['path'] == slot and health.active(b) for b in inventory['builders']):
            raise ValueError('reconcile withheld: slot health is live, unknown or incomplete')
        receipts = list(state.read(c, 'launches', {}).values()) + [state.read(c, 'last-launch', {})]
        if not any(r.get('id') == launch_id and r.get('worktree', {}).get('path') == slot for r in receipts):
            raise ValueError('reconcile requires an exact recorded launch ID and slot')
        receipt = {'slot': slot, 'launch_id': launch_id, 'at': time.time(),
                   'actor': actor, 'evidence': evidence.strip()}
        reconciled = state.read(c, 'reconciliations', {})
        reconciled[launch_id] = receipt
        state.write(c, 'reconciliations', reconciled)
        return receipt


def slot_reasons(c, inventory, path):
    reasons = []
    trees = [w for w in inventory.get('worktrees', []) if w['path'] == path]
    builders = [b for b in inventory.get('builders', []) if b['path'] == path]
    if not Path(path).is_dir() or len(trees) != 1:
        reasons.append('slot must map to exactly one existing Orca worktree')
    if any(health.active(b) for b in builders):
        reasons.append('slot has a live or unconfirmed omp session; finish it first')
    shells = [b for b in builders if not health.active(b)]
    if len(shells) > 1:
        reasons.append('slot has multiple ready shells; refusing ambiguous terminal reuse')
    if not shells:
        reasons.append('slot has no verified ready shell')
    if any(not b.get('incarnation_id') for b in shells):
        reasons.append('slot shell incarnation is unknown')
    reasons.extend(receipt_reasons(c, inventory, path))
    return reasons


def assignments(c, inventory):
    """Actual Git beats cached Orca branches; retain comments and launch receipts."""
    claims = []
    for w in inventory.get('worktrees', []):
        if w['path'] not in c['builder']['slots']:
            continue
        branch = run(['git', 'branch', '--show-current'], w['path']).strip()
        for source, text in (('branch', branch), ('card', w.get('comment', ''))):
            for match in health.REF.finditer(text or ''):
                claims.append((match[1].upper(), w['path'], source))
    for receipt in unresolved(c, inventory):
        claims.append((receipt['ref'], receipt['worktree']['path'], 'unresolved launch receipt'))
    for b in inventory.get('builders', []):
        if health.active(b) and b.get('task'):
            claims.append((b['task'], b['path'], 'active builder'))
    return claims


def duplicate_reasons(ref, claims, slot=None):
    return sorted({f'{ref} already assigned to {path} ({source})'
                   for task, path, source in claims if task == ref
                   and (path != slot or source not in ('branch', 'card'))})


def check_launch(c, ref, inventory, slot, *, retiring=None):
    item = pad(c, 'item', 'show', ref)
    status = fields(item).get('status')
    reasons = [] if status in ('open', 'in-progress') else ['task status is not open or in-progress']
    parent = item.get('parent_ref')
    if not parent or fields(pad(c, 'item', 'show', parent)).get('status') != 'active':
        reasons.append('task is not in an active plan')
    reasons.extend(dependencies(c, ref))
    reasons.extend(duplicate_reasons(ref, assignments(c, inventory), slot))
    reasons.extend(receipt_reasons(c, inventory, slot, retiring=retiring))
    if reasons:
        raise ValueError('launch withheld: ' + '; '.join(reasons))
    return item


def propose(c):
    with state.lock(c, 'launcher'):
        s = snapshot.collect(c)
        admission = snapshot.may_start(c, s)
        inventory = s.get('inventory', {})
        claims = assignments(c, inventory)
        plans = pad(c, 'item', 'list', 'plans', '--full', '--limit', '1000')
        tasks = pad(c, 'item', 'list', 'tasks', '--all', '--full', '--limit', '1000')
        if len(plans) >= 1000 or len(tasks) >= 1000:
            raise ValueError('Pad inventory may be truncated; refusing partial proposal')
        active = {p['ref'] for p in plans if fields(p).get('status') == 'active'}
        slots = [{'path': path, 'reasons': slot_reasons(c, inventory, path)} for path in c['builder']['slots']]
        rows = []
        for item in tasks:
            f = fields(item)
            reasons = []
            if f.get('status') != 'open':
                reasons.append('task status is ' + str(f.get('status', 'unknown')))
            if item.get('parent_ref') not in active:
                reasons.append('task is not in an active plan')
            reasons.extend(dependencies(c, item['ref']))
            reasons.extend(duplicate_reasons(item['ref'], claims))
            rows.append({'ref': item['ref'], 'title': item['title'], 'priority': f.get('priority'),
                         'created_at': item['created_at'], 'not_before': f.get('not_before'), 'reasons': reasons})
        rows.sort(key=lambda r: (-PRIORITY.get(r['priority'], 0), r['created_at'], r['ref']))
        eligible = [r for r in rows if not r['reasons']]
        free = [slot for slot in slots if not slot['reasons']]
        proposal = {'ref': eligible[0]['ref'], 'slot': free[0]['path']} if eligible and free and admission['may_start'] else None
        return {'mode': 'main-confirmed', 'proposal': proposal, 'admission': admission,
                'tasks': rows, 'slots': slots}


def render(result):
    lines = ['Fleet next: Main-confirmed; no launch performed']
    proposal = result['proposal']
    lines.append(f"Proposed: {proposal['ref']} --slot {proposal['slot']}" if proposal else 'No launchable proposal')
    lines.extend('WITHHELD: ' + reason for reason in result['admission']['reasons'])
    for row in result['tasks']:
        lines.append(row['ref'] + ': ' + ('; '.join(row['reasons']) or 'eligible'))
    for row in result['slots']:
        lines.append(row['path'] + ': ' + ('; '.join(row['reasons']) or 'verified free shell'))
    return '\n'.join(lines)

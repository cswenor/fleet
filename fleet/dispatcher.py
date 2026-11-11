"""Read-only Main-confirmed proposals and shared launch eligibility checks."""
import json
import os
import time
import re
from pathlib import Path
from . import config, health, snapshot, state
from .commands import CommandError, json_run, run

PRIORITY = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
# Strip presentation prefixes only; arbitrary prose before a ref is not ownership.
CARD_STATUS = re.compile(
    r'(?:open|ready|working|in-progress|blocked|done|completed|parked|cancelled)(?:\s*:\s*|\s+)',
    re.I)
CARD_DECORATION = re.compile(r'(?:[^\w]|[0-9]\ufe0f?\u20e3)*')


def _card_ref(text):
    pos = CARD_DECORATION.match(text).end()
    status = CARD_STATUS.match(text, pos)
    if status:
        pos = status.end()
    return health.REF.match(text, pos)


def pad(c, *args):
    return json_run(['pad', '--workspace', c['pad_workspace'], *args, '--format', 'json'], c['root'])


def fields(item):
    value = item.get('fields', {})
    return json.loads(value) if isinstance(value, str) else value


def status_policy(c):
    override = c.get('pad', {}).get('statuses')
    if override is not None:
        return config.validate_statuses(override)
    try:
        collections = pad(c, 'collection', 'list')
    except CommandError:
        return config.STATUS_DEFAULTS
    try:
        task_collection = next(row for row in collections if row['slug'] == 'tasks')
        schema = task_collection['schema']
        schema = json.loads(schema) if isinstance(schema, str) else schema
        status = next(f for f in schema['fields'] if f['key'] == 'status')
        options = status['options']
        policy = {'not_started': [status['default']],
                  'in_progress': ['in-progress'] if 'in-progress' in options else [],
                  'terminal': status['terminal_options']}
        config.validate_statuses(policy, allow_no_progress=True)
        if any(name not in options for names in policy.values() for name in names):
            raise ValueError('status category not in options')
        return policy
    except (KeyError, StopIteration, TypeError, ValueError) as e:
        raise ValueError('Pad task status schema invalid; configure pad.statuses explicitly') from e


def eligibility(c, item, statuses, *, continuing=False, cache=None):
    status = fields(item).get('status')
    allowed = statuses['not_started'] + (statuses['in_progress'] if continuing else [])
    reasons = [] if status in allowed else ['task status is not eligible: ' + str(status)]
    reasons.extend(plan_reasons(c, item, cache))
    return reasons


def plan_reasons(c, item, cache=None):
    """Require the nearest PLAN, never skip an inactive nearer plan."""
    cache = {} if cache is None else cache
    seen = {item['ref']}
    for _ in range(20):
        parent = item.get('parent_ref')
        if not parent:
            return ['task ancestry has no PLAN']
        if parent in seen:
            return ['task ancestry cycle at ' + parent]
        seen.add(parent)
        try:
            if parent not in cache:
                cache[parent] = pad(c, 'item', 'show', parent)
            item = cache[parent]
            if item.get('ref') != parent:
                return ['task ancestry missing parent ' + parent]
            if parent.startswith('PLAN-'):
                return [] if fields(item).get('status') == 'active' else ['nearest plan ' + parent + ' is not active']
        except (CommandError, ValueError, TypeError, KeyError):
            return ['task ancestry unreadable parent ' + parent]
    return ['task ancestry exceeds 20 parent links']


def dependencies(c, ref, statuses=None):
    reasons = []
    try:
        edges = pad(c, 'item', 'deps', ref)
    except (CommandError, ValueError, TypeError):
        return ['dependency state unavailable']
    for edge in edges:
        if edge['link_type'] == 'blocks' and edge['target_ref'] == ref:
            if edge.get('source_status') not in (statuses or status_policy(c))['terminal']:
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
    with state.lock(c, 'launcher', operation='reconcile'):
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


def carry(c, slot, launch_id, ref, evidence):
    """Main explicitly assigns a prerequisite to one existing launch attempt."""
    slot = str(Path(slot).expanduser().resolve())
    actor = os.environ.get('ORCA_TERMINAL_HANDLE')
    if actor != c['coordinator']['terminal']:
        raise ValueError('carry requires the configured Main terminal identity')
    if slot not in c['builder']['slots'] or not evidence.strip():
        raise ValueError('carry requires a configured slot and non-empty approval evidence')
    if not re.fullmatch(r'[A-Z]+-\d+', ref):
        raise ValueError('expected uppercase Pad reference')
    with state.lock(c, 'launcher', operation='carry'):
        launch = state.read(c, 'launches', {}).get(slot, {})
        if not launch_id or launch.get('id') != launch_id:
            raise ValueError('carry requires an exact current launch ID and slot')
        if launch['ref'] == ref:
            raise ValueError('cannot carry the primary ref')
        primary = state.read(c, 'threads-' + launch['ref'], {}).get(launch['ref'], {})
        inventory = health.collect(c)
        if (primary.get('attempts', {}).get(launch_id, {}).get('settled') is not False or
                launch not in unresolved(c, inventory)):
            raise ValueError('cannot carry on an unknown or settled launch primary thread')
        if inventory.get('errors') or not any(
                b['path'] == slot and b.get('terminal') == launch.get('terminal')
                and b.get('incarnation_id') == launch.get('terminal_incarnation_id')
                and bool(b.get('incarnation_id')) and b.get('connected') and health.active(b)
                for b in inventory.get('builders', [])):
            raise ValueError('carry requires positively identified live launch health')
        carries = state.read(c, 'carries', {})
        existing = carries.get(launch_id, {}).get(ref)
        claims = [claim for claim in assignments(c, inventory)
                  if claim != (ref, slot, 'carried launch receipt')]
        reasons = duplicate_reasons(ref, claims, slot)
        if reasons:
            raise ValueError('carry withheld: ' + '; '.join(reasons))
        if existing is not None:
            return existing
        receipt = {'ref': ref, 'slot': slot, 'launch_id': launch_id,
                   'actor': actor, 'at': time.time(), 'evidence': evidence.strip()}
        carries.setdefault(launch_id, {})[ref] = receipt
        state.write(c, 'carries', carries)
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


def assignments(c, inventory, *, retiring=None):
    """Actual Git beats cached Orca branches; retain comments and launch receipts."""
    claims = []
    # Only restart supplies a positively verified canonical launch. Branch-derived
    # task labels can already name NEXT while this exact OLD process is still live.
    if retiring is not None and (
            not retiring.get('id') or
            state.read(c, 'launches', {}).get(retiring.get('worktree', {}).get('path')) != retiring):
        retiring = None
    for w in inventory.get('worktrees', []):
        if w['path'] not in c['builder']['slots']:
            continue
        branch = run(['git', 'branch', '--show-current'], w['path']).strip()
        match = health.REF.match(branch)
        if match:
            claims.append((match[1].upper(), w['path'], 'branch'))
        card = w.get('comment') or ''
        match = _card_ref(card)
        if match:
            claims.append((match[1].upper(), w['path'], 'card (leading ref)'))
    carries = state.read(c, 'carries', {})
    for receipt in unresolved(c, inventory):
        if receipt != retiring:
            claims.append((receipt['ref'], receipt['worktree']['path'], 'unresolved launch receipt'))
        for ref, approval in carries.get(receipt.get('id'), {}).items():
            if (approval.get('launch_id') == receipt.get('id') and
                    approval.get('slot') == receipt['worktree']['path'] and
                    approval.get('ref') == ref):
                claims.append((ref, approval['slot'], 'carried launch receipt'))
    for b in inventory.get('builders', []):
        exact_retiring = (retiring is not None and
                          b.get('path') == retiring['worktree']['path'] and
                          b.get('worktree') == retiring['worktree']['id'] and
                          b.get('terminal') == retiring.get('terminal') and
                          bool(retiring.get('terminal_incarnation_id')) and
                          b.get('incarnation_id') == retiring['terminal_incarnation_id'])
        if health.active(b) and b.get('task') and not exact_retiring:
            claims.append((b['task'], b['path'], 'active builder'))
    return claims


def duplicate_reasons(ref, claims, slot=None):
    return sorted({f'{ref} already assigned to {path} ({source})'
                   for task, path, source in claims if task == ref
                   and (path != slot or source not in ('branch', 'card (leading ref)'))})


def check_launch(c, ref, inventory, slot, *, retiring=None):
    item = pad(c, 'item', 'show', ref)
    statuses = status_policy(c)
    reasons = eligibility(c, item, statuses, continuing=True)
    reasons.extend(dependencies(c, ref, statuses))
    reasons.extend(duplicate_reasons(ref, assignments(c, inventory, retiring=retiring), slot))
    reasons.extend(receipt_reasons(c, inventory, slot, retiring=retiring))
    if reasons:
        raise ValueError('launch withheld: ' + '; '.join(reasons))
    return item


def propose(c):
    with state.lock(c, 'launcher', operation='next'):
        s = snapshot.collect(c)
        admission = snapshot.may_start(c, s)
        inventory = s.get('inventory', {})
        claims = assignments(c, inventory)
        statuses = status_policy(c)
        tasks = pad(c, 'item', 'list', 'tasks', '--all', '--full', '--limit', '1000')
        if len(tasks) >= 1000:
            raise ValueError('Pad inventory may be truncated; refusing partial proposal')
        ancestors = {item['ref']: item for item in tasks}
        slots = [{'path': path, 'reasons': slot_reasons(c, inventory, path)} for path in c['builder']['slots']]
        rows = []
        for item in tasks:
            f = fields(item)
            reasons = eligibility(c, item, statuses, cache=ancestors)
            reasons.extend(dependencies(c, item['ref'], statuses))
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

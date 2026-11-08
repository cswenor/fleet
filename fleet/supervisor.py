"""No model calls, no lane input. Durable inbox delivery is not an agent wake."""
from contextlib import nullcontext
import hashlib
import json
import math
import os
import re
import signal
import time
import urllib.request
from . import github, health, snapshot, state, governor
from .commands import orca, run, json_run, CommandError
from .config import URGENT_EVENTS


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def event_signature(event):
    if 'occurrence' in event:
        return digest([event['subject'], event['occurrence'], event.get('type', 'status')])
    content = [event['subject'], event['body'], event.get('branch')]
    if event.get('type', 'status') != 'status':
        content.append(event['type'])
    return digest(content)


def _completed_idle(c, builder):
    """Suppress only positively settled current attempts; uncertainty keeps alerts."""
    try:
        slot = builder['path']
        launch = state.read(c, 'launches', {}).get(slot, {})
        ref = launch.get('ref')
        if (not launch.get('id') or not isinstance(ref, str)
                or not re.fullmatch(r'[A-Z]+-\d+', ref)
                or launch.get('terminal') != builder['terminal']
                or not builder.get('incarnation_id')
                or launch.get('terminal_incarnation_id') != builder['incarnation_id']):
            return False
        task = state.read(c, 'threads-' + ref, {}).get(ref, {})
        attempt = task.get('attempts', {}).get(launch['id'], {})
        cache = c.setdefault('_completed_idle_launches', {})
        identity = (launch['id'], attempt.get('attempt_id', launch['id']))
        if cache.get(slot) == identity:
            return True
        cache.pop(slot, None)
        if (not task.get('id') or attempt.get('settled') is not True
                or not isinstance(attempt.get('result'), str) or not attempt['result'].strip()
                or attempt.get('terminal') != builder['terminal'] or attempt.get('slot') != slot):
            return False
        item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show',
                         ref, '--format', 'json'], c['root'])
        fields = item.get('fields', {})
        if isinstance(fields, str):
            fields = json.loads(fields)
        completed = fields.get('status') == 'done'
        if not completed:
            # Match the retirement publication/handoff convention, not prose alone.
            from . import restart
            comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item',
                                 'comments', ref, '--format', 'json'], c['root'])
            text = attempt['result'] + '\n' + '\n'.join(row['body'] for row in comments)
            refs = set(re.findall(r'(?<![\w-])[A-Z]+-\d+(?![\w-])', text)) - {ref}
            for next_ref in sorted(refs):
                try:
                    proof = restart._completion(c, next_ref, ref, launch)
                except ValueError:
                    continue
                if proof.get('handoff'):
                    completed = True
                    break
        if completed:
            # Process-local only. A replacement launch must earn its own proof.
            cache[slot] = identity
        return completed
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _ready_candidate(c, identity):
    """Re-read authority and ready receipts, including when completion is cached."""
    try:
        launch = state.read(c, 'launches', {}).get(identity['slot'], {})
        if any(launch.get(field) != identity[field] for field in
               ('id', 'ref', 'terminal', 'terminal_incarnation_id')):
            return False
        task = state.read(c, 'threads-' + identity['ref'], {}).get(identity['ref'], {})
        attempt = task.get('attempts', {}).get(identity['id'], {})
        if attempt.get('attempt_id', identity['id']) != identity.get('attempt_id', identity['id']):
            return False
        if attempt.get('ready', {}).get('message_id'):
            return False
        if (not task.get('id') or attempt.get('settled') is not True
                or not isinstance(attempt.get('result'), str) or not attempt['result'].strip()
                or attempt.get('slot') != identity['slot']
                or attempt.get('terminal') != identity['terminal']):
            return None
        return attempt
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return None


def missing_ready_reports(c, s, ledger, now):
    """Persist a timer and one queue identity per verified, exact launch."""
    timers = ledger.setdefault('missing_ready_reports', {})
    result = []
    for builder in (s.get('inventory') or {}).get('builders', []):
        try:
            slot = builder['path']
            launch = state.read(c, 'launches', {}).get(slot, {})
            if (not launch.get('id') or not re.fullmatch(r'[A-Z]+-\d+', launch.get('ref', ''))
                    or launch.get('terminal') != builder['terminal']
                    or not builder.get('incarnation_id')
                    or launch.get('terminal_incarnation_id') != builder['incarnation_id']):
                continue
            identity = {field: launch[field] for field in
                        ('id', 'ref', 'terminal', 'terminal_incarnation_id')}
            identity['slot'] = slot
            task = state.read(c, 'threads-' + launch['ref'], {}).get(launch['ref'], {})
            current = task.get('attempts', {}).get(launch['id'], {})
            identity['attempt_id'] = current.get('attempt_id', launch['id'])
            attempt = _ready_candidate(c, identity)
            if not attempt:
                continue
            parts = [slot, launch['id'], launch['terminal'], launch['terminal_incarnation_id']]
            if identity['attempt_id'] != launch['id']:
                parts.append(identity['attempt_id'])
            key = digest(parts)
            handoffs = c.setdefault('_verified_ready_handoffs', {})
            next_ref = handoffs.get(key)
            if not next_ref:
                from . import restart
                comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item',
                                     'comments', launch['ref'], '--format', 'json'], c['root'])
                text = attempt['result'] + '\n' + '\n'.join(row['body'] for row in comments)
                refs = set(re.findall(r'(?<![\w-])[A-Z]+-\d+(?![\w-])', text))
                for ref in sorted(refs):
                    try:
                        proof = restart._completion(c, ref, launch['ref'], launch, require_handoff=True)
                    except ValueError:
                        continue
                    if proof.get('handoff'):
                        next_ref = handoffs[key] = ref
                        break
            if not next_ref:
                continue
            settled_at = attempt.get('settled_at')
            valid_time = (isinstance(settled_at, (int, float)) and not isinstance(settled_at, bool)
                          and math.isfinite(settled_at) and 0 <= settled_at <= now)
            timer = timers.setdefault(key, {'since': settled_at if valid_time else now})
            minutes = c.get('builder', {}).get('ready_report_minutes', 15)
            if timer.get('queued') or now - timer['since'] < minutes * 60:
                continue
            result.append({'key': 'missing_ready:' + key, 'type': 'status',
                           'subject': f"Builder settled, no ready report: {launch['ref']}",
                           'body': f"{launch['terminal']} {slot}; settled {launch['ref']} has a verified "
                                   f"post-publication handoff to {next_ref}, but no ready report "
                                   f"after {minutes:g} minutes.",
                           'branch': None, 'missing_ready': identity})
            # Persisted with pending before delivery, also in --no-deliver mode.
            timer['queued'] = True
            timer['queued_at'] = now
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            # Unknown evidence must never change the ordinary health policy.
            continue
    return result



def alerts(s, c):
    result = []
    def add(key, subject, body):
        result.append({'key': key, 'subject': subject, 'body': body, 'branch': None})
    for error in s.get('errors', []) + s.get('inventory', {}).get('errors', []):
        add('error:' + error, 'Fleet collection error', error)
    for message in (s.get('github') or {}).get('unavailable', []):
        add('github-unknown:' + digest(message), 'GitHub optional data unknown', message)
    for b in s.get('inventory', {}).get('builders', []):
        for flag in b['flags']:
            if flag == 'idle' and _completed_idle(c, b):
                continue
            body = f'{b["terminal"]} {b["path"]}; model={b["model"] or "unknown"}; flag={flag}'
            if flag in ('stalled', 'progress_unknown'):
                progress = b.get('progress') or {}
                prs = b.get('owned_prs', [])
                body += (f'; task={progress.get("task") or b["task"]}; '
                         f'last JSONL event age={progress.get("event_age_seconds", "unknown")}s; '
                         f'UI={b.get("ui_state", "unknown")}; '
                         f'PRs/failed checks={json.dumps(prs, sort_keys=True)}')
            identity = digest([b['worktree'], b['terminal'], b.get('incarnation_id')])
            add(f'health:{identity}:{flag}', f'Builder {flag}: {b["task"] or b["title"]}', body)
            result[-1]['legacy_key'] = f'health:{b["worktree"]}:{flag}'
            if flag in ('stalled', 'progress_unknown'):
                # Ages change every cycle; only evidence changes or recovery re-alert.
                result[-1]['occurrence'] = [b['terminal'], b.get('incarnation_id'),
                                           progress.get('launch_id'), progress.get('last_event_at'),
                                           prs]
    for path in s.get('inventory', {}).get('stray_worktrees', []):
        add('stray:' + path, 'Stray project worktree', path)
    if not s.get('quota', {}).get('may_start', False):
        q = s.get('quota', {})
        add('quota:' + q.get('reason', 'unavailable'), 'Fleet quota reserve: new builders withheld',
            json.dumps(q, sort_keys=True) + ' ' + governor.CONSENT)
    if s.get('disk', {}).get('low'):
        add('disk', 'Fleet disk reserve reached', json.dumps(s['disk'], sort_keys=True))
    return result


def main_alerts(c, s, ledger, now):
    observation = s.get('coordinator')
    if observation is None:
        return []
    previous = ledger.get('main_liveness', {})
    identity = [observation['terminal'], observation.get('incarnation_id')]
    status = observation['state']
    if status == 'up':
        ledger.pop('main_liveness', None)
        return []
    if status == 'unknown' and identity[1] is None and previous.get('identity', [None])[0] == identity[0]:
        identity = previous['identity']
    same = previous.get('identity') == identity
    since = previous.get('since', now) if same else now
    # Unknown reads are not recovery. Before an alert, restart the threshold
    # rather than treating an observation gap as continuous shell/crash evidence.
    if status == 'unknown':
        if not previous.get('alerted'):
            since = now
        ledger['main_liveness'] = {**(previous if same else {}),
                                   'identity': identity, 'since': since,
                                   'unknown_since': previous.get('unknown_since', now) if same else now}
        if not previous.get('alerted') or not same:
            ledger['main_liveness']['status'] = 'unknown'
        kind = 'main_unknown'
    else:
        if not same or previous.get('status') not in ('suspect', 'down'):
            since = now
        ledger['main_liveness'] = {'identity': identity, 'since': since, 'status': status}
        if (status == 'suspect' and not (same and previous.get('alerted')) and
                now - since < c['coordinator'].get('liveness_minutes', 5) * 60):
            return []
        kind = 'main_down'
        ledger['main_liveness']['alerted'] = True
    minutes = max(0, now - since) / 60
    body = (f'{observation["terminal"]}; {observation["reason"]}; minutes down={minutes:.2f}; '
            f'evidence={observation.get("evidence", "")}; '
            f'recovery hint={observation.get("recovery_hint", "")}')
    recipients = ['run:' + c['coordinator']['run']]
    extra = c['coordinator'].get('escalation_run')
    if extra and 'run:' + extra not in recipients:
        recipients.append('run:' + extra)
    return [{'key': 'main:' + kind, 'subject': 'Main ' + kind, 'body': body,
             'branch': None, 'recipients': recipients,
             'occurrence': [identity, ledger['main_liveness'].get('unknown_since', since), kind]}]


def urgent_event(c, event):
    """Urgency is a wake advisory, never sender authentication or authority."""
    key = event['key']
    kind = None
    if key.startswith('health:'):
        kind = key.rsplit(':', 1)[-1]
    elif key.startswith('main:'):
        kind = key.split(':', 1)[1]
    elif key.startswith('stray:'):
        kind = 'stray_worktree'
    elif key.startswith('group:'):
        kind = 'merge_group_failure'
    elif key.startswith('pr_stuck:'):
        kind = 'pr_stuck'
    if kind in c.get('urgent', {}).get('events', URGENT_EVENTS):
        # Native Orca has no urgent type; question permits ordinary terminal senders.
        return {**event, 'type': 'question', 'subject': '[urgent] ' + event['subject']}
    return event


def stuck_pr_alerts(c, ledger, current, now):
    """Persist observed semantic progress, ignoring comments and updated_at."""
    timers = ledger.setdefault('pr_progress', {})
    active = {}
    result = []
    minutes = c.get('urgent', {}).get('pr_stuck_minutes', 45)
    for p in current['prs']:
        if p['state'] != 'open':
            continue
        key = str(p['number'])
        progress = {field: p.get(field) for field in ('state', 'head', 'merge_state')}
        for field in ('checks', 'reviews'):
            progress[field] = sorted(p.get(field, []), key=lambda row: json.dumps(row, sort_keys=True))
        progress['queue'] = [entry.get('state') for entry in current.get('merge_queue', [])
                             if entry.get('pullRequest', {}).get('number') == p['number']]
        signature = digest(progress)
        prior = timers.get(key)
        timer = prior if prior and prior['signature'] == signature else {'signature': signature, 'since': now}
        active[key] = timer
        if now - timer['since'] >= minutes * 60:
            result.append({'key': 'pr_stuck:' + key, 'subject': f'PR #{p["number"]} stuck',
                           'body': p['url'] + f'\nNo state/check/review progress for {minutes:g} minutes.',
                           'branch': p['branch'], 'pr': p['number']})
    ledger['pr_progress'] = active
    return result


def enqueue(c, event, recipient):
    prefix = 'DRILL ' if c.get('_drill_usage_file') else ''
    if recipient.startswith('pad:'):
        return run(['pad', '--workspace', c['pad_workspace'], 'item', 'comment', recipient[4:],
                    prefix + event['body']], c['root'])
    builder_park = event.get('park') and recipient == event['recipients'][0]
    # Serialize final identity check + enqueue against every Fleet launch/reuse.
    with state.lock(c, 'launcher') if builder_park else nullcontext():
        if builder_park:
            attempt = event['attempt']
            t = orca(c, 'terminal', 'show', '--terminal', recipient)['terminal']
            screen = orca(c, 'terminal', 'read', '--terminal', recipient, '--screen')['terminal']
            current = {'terminal': recipient, 'path': attempt['path'], 'task': attempt['ref'],
                       'incarnation_id': t.get('incarnationId')}
            if (not t.get('connected') or t.get('orphaned') or not t.get('writable')
                    or t.get('worktreeId') != attempt['worktree']
                    or health.shell_ready('\n'.join(screen.get('tail', [])))
                    or not governor.same_attempt(c, attempt, [current])):
                raise CommandError('park owner identity unavailable or changed')
        # Explicit sender permits a systemd process. Never inject terminal input.
        target_run = (recipient[4:] if event['key'].startswith('main:') and recipient.startswith('run:')
                      else c['coordinator']['run'])
        return orca(c, 'orchestration', 'send', '--from', c['coordinator']['terminal'],
                    '--to', recipient, '--run', target_run, '--type', event.get('type', 'status'),
                    '--subject', prefix + event['subject'], '--body', prefix + f"[fleet {c['repo']}] {event['body']}")


def push(c, s, receipt):
    config = c['status']
    if c.get('_drill_usage_file'):
        return receipt
    url = config['url']
    token = os.environ.get(config['token_env'], '') if config['token_env'] else ''
    if not url or not token:
        return receipt
    # Only send credentials over TLS; never follow redirects carrying Authorization.
    if not url.startswith('https://'):
        raise ValueError('status.url must use https')
    comparable = {k: v for k, v in s.items() if k != 'generated_at'}
    signature = digest(comparable)
    now = time.time()
    if receipt.get('signature') == signature and now - receipt.get('at', 0) < config['push_seconds']:
        return receipt
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    req = urllib.request.Request(url, data=json.dumps(s).encode(), headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise ValueError('snapshot push rejected')
    return {'signature': signature, 'at': now}


def github_relays(ledger, events, current):
    """Collapse identical consecutive content per GitHub transition stream.

    Retain the last identity, not a forever-seen content set: A→B→A is new.
    Pending retries share that identity and keep per-recipient receipts.
    """
    streams = ledger.setdefault('github_relays', {})
    # Recovery emits no group relay, but ends this failure occurrence.
    failed_groups = {
        f'group:{r["id"]}:{r["conclusion"]}:{number}'
        for r in current.get('merge_group_runs', [])
        if r['conclusion'] in ('failure', 'cancelled', 'timed_out', 'action_required')
        for number in r.get('prs', []) or [None]}
    for scope in list(streams):
        if scope.startswith('group:') and scope not in failed_groups:
            del streams[scope]
    result = []
    for event in events:
        scope = ':'.join(event['key'].split(':')[:3]) if event['key'].startswith('pr:') else event['key']
        signature = event_signature(event)
        previous = streams.get(scope)
        if previous and previous['signature'] == signature:
            continue
        event = {**event, 'key': event['key'] + ':relay:' + str(time.time_ns())}
        streams[scope] = {'signature': signature}
        result.append(event)
    return result


def cycle(c, deliver=True):
    s = snapshot.collect(c)
    ledger = state.read(c, 'supervisor', {'github': {}, 'pending': [], 'sent': {}, 'active_alerts': []})
    pending = ledger['pending']
    current_g = s.get('github')
    budget = s.get('github_budget', {})
    if budget.get('blocked') and ledger.get('github_budget_window') != budget.get('reset'):
        reset = budget['reset']
        pending.append({'key': f'github-budget:{reset}', 'subject': 'github budget low',
                        'body': f'GitHub reads deferred until {reset}; terminal and quota health continue.',
                        'pr': None, 'branch': None})
        ledger['github_budget_window'] = reset
    pr_alerts = []
    if current_g is not None:
        if current_g.get('merge_group_unknown'):
            # Missing permission is not supersession or recovery evidence.
            current_g['merge_group_runs'] = ledger['github'].get('merge_group_runs', [])
        changes = github.events(ledger['github'], current_g)
        seen = set(ledger.get('seen_prs', []))
        initial = [github.initial_event(p) for p in current_g['prs'] if p['number'] not in seen]
        initial = [e for e in initial if e is not None]
        diagnosed = {e['pr'] for e in initial}
        # A saved baseline from before initial-state support is not a notification receipt.
        changes = [e for e in changes if not (e.get('pr') in diagnosed and e['key'].startswith('pr:'))]
        pending.extend(github_relays(ledger, [urgent_event(c, e) for e in changes + initial], current_g))
        pr_alerts = stuck_pr_alerts(c, ledger, current_g, time.time())
        ledger['seen_prs'] = sorted(seen | {p['number'] for p in current_g['prs']})
        ledger['github'] = current_g
    pending.extend(governor.advance(c, s, ledger))
    s['governor'] = ledger['governor']
    active = alerts(s, c) + pr_alerts + main_alerts(c, s, ledger, time.time())
    if current_g is None:
        # Unknown collection is not recovery: keep stuck occurrences deduplicated.
        active_stuck = {key: value for key, value in ledger.get('active_alerts', {}).items()
                        if key.startswith('pr_stuck:')} if isinstance(ledger.get('active_alerts'), dict) else {}
    else:
        active_stuck = {}
    if s.get('coordinator', {}).get('state') == 'unknown':
        prior_main = ledger.get('active_alerts', {})
        if isinstance(prior_main, dict):
            active_stuck.update({key: value for key, value in prior_main.items()
                                 if key == 'main:main_down'})
    known_quota = s.get('quota', {}).get('known', False)
    ledger['quota_unknown_cycles'] = 0 if known_quota else ledger.get('quota_unknown_cycles', 0) + 1
    if not known_quota and ledger['quota_unknown_cycles'] < c.get('quota', {}).get('unknown_alert_cycles', 3):
        # Keep collection diagnostics in the snapshot/admission gate, but do not
        # let the quota exception path bypass the unknown-quota alert debounce.
        active = [a for a in active if not a['key'].startswith('quota:')
                  and a['key'] != 'error:quota collection unavailable']
    active = [urgent_event(c, a) for a in active]
    prior = ledger['active_alerts']
    # Old ledgers held only keys; migrate by reporting their current content once.
    prior = prior if isinstance(prior, dict) else {}
    missing = ledger.get('health_absent_cycles', {})
    next_active = dict(active_stuck)
    legacy_keys = set()
    for a in active:
        signature = event_signature(a)
        legacy_key = a.pop('legacy_key', None)
        previous = prior.get(a['key'])
        if legacy_key:
            legacy_keys.add(legacy_key)
            if previous is None:
                previous = prior.get(legacy_key)
        if previous != signature:
            # Content changes and every recovery→recurrence receive a new identity.
            pending.append({**a, 'key': a['key'] + ':' + str(time.time_ns())})
        next_active[a['key']] = signature
    next_missing = {}
    recovery_cycles = c.get('builder', {}).get('alert_recovery_cycles', 2)
    for key, signature in prior.items():
        if key.startswith('health:') and key not in next_active and key not in legacy_keys:
            absent = missing.get(key, 0) + 1
            if absent < recovery_cycles:
                next_active[key] = signature
                next_missing[key] = absent
    ledger['active_alerts'] = next_active
    ledger['health_absent_cycles'] = next_missing
    pending.extend(missing_ready_reports(c, s, ledger, time.time()))
    state.write(c, 'snapshot', s)
    state.write(c, 'supervisor', ledger)
    inventory = s.get('inventory')
    remaining = []
    failures = []
    protected = set()
    for event in pending:
        if event.get('missing_ready'):
            candidate = _ready_candidate(c, event['missing_ready'])
            if candidate is False:
                # Ready receipts and replacement launches invalidate queued fallbacks.
                continue
            if candidate is None:
                remaining.append(event)
                protected.update(digest([event['key'], r]) for r in
                                 event.get('recipients', ['run:' + c['coordinator']['run']]))
                continue
        recipients = list(event.get('recipients', ['run:' + c['coordinator']['run']]))
        owning_terminal = health.owner(inventory, event['branch']) if inventory and event.get('branch') else None
        if owning_terminal:
            recipients.append(owning_terminal)
        for recipient in recipients:
            key = digest([event['key'], recipient])
            if key in ledger['sent']:
                continue
            if event.get('park') and recipient == event['recipients'][0]:
                if not governor.deliverable(c, event, s, ledger):
                    # Durable Run/Pad copies retry even after exit/recovery/replacement.
                    active_park = ledger['governor']['active']
                    obsolete = not active_park or active_park['key'] != event['park']
                    if obsolete:
                        ledger['sent'][key] = {'at': time.time(), 'suppressed': 'obsolete builder attempt'}
                    continue
            if not deliver:
                continue
            try:
                receipt = enqueue(c, event, recipient)
                ledger['sent'][key] = {'at': time.time(), 'receipt': receipt}
                state.write(c, 'supervisor', ledger)
            except (CommandError, ValueError) as error:
                failures.append({'recipient': recipient,
                                 'code': error.code if isinstance(error, CommandError) else 'delivery_failed'})
        # Unknown inventory is a transient lookup failure, not proof there is no owner.
        resolved = inventory is not None or not event.get('branch')
        done = resolved and all(digest([event['key'], r]) in ledger['sent'] for r in recipients)
        if not done:
            remaining.append(event)
            protected.update(digest([event['key'], r]) for r in recipients)
        elif c['hooks'].get('event'):
            run(c['hooks']['event'], c['root'])
    ledger['pending'] = remaining
    # Pending events retain successful-recipient receipts through arbitrarily long outages.
    cutoff = time.time() - 7 * 86400
    ledger['sent'] = {k: v for k, v in ledger['sent'].items() if k in protected or v['at'] > cutoff}
    try:
        ledger['push'] = push(c, s, ledger.get('push', {}))
    except Exception:
        # External endpoint availability never disables local supervision; redact URL/token/errors.
        failures.append('snapshot push unavailable')
    state.write(c, 'supervisor', ledger)
    report = {'pending': len(remaining), 'delivery_failures': failures, 'delivery_enabled': deliver}
    if c.get('_github_debug'):
        report['github_requests'] = s.get('github_requests', {})
    return s, report


def serve(c, deliver=True, once=False):
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with state.lock(c, 'supervisor'):
        while not stopping:
            _, report = cycle(c, deliver)
            print(json.dumps(report), flush=True)
            if once:
                break
            end = time.monotonic() + c['interval_seconds']
            while not stopping and time.monotonic() < end:
                time.sleep(min(1, max(0, end - time.monotonic())))

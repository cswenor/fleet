"""No model calls, no lane input. Durable inbox delivery is not an agent wake."""
from contextlib import nullcontext
import hashlib
import json
import os
import signal
import time
import urllib.request
from . import github, health, snapshot, state, governor
from .commands import orca, run, CommandError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def alerts(s):
    result = []
    def add(key, subject, body):
        result.append({'key': key, 'subject': subject, 'body': body, 'branch': None})
    for error in s.get('errors', []) + s.get('inventory', {}).get('errors', []):
        add('error:' + error, 'Fleet collection error', error)
    for b in s.get('inventory', {}).get('builders', []):
        for flag in b['flags']:
            add(f'health:{b["worktree"]}:{flag}', f'Builder {flag}: {b["task"] or b["title"]}',
                f'{b["terminal"]} {b["path"]}; model={b["model"] or "unknown"}; flag={flag}')
    for path in s.get('inventory', {}).get('stray_worktrees', []):
        add('stray:' + path, 'Stray project worktree', path)
    if not s.get('quota', {}).get('may_start', False):
        q = s.get('quota', {})
        add('quota:' + q.get('reason', 'unavailable'), 'Fleet quota reserve: new builders withheld',
            json.dumps(q, sort_keys=True) + ' ' + governor.CONSENT)
    if s.get('disk', {}).get('low'):
        add('disk', 'Fleet disk reserve reached', json.dumps(s['disk'], sort_keys=True))
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
        return orca(c, 'orchestration', 'send', '--from', c['coordinator']['terminal'],
                    '--to', recipient, '--run', c['coordinator']['run'], '--type', 'status',
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


def cycle(c, deliver=True):
    s = snapshot.collect(c)
    ledger = state.read(c, 'supervisor', {'github': {}, 'pending': [], 'sent': {}, 'active_alerts': []})
    pending = ledger['pending']
    current_g = s.get('github')
    if current_g is not None:
        changes = github.events(ledger['github'], current_g)
        seen = set(ledger.get('seen_prs', []))
        initial = [github.initial_event(p) for p in current_g['prs'] if p['number'] not in seen]
        initial = [e for e in initial if e is not None]
        diagnosed = {e['pr'] for e in initial}
        # A saved baseline from before initial-state support is not a notification receipt.
        changes = [e for e in changes if not (e.get('pr') in diagnosed and e['key'].startswith('pr:'))]
        pending.extend(changes + initial)
        ledger['seen_prs'] = sorted(seen | {p['number'] for p in current_g['prs']})
        ledger['github'] = current_g
    pending.extend(governor.advance(c, s, ledger))
    s['governor'] = ledger['governor']
    active = alerts(s)
    prior = set(ledger['active_alerts'])
    for a in active:
        if a['key'] not in prior:
            # Each recovery→recurrence receives a new identity.
            a['key'] += ':' + str(time.time_ns())
            pending.append(a)
    ledger['active_alerts'] = [a['key'] for a in alerts(s)]
    state.write(c, 'snapshot', s)
    state.write(c, 'supervisor', ledger)
    inventory = s.get('inventory')
    remaining = []
    failures = []
    protected = set()
    for event in pending:
        recipients = event.get('recipients', ['run:' + c['coordinator']['run']])
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
            except (CommandError, ValueError):
                failures.append(event['subject'])
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
    return s, {'pending': len(remaining), 'delivery_failures': failures, 'delivery_enabled': deliver}


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

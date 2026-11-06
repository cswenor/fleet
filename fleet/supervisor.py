"""No model calls, no lane input. Durable inbox delivery is not an agent wake."""
import hashlib
import json
import os
import signal
import time
import urllib.request
from . import github, health, snapshot, state
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
        add('quota', 'Fleet quota reserve: new builders withheld', json.dumps(q, sort_keys=True))
    if s.get('disk', {}).get('low'):
        add('disk', 'Fleet disk reserve reached', json.dumps(s['disk'], sort_keys=True))
    return result


def enqueue(c, event, recipient):
    # Explicit sender permits a non-interactive systemd process. Never inject terminal input.
    return orca(c, 'orchestration', 'send', '--from', c['coordinator']['terminal'],
                '--to', recipient, '--run', c['coordinator']['run'], '--type', 'status',
                '--subject', event['subject'], '--body', f"[fleet {c['repo']}] {event['body']}")


def push(c, s, receipt):
    config = c['status']
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
        pending.extend(github.events(ledger['github'], current_g))
        ledger['github'] = current_g
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
    for event in pending:
        recipients = ['run:' + c['coordinator']['run']]
        owning_terminal = health.owner(inventory, event['branch']) if inventory and event.get('branch') else None
        if owning_terminal:
            recipients.append(owning_terminal)
        for recipient in recipients:
            key = digest([event['key'], recipient])
            if key in ledger['sent']:
                continue
            if not deliver:
                continue
            try:
                receipt = enqueue(c, event, recipient)
                ledger['sent'][key] = {'at': time.time(), 'receipt': receipt}
                state.write(c, 'supervisor', ledger)
            except (CommandError, ValueError):
                failures.append(event['subject'])
        # Retain until coordinator delivered. Missing/ambiguous owner is explicit coordinator-only.
        done = all(digest([event['key'], r]) in ledger['sent'] for r in recipients)
        if not done:
            remaining.append(event)
        elif c['hooks'].get('event'):
            run(c['hooks']['event'], c['root'])
    ledger['pending'] = remaining
    # Keep receipts long enough to dedupe recent GitHub history, bound private state size.
    cutoff = time.time() - 7 * 86400
    ledger['sent'] = {k: v for k, v in ledger['sent'].items() if v['at'] > cutoff}
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

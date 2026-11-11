"""Per-project supervision with journaled, positively idle inbox/decision wakes."""
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import shlex
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


def _lane_free_candidate(c, builder):
    """Require positive idle, exact canonical settlement and finished-work proof."""
    try:
        flags = builder.get('flags', [])
        if (not builder.get('connected') or
                any('stalled' in flag or 'blocked' in flag for flag in flags) or
                not (builder.get('shell_ready') or builder.get('ui_state') == 'idle')):
            return None
        slot = builder['path']
        launch = state.read(c, 'launches', {}).get(slot, {})
        if (not launch.get('id') or not re.fullmatch(r'[A-Z]+-\d+', launch.get('ref', ''))
                or launch.get('terminal') != builder['terminal']
                or not builder.get('incarnation_id')
                or launch.get('terminal_incarnation_id') != builder['incarnation_id']):
            return None
        identity = {field: launch[field] for field in
                    ('id', 'ref', 'terminal', 'terminal_incarnation_id')}
        identity['slot'] = slot
        task = state.read(c, 'threads-' + launch['ref'], {}).get(launch['ref'], {})
        attempt = task.get('attempts', {}).get(launch['id'], {})
        identity['attempt_id'] = attempt.get('attempt_id', launch['id'])
        if (not task.get('id') or attempt.get('settled') is not True
                or not isinstance(attempt.get('result'), str) or not attempt['result'].strip()
                or attempt.get('terminal') != builder['terminal'] or attempt.get('slot') != slot):
            return None
        cache = c.setdefault('_lane_free_completions', {})
        proof_key = (launch['id'], identity['attempt_id'], launch['terminal_incarnation_id'])
        if cache.get(slot) != proof_key:
            cache.pop(slot, None)
            from . import restart
            if not restart._finished_completion(c, launch['ref'], launch):
                return None
            cache[slot] = proof_key
        return identity
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return None


def _lane_free_reported(c, identity):
    """Pending/ambiguous reports also own the decision and forbid a second send."""
    try:
        task = state.read(c, 'threads-' + identity['ref'], {}).get(identity['ref'], {})
        from .threads import _launch_report
        return (bool(_launch_report(task, identity['id'], 'ready'))
                or bool(_launch_report(task, identity['id'], 'needs_next'))
                or identity['id'] in state.read(c, 'needs-next', {}))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return None


def lane_free_reports(c, candidates, ledger):
    """Persist launch-level dedupe separately from expiring transport receipts."""
    queued = ledger.setdefault('lane_free_reports', {})
    result = []
    for identity in candidates.values():
        key = digest([identity['slot'], identity['id'], identity['terminal_incarnation_id']])
        if key in queued or _lane_free_reported(c, identity) is not False:
            continue
        result.append({'key': 'lane_free:' + key, 'type': 'question',
                       'subject': f"Lane free: {identity['slot']} finished {identity['ref']}; needs next task",
                       'body': f"{identity['terminal']} finished {identity['ref']} in "
                               f"{identity['slot']}. The canonical attempt is settled and "
                               "the builder is positively idle. Decide the next task or retirement.",
                       'branch': None, 'lane_free': identity})
        queued[key] = identity
    return result


def missing_ready_reports(c, s, ledger, now, lane_free=None):
    """Persist a timer and one queue identity per verified, exact launch."""
    timers = ledger.setdefault('missing_ready_reports', {})
    result = []
    for builder in (s.get('inventory') or {}).get('builders', []):
        try:
            slot = builder['path']
            if slot in (lane_free or {}):
                continue
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



def alerts(s, c, lane_free=None):
    result = []
    def add(key, subject, body):
        result.append({'key': key, 'subject': subject, 'body': body, 'branch': None})
    for error in s.get('errors', []) + s.get('inventory', {}).get('errors', []):
        add('error:' + error, 'Fleet collection error', error)
    for message in (s.get('github') or {}).get('unavailable', []):
        add('github-unknown:' + digest(message), 'GitHub optional data unknown', message)
    for b in s.get('inventory', {}).get('builders', []):
        launch_at = b.get('launch_at')
        observed_at = s.get('generated_at')
        starting = (isinstance(launch_at, (int, float)) and not isinstance(launch_at, bool)
                    and isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool)
                    and math.isfinite(launch_at) and math.isfinite(observed_at)
                    and launch_at > 0 and 0 <= observed_at - launch_at < 120)
        for flag in b['flags']:
            if starting and flag in ('model_unknown', 'progress_unknown'):
                continue
            if flag == 'idle' and (b['path'] in (lane_free or {}) or _completed_idle(c, b)):
                continue
            body = f'{b["terminal"]} {b["path"]}; model={b["model"] or "unknown"}; flag={flag}'
            if flag in ('model_unknown', 'progress_unknown'):
                observed = (datetime.fromtimestamp(observed_at, timezone.utc).isoformat()
                            if isinstance(observed_at, (int, float))
                            and not isinstance(observed_at, bool)
                            and math.isfinite(observed_at) else 'unknown')
                body += f'; observed at {observed}'
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
            if flag == 'model_unknown':
                # Delivery can be delayed; observation time is not new evidence.
                result[-1]['occurrence'] = [b['terminal'], b.get('incarnation_id'),
                                           b.get('launch_id'), b.get('ui_state')]
            if flag in ('stalled', 'progress_unknown'):
                # Ages change every cycle; only evidence changes or recovery re-alert.
                result[-1]['occurrence'] = [b['terminal'], b.get('incarnation_id'),
                                           progress.get('launch_id') or b.get('launch_id'),
                                           progress.get('last_event_at'),
                                           prs]
    for path in s.get('inventory', {}).get('stray_worktrees', []):
        if path in s['inventory'].get('expected_worktrees', []):
            add('expected:' + path, 'expected scratch worktree: ' + path, path)
        else:
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
        unknown_cycles = previous.get('unknown_cycles', 0) + 1 if same else 1
        ledger['main_liveness']['unknown_cycles'] = unknown_cycles
        if not previous.get('alerted') or not same:
            ledger['main_liveness']['status'] = 'unknown'
        if unknown_cycles < c['coordinator'].get('unknown_alert_cycles', 3):
            return []
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
    elif key.startswith('group:') and not event.get('superseded_cancelled'):
        kind = 'merge_group_failure'
    elif key.startswith('pr_stuck:'):
        kind = 'pr_stuck'
    if kind in c.get('urgent', {}).get('events', URGENT_EVENTS):
        # Native Orca has no urgent type; question permits ordinary terminal senders.
        return {**event, 'type': 'question', 'subject': '[urgent] ' + event['subject']}
    return event


def _stuck_pr_paused(c, p):
    parked = c.get('github', {}).get('parked_labels', ['parked'])
    return p.get('draft', False) or any(
        label.casefold() == configured.casefold()
        for label in p.get('labels', []) for configured in parked)


def _stuck_queue_entries(current, number):
    entries = current.get('merge_queue', [])
    for index, entry in enumerate(entries):
        if entry.get('pullRequest', {}).get('number') == number:
            # A waiting entry without its own group depends on the queue ahead.
            return [entry] if entry.get('runs') else entries[:index + 1]
    return []


def _stuck_progress_unknown(current, number):
    return current.get('merge_queue_unknown', False) or any(
        entry.get('progress_unknown', False)
        for entry in _stuck_queue_entries(current, number))


def stuck_pr_alerts(c, ledger, current, now):
    """Persist observed semantic progress, ignoring comments and updated_at."""
    timers = ledger.setdefault('pr_progress', {})
    active = {}
    result = []
    minutes = c.get('urgent', {}).get('pr_stuck_minutes', 45)
    for p in current['prs']:
        if p['state'] != 'open' or _stuck_pr_paused(c, p):
            continue
        key = str(p['number'])
        if _stuck_progress_unknown(current, p['number']):
            if key in timers:
                active[key] = timers[key]
            continue
        progress = {field: p.get(field) for field in ('state', 'head', 'merge_state')}
        for field in ('checks', 'reviews'):
            progress[field] = sorted(p.get(field, []), key=lambda row: json.dumps(row, sort_keys=True))
        queue = _stuck_queue_entries(current, p['number'])
        progress['queue'] = [
            {field: entry.get(field) for field in ('state', 'headCommit', 'runs', 'pullRequest')}
            for entry in queue]
        signature = digest(progress)
        prior = timers.get(key)
        timer = prior if prior and prior['signature'] == signature else {'signature': signature, 'since': now}
        if any(run.get('status') in ('queued', 'in_progress')
               for entry in queue[:-1] for run in entry.get('runs', [])):
            timer = {'signature': signature, 'since': now}
        active[key] = timer
        if now - timer['since'] >= minutes * 60:
            result.append({'key': 'pr_stuck:' + key, 'subject': f'PR #{p["number"]} stuck',
                           'body': p['url'] + f'\nNo state/check/review progress for {minutes:g} minutes.',
                           'branch': p['branch'], 'pr': p['number']})
    ledger['pr_progress'] = active
    return result


def enqueue(c, event, recipient):
    # PARK is task-addressed mail: prevent retirement/reuse between proof and
    # delivery just as for builder wake, without acquiring the global launcher.
    lease = (state.slot_lock(c, event['attempt']['path'])
             if event.get('park') and recipient == event['recipients'][0]
             else nullcontext())
    with lease:
        return _enqueue(c, event, recipient)


def _enqueue(c, event, recipient):
    prefix = 'DRILL ' if c.get('_drill_usage_file') else ''
    if recipient.startswith('pad:'):
        return run(['pad', '--workspace', c['pad_workspace'], 'item', 'comment', recipient[4:],
                    prefix + event['body']], c['root'])
    builder_park = event.get('park') and recipient == event['recipients'][0]
    if builder_park:
        attempt = event['attempt']
        t = orca(c, 'terminal', 'show', '--terminal', recipient)['terminal']
        screen = orca(c, 'terminal', 'read', '--terminal', recipient, '--screen')['terminal']
        current = {'terminal': recipient, 'path': attempt['path'], 'task': attempt['ref'],
                   'incarnation_id': t.get('incarnationId')}
        if (not t.get('connected') or t.get('orphaned') or not t.get('writable')
                or t.get('worktreeId') != attempt['worktree'] or health.shell_ready(screen)):
            raise CommandError('park owner identity unavailable or changed')
    # Lane-free reservations use only file operations under launcher/needs-next.
    # PARK ownership is protected throughout by enqueue's per-slot lease.
    with (state.lock(c, 'launcher', operation='supervisor enqueue reserve') if event.get('lane_free') else nullcontext(),
          state.lock(c, 'needs-next', wait_seconds=5, operation='supervisor next reserve') if event.get('lane_free') else nullcontext()):
        if event.get('lane_free'):
            identity = event['lane_free']
            if recipient != 'run:' + c['coordinator']['run']:
                raise ValueError('lane-free notice must target the coordinator Run')
            if not _ready_candidate(c, identity):
                raise CommandError('lane-free launch or report changed')
            notices = state.read(c, 'needs-next', {})
            notice = notices.get(identity['id'])
            if notice:
                if not notice.get('message_id'):
                    raise ValueError('ambiguous lane-free send; reconcile native inbox; never resend')
                return {'deduplicated': True, 'report': notice}
            if _lane_free_reported(c, identity) is not False:
                raise CommandError('lane-free launch or report changed')
        if builder_park and not governor.same_attempt(c, attempt, [current]):
            raise CommandError('park owner identity unavailable or changed')
        if event.get('lane_free'):
            notice = {'ref': identity['ref'], 'slot': identity['slot'],
                      'channel': 'supervisor', 'status': 'sending',
                      'body': prefix + f"[fleet {c['repo']}] {event['body']}"}
            notices[identity['id']] = notice
            # Reserve before native I/O; ambiguous sends still forbid duplicates.
            state.write(c, 'needs-next', notices)
    # Explicit sender permits a systemd process. Never inject terminal input.
    target_run = (recipient[4:] if event['key'].startswith('main:') and recipient.startswith('run:')
                  else c['coordinator']['run'])
    receipt = orca(c, 'orchestration', 'send', '--from', c['coordinator']['terminal'],
                   '--to', recipient, '--run', target_run, '--type', event.get('type', 'status'),
                   '--subject', prefix + event['subject'], '--body', prefix + f"[fleet {c['repo']}] {event['body']}")
    if event.get('lane_free'):
        message_id = receipt.get('message', {}).get('id')
        if not isinstance(message_id, str) or not message_id:
            raise ValueError('ambiguous lane-free response; reconcile native inbox; never resend')
        from .threads import _record_next_report
        notice.update(message_id=message_id, status='sent', at=time.time())
        try:
            _record_next_report(c, identity['id'], notice)
        except ValueError:
            # The native ID is known: journal it now, never repeat the send.
            pending = {identity['id']: notice}
            ledger = state.read(c, 'supervisor', {'github': {}, 'pending': [], 'sent': {}, 'active_alerts': []})
            ledger.setdefault('pending_receipts', {}).update(pending)
            state.write(c, 'supervisor', ledger)
            receipt['pending_receipts'] = pending
    return receipt


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


def _group_alerts(events, current):
    """Downgrade only positively superseded cancellations, once per run."""
    if 'merge_queue' not in current or current.get('merge_queue_unknown'):
        return events
    heads = {(entry.get('headCommit') or {}).get('oid')
             for entry in current['merge_queue']}
    superseded = {
        str(run['id']): run for run in current.get('merge_group_runs', [])
        if run['conclusion'] == 'cancelled' and run.get('head_sha')
        and run['head_sha'] not in heads}
    result = []
    seen = set()
    for event in events:
        parts = event['key'].split(':')
        run = (superseded.get(parts[1])
               if parts[0] == 'group' and parts[2] == 'cancelled' else None)
        if run:
            if run['id'] in seen:
                continue
            seen.add(run['id'])
            event = {**event, 'key': f'group:{run["id"]}:cancelled:superseded',
                     'subject': f'Merge group {run["id"]} superseded, cancelled',
                     'body': run['url'] + '\nsuperseded, cancelled',
                     'branch': None, 'pr': None, 'type': 'status',
                     'superseded_cancelled': True}
        result.append(event)
    return result


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
    failed_groups.update(
        f'group:{r["id"]}:cancelled:superseded'
        for r in current.get('merge_group_runs', []) if r['conclusion'] == 'cancelled')
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


def wake_main(c, ledger, now, deliver):
    """Peek only. Journal before input so ambiguous sends cannot cause rapid repeats."""
    coordinator = c['coordinator']
    if not coordinator.get('wake', True) or not coordinator.get('terminal'):
        return []
    from .orca_capture import _mailbox
    try:
        mailbox = _mailbox(lambda label, *args: orca(c, *args), coordinator['run'])
        if not mailbox['valid']:
            return []
        # fleet wait classifies native question/escalation as decisions or urgent;
        # ordinary status relays must not affect age, cooldown or relay dedupe.
        messages = [m for m in mailbox['messages'] if m.get('type') in ('question', 'escalation')]
        if not messages:
            return []
        dated = []
        for message in messages:
            created = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
            if created.tzinfo is None:
                return []
            dated.append((created.timestamp(), message))
        oldest_at, oldest = min(dated, key=lambda row: (row[0], row[1]['id']))
        if not math.isfinite(oldest_at) or now - oldest_at < coordinator.get('wake_after_seconds', 120):
            return []
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        return []
    signature = digest([coordinator['run'], sorted(m['id'] for m in messages)])
    journal = ledger.setdefault('main_wakes', {'attempts': [], 'unwakeable': []})
    try:
        if mailbox['run'].get('coordinator_handle') != coordinator['terminal']:
            raise ValueError('Main Run binding unproven')
        proof = health.main_wake_proof(c)
    except ValueError as exc:
        # Only our fixed proof diagnostics can enter the relay, not native output.
        reason = str(exc) if str(exc) in (
            'Main terminal identity unproven', 'Main Run binding unproven',
            'Main pane inventory incomplete', 'Main exact pane is not idle',
            'Main screen unavailable', 'Main terminal identity changed',
            *(f'Main composer state: {ui}' for ui in
              ('draft', 'working', 'dialog', 'background', 'unknown'))
        ) else 'Main observation unavailable'
    except (RuntimeError, KeyError, TypeError, AttributeError):
        reason = 'Main observation unavailable'
    else:
        previous = [w['at'] for w in journal['attempts'] if w['messages'] == signature]
        if not deliver or (previous and now - max(previous) < coordinator.get('wake_cooldown_seconds', 600)):
            return []
        since = datetime.fromtimestamp(oldest_at, timezone.utc).isoformat()
        attempt = {'at': now, 'run': coordinator['run'], 'oldest_unread_id': oldest['id'],
                   'messages': signature, 'proof': proof, 'status': 'attempted'}
        journal['attempts'].append(attempt)
        journal['attempts'] = journal['attempts'][-200:]
        state.write(c, 'supervisor', ledger)
        try:
            attempt['receipt'] = orca(
                c, 'terminal', 'send', '--terminal', coordinator['terminal'], '--text',
                f'fleet: unread messages on Run {coordinator["run"]} since {since}; '
                'check your inbox (fleet wait / orchestration check) and act on them.', '--enter')
            attempt['status'] = 'sent'
        except (RuntimeError, ValueError):
            attempt['status'] = 'unknown'
        state.write(c, 'supervisor', ledger)
        return []
    extra = coordinator.get('escalation_run')
    if (not extra or signature in journal['unwakeable'] or
            now - oldest_at < coordinator.get('unwakeable_after_seconds', 900)):
        return []
    journal['unwakeable'].append(signature)
    journal['unwakeable'] = journal['unwakeable'][-200:]
    return [{'key': 'main:main_unread_unwakeable:' + signature,
             'subject': 'Main main_unread_unwakeable', 'type': 'status', 'branch': None,
             'recipients': ['run:' + extra],
             'body': f'Run {coordinator["run"]}; oldest unread {oldest["id"]}; {reason}'}]


def wake_builders(c, ledger, now, deliver):
    """Wake only current pending decisions with native reply and exact idle proof."""
    if not c['coordinator'].get('builder_wake', True):
        return []
    from . import threads
    events = []
    try:
        with state.lock(c, 'launcher', operation='supervisor builder wake snapshot'):
            launches = state.read(c, 'launches', {})
    except (RuntimeError, ValueError, OSError):
        return []
    for slot in c.get('builder', {}).get('slots', []):
        launch = launches.get(slot, {})
        if not launch.get('id') or not launch.get('terminal'):
            continue
        try:
            refs = [launch.get('ref')]
            refs.extend(state.read(c, 'carries', {}).get(launch['id'], {}))
            for ref in dict.fromkeys(refs):
                if not isinstance(ref, str) or not re.fullmatch(r'[A-Z]+-\d+', ref):
                    continue
                with state.slot_lock(c, slot):
                    with state.lock(c, 'threads-' + ref, wait_seconds=0):
                        # Snapshot only file-backed state under the task lock.
                        latest = state.read(c, 'launches', {}).get(slot, {})
                        if any(latest.get(field) != launch.get(field)
                               for field in ('id', 'terminal', 'terminal_incarnation_id')):
                            continue
                        question = next((q for r, q in threads.pending_questions(c, slot, latest)
                                         if r == ref), None)
                        if question is None:
                            continue
                    # The slot lease excludes restart throughout native observation,
                    # revalidation and send; task writers remain free to settle.
                    reply = threads.decision_reply(c, launch, question)
                    if reply is None:
                        continue
                    replied = datetime.fromisoformat(reply['created_at'].replace('Z', '+00:00'))
                    if replied.tzinfo is None or not math.isfinite(replied.timestamp()):
                        continue
                    journal = ledger.setdefault('builder_wakes', {})
                    lane = journal.get(slot)
                    if lane is None or lane['launch_id'] != launch['id']:
                        lane = {'launch_id': launch['id'], 'attempts': {},
                                'unwakeable': [], 'last_at': None}
                        journal[slot] = lane
                    signature = digest([question['message_id'], reply['id']])
                    if signature in lane['attempts']:
                        continue
                    try:
                        config_path = c.get('_path')
                        if (not isinstance(config_path, str) or not os.path.isabs(config_path)
                                or not all(char.isprintable() for char in config_path)):
                            raise ValueError('Builder config path unproven')
                        check = shlex.join(['fleet', '--config', config_path,
                                            'thread', 'check', ref])
                        wait = shlex.join(['fleet', '--config', config_path,
                                           'thread', 'wait', ref, '--timeout', '600'])
                        proof = health.builder_wake_proof(c, launch, slot)
                    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError):
                        if (now - replied.timestamp() >= c['coordinator'].get(
                                'unwakeable_after_seconds', 900)
                                and signature not in lane['unwakeable']):
                            lane['unwakeable'].append(signature)
                            events.append({
                                'key': 'builder_reply_unwakeable:' + signature,
                                'subject': ref + ' builder_reply_unwakeable',
                                'type': 'status', 'branch': None,
                                'recipients': ['run:' + c['coordinator']['run']],
                                'body': f'{ref}; question {question["message_id"]}; '
                                        'builder idle launch/composer or config-path proof unavailable'})
                        continue
                    if (not deliver or (lane['last_at'] is not None
                            and now - lane['last_at'] < c['coordinator'].get(
                                'wake_cooldown_seconds', 600))):
                        continue
                    with state.lock(c, 'threads-' + ref, wait_seconds=0):
                        # Native observation may race settlement or a new decision.
                        # Revalidate exact authority before journaling permission.
                        latest = state.read(c, 'launches', {}).get(slot, {})
                        if (any(latest.get(field) != launch.get(field)
                                for field in ('id', 'terminal', 'terminal_incarnation_id'))
                                or not any(r == ref and q == question
                                           for r, q in threads.pending_questions(c, slot, latest))):
                            continue
                        attempt = {'at': now, 'ref': ref, 'question_id': question['message_id'],
                                   'reply_id': reply['id'], 'proof': proof, 'status': 'attempted'}
                        lane['attempts'][signature] = attempt
                        lane['last_at'] = now
                        state.write(c, 'supervisor', ledger)
                    try:
                        attempt['receipt'] = orca(
                            c, 'terminal', 'send', '--terminal', launch['terminal'], '--text',
                            f'fleet: Main replied to your pending decision {question["message_id"]} '
                            f'on {ref}; run {check}, then {wait} '
                            'to read and record the decision before continuing.',
                            '--enter')
                        attempt['status'] = 'sent'
                    except (RuntimeError, ValueError):
                        attempt['status'] = 'unknown'
                    state.write(c, 'supervisor', ledger)
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError):
            # Busy thread/transport/unknown evidence is not permission for input.
            continue
    return events


def cycle(c, deliver=True):
    s = snapshot.collect(c)
    ledger = state.read(c, 'supervisor', {'github': {}, 'pending': [], 'sent': {}, 'active_alerts': []})
    from .threads import _record_next_report
    for launch_id, notice in list(ledger.get('pending_receipts', {}).items()):
        try:
            _record_next_report(c, launch_id, notice)
        except ValueError:
            break
        del ledger['pending_receipts'][launch_id]
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
        changes = github.events(ledger['github'], current_g, aggregate_checks=True)
        seen = set(ledger.get('seen_prs', []))
        initial = [github.initial_event(p) for p in current_g['prs'] if p['number'] not in seen]
        initial = [e for e in initial if e is not None]
        diagnosed = {e['pr'] for e in initial}
        # A saved baseline from before initial-state support is not a notification receipt.
        changes = [e for e in changes if not (e.get('pr') in diagnosed and e['key'].startswith('pr:'))]
        changes = _group_alerts(changes + initial, current_g)
        pending.extend(github_relays(ledger, [urgent_event(c, e) for e in changes], current_g))
        pr_alerts = stuck_pr_alerts(c, ledger, current_g, time.time())
        ledger['seen_prs'] = sorted(seen | {p['number'] for p in current_g['prs']})
        ledger['github'] = current_g
    pending.extend(governor.advance(c, s, ledger))
    s['governor'] = ledger['governor']
    lane_free = {}
    for builder in (s.get('inventory') or {}).get('builders', []):
        candidate = _lane_free_candidate(c, builder)
        if candidate:
            lane_free[builder['path']] = candidate
    failures = {error for error in s.get('errors', [])
                if error.endswith(' collection unavailable')}
    previous_failures = ledger.get('collection_failure_streaks', {})
    streaks = {error: min(2, previous_failures.get(error, 0) + 1) for error in failures}
    ledger['collection_failure_streaks'] = streaks
    active = alerts(s, c, lane_free) + pr_alerts + main_alerts(c, s, ledger, time.time())
    active = [a for a in active if a['key'].removeprefix('error:') not in streaks
              or streaks[a['key'].removeprefix('error:')] >= 2]
    for alert in active:
        reason = s.get('error_details', {}).get(alert['key'].removeprefix('error:'))
        if reason:
            alert['body'] += ': ' + reason
    # Unknown collection/progress is not recovery: retain occurrence dedupe.
    prior_alerts = ledger.get('active_alerts', {})
    active_stuck = {
        key: value for key, value in prior_alerts.items()
        if key.startswith('pr_stuck:') and (
            current_g is None or
            _stuck_progress_unknown(current_g, int(key.split(':')[1])))
    } if isinstance(prior_alerts, dict) else {}
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
    pending.extend(lane_free_reports(c, lane_free, ledger))
    pending.extend(missing_ready_reports(c, s, ledger, time.time(), lane_free))
    pending.extend(wake_main(c, ledger, time.time(), deliver))
    pending.extend(wake_builders(c, ledger, time.time(), deliver))
    state.write(c, 'snapshot', s)
    state.write(c, 'supervisor', ledger)
    inventory = s.get('inventory')
    remaining = []
    failures = []
    protected = set()
    paused_prs = {p['number'] for p in current_g['prs'] if _stuck_pr_paused(c, p)} if current_g else set()
    if current_g is not None:
        reclassified = []
        for event in pending:
            updated = _group_alerts([event], current_g)[0]
            if updated.get('superseded_cancelled'):
                # Reclassify retries too; keep a successful coordinator receipt
                # when collapsing old per-member urgent relays to one run status.
                recipient = 'run:' + c['coordinator']['run']
                old_key = digest([event['key'], recipient])
                new_key = digest([updated['key'], recipient])
                if old_key in ledger['sent']:
                    ledger['sent'][new_key] = ledger['sent'][old_key]
            reclassified.append(updated)
        pending = reclassified
    for event in pending:
        if (event['key'].startswith('stray:') and inventory is not None
                and event['body'] in inventory.get('expected_worktrees', [])):
            # A newly declared scratch path must not replay an old urgent retry.
            continue
        if event['key'].startswith('pr_stuck:') and event.get('pr') in paused_prs:
            # A newly observed pause also invalidates undelivered old occurrences.
            continue
        if event.get('lane_free'):
            identity = event['lane_free']
            builder = next((b for b in (inventory or {}).get('builders', [])
                            if b.get('path') == identity['slot']), {})
            candidate = _ready_candidate(c, identity)
            reported = _lane_free_reported(c, identity)
            if reported is True:
                continue
            if candidate is False:
                # No delivery occurred: continuation/replacement invalidated this
                # attempt, not the launch's right to a later completion request.
                ledger['lane_free_reports'].pop(event['key'].removeprefix('lane_free:'), None)
                continue
            if reported is None or _lane_free_candidate(c, builder) != identity:
                # Unknown/busy observations defer, never authorize a decision alert.
                remaining.append(event)
                protected.update(digest([event['key'], r]) for r in
                                 event.get('recipients', ['run:' + c['coordinator']['run']]))
                continue
        if event.get('missing_ready') and event['missing_ready']['slot'] in lane_free:
            continue
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
        # watch-pr already exposes PR/group transitions to builders. Only the
        # supervisor's inactivity timer has no watch-pr equivalent.
        if event['key'].startswith('pr_stuck:'):
            owning_terminal = health.owner(inventory, event['branch']) if inventory and event.get('branch') else None
            if owning_terminal:
                recipients.append(owning_terminal)
        if (event['key'].startswith('pr_stuck:') and current_g is not None
                and _stuck_progress_unknown(current_g, event.get('pr'))):
            remaining.append(event)
            protected.update(digest([event['key'], r]) for r in recipients)
            continue
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
                if isinstance(receipt, dict) and receipt.get('pending_receipts'):
                    ledger.setdefault('pending_receipts', {}).update(receipt['pending_receipts'])
                ledger['sent'][key] = {'at': time.time(), 'receipt': receipt}
                state.write(c, 'supervisor', ledger)
            except (CommandError, ValueError) as error:
                failures.append({'recipient': recipient,
                                 'code': error.code if isinstance(error, CommandError) else 'delivery_failed'})
        # Only supervisor-only inactivity alerts need branch ownership discovery.
        resolved = not event['key'].startswith('pr_stuck:') or inventory is not None
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

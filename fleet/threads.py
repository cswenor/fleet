"""Pad-backed task identity; terminal-only replies are copied without consuming mail."""
import math
import os
from pathlib import Path
import re
import time
import uuid
from . import state
from .commands import run, orca
from .mailbox import request, MailboxError


def _name(ref):
    if not re.fullmatch(r'[A-Z]+-\d+', ref):
        raise ValueError('expected uppercase Pad reference')
    return 'threads-' + ref


def identity(c):
    caller = os.environ.get('ORCA_TERMINAL_HANDLE')
    if not caller:
        raise ValueError('thread commands require ORCA_TERMINAL_HANDLE')
    bound = request(c, 'run-current', '--from', caller)
    if 'run' not in bound:
        raise ValueError('builder terminal Run binding is unknown')
    if bound['run'] is not None:
        raise ValueError('builder terminal must not be Run-bound')
    return caller


def pad(c, ref, body):
    run(['pad', '--workspace', c['pad_workspace'], 'item', 'comment', ref, body], c['root'])


def _record_question(c, ref, task, key, q):
    if not q.get('question_recorded'):
        pad(c, ref, f"Fleet task thread: {task['id']}\nAttempt: {key}\nQuestion: {q['message_id']}\nNative subthread: {q['thread_id']}\n{q['body']}")
        q['question_recorded'] = True



def authorized(c, launch, slot, ref):
    if not launch.get('id'):
        return False
    if launch.get('ref') == ref:
        return True
    approval = state.read(c, 'carries', {}).get(launch['id'], {}).get(ref, {})
    if not (approval.get('ref') == ref and approval.get('slot') == slot and
            approval.get('launch_id') == launch['id']):
        return False
    primary = state.read(c, _name(launch['ref']), {}).get(launch['ref'], {})
    if primary.get('attempts', {}).get(launch['id'], {}).get('settled') is not False:
        return False
    reconciled = state.read(c, 'reconciliations', {}).get(launch['id'], {})
    retirement = state.read(c, 'retirements', {}).get(slot, {})
    return not (reconciled.get('launch_id') == launch['id'] or
                retirement.get('launch_id') == launch['id'])


def _ownership(c, launch, slot, caller):
    terminal = orca(c, 'terminal', 'show', '--terminal', caller)['terminal']
    worktree = launch.get('worktree', {})
    if (not launch.get('terminal_incarnation_id')
            or terminal.get('handle') != caller
            or terminal.get('incarnationId') != launch['terminal_incarnation_id']
            or not worktree.get('id') or worktree.get('path') != slot
            or terminal.get('worktreeId') != worktree['id']
            or not terminal.get('connected') or not terminal.get('writable')
            or terminal.get('orphaned')):
        raise ValueError('thread terminal ownership is unknown or changed')


def reopen(c, ref, authorized_by, reason=''):
    """Continue a settled launch, retaining every earlier attempt and send receipt."""
    name = _name(ref)
    if not isinstance(authorized_by, str) or not authorized_by.strip():
        raise ValueError('a recorded authorization message ID is required')
    authorized_by = authorized_by.strip()
    caller = identity(c)
    with state.lock(c, 'launcher'):
        matches = [(slot, launch) for slot, launch in state.read(c, 'launches', {}).items()
                   if slot in c['builder']['slots'] and launch.get('terminal') == caller
                   and authorized(c, launch, slot, ref)]
        if len(matches) != 1:
            raise ValueError('no unique authoritative launch for this task/terminal')
        slot, launch = matches[0]
        with state.lock(c, name):
            ledger = state.read(c, name, {})
            task = ledger.get(ref, {})
            attempt = task.get('attempts', {}).get(launch['id'], {})
            if (not task.get('id') or attempt.get('terminal') != caller
                    or attempt.get('slot') != slot):
                raise ValueError('reopen requires a settled attempt on this launch')
            _ownership(c, launch, slot, caller)
            if attempt.get('authorized_by') == authorized_by:
                if not attempt.get('open_message_id'):
                    raise ValueError('ambiguous reopen send; reconcile native inbox; never resend')
            else:
                if any(a.get('authorized_by') == authorized_by
                       for a in task.get('history', {}).values()):
                    raise ValueError('authorization already used by an older attempt')
                if attempt.get('settled') is not True:
                    raise ValueError('latest attempt is unsettled; use the existing thread')
                previous = attempt.get('attempt_id', launch['id'])
                task.setdefault('history', {})[previous] = attempt
                attempt_id = launch['id'] + '/' + str(uuid.uuid4())
                attempt = {'attempt_id': attempt_id, 'previous_attempt': previous,
                           'thread_id': task['id'] + '/' + attempt_id,
                           'authorized_by': authorized_by, 'reason': reason,
                           'terminal': caller, 'slot': slot,
                           'branch': run(['git', 'branch', '--show-current'], slot).strip(),
                           'questions': {}, 'settled': False}
                task['attempts'][launch['id']] = attempt
                state.write(c, name, ledger)
                body = (f"Pad-Ref: {ref}\nContinuation of settled attempt {previous}, "
                        f"authorized by {authorized_by}, reason: {reason or '(not supplied)'}\n"
                        f"Attempt: {attempt_id}\nFleet task thread: {attempt['thread_id']}")
                message = request(c, 'send', '--from', caller,
                                  '--to', 'run:' + c['coordinator']['run'],
                                  '--type', 'status', '--thread-id', attempt['thread_id'],
                                  '--subject', ref + ': thread reopened', '--body', body)['message']
                attempt['open_message_id'] = message['id']
                state.write(c, name, ledger)
            if not attempt.get('recorded'):
                pad(c, ref, f"Pad-Ref: {ref}\nFleet task thread: {attempt['thread_id']}\n"
                            f"Attempt: {attempt['attempt_id']}\n"
                            f"Continuation of settled attempt {attempt['previous_attempt']}, "
                            f"authorized by {authorized_by}, reason: {attempt['reason']}\n"
                            f"Native thread-open message: {attempt['open_message_id']}")
                attempt['recorded'] = True
                state.write(c, name, ledger)
            return {'ref': ref, 'attempt': attempt['attempt_id'], **attempt}

def open_thread(c, ref, slot=None):
    name = _name(ref)
    caller = identity(c)
    slot = str(Path(slot or os.getcwd()).resolve())
    if slot not in c['builder']['slots']:
        raise ValueError('thread slot must be configured')
    branch = run(['git', 'branch', '--show-current'], slot).strip()
    launch = state.read(c, 'launches', {}).get(slot, {})
    if not authorized(c, launch, slot, ref) or launch.get('terminal') != caller:
        raise ValueError('task/terminal must match the authoritative launch receipt')
    with state.lock(c, name):
        ledger = state.read(c, name, {})
        task = ledger.setdefault(ref, {'id': 'fleet-' + str(uuid.uuid4()), 'attempts': {}})
        attempt = task['attempts'].setdefault(launch['id'], {
            'terminal': caller, 'slot': slot, 'branch': branch, 'questions': {}, 'settled': False})
        attempt.setdefault('attempt_id', launch['id'])
        if attempt['settled']:
            raise ValueError('task attempt is settled; use thread reopen with Main authorization or a fresh launch')
        if attempt.get('authorized_by') and not (attempt.get('open_message_id') and attempt.get('recorded')):
            raise ValueError('finish thread reopen recording before continuing')
        state.write(c, name, ledger)
        if not attempt.get('recorded'):
            pad(c, ref, f"Pad-Ref: {ref}\nFleet task thread: {task['id']}\nAttempt: {launch['id']}\nBranch: {branch}\nSlot: {slot}\nTerminal (disposable): {caller}\nNo supervised Task/Dispatch for this terminal launch.")
            attempt['recorded'] = True
            state.write(c, name, ledger)
        return {'ref': ref, 'thread_id': attempt.get('thread_id', task['id']),
                'attempt': attempt.get('attempt_id', launch['id']), **attempt}


def current(c, ref, caller):
    ledger = state.read(c, _name(ref), {})
    task = ledger.get(ref)
    if not task:
        raise ValueError('run fleet thread open REF first')
    launches = state.read(c, 'launches', {})
    matches = [(key, a) for key, a in task['attempts'].items()
               if a['terminal'] == caller and not a['settled']
               and launches.get(a['slot'], {}).get('id') == key
               and launches[a['slot']].get('terminal') == caller
               and authorized(c, launches[a['slot']], a['slot'], ref)]
    if len(matches) != 1:
        raise ValueError('no unique current task attempt; old replies cannot authorize new work')
    key, attempt = matches[0]
    if attempt.get('authorized_by') and not (attempt.get('open_message_id') and attempt.get('recorded')):
        raise ValueError('finish thread reopen recording before continuing')
    return ledger, task, attempt.get('attempt_id', key), attempt


def question(c, ref, body, subject=None):
    name = _name(ref)
    caller = identity(c)
    with state.lock(c, name):
        ledger, task, key, attempt = current(c, ref, caller)
        for pending in attempt['questions'].values():
            if not pending.get('reply_recorded'):
                ident = pending.get('message_id', 'unknown (ambiguous send)')
                raise ValueError(f"pending question {ident}; native subthread {pending['thread_id']}; "
                                 'resume thread wait, never resend or send a separate direct question')
        # Unique native subthread prevents a late answer to an old question/attempt
        # from being interpreted as the next decision. Pad is the canonical task thread.
        token = task['id'] + '/' + key + '/' + str(uuid.uuid4())
        q = {'thread_id': token, 'body': body, 'subject': subject or ref + ': decision',
             'status': 'sending'}
        attempt['questions'][token] = q
        attempt['latest_question'] = token
        state.write(c, name, ledger)
        message = request(c, 'send', '--from', caller, '--to', 'run:' + c['coordinator']['run'],
                          '--type', 'question', '--thread-id', token,
                          '--subject', q['subject'], '--body', body)['message']
        q.update(message_id=message['id'], status='pending')
        state.write(c, name, ledger)
        _record_question(c, ref, task, key, q)
        state.write(c, name, ledger)
        return q


def wait(c, ref, timeout=900):
    name = _name(ref)
    if not math.isfinite(timeout) or not 1 <= timeout <= 1800:
        raise ValueError('timeout must be 1 to 1800 seconds')
    caller = identity(c)
    deadline = time.monotonic() + timeout
    with state.lock(c, 'thread-wait-' + caller):
        while True:
            with state.lock(c, name):
                ledger, task, key, attempt = current(c, ref, caller)
                token = attempt.get('latest_question')
                if not token:
                    raise ValueError('no question to wait for')
                q = attempt['questions'][token]
                if q['status'] == 'sending':
                    raise ValueError('ambiguous question send; reconcile native inbox before retrying')
                if not q.get('reply'):
                    history = request(c, 'inbox', '--terminal', caller, '--limit', '1000')
                    messages = history.get('messages')
                    if not isinstance(messages, list) or history.get('count') != len(messages):
                        raise MailboxError('invalid_terminal_inbox')
                    # Persist all observed types before doing anything with the decision.
                    attempt.setdefault('inbox', {}).update({m['id']: m for m in messages})
                    replies = [m for m in messages if m.get('thread_id') == q['thread_id']
                               and m.get('from_handle') == c['coordinator']['terminal']
                               and m.get('to_handle') == caller and m.get('type') == 'status']
                    if replies:
                        q['reply'] = replies[0]
                state.write(c, name, ledger)
                _record_question(c, ref, task, key, q)
                state.write(c, name, ledger)
                if q.get('reply'):
                    if not q.get('reply_recorded'):
                        m = q['reply']
                        pad(c, ref, f"Fleet task thread: {task['id']}\nAttempt: {key}\nQuestion: {q['message_id']}\nReply: {m['id']}\nNative subthread: {q['thread_id']}\nDecision: {m['body']}")
                        q['reply_recorded'] = True
                        state.write(c, name, ledger)
                    return {'event': 'reply', 'thread_id': attempt.get('thread_id', task['id']), 'attempt': key,
                            'question': q['message_id'], 'reply': q['reply'],
                            'messages': list(attempt['inbox'].values()),
                            'readOnly': True, 'requestedLimit': 1000}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'event': 'timeout', 'thread_id': attempt.get('thread_id', task['id']), 'attempt': key,
                        'question': q['message_id'], 'messages': list(attempt['inbox'].values()),
                        'readOnly': True, 'requestedLimit': 1000}
            # Terminal-only native ask requires a Dispatch. Inspect persistent history
            # in this one background process, never poll with model turns or consume it.
            time.sleep(min(2, remaining))


def result(c, ref, body):
    name = _name(ref)
    caller = identity(c)
    with state.lock(c, name):
        ledger, task, key, attempt = current(c, ref, caller)
        if any(not q.get('reply_recorded') for q in attempt['questions'].values()):
            raise ValueError('pending decision must be resolved and recorded before settlement')
        pad(c, ref, f"Pad-Ref: {ref}\nFleet task thread: {task['id']}\nAttempt: {key}\nBranch: {attempt['branch']}\nSlot: {attempt['slot']}\nResult: {body}")
        receipt = request(c, 'send', '--from', caller, '--to', 'run:' + c['coordinator']['run'],
                          '--type', 'status', '--thread-id', attempt.get('thread_id', task['id']),
                          '--subject', ref + ': result', '--body', body)
        attempt.update(settled=True, settled_at=time.time(), result=body)
        state.write(c, name, ledger)
        return {'thread_id': attempt.get('thread_id', task['id']), 'attempt': key,
                'receipt': receipt, 'settled': True}


def _launch_report(task, launch_id, kind):
    """Reports belong to launches even when authorized continuation archives them."""
    current_report = task.get('attempts', {}).get(launch_id, {}).get(kind)
    if current_report:
        return current_report
    for key, attempt in task.get('history', {}).items():
        if (key == launch_id or key.startswith(launch_id + '/')) and attempt.get(kind):
            return attempt[kind]
    return None


def ready(c, next_ref, body=''):
    """Report a settled primary launch's next handoff, never resend an uncertain send."""
    _name(next_ref)
    caller = identity(c)
    # Serialize against launch/restart: readiness must never be attributed to a
    # replacement launch while publication or native delivery is in flight.
    with state.lock(c, 'launcher'):
        matches = [(slot, launch) for slot, launch in state.read(c, 'launches', {}).items()
                   if slot in c['builder']['slots'] and launch.get('terminal') == caller]
        if len(matches) != 1:
            raise ValueError('no unique authoritative launch for this terminal')
        slot, launch = matches[0]
        ref = launch['ref']
        name = _name(ref)
        with state.lock(c, name):
            ledger = state.read(c, name, {})
            task = ledger.get(ref, {})
            key = launch['id']
            attempt = task.get('attempts', {}).get(key, {})
            if (not task.get('id') or attempt.get('settled') is not True
                    or attempt.get('terminal') != caller or attempt.get('slot') != slot):
                raise ValueError('ready requires the current primary task attempt to be settled')
            report = _launch_report(task, key, 'ready')
            if report:
                if report['next_ref'] != next_ref:
                    raise ValueError('ready already targets a different next ref')
                if not report.get('message_id'):
                    raise ValueError('ambiguous ready send; reconcile native inbox before retrying; never resend')
            else:
                if state.read(c, 'needs-next', {}).get(key):
                    raise ValueError('launch already requested a next ref; use its native thread')
                from .restart import _completion
                proof = _completion(c, next_ref, ref, launch, require_handoff=True)
                _ownership(c, launch, slot, caller)
                report = {'next_ref': next_ref, 'handoff': proof['handoff'],
                          'pr': proof['pr'], 'status': 'sending',
                          'body': f"Pad-Ref: {ref}\nFRESH-READY {next_ref}\n"
                                  f"Handoff: {next_ref} comment {proof['handoff']}\n"
                                  f"Published: {proof['pr']}\n{body}".rstrip()}
                attempt['ready'] = report
                state.write(c, name, ledger)
                message = request(c, 'send', '--from', caller,
                                  '--to', 'run:' + c['coordinator']['run'],
                                  '--type', 'question', '--thread-id', attempt.get('thread_id', task['id']),
                                  '--subject', f'{ref}: FRESH-READY {next_ref}',
                                  '--body', report['body'])['message']
                report.update(message_id=message['id'], status='sent', at=time.time())
                state.write(c, name, ledger)
            if not report.get('recorded'):
                pad(c, ref, f"Fleet task thread: {attempt.get('thread_id', task['id'])}\n"
                            f"Attempt: {attempt.get('attempt_id', key)}\n"
                            f"Ready report: {report['message_id']}\n{report['body']}")
                report['recorded'] = True
                state.write(c, name, ledger)
            return {'ref': ref, 'attempt': attempt.get('attempt_id', key), **report}


def needs_next(c, ref):
    """Request assignment once for a completed launch without a next-ref handoff."""
    name = _name(ref)
    caller = identity(c)
    with state.lock(c, 'launcher'):
        matches = [(slot, launch) for slot, launch in state.read(c, 'launches', {}).items()
                   if slot in c['builder']['slots'] and launch.get('terminal') == caller
                   and launch.get('ref') == ref]
        if len(matches) != 1:
            raise ValueError('no unique authoritative primary launch for this task/terminal')
        slot, launch = matches[0]
        with state.lock(c, name):
            ledger = state.read(c, name, {})
            task = ledger.get(ref, {})
            key = launch['id']
            attempt = task.get('attempts', {}).get(key, {})
            if (not task.get('id') or attempt.get('settled') is not True
                    or attempt.get('terminal') != caller or attempt.get('slot') != slot):
                raise ValueError('needs-next requires the current primary task attempt to be settled')
            _ownership(c, launch, slot, caller)
            reports = state.read(c, 'needs-next', {})
            report = reports.get(key)
            if report:
                if not report.get('message_id'):
                    raise ValueError('ambiguous needs-next send; reconcile native inbox; never resend')
            else:
                if _launch_report(task, key, 'ready'):
                    raise ValueError('launch already has a ready report; use its native thread')
                from .restart import _finished_completion
                proof = _finished_completion(c, ref, launch)
                report = {'ref': ref, 'slot': slot, 'status': 'sending',
                          'completion': proof,
                          'body': f'Pad-Ref: {ref}\nNEEDS-NEXT-REF\n'
                                  f'{ref} complete in {slot}; needs next ref'}
                reports[key] = report
                state.write(c, 'needs-next', reports)
                message = request(c, 'send', '--from', caller,
                                  '--to', 'run:' + c['coordinator']['run'],
                                  '--type', 'question',
                                  '--thread-id', attempt.get('thread_id', task['id']),
                                  '--subject', f'{ref}: NEEDS-NEXT-REF',
                                  '--body', report['body'])['message']
                report.update(message_id=message['id'], status='sent', at=time.time())
                state.write(c, 'needs-next', reports)
            attempt['needs_next'] = report
            state.write(c, name, ledger)
            if not report.get('recorded'):
                pad(c, ref, f"Fleet task thread: {attempt.get('thread_id', task['id'])}\n"
                            f"Attempt: {attempt.get('attempt_id', key)}\n"
                            f"Needs-next report: {report['message_id']}\n{report['body']}")
                report['recorded'] = True
                state.write(c, 'needs-next', reports)
                state.write(c, name, ledger)
            return {'attempt': key, **report}

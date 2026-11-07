"""Pad-backed task identity; terminal-only replies are copied without consuming mail."""
import math
import os
from pathlib import Path
import re
import time
import uuid
from . import state
from .commands import run
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
        if attempt['settled']:
            raise ValueError('task attempt is settled; a fresh launch is required')
        state.write(c, name, ledger)
        if not attempt.get('recorded'):
            pad(c, ref, f"Pad-Ref: {ref}\nFleet task thread: {task['id']}\nAttempt: {launch['id']}\nBranch: {branch}\nSlot: {slot}\nTerminal (disposable): {caller}\nNo supervised Task/Dispatch for this terminal launch.")
            attempt['recorded'] = True
            state.write(c, name, ledger)
        return {'ref': ref, 'thread_id': task['id'], 'attempt': launch['id'], **attempt}


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
    return ledger, task, key, attempt


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
                    return {'event': 'reply', 'thread_id': task['id'], 'attempt': key,
                            'question': q['message_id'], 'reply': q['reply'],
                            'messages': list(attempt['inbox'].values()),
                            'readOnly': True, 'requestedLimit': 1000}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'event': 'timeout', 'thread_id': task['id'], 'attempt': key,
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
                          '--type', 'status', '--thread-id', task['id'],
                          '--subject', ref + ': result', '--body', body)
        attempt.update(settled=True, result=body)
        state.write(c, name, ledger)
        return {'thread_id': task['id'], 'attempt': key, 'receipt': receipt, 'settled': True}

"""Attempt-scoped Main steering, surfaced explicitly without terminal input."""
from contextlib import contextmanager, ExitStack
import os
import time
import uuid
from . import state, threads
from .mailbox import request, _binding


def _main(c):
    caller = os.environ.get('ORCA_TERMINAL_HANDLE')
    if caller != c['coordinator']['terminal']:
        raise ValueError('steering requires the configured Main terminal identity')
    _binding(request(c, 'run-current', '--from', caller).get('run'),
             c['coordinator']['run'], caller)
    return caller


def _target(c, ref):
    ledger = state.read(c, threads._name(ref), {})
    task = ledger.get(ref, {})
    matches = []
    for slot, launch in state.read(c, 'launches', {}).items():
        if slot not in c['builder']['slots'] or not threads.authorized(c, launch, slot, ref):
            continue
        attempt = task.get('attempts', {}).get(launch['id'], {})
        if (task.get('id') and attempt.get('settled') is False
                and attempt.get('recorded') and attempt.get('slot') == slot
                and attempt.get('terminal') == launch.get('terminal')
                and (not attempt.get('authorized_by') or attempt.get('open_message_id'))):
            matches.append((slot, launch, attempt))
    if len(matches) != 1:
        raise ValueError('steering requires a unique opened unsettled authoritative attempt')
    slot, launch, attempt = matches[0]
    threads._ownership(c, launch, slot, launch['terminal'])
    bound = request(c, 'run-current', '--from', launch['terminal'])
    if 'run' not in bound or bound['run'] is not None:
        raise ValueError('steering target must be positively unbound from every Run')
    return ledger, task, launch, attempt


def send(c, ref, body, priority=False):
    threads._name(ref)
    if priority:
        raise ValueError('priority steering is unavailable: stock interrupt can inject text into a child PTY; use normal send and bounded checks')
    if not isinstance(body, str) or not body.strip():
        raise ValueError('steering body must be non-empty')
    caller = _main(c)
    with state.lock(c, 'launcher'), state.lock(c, threads._name(ref)):
        ledger, task, launch, attempt = _target(c, ref)
        if any(row.get('status') == 'sending' for row in attempt.get('steering', {}).values()):
            raise ValueError('ambiguous steering send; reconcile native inbox before sending again')
        token = str(uuid.uuid4())
        receipt = {'token': token, 'body': body, 'at': time.time(), 'status': 'sending'}
        attempt.setdefault('steering', {})[token] = receipt
        state.write(c, threads._name(ref), ledger)
        # Journal intent first. An exception leaves an ambiguous send visible;
        # never automatically repeat it or inject a wake into the terminal.
        message = request(c, 'send', '--from', caller, '--to', launch['terminal'],
                          '--type', 'status', '--thread-id', attempt.get('thread_id', task['id']),
                          '--subject', ref + ': steering', '--body', body)['message']
        if not isinstance(message.get('id'), str) or not message['id']:
            raise ValueError('ambiguous steering send; reconcile native inbox before sending again')
        receipt.update(message_id=message['id'], status='sent')
        state.write(c, threads._name(ref), ledger)
        return dict(receipt)


@contextmanager
def _checkpoint_lock(c):
    with ExitStack() as stack:
        try:
            stack.enter_context(state.lock(c, 'launcher', wait_seconds=180))
        except ValueError as e:
            if not isinstance(e.__cause__, BlockingIOError):
                raise
            raise ValueError('fleet launcher busy for >180s; retry thread check') from e
        yield


def check(c, ref, acknowledge=None):
    caller = threads.identity(c)
    name = threads._name(ref)
    with _checkpoint_lock(c), state.lock(c, name):
        ledger, task, key, attempt = threads.current(c, ref, caller)
        launch = state.read(c, 'launches', {})[attempt['slot']]
        threads._ownership(c, launch, attempt['slot'], caller)
        receipts = attempt.get('steering', {})
        if acknowledge is not None:
            receipt = receipts.get(acknowledge)
            if not receipt or receipt.get('status') != 'sent':
                raise ValueError('unknown or unsent steering acknowledgement token')
            receipt.setdefault('acknowledged_at', time.time())
            state.write(c, name, ledger)
        return {'ref': ref, 'attempt': key, 'messages': [dict(row)
                for row in sorted(receipts.values(), key=lambda row: row['at'])
                if row.get('status') == 'sent' and not row.get('acknowledged_at')]}


def pending(c):
    _main(c)
    lanes = []
    with _checkpoint_lock(c):
        for slot, launch in state.read(c, 'launches', {}).items():
            if slot not in c['builder']['slots']:
                continue
            # Include carried refs too: their journals remain ref-local.
            refs = [launch['ref'], *state.read(c, 'carries', {}).get(launch['id'], {})]
            for ref in dict.fromkeys(refs):
                with state.lock(c, threads._name(ref)):
                    task = state.read(c, threads._name(ref), {}).get(ref, {})
                    attempt = task.get('attempts', {}).get(launch['id'], {})
                    messages = [{**row, 'age_seconds': max(0, time.time() - row['at'])}
                                for row in sorted(attempt.get('steering', {}).values(),
                                                  key=lambda row: row['at'])
                                if not row.get('acknowledged_at')]
                    if messages:
                        lanes.append({'ref': ref, 'slot': slot, 'launch_id': launch['id'],
                                      'attempt': attempt.get('attempt_id', launch['id']),
                                      'messages': messages})
    return {'lanes': lanes}

"""One native, replayable Run wait; terminal history is read-only diagnostics."""
import json
import math
import os
import re
import subprocess
from .commands import CommandError


class MailboxError(CommandError):
    pass


def request(c, *args, timeout=60):
    try:
        p = subprocess.run([c['orca'], 'orchestration', *args, '--json'],
                           cwd=c['root'], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MailboxError('orca_unavailable') from e
    try:
        data = json.loads(p.stdout)
    except (ValueError, TypeError) as e:
        raise MailboxError('invalid_orca_json') from e
    if not isinstance(data, dict):
        raise MailboxError('invalid_orca_response')
    if data.get('ok') is False:
        error = data.get('error')
        code = error.get('code') if isinstance(error, dict) else None
        raise MailboxError(code if isinstance(code, str) and re.fullmatch('[a-z_]+', code)
                           else 'orca_rejected')
    if p.returncode or data.get('ok') is not True or not isinstance(data.get('result'), dict):
        raise MailboxError('invalid_orca_response')
    return data['result']


def delivery(result, run):
    """Reject non-replayable/ambiguous responses rather than calling them empty."""
    if result.get('connectionLost'):
        raise MailboxError('connection_lost')
    if result.get('cancelled'):
        raise MailboxError('wait_cancelled')
    messages = result.get('messages')
    if (result.get('runId') != run or not isinstance(messages, list)
            or result.get('count') != len(messages)
            or not all(isinstance(m, dict) and isinstance(m.get('id'), str) for m in messages)
            or not isinstance(result.get('timedOut'), bool)):
        raise MailboxError('invalid_delivery')
    delivery_id = result.get('deliveryId')
    if messages:
        if not isinstance(delivery_id, str) or not delivery_id or result['timedOut']:
            raise MailboxError('invalid_delivery')
        return {'source': 'run', 'deliveryId': delivery_id, 'messages': messages,
                'replayed': bool(result.get('replayed'))}
    if delivery_id is not None or not result['timedOut']:
        raise MailboxError('invalid_delivery')
    return None


def wait(c, timeout=900, ack_run=None, terminal=None):
    run = c['coordinator']['run']
    caller = os.environ.get('ORCA_TERMINAL_HANDLE')
    result = {'event': 'error', 'runId': run, 'terminal': caller or terminal,
              'batches': [], 'legacyTerminalInbox': {'readOnly': True}}
    try:
        if not math.isfinite(timeout) or not 1 <= timeout <= 1800:
            raise MailboxError('timeout_must_be_1_to_1800_seconds')
        if caller and terminal and caller != terminal:
            raise MailboxError('caller_identity_mismatch')
        caller = caller or terminal
        if not caller:
            raise MailboxError('caller_identity_required')
        bound = request(c, 'run-current', '--from', caller).get('run')
        if (not isinstance(bound, dict) or bound.get('id') != run
                or bound.get('coordinator_handle') != caller or bound.get('legacy')):
            raise MailboxError('caller_not_bound_to_configured_run')
        args = ['check', '--terminal', caller, '--run', run, '--wait',
                '--timeout-ms', str(int(timeout * 1000))]
        if ack_run:
            args.extend(['--ack', ack_run])
        received = request(c, *args, timeout=timeout + 30)
        batch = delivery(received, run)
        if batch:
            result['batches'].append(batch)
        result.update(event='messages' if batch else 'timeout',
                      acknowledged=received.get('acknowledged'))
    except MailboxError as e:
        result['error'] = str(e)
    # Never consume legacy terminal mail: its native check has no replay/ack.
    # Even failures carry diagnostics, without discarding an already fetched Run batch.
    if caller:
        try:
            history = request(c, 'inbox', '--terminal', caller, '--limit', '1000')
            if (not isinstance(history.get('messages'), list)
                    or history.get('count') != len(history['messages'])):
                raise MailboxError('invalid_terminal_inbox')
            result['legacyTerminalInbox'].update(history, requestedLimit=1000)
        except MailboxError as e:
            result['legacyTerminalInbox']['error'] = str(e)
            result.update(event='error', error=result.get('error', 'terminal_inbox_unavailable'))
    else:
        result['legacyTerminalInbox']['error'] = 'caller_identity_required'
    return result

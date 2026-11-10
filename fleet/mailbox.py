"""One native Run wait; preserve native acknowledgment and recovery semantics."""
import errno
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import unicodedata
import tempfile
import subprocess
import time
from .commands import CommandError, orchestration_args


class MailboxError(CommandError):
    def __init__(self, message, *, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


_UNAVAILABLE = frozenset(('orca_unavailable', 'runtime_unavailable',
                          'service_unavailable', 'connection_lost',
                          'connection_refused', 'transport_error'))


def _metadata(data):
    """Retain native recovery identifiers without interpreting mutation success."""
    return {key: data[key] for key in (
        'deliveryId', 'mutation', 'requestId', 'deliveryAcknowledged', 'acknowledged')
        if key in data}


def _binding(bound, run, caller):
    if (not isinstance(bound, dict) or bound.get('id') != run
            or bound.get('coordinator_handle') != caller or bound.get('legacy')):
        raise MailboxError('caller_not_bound_to_configured_run')


class _Recovery:
    """One monotonic outage budget, shared by all read-only recovery operations."""
    def __init__(self, c, run, caller):
        self.c, self.run, self.caller = c, run, caller
        self.deadline = None
        self.recovered = False

    def pause(self):
        if self.deadline is None:
            self.deadline = time.monotonic() + 30
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MailboxError('runtime_unavailable')
        time.sleep(min(1, remaining))
        if time.monotonic() >= self.deadline:
            raise MailboxError('runtime_unavailable')

    def read(self, verb, *args):
        if self.deadline is None:
            self.deadline = time.monotonic() + 30
        retry = False
        while True:
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise MailboxError('runtime_unavailable')
                if retry and verb != 'run-current':
                    bound = self.read('run-current', '--from', self.caller).get('run')
                    _binding(bound, self.run, self.caller)
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        raise MailboxError('runtime_unavailable')
                value = request(self.c, verb, *args, timeout=min(30, remaining))
                if retry:
                    self.recovered = True
                return value
            except MailboxError as error:
                if str(error) not in _UNAVAILABLE:
                    raise
                self.pause()
                retry = True

    def reconnect(self):
        # Never reissue check or its --ack after an ambiguous response.
        self.pause()
        bound = self.read('run-current', '--from', self.caller).get('run')
        _binding(bound, self.run, self.caller)
        self.recovered = True


def request(c, *args, timeout=60):
    deadline = time.monotonic() + timeout
    try:
        args = orchestration_args(c['orca'], args, timeout=min(30, timeout))
    except CommandError as e:
        raise MailboxError('orca_unavailable' if e.code == 'orca_capability_timeout'
                           else 'orca_capabilities_unavailable') from e
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MailboxError('orca_unavailable')
    try:
        p = subprocess.run([c['orca'], 'orchestration', *args, '--json'],
                           cwd=c['root'], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, timeout=remaining)
    except (FileNotFoundError, PermissionError) as e:
        raise MailboxError('orca_executable_unavailable') from e
    except subprocess.TimeoutExpired as e:
        raise MailboxError('orca_unavailable') from e
    except OSError as e:
        transport = e.errno in (errno.ECONNREFUSED, errno.ECONNRESET,
                               errno.ECONNABORTED, errno.ETIMEDOUT,
                               errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EPIPE)
        raise MailboxError('orca_unavailable' if transport else 'orca_execution_failed') from e
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
                           else 'orca_rejected',
                           metadata={**_metadata(data), **_metadata(error if isinstance(error, dict) else {})})
    if p.returncode or data.get('ok') is not True or not isinstance(data.get('result'), dict):
        raise MailboxError('invalid_orca_response')
    return data['result']


def delivery(result, run):
    """Reject ambiguous responses; never infer delivery fields from concise JSON."""
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
    if 'deliveryAcknowledged' in result and not isinstance(result['deliveryAcknowledged'], bool):
        raise MailboxError('invalid_delivery')
    delivery_id = result.get('deliveryId')
    if messages:
        if not isinstance(delivery_id, str) or not delivery_id or result['timedOut']:
            raise MailboxError('invalid_delivery')
        batch = {'source': 'run', 'deliveryId': delivery_id, 'messages': messages,
                 'replayed': bool(result.get('replayed'))}
        if 'deliveryAcknowledged' in result:
            batch.update(deliveryAcknowledged=result['deliveryAcknowledged'],
                         nativeAutoAck=result['deliveryAcknowledged'])
            mutation = result.get('mutation')
            if isinstance(mutation, dict) and isinstance(mutation.get('requestId'), str):
                batch['nativeRequestId'] = mutation['requestId']
        return batch
    if delivery_id is not None or not result['timedOut']:
        raise MailboxError('invalid_delivery')
    return None


CLASSES = ('builder_decision_request', 'supervisor_urgent', 'supervisor_relay', 'builder_report')


def classify(messages, coordinator):
    """Keep every native field and delivery, prioritizing decisions stably."""
    classified = []
    counts = dict.fromkeys(CLASSES, 0)
    for message in messages:
        body = message.get('body')
        subject = message.get('subject')
        relay = (coordinator and message.get('from_handle') == coordinator
                 and isinstance(body, str) and body.startswith('[fleet '))
        urgent = (relay and message.get('type') == 'question'
                  and isinstance(subject, str) and subject.startswith('[urgent] '))
        if urgent:
            kind = 'supervisor_urgent'
        elif message.get('type') in ('question', 'escalation'):
            kind = 'builder_decision_request'
        elif relay:
            kind = 'supervisor_relay'
        else:
            kind = 'builder_report'
        row = {**message, 'classification': kind}
        if urgent:
            # Advisory presentation only: neither sender nor markers authenticate.
            row.update(replyExpected=False, triageNote='no reply expected')
        classified.append(row)
        counts[kind] += 1
    classified.sort(key=lambda m: 0 if m['classification'] == 'builder_decision_request'
                    else 1 if m['classification'] == 'supervisor_urgent' else 2)
    return classified, counts


def wake_types(kinds):
    """Urgent aliases question; native escalation requires an active Dispatch."""
    if not isinstance(kinds, str):
        raise MailboxError('invalid_wake_on')
    selected = [kind.strip() for kind in kinds.split(',')]
    if not selected or any(kind not in ('all', 'question', 'escalation', 'urgent', 'status', 'builder')
                           for kind in selected):
        raise MailboxError('invalid_wake_on')
    if 'all' in selected:
        return None
    return ','.join(dict.fromkeys('question' if kind == 'urgent' else kind
                                 for kind in selected if kind != 'builder'))


def _selected(rows, selected):
    return any(row['classification'] in ('builder_report', 'builder_decision_request')
               or row.get('type') in selected for row in rows)


def _builder_pending(c, recovery, run, caller, timeout, selected, interval):
    """Peek without consuming relay traffic; stock native waits cannot filter senders."""
    deadline = time.monotonic() + timeout
    while True:
        recovery.deadline = time.monotonic() + min(30, max(1, deadline - time.monotonic()))
        pending = recovery.read('check', '--terminal', caller, '--run', run, '--peek')
        messages = pending.get('messages')
        if (pending.get('runId') != run or not isinstance(messages, list)
                or pending.get('count') != len(messages)
                or not all(isinstance(row, dict) and isinstance(row.get('id'), str)
                           for row in messages)):
            raise MailboxError('invalid_builder_peek')
        rows, _ = classify(messages, c['coordinator'].get('terminal'))
        if _selected(rows, selected):
            recovery.deadline = None
            return True
        if len(rows) >= 100:
            raise MailboxError('builder_peek_incomplete')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            recovery.deadline = None
            return bool(rows)
        time.sleep(min(interval, remaining))


def wait(c, timeout=900, ack_run=None, terminal=None, wake_on='all', interval=3,
         *, before_check=None):
    run = c['coordinator']['run']
    caller = os.environ.get('ORCA_TERMINAL_HANDLE')
    result = {'event': 'error', 'runId': run, 'terminal': caller or terminal,
              'batches': [], 'counts': dict.fromkeys(CLASSES, 0),
              'legacyTerminalInbox': {'readOnly': True}}
    try:
        if not math.isfinite(timeout) or not 1 <= timeout <= 1800:
            raise MailboxError('timeout_must_be_1_to_1800_seconds')
        types = wake_types(wake_on)
        if not math.isfinite(interval) or not 1 <= interval <= 30:
            raise MailboxError('interval_must_be_1_to_30_seconds')
        kinds = {kind.strip() for kind in wake_on.split(',')}
        builder = 'builder' in kinds and 'all' not in kinds
        if caller and terminal and caller != terminal:
            raise MailboxError('caller_identity_mismatch')
        caller = caller or terminal
        if not caller:
            raise MailboxError('caller_identity_required')
        recovery = _Recovery(c, run, caller)
        bound = recovery.read('run-current', '--from', caller).get('run')
        wait_deadline = time.monotonic() + timeout
        _binding(bound, run, caller)
        # A later outage after the potentially long native wait gets its own
        # budget; never extend the deadline within one recovery operation.
        recovery.deadline = None
        args = ['check', '--terminal', caller, '--run', run, '--wait',
                '--timeout-ms', str(int(timeout * 1000))]
        selected = types.split(',') if types else []
        pending = True
        if builder:
            # Submit the previous ack once, then hold unselected deliveries
            # through the remaining class-gated wait.
            args[-1] = '1'
            if not ack_run:
                pending = _builder_pending(
                    c, recovery, run, caller, timeout, selected, interval)
        if types and not builder:
            args.extend(['--types', types])
        if ack_run:
            args.extend(['--ack', ack_run])
        if pending and ack_run and before_check:
            before_check()
        try:
            received = (request(c, *args, timeout=(30 if builder else timeout + 30))
                        if pending else {'runId': run, 'messages': [], 'count': 0,
                                         'deliveryId': None, 'timedOut': True})
            result.update(_metadata(received))
            batch = delivery(received, run)
            if builder and ack_run:
                result['ackCheck'] = {'ackRunRequested': ack_run, **_metadata(received)}
                result['acknowledged'] = received.get('acknowledged')
                ack_run = None
                args = args[:-2]
                rows = classify(batch['messages'], c['coordinator'].get('terminal'))[0] if batch else []
                if not _selected(rows, selected):
                    if batch:
                        batch['messages'], batch['counts'] = classify(
                            batch['messages'], c['coordinator'].get('terminal'))
                        result['batches'].append(batch)
                        for kind in CLASSES:
                            result['counts'][kind] += batch['counts'][kind]
                    remaining = wait_deadline - time.monotonic()
                    pending = remaining > 0 and _builder_pending(
                        c, recovery, run, caller, remaining, selected, interval)
                    # Old runtimes replay an explicit-ack delivery. Never ack
                    # held, unprinted mail just to fetch the selected arrival.
                    if pending and (batch is None or batch.get('nativeAutoAck')):
                        received = request(c, *args, timeout=30)
                        result.update(_metadata(received))
                        batch = delivery(received, run)
                    else:
                        batch = None
        except MailboxError as error:
            result.update(error.metadata)
            if 'received' in locals():
                result['nativeResult'] = received
            if str(error) not in _UNAVAILABLE:
                raise
            result.update(reason='consuming_wait_interrupted_outcome_unknown',
                          interruptedOperation='check', acknowledgmentStatus='unknown; do not repeat acknowledgment',
                          ackRunRequested=ack_run)
            recovery.reconnect()
            result.update(event='rearm', error=str(error), runtimeRecovered=True)
            return result
        if batch:
            batch['messages'], batch['counts'] = classify(
                batch['messages'], c['coordinator'].get('terminal'))
            for kind in CLASSES:
                result['counts'][kind] += batch['counts'][kind]
            result['batches'].append(batch)
        if result['batches'] and 'nativeAutoAck' in result['batches'][-1]:
            result['nativeAutoAck'] = result['batches'][-1]['nativeAutoAck']
            if result['nativeAutoAck']:
                result['acknowledgmentStatus'] = 'already acknowledged natively'
        result.update(event='messages' if result['batches'] else 'timeout',
                      acknowledged=received.get('acknowledged', result.get('acknowledged')))
        if recovery.recovered:
            result['runtimeRecovered'] = True
    except MailboxError as e:
        result['error'] = str(e)
        if str(e) == 'runtime_unavailable':
            result['event'] = 'runtime_unavailable'
            return result
    # Never consume legacy terminal mail: its native check has no replay/ack.
    # Even failures carry diagnostics, without discarding an already fetched Run batch.
    if caller:
        try:
            diagnostics = recovery if 'recovery' in locals() else _Recovery(c, run, caller)
            history = diagnostics.read('inbox', '--terminal', caller, '--limit', '1000')
            if diagnostics.recovered:
                result['runtimeRecovered'] = True
            if (not isinstance(history.get('messages'), list)
                    or not all(isinstance(m, dict) for m in history['messages'])
                    or history.get('count') != len(history['messages'])):
                raise MailboxError('invalid_terminal_inbox')
            result['legacyTerminalInbox'].update(history, requestedLimit=1000)
            messages, counts = classify(history['messages'], c['coordinator'].get('terminal'))
            result['legacyTerminalInbox'].update(messages=messages, counts=counts)
            for kind in CLASSES:
                result['counts'][kind] += counts[kind]
        except MailboxError as e:
            result['legacyTerminalInbox']['error'] = str(e)
            if str(e) == 'runtime_unavailable':
                result.update(event='runtime_unavailable', error=str(e))
            else:
                result.update(event='error', error=result.get('error', 'terminal_inbox_unavailable'))
    else:
        result['legacyTerminalInbox']['error'] = 'caller_identity_required'
    return result


def check_save_path(path):
    """Probe private sibling creation without replacing an existing saved batch."""
    path = Path(path)
    if path.is_dir():
        raise ValueError('save path must not be a directory')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.'):
        pass


def save_result(path, result):
    """Replace a saved batch only after its private JSON is fully written."""
    path = Path(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def consumer(c, terminal=None):
    """Serialize the entire CLI transaction, including output and ack receipts."""
    run = c['coordinator']['run']
    caller = os.environ.get('ORCA_TERMINAL_HANDLE') or terminal
    if not caller:
        raise MailboxError('caller_identity_required')
    key = hashlib.sha256(json.dumps([run, caller]).encode()).hexdigest()
    root = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'fleet/waits'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / key
    fd = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = lock.read().strip() or 'unknown'
            raise MailboxError(f'another fleet wait is active for run {run} (pid {pid})')
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        yield _AckReceipt(path.with_suffix('.json'))


class _AckReceipt:
    def __init__(self, path):
        self.path = path

    def pending(self, explicit=None, auto=False):
        pending = None
        if self.path.exists():
            pending = json.loads(self.path.read_text()).get('deliveryId')
        return explicit or (pending if auto else None)

    def consume(self):
        # Journal intent only after validation/binding, immediately before check.
        save_result(self.path, {'deliveryId': None})

    def printed(self, result):
        for batch in result.get('batches', []):
            if batch.get('deliveryId') and not batch.get('nativeAutoAck'):
                save_result(self.path, {'deliveryId': batch['deliveryId']})


def run_wait(c, args):
    """CLI lifecycle: lock -> save -> fully flush output -> arm the next ack."""
    if args.save:
        check_save_path(args.save)
    bodies = args.bodies if args.bodies is not None else args.summary
    if args.auto_ack and args.summary and not bodies:
        raise ValueError('--auto-ack requires full output; remove --no-bodies')
    with consumer(c, args.terminal) as receipt:
        ack_run = receipt.pending(args.ack_run, args.auto_ack)
        recoveries = []
        while True:
            result = wait(c, args.timeout, ack_run, args.terminal, args.wake_on, args.interval,
                          before_check=receipt.consume)
            if recoveries:
                result['rearmEvents'] = list(recoveries)
            if args.save:
                try:
                    save_result(args.save, result)
                except OSError as error:
                    result['saveError'] = str(error)
                    print(json.dumps(result), flush=True)
                    return 2
            if result.get('batches') or not args.loop or result['event'] not in ('timeout', 'rearm'):
                break
            ack_run = None
            if result['event'] == 'rearm':
                recoveries.append({key: value for key, value in result.items()
                                   if key != 'rearmEvents'})
        print(summary(result, bodies=bodies, legacy_history=args.legacy_history)
              if args.summary or bodies else json.dumps(result),
              flush=True)
        if args.auto_ack:
            receipt.printed(result)
        return {'messages': 0, 'timeout': 3, 'error': 2, 'rearm': 4,
                'runtime_unavailable': 2}[result['event']]


def summary(result, bodies=False, legacy_history=False):
    """Present current deliveries; legacy diagnostics are explicitly opt-in."""
    def field(value):
        return json.dumps(value, ensure_ascii=True)

    def body_text(value):
        # Mail is untrusted terminal input. Retain all content, but make control
        # bytes and bidi/format overrides visible rather than executable.
        return ''.join(json.dumps(ch, ensure_ascii=True)[1:-1]
                       if ch not in '\n\t' and unicodedata.category(ch) in ('Cc', 'Cf')
                       else ch for ch in value)

    lines = [f"FLEET-WAIT event={result['event']} counts={json.dumps(result['counts'])}"]
    if result.get('error'):
        lines.append(f"ERROR {field(result['error'])}")
    for batch in result['batches']:
        lines.append(f"DELIVERY {field(batch['deliveryId'])} source={batch['source']} "
                     f"n={len(batch['messages'])}")
        for row in batch['messages']:
            if bodies or row['classification'] != 'supervisor_relay':
                sender = row.get('from_handle', row.get('from', ''))
                lines.append(f"{field(row['id'])} {field(sender)} "
                             f"{row['classification']} {field(row.get('subject', ''))}")
                payload = row.get('payload')
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except ValueError:
                        payload = None
                report = payload.get('fleet_report') if isinstance(payload, dict) else None
                if isinstance(report, dict):
                    lines[-1] += (f" kind={field(report.get('kind'))}"
                                  f" PR={field(report.get('pr'))} head={field(report.get('head'))}")
                if bodies:
                    lines[-1] += f" type={field(row.get('type'))}"
                    lines.extend(('--- BODY ---', body_text(row.get('body') or ''),
                                  '--- END MESSAGE ---'))
    if bodies and legacy_history:
        legacy = result.get('legacyTerminalInbox', {})
        if legacy.get('messages'):
            lines.append('LEGACY TERMINAL INBOX (read-only history)')
            lines.append(summary({'event': result['event'], 'counts': legacy.get('counts', {}),
                                  'batches': [{'deliveryId': None, 'source': 'legacy',
                                               'messages': legacy['messages']}]}, bodies=True))
    return '\n'.join(lines)

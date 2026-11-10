"""Private, journaled Orca restart with preservation checks and no relaunches."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from . import orca_capture, state
from .commands import CommandError, orca, run
from .orca_restart_preflight import load_plan, inspect, RESTART_ARGV


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _private(path, directory=False):
    path = Path(path)
    info = path.lstat()
    if (path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077 or
            (not path.is_dir() if directory else not path.is_file())):
        raise ValueError('unsafe restart evidence permissions or ownership')
    return path


def _atomic(path, value):
    state.write({'_state': str(path.parent)}, path.stem, value)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class PreservationError(ValueError):
    """Source-defined failure reason safe to retain in the private journal."""


class Journal:
    def __init__(self, plan):
        self.plan = plan
        self.directory = Path(plan['state_dir'])
        if not self.directory.exists():
            self.directory.mkdir(mode=0o700, parents=False)
        _private(self.directory, True)
        self.path = self.directory / 'journal.json'
        self.plan_digest = _digest({k: v for k, v in plan.items() if k != '_path'})
        if self.path.exists():
            self.data = json.loads(_private(self.path).read_text())
            if self.data.get('schema') != 2 or self.data.get('plan_sha256') != self.plan_digest:
                raise ValueError('restart plan changed; reconcile original journal, do not replay')
        else:
            self.data = {'schema': 2, 'plan_id': plan['id'], 'plan_sha256': self.plan_digest,
                         'steps': {}, 'events': [], 'status': 'new'}
            self.save()

    def save(self):
        self.data['updated_at'] = datetime.now(timezone.utc).isoformat()
        _atomic(self.path, self.data)
        _atomic(self.directory / 'status.json', {
            key: self.data[key] for key in ('schema', 'plan_id', 'status', 'updated_at')})

    def event(self, step, status, receipt=None):
        entry = {'step': step, 'status': status,
                 'at': datetime.now(timezone.utc).isoformat()}
        if receipt is not None:
            entry['receipt'] = receipt
        self.data['events'].append(entry)
        self.data['steps'][step] = entry
        self.data['status'] = status
        self.save()
        return entry

    def step(self, name):
        return self.data['steps'].get(name, {})

    def done(self, name):
        return self.step(name).get('status') == 'done'

    def evidence(self, name):
        result = json.loads(_private(self.directory / (name + '.json')).read_text())
        if not self.done(name) or _digest(result) != self.step(name)['receipt']['sha256']:
            raise PreservationError('restart evidence changed; manual reconciliation required')
        return result

    def record(self, name, result):
        _atomic(self.directory / (name + '.json'), result)
        self.event(name, 'done', {'sha256': _digest(result)})
        return result


def _caller(c):
    expected = c['coordinator']
    if os.environ.get('ORCA_TERMINAL_HANDLE') != expected['terminal']:
        raise PreservationError('initiating caller must own the configured coordinator terminal')
    terminal = orca(c, 'terminal', 'show', '--terminal', expected['terminal']).get('terminal', {})
    if (terminal.get('handle') != expected['terminal'] or not terminal.get('incarnationId') or
            terminal.get('worktreePath') != c['root'] or not terminal.get('connected') or
            not terminal.get('writable') or terminal.get('orphaned')):
        raise PreservationError('initiating caller terminal is not connected and writable')
    binding = orca(c, 'orchestration', 'run-current', '--from', expected['terminal'])
    if not isinstance(binding.get('run'), dict) or binding['run'].get('id') != expected['run']:
        raise PreservationError('initiating caller Run binding changed')
    return terminal


def _service(unit, timeout=60):
    text = run(['/usr/bin/systemctl', 'show', unit, '--property=MainPID,ActiveState'],
               timeout=timeout)
    return dict(row.split('=', 1) for row in text.splitlines() if '=' in row)


def _wait_ready(c, plan, target):
    deadline = time.monotonic() + plan['timeout_seconds']
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreservationError('replacement service active/new PID/runtime unverified; no retry')
        service, status = {}, {}
        try:
            service = _service(plan['service'], timeout=remaining)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                status = orca(c, 'status', timeout=remaining)
        except (CommandError, ValueError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreservationError('replacement service active/new PID/runtime unverified; no retry')
        runtime = status.get('runtime', {})
        pid = int(service.get('MainPID', 0))
        if (service.get('ActiveState') == 'active' and pid > 0 and pid != target['pid'] and
                runtime.get('state') == 'ready' and runtime.get('reachable') and
                runtime.get('runtimeId') and runtime['runtimeId'] != target['runtime_id']):
            return {'runtime_id': runtime['runtimeId'], 'service_pid': pid}
        time.sleep(min(1, remaining))


def _capture(c, projects, journal, name):
    return journal.record(name, orca_capture.collect(
        c, list(dict.fromkeys(p['service'] for p in projects)),
        run_ids=list(dict.fromkeys(p['run'] for p in projects))))


def _restart_service(c, plan, proof, journal):
    target = proof['target']
    prior = journal.step('restart')
    if prior:
        # Even a failed/timed-out invocation may have mutated systemd. Observe only.
        receipt = _wait_ready(c, plan, target)
        if journal.done('restart') and receipt != prior['receipt']:
            raise PreservationError('recorded replacement service changed; no retry')
        journal.event('restart', 'done', receipt)
        return receipt
    checked = inspect(plan, c)
    if checked['blockers'] or checked['target'] != target or checked['projects'] != proof['projects']:
        raise PreservationError('restart authority or project bindings changed before mutation')
    before = checked['snapshot']
    if _capture_errors(before):
        raise PreservationError('BEFORE capture incomplete; restart withheld')
    required_runs = {c['coordinator']['run'], *(p['run'] for p in checked['projects'])}
    if not required_runs <= before.get('mailboxes', {}).keys():
        raise PreservationError('BEFORE project pending mail inventory absent')
    _mail_changes(before, before)
    # Before any restart intent, refresh the baseline from the final authority
    # inspection. A resumed preflight/BEFORE may precede newly added handles/mail.
    journal.record('before', before)
    _caller(c)
    journal.event('restart', 'intent', {'target': target, 'argv': RESTART_ARGV})
    try:
        run(list(RESTART_ARGV), timeout=plan['timeout_seconds'])
    except CommandError:
        # Intent survives interruption; success is established by observation, not exit code.
        pass
    receipt = _wait_ready(c, plan, target)
    journal.event('restart', 'done', receipt)
    return receipt


def _mail_changes(before, after):
    changes = {}
    if not before.get('mailboxes'):
        raise PreservationError('BEFORE pending mail inventory absent')
    for identifier, mailbox in before.get('mailboxes', {}).items():
        current = after.get('mailboxes', {}).get(identifier, {})
        if not mailbox.get('complete') or not current.get('complete'):
            raise PreservationError('pending mail inventory incomplete')
        old = {row['id'] for row in mailbox['messages']}
        new = {row['id'] for row in current['messages']}
        changes[identifier] = {'lost': sorted(old - new), 'new': sorted(new - old)}
    return changes


def _capture_errors(snapshot):
    # Session census is diagnostic only: no process/session relaunch is authorized.
    return [error for error in snapshot.get('errors', [])
            if error not in ('proc_scan_incomplete', 'owned_omp_incomplete')]


def _verify(c, plan, projects, before, after, receipt):
    if _capture_errors(before) or _capture_errors(after):
        raise PreservationError('preservation capture incomplete')
    if not before.get('boot_id') or before['boot_id'] != after.get('boot_id'):
        raise PreservationError('host identity changed during restart')
    service = _service(plan['service'])
    runtime = after.get('runtime', {})
    if (service.get('ActiveState') != 'active' or
            int(service.get('MainPID', 0)) != receipt['service_pid'] or
            runtime.get('runtimeId') != receipt['runtime_id'] or
            runtime.get('state') != 'ready' or not runtime.get('reachable')):
        raise PreservationError('replacement service inactive or identity changed')
    terminals = {row['handle']: row for row in after['terminals']}
    for old in before['terminals']:
        current = terminals.get(old['handle'], {})
        if not current.get('connected') or current.get('orphaned'):
            raise PreservationError('BEFORE terminal missing or disconnected AFTER')
        for field in ('incarnationId', 'worktreePath'):
            if old.get(field) != current.get(field):
                raise PreservationError('BEFORE terminal identity changed AFTER')
    services = {row['unit']: row for row in after['services']}
    for project in projects:
        terminal = terminals.get(project['terminal'], {})
        if (not terminal.get('writable') or not isinstance(terminal.get('binding'), dict) or
                terminal['binding'].get('id') != project['run']):
            raise PreservationError('project coordinator Run binding changed')
        if services.get(project['service'], {}).get('ActiveState') != 'active':
            raise PreservationError('project supervisor inactive AFTER')
    _caller(c)


def _notify(c, projects, journal, outcome):
    pending = []
    for project in projects:
        step = 'notify-' + outcome + '-' + project['name']
        if journal.step(step):
            if not journal.done(step):
                pending.append(step)
            continue  # Never duplicate an ambiguous enqueue, including during recovery.
        try:
            _caller(c)
            body = ('Orca restart ' + journal.data['plan_id'] + ': ' + outcome +
                    '. Review the private restart journal and pending-ID comparison; '
                    'can you confirm your surviving terminal and Run inbox? '
                    'Enqueue receipt does not establish delivery or read status.')
            journal.event(step, 'intent', {'destination_run': project['run'],
                                         'sender_terminal': c['coordinator']['terminal'],
                                         'sender_run': c['coordinator']['run']})
            result = orca(c, 'orchestration', 'send', '--from', c['coordinator']['terminal'],
                          '--run', c['coordinator']['run'], '--to', 'run:' + project['run'],
                          '--type', 'question', '--subject', 'Orca restart preservation check', '--body', body)
            message_id = result.get('message', {}).get('id')
            if not isinstance(message_id, str) or not message_id:
                raise PreservationError('notification enqueue receipt missing')
            journal.event(step, 'done', {'message_id': message_id,
                                        'destination_run': project['run'], 'enqueue_receipt_only': True})
        except (CommandError, OSError, ValueError, KeyError, TypeError):
            pending.append(step)
    return pending


def _pending_count(result, run_id):
    """Reject partial, capped or malformed inventories; zero must be proven."""
    if not isinstance(result, dict):
        return None
    rows = result.get('messages')
    if (not isinstance(rows, list) or len(rows) >= 100 or
            type(result.get('count')) is not int or result['count'] != len(rows) or
            result.get('runId') != run_id or
            any(result.get(key) for key in
                ('truncated', 'cancelled', 'connectionLost', 'timedOut', 'error')) or
            result.get('acknowledged') is not None):
        return None
    ids = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or
                row['id'] in ids or row.get('run_id') != run_id or
                row.get('to_handle') != 'run:' + run_id or
                row.get('delivery_contract') != 'current_delivery'):
            return None
        ids.add(row['id'])
    return len(rows)


def _pending_evidence(result, count):
    """Persist identities only, never inbox bodies/subjects or arbitrary native fields."""
    if not isinstance(result, dict):
        result = {}
    def pick(value, fields):
        return {key: value[key] for key in fields
                if isinstance(value, dict) and key in value
                and isinstance(value[key], (str, int, float, bool, type(None)))}
    metadata = pick(result, ('runId', 'count', 'deliveryId', 'requestId'))
    metadata['complete'] = count is not None
    metadata['mutation'] = pick(result.get('mutation'), ('requestId', 'replayed'))
    rows = result.get('messages')
    metadata['messages'] = [
        pick(row, (*orca_capture.MESSAGE_FIELDS, 'deliveryId'))
        for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return metadata


def _consumption(c, plan, projects, journal, outcome):
    """Observe consumption after enqueue; never consume or send another question."""
    name = 'consumption-' + outcome
    start_name = name + '-started'
    prior = None
    if journal.done(name):
        prior = journal.evidence(name)
        if prior['finished']:
            return prior
    if not journal.step(start_name):
        journal.event(start_name, 'started', {'started_at': time.time()})
    started = journal.step(start_name)['receipt']['started_at']
    timeout = plan.get('consumption_timeout_seconds', 120)
    remaining = max(0, timeout - max(0, time.time() - started))
    deadline = time.monotonic() + remaining
    rows = []
    while True:
        if prior is not None and time.monotonic() >= deadline:
            prior['finished'] = True
            prior['elapsed_seconds'] = round(max(0, time.time() - started), 3)
            for row in prior['projects']:
                row['elapsed_seconds'] = prior['elapsed_seconds']
            journal.record(name, prior)
            return prior
        rows = []
        for project in projects:
            notification = journal.step('notify-' + outcome + '-' + project['name'])
            receipt = notification.get('receipt', {})
            message_id = receipt.get('message_id') if notification.get('status') == 'done' else None
            count = None
            remaining = deadline - time.monotonic()
            if remaining > 0 and project.get('run') and project.get('terminal'):
                try:
                    result = orca(c, 'orchestration', 'check', '--terminal', project['terminal'],
                                  '--run', project['run'], '--peek', timeout=min(60, remaining))
                    count = _pending_count(result, project['run'])
                    journal.record(name + '-peek-' + str(len(journal.data['events'])),
                                   _pending_evidence(result, count))
                except (CommandError, OSError, ValueError, KeyError, TypeError):
                    count = None
            status = ('unknown' if count is None or not message_id else
                      'consumed' if count == 0 else 'undrained')
            elapsed = round(max(0, time.time() - started), 3)
            rows.append({'project': project['name'], 'terminal': project.get('terminal'),
                         'main': project.get('terminal'), 'run': project.get('run'),
                         'result_message_id': message_id, 'status': status,
                         'pending_count': count, 'inventory_complete': count is not None,
                         'elapsed_seconds': elapsed,
                         'nudge': None if status == 'consumed' else
                         'Nudge Main ' + str(project.get('terminal')) + ' for project ' +
                         project['name'] + ' to drain Run ' + str(project.get('run'))})
        finished = all(row['status'] == 'consumed' for row in rows) or time.monotonic() >= deadline
        report = {'projects': rows, 'attention_required': [
            row for row in rows if row['status'] != 'consumed'],
            'timeout_seconds': timeout, 'elapsed_seconds': round(max(0, time.time() - started), 3),
            'finished': finished}
        journal.record(name, report)
        prior = report
        if finished:
            return report
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def conduct(c, path, dry_run=False):
    plan = load_plan(path)
    # Never search the initiating shell's PATH for the Orca or OMP executables.
    # OMP is observed only; the conductor never launches it.
    if not Path(c['orca']).is_absolute():
        executable = shutil.which(c['orca'], path=os.defpath)
        if not executable:
            raise ValueError('configure an absolute Orca CLI executable for restart')
        c = {**c, 'orca': executable}
    if dry_run:
        proof = inspect(plan, c)
        return {'event': 'dry-run', 'plan_id': plan['id'], 'target': proof['target'],
                'before': proof['snapshot'], 'projects': proof['projects'],
                'blockers': proof['blockers'], 'actions': proof['actions']}
    directory = Path(plan['state_dir'])
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=False)
    _private(directory, True)
    with state.lock({'_state': plan['state_dir']}, 'conductor'):
        journal = Journal(plan)
        projects = []
        changes = {}
        try:
            _caller(c)
            if journal.done('preflight'):
                proof = journal.evidence('preflight')
            else:
                proof = inspect(plan, c)
                projects = proof['projects']
                if proof['blockers']:
                    journal.event('preflight', 'blocked', {'blockers': proof['blockers']})
                    pending = _notify(c, [p for p in projects if p.get('run')], journal, 'blocked')
                    consumption = _consumption(c, plan, projects, journal, 'blocked')
                    journal.data['status'] = 'blocked'
                    journal.save()
                    return {'event': 'blocked', 'blockers': proof['blockers'],
                            'notifications_pending': pending, 'journal': str(journal.path),
                            'consumption': consumption['projects'],
                            'attention_required': consumption['attention_required']}
                journal.record('preflight', proof)
            projects = proof['projects']
            before = journal.evidence('before') if journal.step('restart') else proof['snapshot']
            if _capture_errors(before):
                raise PreservationError('BEFORE capture incomplete; restart withheld')
            required_runs = {c['coordinator']['run'], *(p['run'] for p in projects)}
            if not required_runs <= before.get('mailboxes', {}).keys():
                raise PreservationError('BEFORE project pending mail inventory absent')
            _mail_changes(before, before)
            journal.data['stage'] = 'restart'
            journal.save()
            receipt = _restart_service(c, plan, proof, journal)
            before = journal.evidence('before')
            journal.data['stage'] = 'preservation'
            journal.save()
            reused_after = journal.done('after')
            after = journal.evidence('after') if reused_after else _capture(c, projects, journal, 'after')
            changes = _mail_changes(before, after)
            preserved = journal.done('preservation')
            if preserved:
                if changes != journal.step('preservation')['receipt']['pending_mail']:
                    raise PreservationError('completed preservation comparison changed')
            else:
                journal.event('pending-mail', 'done', changes)
                if any(row['lost'] for row in changes.values()):
                    raise PreservationError('BEFORE pending message IDs lost AFTER')
            # Pending mail can legitimately drain after preservation. Keep the
            # original AFTER/comparison, but check current terminal/service health.
            observation = (_capture(c, projects, journal, 'resume-observation')
                           if reused_after else after)
            _verify(c, plan, projects, before, observation, receipt)
            if not preserved:
                journal.event('preservation', 'done', {'pending_mail': changes})
            journal.data['stage'] = 'notifications'
            journal.save()
            pending = _notify(c, projects, journal, 'complete')
            if pending:
                raise PreservationError('notification enqueue unresolved; never duplicate ambiguous send')
            consumption = _consumption(c, plan, projects, journal, 'complete')
            journal.data['status'] = 'complete'
            journal.save()
            return {'event': 'complete', 'journal': str(journal.path),
                    'runtime_id': receipt['runtime_id'], 'pending_mail': changes,
                    'consumption': consumption['projects'],
                    'attention_required': consumption['attention_required']}
        except (OSError, ValueError, CommandError, KeyError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, PreservationError) else 'restart evidence or command unavailable'
            journal.event('failure', 'failed_manual_reconciliation', {
                'stage': journal.data.get('stage', 'preflight'), 'reason': reason,
                'error_type': type(exc).__name__,
                'guidance': 'Inspect retained receipts; never repeat an unresolved mutation intent.'})
            pending = _notify(c, [p for p in projects if p.get('run')], journal, 'failed')
            consumption = _consumption(c, plan, projects, journal, 'failed')
            journal.data['status'] = 'failed_manual_reconciliation'
            journal.save()
            return {'event': 'failed_manual_reconciliation', 'journal': str(journal.path),
                    'reason': reason, 'pending_mail': changes, 'notifications_pending': pending,
                    'consumption': consumption['projects'],
                    'attention_required': consumption['attention_required']}

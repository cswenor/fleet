"""Private read-only restart evidence; never starts, stops, binds or consumes mail."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import sys
from .commands import CommandError, orca, run
from .config import load
from .process_identity import omp_executable


PROC = Path('/proc')
TERMINAL_FIELDS = ('handle', 'incarnationId', 'worktreeId', 'worktreePath',
                   'connected', 'writable', 'orphaned', 'executionHostId', 'agentIdentity')
MESSAGE_FIELDS = ('id', 'run_id', 'delivery_contract', 'from_handle', 'to_handle',
                  'type', 'read', 'sequence', 'created_at', 'delivered_at')
RUN_FIELDS = ('id', 'coordinator_handle', 'consumer_generation', 'legacy')


def _pick(value, fields):
    return {key: value[key] for key in fields if key in value}


def _stat(proc):
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': int(proc.name), 'start_ticks': int(fields[19]),
            'state': fields[0], 'ppid': int(fields[1]), 'pgrp': int(fields[2]),
            'tty': int(fields[4]), 'foreground_pgrp': int(fields[5])}


def _session(path):
    """Stream only allowlisted event metadata; never copy transcript contents."""
    result = {'path': str(path), 'model': None, 'thinking': None}
    malformed = False
    headers = 0
    with path.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                malformed = True
                continue
            kind = event.get('type')
            if kind == 'session':
                headers += 1
                if 'timestamp' in event:
                    result['started_at'] = event['timestamp']
                result.update(_pick(event, ('id', 'cwd')))
            elif kind == 'model_change':
                result['model'] = _pick(event, ('model', 'resolvedModelIsFallback', 'timestamp', 'id'))
            elif kind == 'thinking_level_change':
                result['thinking'] = _pick(event, ('thinkingLevel', 'timestamp', 'id'))
    result['complete'] = bool(headers == 1 and result.get('id') and result['model'] and result['model'].get('model')
                              and result['thinking'] and result['thinking'].get('thinkingLevel') and not malformed)
    return result

def _argv_session(proc, expected_id=None):
    """Prove a fresh canonical log from one exact live argv, never default paths."""
    command = (proc / 'cmdline').read_bytes()
    arguments = command.split(b'\0')
    if arguments[-1] == b'':
        arguments.pop()
    argv = [os.fsdecode(arg) for arg in arguments]
    options = argv[:argv.index('--')] if '--' in argv else argv
    if any(arg == flag or arg.startswith(flag + '=')
           for arg in options for flag in ('--resume', '--continue', '--restore')):
        return None
    directories = []
    for index, arg in enumerate(options):
        if arg == '--session-dir':
            if index + 1 >= len(options):
                raise ValueError('session directory argument missing')
            directories.append(options[index + 1])
        elif arg.startswith('--session-dir='):
            directories.append(arg.split('=', 1)[1])
    if not directories:
        return None
    if len(directories) != 1:
        raise ValueError('session directory ambiguous')
    before = _stat(proc)
    executable = (proc / 'exe').readlink()
    if not argv or not omp_executable(executable, argv[0]):
        raise ValueError('not the recorded model executable')
    boot = (PROC / 'sys/kernel/random/boot_id').read_text().strip()
    cwd = str((proc / 'cwd').resolve(strict=True))
    uid_rows = [line.split()[1:] for line in (proc / 'status').read_text().splitlines()
                if line.startswith('Uid:')]
    if len(uid_rows) != 1 or len(uid_rows[0]) != 4:
        raise ValueError('process owner unproven')
    real_uid = int(uid_rows[0][0])
    directory = Path(directories[0])
    if not directory.is_absolute() or '..' in directory.parts:
        raise ValueError('session directory is not absolute')
    for part in (directory, *directory.parents):
        if part.is_symlink():
            raise ValueError('session directory traverses a symlink')
    info = directory.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != real_uid or info.st_mode & 0o022:
        raise ValueError('unsafe session directory ownership or permissions')
    logs = list(directory.glob('*.jsonl'))
    if len(logs) != 1 or logs[0].is_symlink() or not logs[0].is_file():
        raise ValueError('canonical session log ambiguous or unsafe')
    log_info = logs[0].stat()
    if log_info.st_uid != real_uid or log_info.st_mode & 0o022:
        raise ValueError('unsafe canonical log ownership or permissions')
    try:
        session = _session(logs[0])
    except (AttributeError, TypeError, ValueError):
        raise ValueError('canonical session metadata invalid') from None
    if (not session['complete'] or not isinstance(session.get('id'), str) or session.get('cwd') != cwd or
            expected_id is not None and session['id'] != expected_id):
        raise ValueError('canonical session identity mismatch')
    if not isinstance(session.get('started_at'), str):
        raise ValueError('canonical session start time missing')
    started = datetime.fromisoformat(session['started_at'])
    boot_clock = time.clock_gettime(time.CLOCK_BOOTTIME)
    now = time.time()
    # start_ticks is truncated to clock ticks. Require a timestamp after its
    # conservative upper bound, rather than accepting a possibly older log.
    process_started = now - boot_clock + (before['start_ticks'] + 1) / os.sysconf('SC_CLK_TCK')
    if started.tzinfo is None or not process_started <= started.timestamp() <= now:
        raise ValueError('canonical log predates process or is future dated')
    after_directory, after_log = directory.stat(), logs[0].stat()
    after_uid_rows = [line.split()[1:] for line in (proc / 'status').read_text().splitlines()
                      if line.startswith('Uid:')]
    if (_stat(proc)['start_ticks'] != before['start_ticks'] or
            (proc / 'cmdline').read_bytes() != command or
            (proc / 'exe').readlink() != executable or
            str((proc / 'cwd').resolve(strict=True)) != cwd or
            (PROC / 'sys/kernel/random/boot_id').read_text().strip() != boot or
            after_uid_rows != uid_rows or logs[0].is_symlink() or
            any(part.is_symlink() for part in (directory, *directory.parents)) or
            any(getattr(after_directory, key) != getattr(info, key)
                for key in ('st_dev', 'st_ino', 'st_uid', 'st_mode')) or
            any(getattr(after_log, key) != getattr(log_info, key)
                for key in ('st_dev', 'st_ino', 'st_uid', 'st_mode'))):
        raise ValueError('argv session evidence changed')
    proof = {'pid': before['pid'], 'start_ticks': before['start_ticks'], 'boot_id': boot,
             'argv_sha256': hashlib.sha256(command).hexdigest(), 'cwd': cwd,
             'directory': str(directory), 'directory_device': info.st_dev,
             'directory_inode': info.st_ino, 'session_path': str(logs[0]),
             'session_id': session['id'], 'log_device': log_info.st_dev, 'log_inode': log_info.st_ino}
    return proof, session



def _processes(paths):
    rows, errors, diagnostics = [], [], []
    if not sys.platform.startswith('linux') or not PROC.is_dir():
        return [], ['local_linux_proc_unavailable'], []
    for proc in PROC.glob('[0-9]*'):
        field, owned, before, uids = 'owner', None, None, None
        try:
            # Non-dumpable processes can have root-owned /proc directories even
            # when their real/effective credentials are ours. Kernel status,
            # not directory ownership, determines the conservative user scope.
            uid_rows = [line.split()[1:] for line in (proc / 'status').read_text().splitlines()
                        if line.startswith('Uid:')]
            if len(uid_rows) != 1 or len(uid_rows[0]) != 4 or not all(v.isdecimal() for v in uid_rows[0]):
                raise ValueError('process credentials unavailable')
            uids = list(map(int, uid_rows[0]))
            owned = os.getuid() in uids
            if not owned:
                diagnostics.append({'pid': int(proc.name), 'field': field, 'reason': 'non_owned'})
                continue
            field = 'stat'
            before = _stat(proc)
            field = 'exe'
            executable = (proc / 'exe').readlink()
            if not omp_executable(executable, 'omp'):
                continue
            field = 'cmdline'
            command = (proc / 'cmdline').read_bytes()
            argv = command.split(b'\0')
            if not omp_executable(executable, os.fsdecode(argv[0])):
                continue
            helper = next((kind for kind in ('daemon_broker', 'text_predict', 'js_eval_process')
                           if argv[1:2] == [('__omp_worker_' + kind).encode()]), None)
            flags = ('--model', '--thinking', '--session-dir', '--resume', '--restore',
                     '--continue', '--print', '--mode', '--no-session')
            shape = [flag for flag in flags if any(
                arg == flag.encode() or arg.startswith((flag + '=').encode()) for arg in argv)]
            field = 'cwd'
            cwd = str((proc / 'cwd').resolve(strict=True))
            row = {**before, 'exe': str(executable), 'cwd': cwd, 'configured_cwd': cwd in paths,
                   'kind': helper or 'agent', 'argv_shape': shape,
                   'argv_sha256': hashlib.sha256(command).hexdigest(),
                   'sessions': [], 'errors': []}
            logs = set()
            field = 'fd'
            for fd in (proc / 'fd').iterdir():
                try:
                    path = fd.readlink()
                    # Pipes/sockets/anon_inode are not filesystem paths. Do not
                    # resolve them and mistake ENOENT for a lost session log.
                    if path.is_absolute() and path.suffix == '.jsonl':
                        logs.add(path)
                except OSError as exc:
                    diagnostics.append({'pid': int(proc.name), 'field': 'fd_readlink',
                                        'errno': exc.errno, 'reason': 'fd_changed_or_unreadable'})
                    row['errors'].append('fd_scan_incomplete')
            argv_session = None
            if not helper and not logs:
                try:
                    argv_session = _argv_session(proc)
                    if argv_session:
                        binding, session = argv_session
                        if (binding['start_ticks'] != before['start_ticks'] or
                                binding['argv_sha256'] != row['argv_sha256'] or binding['cwd'] != cwd):
                            raise ValueError('argv session process changed')
                        row['argv_session'] = binding
                        row['sessions'].append(session)
                except (OSError, ValueError, KeyError, TypeError):
                    row['errors'].append('argv_session_unproven')
            for path in sorted(logs):
                try:
                    row['sessions'].append(_session(path))
                except (OSError, ValueError, TypeError, AttributeError):
                    row['errors'].append('session_unreadable')
            field = 'stat_after'
            after = _stat(proc)
            row['identity_stable'] = (before['start_ticks'] == after['start_ticks']
                                      and (proc / 'exe').readlink() == executable
                                      and (proc / 'cmdline').read_bytes() == command
                                      and str((proc / 'cwd').resolve(strict=True)) == cwd)
            row['foreground'] = before['tty'] != 0 and before['pgrp'] == before['foreground_pgrp']
            if not row['identity_stable']:
                row['errors'].append('process_identity_changed')
            row['session_status'] = 'not_applicable_internal_worker' if helper else 'observed'
            if not helper and (not row['sessions'] or any(not s['complete'] for s in row['sessions'])):
                row['errors'].append('session_metadata_incomplete')
            if row['errors']:
                errors.append('owned_omp_incomplete')
            rows.append(row)
        except (OSError, ValueError, IndexError) as exc:
            reason = 'vanished_mid_scan' if not proc.exists() else 'unreadable'
            try:
                current = _stat(proc)
            except (OSError, ValueError, IndexError):
                current = None
            if before and (before['state'] == 'Z' or
                           current and current['start_ticks'] == before['start_ticks'] and current['state'] == 'Z'):
                reason = 'zombie_no_executable'
            diagnostics.append({'pid': int(proc.name), 'owned': owned, 'uids': uids, 'field': field,
                                'errno': getattr(exc, 'errno', None), 'reason': reason})
            if reason == 'unreadable':
                errors.append('proc_scan_incomplete')
    return rows, sorted(set(errors)), diagnostics


def _mailbox(query, run_id):
    current = query('run', 'orchestration', 'run-show', '--id', run_id)
    run = current.get('run')
    run = run if isinstance(run, dict) else {}
    history = query('inbox', 'orchestration', 'inbox', '--terminal', 'run:' + run_id,
                    '--limit', '1000')
    history_rows = history.get('messages')
    history_returned = len(history_rows) if isinstance(history_rows, list) else None
    # Orca's read-only Run peek selects unread current_delivery rows (including
    # delivered but unacknowledged rows), oldest first, with a fixed limit of 100.
    # Its count is the returned size, not a total. At the cap completeness is unknown.
    terminal = run.get('coordinator_handle')
    pending = (query('pending', 'orchestration', 'check', '--terminal', terminal,
                     '--run', run_id, '--peek')
               if run.get('id') == run_id and isinstance(terminal, str) and terminal else {})
    rows = pending.get('messages')
    valid = (isinstance(rows, list) and len(rows) <= 100 and
                type(pending.get('count')) is int and pending['count'] == len(rows) and
                pending.get('runId') == run_id and not pending.get('truncated') and
                not pending.get('cancelled') and not pending.get('connectionLost') and
                not pending.get('timedOut') and not pending.get('error') and
                pending.get('acknowledged') is None)
    ids = set()
    for row in rows if isinstance(rows, list) else []:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or
                row['id'] in ids or row.get('run_id') != run_id or
                row.get('to_handle') != 'run:' + run_id or
                row.get('delivery_contract') != 'current_delivery'):
            valid = False
        else:
            ids.add(row['id'])
    return {'run': _pick(run, RUN_FIELDS),
            'messages': [_pick(row, MESSAGE_FIELDS) for row in rows if isinstance(row, dict)]
                        if isinstance(rows, list) else [],
            'complete': valid and len(rows) < 100, 'valid': valid, 'limit': 100,
            'returned': len(rows) if isinstance(rows, list) else None,
            'history': {'count_lower_bound': history_returned,
                        'returned': history_returned, 'limit': 1000},
            'native_delivery_identity': 'unavailable_read_only_api'}


def collect(c, services, *, run_ids=()):
    errors = []
    def query(label, *args):
        try:
            result = orca(c, *args)
            if not isinstance(result, dict):
                raise TypeError('invalid query result')
            return result
        except (CommandError, ValueError, TypeError):
            errors.append(label + '_unavailable')
            return {}

    started = datetime.now(timezone.utc).isoformat()
    status = query('status', 'status')
    runtime = status.get('runtime', {})
    inventory = query('terminals', 'terminal', 'list', '--limit', '1000')
    terminals = []
    if inventory.get('truncated') or inventory.get('hostScope', {}).get('omittedHostIds'):
        errors.append('terminal_inventory_incomplete')
    for terminal in inventory.get('terminals', []):
        row = _pick(terminal, TERMINAL_FIELDS)
        # Titles are arbitrary user/agent text, not safe metadata. Preserve identity
        # for comparison without risking credentials in titles or terminal previews.
        title = terminal.get('title')
        row['title_sha256'] = hashlib.sha256(title.encode()).hexdigest() if isinstance(title, str) else None
        row['title_pad_refs'] = sorted(set(re.findall(r'\b[A-Z]+-[0-9]+\b', title or '')))
        bound = query('binding', 'orchestration', 'run-current', '--from', terminal['handle'])
        row['binding'] = (_pick(bound['run'], RUN_FIELDS) if isinstance(bound.get('run'), dict)
                          else None if 'run' in bound and bound['run'] is None else 'unknown')
        terminals.append(row)
    run_id = c['coordinator']['run']
    mailboxes = {identifier: _mailbox(query, identifier)
                 for identifier in dict.fromkeys((run_id, *run_ids))}
    if any(not mailbox['complete'] for mailbox in mailboxes.values()):
        errors.append('inbox_inventory_incomplete')
    processes, proc_errors, proc_diagnostics = _processes({t.get('worktreePath') for t in terminals} | set(c['builder']['slots']) | {c['root']})
    errors.extend(proc_errors)
    states = []
    for service in services:
        try:
            if sys.platform == 'darwin':
                from .launchd import status as launchd_status
                states.append(launchd_status(service))
                continue
            if not sys.platform.startswith('linux'):
                raise CommandError('service capture unsupported on this platform')
            text = run(['/usr/bin/systemctl', '--user', 'show', service,
                        '--property=LoadState,ActiveState,SubState,MainPID,NRestarts'])
            states.append({'unit': service, **dict(line.split('=', 1) for line in text.splitlines() if '=' in line)})
        except CommandError:
            states.append({'unit': service, 'state': 'unknown'})
            errors.append('service_state_unavailable')
    final = query('final_status', 'status').get('runtime', {})
    if not runtime.get('runtimeId') or final.get('runtimeId') != runtime.get('runtimeId'):
        errors.append('runtime_identity_unstable')
    boot_id = None
    if sys.platform.startswith('linux'):
        try:
            boot_id = (PROC / 'sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            errors.append('boot_identity_unavailable')
    else:
        errors.append('boot_identity_unavailable')
    return {'schema': 1, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'capture_pid': os.getpid(), 'boot_id': boot_id,
            'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
            'runtime': _pick(runtime, ('runtimeId', 'appVersion', 'state', 'reachable')),
            'runtime_process': _pick(status.get('app', {}), ('pid', 'running')),
            'terminals': terminals, 'processes': processes, 'services': states,
            'proc_diagnostics': proc_diagnostics,
            'mailboxes': mailboxes,
            'errors': sorted(set(errors)),
            'limitations': ['No consuming check/ack; Main must retain existing native Delivery receipt separately.',
                            'Titles hashed; no bodies, subjects, payloads, previews, raw argv or environments retained.',
                            'Processes mapped by cwd, not terminal ownership; absence never proves exit.',
                            'Open session logs only; known internal workers have no model session; other missing logs stay unknown.',
                            'Capture is sequential, not an atomic runtime snapshot; no idle/safety verdict.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', required=True, help='new private directory; never overwrite evidence')
    parser.add_argument('--service', action='append', default=[], help='additional supervisor unit (repeatable)')
    args = parser.parse_args()
    try:
        c = load(args.config)
        services = ['fleet-' + re.sub(r'[^a-zA-Z0-9_.-]', '-', c['repo']) + '.service', *args.service]
        if not all(re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service', s) for s in services):
            raise ValueError('invalid service unit')
        directory = Path(args.out).expanduser()
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        result = collect(c, list(dict.fromkeys(services)))
        path = directory / 'snapshot.json'
        with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'snapshot': str(path), 'runtime': result['runtime'],
                          'terminals': len(result['terminals']), 'processes': len(result['processes']),
                          'errors': result['errors']}))
        return 1 if result['errors'] else 0
    except (OSError, ValueError, CommandError):
        # No raw exception text: paths, command output and JSON may contain secrets.
        print('orca-capture: capture failed; check prerequisites and use a new output directory', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

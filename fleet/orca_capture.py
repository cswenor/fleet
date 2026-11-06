"""Private read-only restart evidence; never starts, stops, binds or consumes mail."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from .commands import CommandError, orca, run
from .config import load


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
    with path.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                malformed = True
                continue
            kind = event.get('type')
            if kind == 'session':
                result.update(_pick(event, ('id', 'cwd')))
            elif kind == 'model_change':
                result['model'] = _pick(event, ('model', 'resolvedModelIsFallback', 'timestamp', 'id'))
            elif kind == 'thinking_level_change':
                result['thinking'] = _pick(event, ('thinkingLevel', 'timestamp', 'id'))
    result['complete'] = bool(result.get('id') and result['model'] and result['model'].get('model')
                              and result['thinking'] and result['thinking'].get('thinkingLevel') and not malformed)
    return result


def _processes(paths):
    rows, errors = [], []
    if not sys.platform.startswith('linux') or not PROC.is_dir():
        return [], ['local_linux_proc_unavailable']
    for proc in PROC.glob('[0-9]*'):
        try:
            if (proc / 'exe').resolve().name != 'omp':
                continue
            cwd = str((proc / 'cwd').resolve(strict=True))
            if cwd not in paths:
                continue
            before = _stat(proc)
            row = {**before, 'cwd': cwd, 'sessions': [], 'errors': []}
            logs = set()
            for fd in (proc / 'fd').iterdir():
                try:
                    path = fd.resolve(strict=True)
                    if path.suffix == '.jsonl':
                        logs.add(path)
                except OSError:
                    continue
            for path in sorted(logs):
                try:
                    row['sessions'].append(_session(path))
                except (OSError, ValueError, TypeError, AttributeError):
                    row['errors'].append('session_unreadable')
            after = _stat(proc)
            row['identity_stable'] = before['start_ticks'] == after['start_ticks'] and before['pid'] == after['pid']
            row['foreground'] = before['tty'] != 0 and before['pgrp'] == before['foreground_pgrp']
            if not row['identity_stable']:
                row['errors'].append('process_identity_changed')
            if not row['sessions'] or any(not s['complete'] for s in row['sessions']):
                row['errors'].append('session_metadata_incomplete')
            rows.append(row)
        except (OSError, ValueError, IndexError):
            # Races and permission failures cannot prove an absent process exited.
            errors.append('proc_scan_incomplete')
    return rows, sorted(set(errors))


def collect(c, services):
    errors = []
    def query(label, *args):
        try:
            return orca(c, *args)
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
    current = query('run', 'orchestration', 'run-show', '--id', run_id)
    inbox = query('inbox', 'orchestration', 'inbox', '--terminal', 'run:' + run_id, '--limit', '1000')
    messages = inbox.get('messages', [])
    # This API has no pagination or Delivery identity. At the cap, don't claim a
    # complete set even if the server omits an explicit truncation flag.
    if len(messages) >= 1000 or inbox.get('truncated') or inbox.get('count', len(messages)) > len(messages):
        errors.append('inbox_inventory_incomplete')
    processes, proc_errors = _processes({t.get('worktreePath') for t in terminals} | set(c['builder']['slots']) | {c['root']})
    errors.extend(proc_errors)
    states = []
    for service in services:
        try:
            text = run(['systemctl', '--user', 'show', service,
                        '--property=LoadState,ActiveState,SubState,MainPID,NRestarts'])
            states.append({'unit': service, **dict(line.split('=', 1) for line in text.splitlines() if '=' in line)})
        except CommandError:
            states.append({'unit': service, 'state': 'unknown'})
            errors.append('service_state_unavailable')
    final = query('final_status', 'status').get('runtime', {})
    if not runtime.get('runtimeId') or final.get('runtimeId') != runtime.get('runtimeId'):
        errors.append('runtime_identity_unstable')
    return {'schema': 1, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'capture_pid': os.getpid(), 'boot_id': (PROC / 'sys/kernel/random/boot_id').read_text().strip(),
            'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
            'runtime': _pick(runtime, ('runtimeId', 'appVersion', 'state', 'reachable')),
            'runtime_process': _pick(status.get('app', {}), ('pid', 'running')),
            'terminals': terminals, 'processes': processes, 'services': states,
            'run': _pick(current.get('run') or {}, RUN_FIELDS),
            'mailbox': {'messages': [_pick(m, MESSAGE_FIELDS) for m in messages],
                        'native_delivery_identity': 'unavailable_read_only_api',
                        'limit': 1000, 'returned': len(messages)},
            'errors': sorted(set(errors)),
            'limitations': ['No consuming check/ack; Main must retain existing native Delivery receipt separately.',
                            'Titles hashed; no bodies, subjects, payloads, previews, argv or environments retained.',
                            'Processes mapped by cwd, not terminal ownership; absence never proves exit.',
                            'Open session logs only; missing/incomplete logs remain unknown.',
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

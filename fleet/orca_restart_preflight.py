"""Read-only, fail-closed proof for an explicitly approved shared Orca restart."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import yaml

from . import config, health, orca_capture, restart
from .commands import CommandError, json_run, orca, run
from .process_identity import omp_executable

PROC = Path('/proc')
# This is the sole privileged operation granted by the operator, not a generic
# service-management facility. Authorization probing below never executes it.
SERVICE = 'orca-serve.service'
RESTART_ARGV = ['sudo', '-n', 'systemctl', 'restart', SERVICE]
FAILURES = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
            UnicodeError, CommandError, RecursionError)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key, value in node.value:
        key = loader.construct_object(key, deep=True)
        if not isinstance(key, str) or key in result:
            raise ValueError('invalid mapping keys')
        result[key] = loader.construct_object(value, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise ValueError('invalid fields')


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(c in value for c in ('\0', '\r', '\n'))


def _absolute(value):
    if not _text(value) or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('absolute path required')
    return Path(value)


def _private(path, directory=False):
    path = _absolute(str(path))
    info = path.lstat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o077 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError('private owned path required')
    # A private leaf is not private if an ancestor can redirect it. Sticky /tmp
    # is safe for an owned private child, but arbitrary writable ancestors are not.
    for parent in path.parents:
        p = parent.lstat()
        if (not stat.S_ISDIR(p.st_mode) or p.st_uid not in (0, os.getuid()) or
                p.st_mode & 0o022 and not p.st_mode & stat.S_ISVTX):
            raise ValueError('unsafe path ancestor')
    return path


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed


def _argv(value):
    if not isinstance(value, list) or not value or not all(_text(v) for v in value):
        raise ValueError('argv required')


def _option(argv, flag):
    values = []
    for index, arg in enumerate(argv):
        if arg == flag:
            if index + 1 >= len(argv) or argv[index + 1].startswith('--'):
                raise ValueError('missing option value')
            values.append(argv[index + 1])
        elif arg.startswith(flag + '='):
            values.append(arg[len(flag) + 1:])
    if len(values) != 1:
        raise ValueError('unique option required')
    return values[0]


def _main_command(argv):
    _argv(argv)
    if Path(argv[0]).name != 'omp' or any(
            a.split('=', 1)[0] in ('--resume', '--restore', '--continue', '--no-session')
            or a in ('-r', '-c') for a in argv[1:]):
        raise ValueError('fresh omp command required')
    if argv.count('--session-dir') != 1 or any(a.startswith('--session-dir=') for a in argv):
        raise ValueError('literal session directory flag required')
    return _absolute(_option(argv, '--session-dir'))


def load_plan(path):
    """Load only the closed, private operator schema; never create any paths."""
    try:
        path = _private(_absolute(str(path)))
        _private(path.parent, directory=True)
        plan = yaml.load(path.read_text(), Loader=_UniqueLoader)
        _keys(plan, ('version', 'id', 'state_dir', 'execute_after', 'expires_at',
                     'target', 'service', 'projects', 'session_records', 'confirmations'),
              ('timeout_seconds', 'launch_attempts', 'allow_systemd_sigkill'))
        if type(plan['version']) is not int or plan['version'] != 1:
            raise ValueError('version required')
        if not isinstance(plan['id'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', plan['id']):
            raise ValueError('invalid plan id')
        state_dir = _absolute(plan['state_dir'])
        if state_dir.exists() or state_dir.is_symlink():
            _private(state_dir, directory=True)
        else:
            _private(state_dir.parent, directory=True)
        if _timestamp(plan['execute_after']) >= _timestamp(plan['expires_at']):
            raise ValueError('invalid execution window')
        if plan['service'] != SERVICE:
            raise ValueError('service outside privilege grant')
        if type(plan.setdefault('allow_systemd_sigkill', False)) is not bool:
            raise ValueError('invalid systemd escalation approval')
        target = plan['target']
        _keys(target, ('pid', 'start_ticks', 'exe', 'argv', 'cwd', 'runtime_id', 'runtime_pid'))
        for name in ('pid', 'start_ticks', 'runtime_pid'):
            if type(target[name]) is not int or target[name] <= 0:
                raise ValueError('invalid process identity')
        _absolute(target['exe'])
        _absolute(target['cwd'])
        _argv(target['argv'])
        if not _text(target['runtime_id']):
            raise ValueError('invalid runtime identity')
        for name, default, maximum in (('timeout_seconds', 60, 300), ('launch_attempts', 2, 3)):
            value = plan.setdefault(name, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid bounded attempt policy')
        projects = plan['projects']
        if not isinstance(projects, list) or not projects:
            raise ValueError('projects required')
        seen = {key: set() for key in ('name', 'config', 'worktree')}
        sessions = set()
        for project in projects:
            _keys(project, ('name', 'config', 'worktree', 'delivery', 'mode'), ('command', 'handoff'))
            if not isinstance(project['name'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', project['name']):
                raise ValueError('invalid project name')
            if type(project['delivery']) is not bool or project['mode'] not in ('main', 'shell'):
                raise ValueError('invalid project mode')
            for key in seen:
                if key != 'name':
                    _absolute(project[key])
                if project[key] in seen[key]:
                    raise ValueError('duplicate project')
                seen[key].add(project[key])
            if project['mode'] == 'main':
                if not {'command', 'handoff'} <= project.keys():
                    raise ValueError('Main startup proof required')
                _absolute(project['handoff'])
                if project['command'] != []:
                    session = str(_main_command(project['command']))
                    if session in sessions:
                        raise ValueError('duplicate new session directory')
                    sessions.add(session)
            elif {'command', 'handoff'} & project.keys():
                raise ValueError('shell cannot launch Main')
        records = plan['session_records']
        if not isinstance(records, list) or not records or len(set(records)) != len(records):
            raise ValueError('unique session record files required')
        for record in records:
            _absolute(record)
        _keys(plan['confirmations'], ('project_alpha_idle', 'fleet_idle'))
        for proof in plan['confirmations'].values():
            _absolute(proof)
        plan['_path'] = str(path)
        return plan
    except (*FAILURES, yaml.YAMLError, RecursionError):
        raise ValueError('invalid or nonprivate Orca restart plan') from None


def _json(path):
    return json.loads(_private(path).read_text())


def _process(pid):
    proc = PROC / str(pid)
    before = orca_capture._stat(proc)
    uids = [line.split()[1:] for line in (proc / 'status').read_text().splitlines() if line.startswith('Uid:')]
    if len(uids) != 1 or len(uids[0]) != 4 or any(int(uid) != os.getuid() for uid in uids[0]):
        raise ValueError('process ownership unknown')
    raw = (proc / 'cmdline').read_bytes()
    if not raw.endswith(b'\0'):
        raise ValueError('process argv incomplete')
    argv = [v.decode() for v in raw[:-1].split(b'\0')]
    exe = str((proc / 'exe').readlink())
    cwd = str((proc / 'cwd').resolve(strict=True))
    after = orca_capture._stat(proc)
    if (any(before[key] != after[key] for key in before if key != 'state') or
            before['state'] not in ('R', 'S') or after['state'] not in ('R', 'S') or
            (proc / 'cmdline').read_bytes() != raw or str((proc / 'exe').readlink()) != exe or
            str((proc / 'cwd').resolve(strict=True)) != cwd):
        raise ValueError('unstable process identity')
    return {**{key: value for key, value in before.items() if key != 'state'},
            'exe': exe, 'cwd': cwd, 'argv': argv}


def _target(plan, snapshot):
    target = plan['target']
    observed = _process(target['pid'])
    if any(observed[key] != target[key] for key in ('start_ticks', 'exe', 'argv', 'cwd')):
        raise ValueError('target identity mismatch')
    if (snapshot.get('runtime', {}).get('runtimeId') != target['runtime_id'] or
            snapshot.get('runtime_process', {}).get('pid') != target['runtime_pid']):
        raise ValueError('runtime identity mismatch')
    # Prove the service process really owns this runtime, without process-name
    # heuristics. Re-read every member to detect a changed ancestry during proof.
    chain, pid = [], target['runtime_pid']
    while pid != target['pid']:
        if pid <= 1 or pid in {p['pid'] for p in chain} or len(chain) >= 128:
            raise ValueError('runtime is not target descendant')
        process = _process(pid)
        chain.append(process)
        pid = process['ppid']
    for process in [observed, *chain]:
        if _process(process['pid']) != process:
            raise ValueError('runtime ancestry changed')
    return {'pid': observed['pid'], 'start_ticks': observed['start_ticks'], 'exe': observed['exe'],
            'argv_sha256': hashlib.sha256(json.dumps(observed['argv'], separators=(',', ':')).encode()).hexdigest()}


def _service(plan):
    text = run(['systemctl', 'show', plan['service'],
                '--property=ActiveState,MainPID,ControlGroup,KillMode,SendSIGKILL,TimeoutStopUSec'])
    fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    group = fields.get('ControlGroup', '')
    if (fields.get('ActiveState') != 'active' or fields.get('MainPID') != str(plan['target']['pid']) or
            not group.startswith('/') or group == '/'):
        raise ValueError('service ownership unknown')
    groups = [line.split(':', 2)[2] for line in (PROC / str(plan['target']['pid']) / 'cgroup').read_text().splitlines()]
    if not any(path == group or path.startswith(group + '/') for path in groups):
        raise ValueError('service cgroup mismatch')
    policy = {key: fields.get(key) for key in
              ('ActiveState', 'MainPID', 'ControlGroup', 'KillMode', 'SendSIGKILL', 'TimeoutStopUSec')}
    if (policy['KillMode'] not in ('control-group', 'mixed', 'process') or
            policy['SendSIGKILL'] not in ('yes', 'no') or not _text(policy['TimeoutStopUSec'])):
        raise ValueError('systemd stop policy unknown')
    policy['sigkill_approved'] = plan.get('allow_systemd_sigkill', False)
    run(['sudo', '-n', '-l', '--', 'systemctl', 'restart', plan['service']])
    return policy


def _confirm(plan, path, now):
    proof = _json(path)
    _keys(proof, ('plan_id', 'confirmed_at', 'expires_at', 'idle'))
    confirmed, expires = _timestamp(proof['confirmed_at']), _timestamp(proof['expires_at'])
    if (proof['plan_id'] != plan['id'] or proof['idle'] is not True or
            not confirmed <= now < expires <= _timestamp(plan['expires_at']) or
            not 0 <= (now - confirmed).total_seconds() < 3600):
        raise ValueError('idle confirmation stale or mismatched')


def _records(plan):
    records = []
    for path in plan['session_records']:
        data = _json(path)
        if not isinstance(data, dict):
            raise ValueError('invalid session records')
        rows = [data] if 'terminal' in data else list(data.values())
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('invalid session record')
            if 'session_dir' in row:
                _absolute(row['session_dir'])
            else:
                _absolute(row['session_path'])
                if any(type(row[key]) is not int or row[key] <= 0 for key in ('pid', 'start_ticks')):
                    raise ValueError('manual Main process identity missing')
            _absolute(row['worktree']['path'])
            if not all(_text(row.get(key)) for key in ('terminal', 'terminal_incarnation_id')) or not _text(row['worktree'].get('id')):
                raise ValueError('record terminal identity missing')
            records.append(row)
    identities = [(r.get('session_dir'), r.get('pid'), r.get('start_ticks')) for r in records]
    if len(set(identities)) != len(records):
        raise ValueError('ambiguous session records')
    return records


def _match_logs(row, record, open_logs):
    logs = ([Path(record['session_path'])] if 'session_path' in record and 'session_dir' not in record
            else list(Path(record['session_dir']).glob('*.jsonl')))
    if len(logs) != 1:
        raise ValueError('canonical session log ambiguity')
    canonical = [s for s in row['sessions'] if s.get('path') == str(logs[0])]
    if len(canonical) != 1 or canonical[0].get('cwd') != row['cwd']:
        raise ValueError('canonical session log missing or wrong cwd')
    if record.get('session_id', canonical[0]['id']) != canonical[0]['id']:
        raise ValueError('recorded canonical session ID changed')
    bound_logs = set(open_logs)
    if row.get('argv_session') or str(logs[0]) not in bound_logs:
        observed = orca_capture._argv_session(PROC / str(row['pid']), canonical[0]['id'])
        if observed is None:
            raise ValueError('canonical log not bound by FD or fresh argv')
        binding, session = observed
        if (binding['start_ticks'] != row['start_ticks'] or binding['cwd'] != row['cwd'] or
                binding['argv_sha256'] != row.get('argv_sha256') or session != canonical[0] or
                row.get('argv_session') and binding != row['argv_session'] or
                record.get('boot_id', binding['boot_id']) != binding['boot_id']):
            raise ValueError('argv-bound canonical session changed')
        bound_logs.add(binding['session_path'])
    root = Path(record['session_dir']) if 'session_dir' in record else logs[0].with_suffix('')
    for session in row['sessions']:
        path = _absolute(session['path'])
        if (str(path) not in bound_logs or
                path != logs[0] and not path.is_relative_to(root) or
                '..' in path.parts or
                not session.get('complete') or not session.get('model', {}).get('model') or
                not session.get('thinking', {}).get('thinkingLevel') or
                orca_capture._session(path) != session):
            raise ValueError('actual session metadata incomplete, foreign or changed')


def _match_process(row, records, terminals):
    if row.get('errors') or not row.get('identity_stable') or not row.get('sessions'):
        raise ValueError('model-bearing session incomplete')
    observed = _process(row['pid'])
    if (observed['start_ticks'] != row['start_ticks'] or observed['cwd'] != row['cwd'] or
            not omp_executable(observed['exe'], observed['argv'][0]) or
            row.get('exe', observed['exe']) != observed['exe']):
        raise ValueError('captured session changed')
    manual = [r for r in records if 'session_dir' not in r and r['pid'] == row['pid']
              and r['start_ticks'] == row['start_ticks']]
    if manual:
        matches = manual
    else:
        directory = str(_absolute(_option(observed['argv'], '--session-dir')))
        matches = [r for r in records if r.get('session_dir') == directory]
    if len(matches) != 1:
        raise ValueError('session has no unique launch record')
    record = matches[0]
    if record['worktree']['path'] != row['cwd']:
        raise ValueError('session cwd mismatch')
    if (record.get('boot_id') and
            record['boot_id'] != (PROC / 'sys/kernel/random/boot_id').read_text().strip()):
        raise ValueError('recorded process boot changed')
    open_logs = {str(fd.readlink()) for fd in (PROC / str(row['pid']) / 'fd').iterdir()}
    _match_logs(row, record, open_logs)
    matched = [t for t in terminals if t.get('handle') == record['terminal']]
    if len(matched) != 1:
        raise ValueError('terminal absent or ambiguous')
    terminal = matched[0]
    if (terminal.get('incarnationId') != record['terminal_incarnation_id'] or
            terminal.get('worktreeId') != record['worktree']['id'] or
            terminal.get('worktreePath') != row['cwd'] or not terminal.get('connected') or
            not terminal.get('writable') or terminal.get('orphaned')):
        raise ValueError('terminal binding mismatch')
    route = ('ORCA_TERMINAL_HANDLE=' + record['terminal']).encode()
    if route not in (PROC / str(row['pid']) / 'environ').read_bytes().split(b'\0'):
        raise ValueError('process terminal binding unknown')
    groups = [line.split(':', 2)[2] for line in (PROC / str(row['pid']) / 'cgroup').read_text().splitlines()]
    if not groups or any(not group.startswith('/') for group in groups):
        raise ValueError('session cgroup unknown')
    row['cgroup'] = groups
    service_group = '/system.slice/' + SERVICE
    if any(group == service_group or group.startswith(service_group + '/') for group in groups):
        raise ValueError('model session belongs to restarted service')
    if _process(row['pid']) != observed:
        raise ValueError('session process changed')
    return record


def _idle_session(record):
    if 'session_dir' in record:
        return restart._session(record, record['worktree']['path'])
    # Manually started Mains have an exact open log, not necessarily a unique
    # directory. Apply restart's completed-turn policy to that exact file only.
    events = [json.loads(line) for line in Path(record['session_path']).read_bytes().splitlines()]
    headers = [e for e in events if e.get('type') == 'session']
    if len(headers) != 1 or headers[0].get('cwd') != record['worktree']['path'] or not headers[0].get('id'):
        raise ValueError('manual Main log identity unknown')
    restart._final_stop(events)
    return {'id': headers[0]['id']}


@contextmanager
def _idle_check(checks, name, reason):
    """Keep diagnostics bounded and source-defined; exceptions may contain secrets."""
    checks[name] = {'status': 'failed', 'reason': reason}
    try:
        yield
    except FAILURES:
        return
    checks[name] = {'status': 'passed'}


def _main_idle(c, record, terminal, row, checks=None):
    """The same positive idle proof as restart._idle, without builder argv policy."""
    checks = checks if checks is not None else {}
    with _idle_check(checks, 'terminal_identity', 'Main terminal identity changed'):
        detail = orca(c, 'terminal', 'show', '--terminal', record['terminal'])['terminal']
        if any(detail.get(key) != terminal.get(key) for key in ('handle', 'incarnationId', 'worktreeId')) or not detail.get('connected') or not detail.get('writable') or detail.get('orphaned') or detail.get('agentIdentity') != 'omp':
            raise ValueError
    text, ui = '', 'unknown'
    with _idle_check(checks, 'footer_composer', 'glyph/footer, working/background UI or nonempty composer'):
        screen = orca(c, 'terminal', 'read', '--terminal', record['terminal'], '--screen')['terminal']
        text = '\n'.join(screen.get('tail', []))
        ui = health.omp_ui(text)
        if ui != 'idle':
            raise ValueError
    with _idle_check(checks, 'background_job', 'background job'):
        if ui == 'background' or re.search(r'waiting for|background job', '\n'.join(
                health.ANSI.sub('', text).splitlines()[-5:]), re.I):
            raise ValueError
    with _idle_check(checks, 'dialog', 'permission or question dialog'):
        if ui == 'dialog' or re.search(r'permission|approve|deny|ask \d* question',
                                      '\n'.join(health.ANSI.sub('', text).splitlines()[-5:]), re.I):
            raise ValueError
    with _idle_check(checks, 'pane_agent_state', 'Main exact pane is not idle or inventory incomplete'):
        if checks['terminal_identity']['status'] != 'passed':
            raise ValueError
        ps = orca(c, 'worktree', 'ps')
        if ps.get('truncated') or ps.get('hostScope', {}).get('omittedHostIds'):
            raise ValueError
        pane = detail.get('tabId', '') + ':' + detail.get('leafId', '')
        agents = [a for w in ps['worktrees'] if w['worktreeId'] == record['worktree']['id']
                  for a in w.get('agents', []) if a.get('paneKey') == pane and not a.get('parentPaneKey')]
        if (len(agents) != 1 or agents[0].get('agentType') != 'omp' or
                agents[0].get('state') not in ('idle', 'done') or agents[0].get('interrupted') or
                agents[0].get('programStatus', {}).get('state') not in ('idle', 'done')):
            raise ValueError
    proof = None
    with _idle_check(checks, 'session_final_stop', 'session identity or final assistant stop unproven'):
        proof = _idle_session(record)
    checks['lease'] = {'status': 'skipped', 'reason': 'session final stop unproven'}
    if proof is not None:
        with _idle_check(checks, 'lease', 'Main session lease unknown'):
            lease = json_run(['omp', 'session', 'lease', '--json', '--', proof['id']], c['root'])
            if lease.get('held') != [proof['id']] or lease.get('unknown') != []:
                raise ValueError
    with _idle_check(checks, 'foreground_pgrp', 'Main foreground identity unknown'):
        observed = _process(row['pid'])
        if (observed['start_ticks'] != row['start_ticks'] or observed['tty'] == 0 or
                observed['pgrp'] != observed['foreground_pgrp']):
            raise ValueError
    if any(check['status'] != 'passed' for check in checks.values()):
        raise ValueError('Main idle proof failed')
    return checks


def inspect(plan, c):
    """Accumulate symbolic blockers and private metadata, with no mutation/retry."""
    blockers, projects, loaded = [], [], []
    now = datetime.now(timezone.utc)
    if not _timestamp(plan['execute_after']) <= now < _timestamp(plan['expires_at']):
        blockers.append('execution_window_closed')
    for name, path in plan['confirmations'].items():
        try:
            _confirm(plan, path, now)
        except FAILURES:
            blockers.append(name + '_confirmation_invalid')
    for project in plan['projects']:
        entry = dict(project)
        command = entry.pop('command', None)
        if project['mode'] == 'main':
            entry['command_sha256'] = (hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
                                       if command else None)
        projects.append(entry)
        try:
            _private(project['config'])
            pc = config.load(project['config'])
            if project['worktree'] != pc['root']:
                raise ValueError('Main worktree is not configured project root')
            unit = 'fleet-' + re.sub(r'[^a-zA-Z0-9_.-]', '-', pc['repo']) + '.service'
            entry.update(run=pc['coordinator']['run'], terminal=pc['coordinator']['terminal'],
                         service=unit, repo=pc['repo'])
            loaded.append((entry, pc))
            if project['mode'] == 'main' and not project['command']:
                blockers.append(project['name'] + ':main_command_missing')
            if project['mode'] == 'main':
                if project['command']:
                    directory = _main_command(project['command'])
                    entry['fresh_session_dir'] = str(directory)
                    if directory.exists() or directory.is_symlink():
                        raise ValueError('Main session directory already exists')
                    _private(directory.parent, directory=True)
                handoff = _private(project['handoff'])
                modified = handoff.stat().st_mtime
                if (not handoff.read_bytes().strip() or
                        modified < _timestamp(plan['execute_after']).timestamp() - 1800 or
                        modified > now.timestamp()):
                    raise ValueError('handoff missing, unsafe or older than T-30')
                entry['handoff_sha256'] = hashlib.sha256(handoff.read_bytes()).hexdigest()
                entry['handoff_mtime'] = modified
        except FAILURES:
            blockers.append(project['name'] + '_project_invalid')
    try:
        snapshot = orca_capture.collect(c, list(dict.fromkeys(p['service'] for p, _ in loaded)),
                                        run_ids=[p['run'] for p, _ in loaded])
        blockers.extend('capture_' + error for error in snapshot.get('errors', []) if error != 'proc_scan_incomplete')
    except FAILURES:
        snapshot = {}
        blockers.append('capture_unavailable')
    for entry, _ in loaded:
        units = [unit for unit in snapshot.get('services', []) if unit.get('unit') == entry['service']]
        if (len(units) != 1 or units[0].get('LoadState') != 'loaded' or
                units[0].get('ActiveState') != 'active' or units[0].get('SubState') != 'running'):
            blockers.append(entry['name'] + '_supervisor_not_running')
    target = {}
    try:
        target = _target(plan, snapshot)
    except FAILURES:
        blockers.append('target_identity_unproven')
    try:
        snapshot['stop_policy'] = _service(plan)
        snapshot['service_state'] = snapshot['stop_policy'].copy()
        if snapshot['stop_policy']['SendSIGKILL'] == 'yes' and not snapshot['stop_policy']['sigkill_approved']:
            blockers.append('systemd_sigkill_not_approved')
    except FAILURES:
        blockers.append('service_restart_authority_unproven')
    try:
        records = _records(plan)
    except FAILURES:
        records = []
        blockers.append('session_records_invalid')
    terminals = snapshot.get('terminals', [])
    matched = {}
    for row in snapshot.get('processes', []):
        if row.get('session_status') == 'not_applicable_internal_worker' and row.get('kind') in ('daemon_broker', 'text_predict', 'js_eval_process'):
            continue
        try:
            record = _match_process(row, records, terminals)
            if record['terminal'] in matched:
                raise ValueError('multiple processes own terminal')
            matched[record['terminal']] = (record, row)
        except FAILURES:
            blockers.append('owned_session_launch_match_unproven')
    known_terminals = set()
    for entry, pc in loaded:
        name = entry['name']
        checks = entry['idle_checks'] = {}
        with _idle_check(checks, 'health_inventory', 'health inventory incomplete'):
            inventory = health.collect(pc)
            if inventory['errors']:
                raise ValueError
        with _idle_check(checks, 'coordinator_binding', 'coordinator identity or Run binding invalid'):
            coordinators = [t for t in terminals if t.get('handle') == entry['terminal']]
            if len(coordinators) != 1:
                raise ValueError
            terminal = coordinators[0]
            entry['terminal_metadata'] = terminal
            if (terminal.get('worktreePath') != entry['worktree'] or not terminal.get('incarnationId') or
                    not terminal.get('connected') or not terminal.get('writable') or terminal.get('orphaned') or
                    not isinstance(terminal.get('binding'), dict) or terminal['binding'].get('id') != entry['run']):
                raise ValueError
            known_terminals.add(entry['terminal'])
        if checks['coordinator_binding']['status'] == 'passed':
            if entry['mode'] == 'main':
                with _idle_check(checks, 'main_process', 'Main launch/process binding unproven'):
                    record, row = matched[entry['terminal']]
                    entry['main_process'] = dict(row)
                if checks['main_process']['status'] == 'passed':
                    main_checks = entry['main_idle_checks'] = {}
                    with _idle_check(checks, 'main_idle', 'Main idle sub-check failed'):
                        _main_idle(pc, record, terminal, row, main_checks)
            else:
                with _idle_check(checks, 'coordinator_shell', 'reserved coordinator shell is not proven'):
                    screen = orca(pc, 'terminal', 'read', '--terminal', entry['terminal'], '--screen')['terminal']
                    if entry['terminal'] in matched or not health.shell_ready(screen):
                        raise ValueError
        for slot in pc['builder']['slots']:
            if checks['health_inventory']['status'] != 'passed':
                checks['builder lane ' + slot] = {'status': 'skipped', 'reason': 'health inventory incomplete'}
                continue
            with _idle_check(checks, 'builder lane ' + slot, 'builder lane shell, binding or exact idle proof unproven'):
                builders = [b for b in inventory['builders'] if b['path'] == slot]
                if not builders:
                    raise ValueError
                for builder in builders:
                    handle = builder['terminal']
                    known_terminals.add(handle)
                    if builder.get('binding', {}).get('state') != 'none' or not builder['connected']:
                        raise ValueError
                    if builder.get('shell_ready'):
                        if handle in matched:
                            raise ValueError
                        continue
                    record, row = matched[handle]
                    trees = [t for t in inventory['worktrees'] if t['id'] == record['worktree']['id'] and t['path'] == slot]
                    if len(trees) != 1:
                        raise ValueError
                    proof = restart._idle(pc, record, trees[0], handle)
                    if proof['process']['pid'] != row['pid'] or int(proof['process']['start']) != row['start_ticks']:
                        raise ValueError
        if any(check['status'] != 'passed' for check in checks.values()):
            blockers.append(name + '_idle_binding_unproven')
    if set(matched) - known_terminals:
        blockers.append('owned_session_project_unmapped')
    actions = [{'action': 'capture_before'},
               {'action': 'restart_service', 'argv': RESTART_ARGV.copy(),
                'stop_policy': snapshot.get('stop_policy', {})},
               {'action': 'capture_after_and_match_survivors'}]
    for entry in projects:
        if entry['mode'] == 'main':
            actions.append({'action': ('start_fresh_main_if_no_survivor' if entry['command_sha256']
                                       else 'main_command_unresolved'),
                            'project': entry['name'], 'worktree': entry['worktree'],
                            'command_sha256': entry['command_sha256'],
                            'session_dir': entry.get('fresh_session_dir')})
            actions.append({'action': 'bind_run_inside_fresh_main_terminal_only',
                            'project': entry['name'], 'run': entry.get('run'),
                            'argv': [c['orca'], 'orchestration', 'run-use', '--id',
                                     entry.get('run'), '--json']})
        else:
            actions.append({'action': 'retain_verified_shell', 'project': entry['name']})
        actions.append({'action': 'update_private_coordinator_terminal_if_changed',
                        'project': entry['name'], 'config': entry['config']})
        actions.append({'action': 'reinstall_project_supervisor',
                        'project': entry['name'],
                        'condition': 'after_verified_main_mapping' if entry['mode'] == 'main' else 'only_if_shell_handle_changed',
                        'argv': ['fleet', '--config', entry['config'], 'install-service'] +
                                (['--deliver'] if entry['delivery'] else [])})
        actions.append({'action': 'durable_status_to_run', 'project': entry['name'],
                        'runs': [run for run in dict.fromkeys((entry.get('run'), c['coordinator']['run'])) if run],
                        'delivery_is_enqueue_not_wake': True})
    return {'blockers': sorted(set(blockers)), 'snapshot': snapshot, 'target': target,
            'projects': projects, 'actions': actions}

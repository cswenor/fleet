"""Private minimal restart plans and read-only shared Orca service inspection."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

import yaml

from . import config, orca_capture
from .commands import CommandError, run

PROC = Path('/proc')
# The sole privileged operation granted by the operator. Probing never runs it.
SERVICE = 'orca-serve.service'
RESTART_ARGV = ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', SERVICE]
FAILURES = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
            UnicodeError, CommandError, RecursionError)
SERVICE_FIELDS = ('ActiveState', 'MainPID', 'ControlGroup', 'KillMode',
                  'SendSIGKILL', 'TimeoutStopUSec')


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
    if (not isinstance(value, dict) or not set(required) <= value.keys() or
            set(value) - set(required) - set(optional)):
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
    # Sticky /tmp is safe for an owned private child; writable ancestors are not.
    for parent in path.parents:
        p = parent.lstat()
        if (not stat.S_ISDIR(p.st_mode) or p.st_uid not in (0, os.getuid()) or
                p.st_mode & 0o022 and not p.st_mode & stat.S_ISVTX):
            raise ValueError('unsafe path ancestor')
    return path


def load_plan(path):
    """Load the closed v2 schema without creating or modifying any paths."""
    try:
        path = _private(_absolute(str(path)))
        _private(path.parent, directory=True)
        plan = yaml.load(path.read_text(), Loader=_UniqueLoader)
        _keys(plan, ('version', 'id', 'state_dir', 'service', 'projects'),
              ('timeout_seconds', 'consumption_timeout_seconds', 'allow_systemd_sigkill'))
        if type(plan['version']) is not int or plan['version'] != 2:
            raise ValueError('version required')
        if not isinstance(plan['id'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', plan['id']):
            raise ValueError('invalid plan id')
        state_dir = _absolute(plan['state_dir'])
        if state_dir.exists() or state_dir.is_symlink():
            _private(state_dir, directory=True)
        else:
            _private(state_dir.parent, directory=True)
        if plan['service'] != SERVICE:
            raise ValueError('service outside privilege grant')
        if type(plan.setdefault('allow_systemd_sigkill', False)) is not bool:
            raise ValueError('invalid systemd escalation approval')
        timeout = plan.setdefault('timeout_seconds', 60)
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError('invalid timeout')
        consumption_timeout = plan.setdefault('consumption_timeout_seconds', 120)
        if type(consumption_timeout) is not int or not 1 <= consumption_timeout <= 600:
            raise ValueError('invalid consumption timeout')
        if not isinstance(plan['projects'], list) or not plan['projects']:
            raise ValueError('projects required')
        names, paths = set(), set()
        for project in plan['projects']:
            _keys(project, ('name', 'config'))
            if not isinstance(project['name'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', project['name']):
                raise ValueError('invalid project name')
            path_config = _private(_absolute(project['config']))
            if project['name'] in names or str(path_config) in paths:
                raise ValueError('duplicate project')
            names.add(project['name'])
            paths.add(str(path_config))
        plan['_path'] = str(path)
        return plan
    except (*FAILURES, yaml.YAMLError):
        raise ValueError('invalid or nonprivate Orca restart plan') from None


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
    if not argv or not all(_text(arg) for arg in argv):
        raise ValueError('process argv invalid')
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


def _service_state(service):
    text = run(['/usr/bin/systemctl', 'show', service, '--property=' + ','.join(SERVICE_FIELDS)])
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def _service(plan):
    fields = _service_state(plan['service'])
    group = fields.get('ControlGroup', '')
    pid = fields.get('MainPID', '')
    if (fields.get('ActiveState') != 'active' or not pid.isdecimal() or int(pid) <= 0 or
            not group.startswith('/') or group == '/' or '..' in Path(group).parts):
        raise ValueError('service ownership unknown')
    if (fields.get('KillMode') not in ('control-group', 'mixed', 'process') or
            fields.get('SendSIGKILL') not in ('yes', 'no') or not _text(fields.get('TimeoutStopUSec'))):
        raise ValueError('systemd stop policy unknown')
    run(['/usr/bin/sudo', '-n', '-l', '--', '/usr/bin/systemctl', 'restart', plan['service']])
    return {**fields, 'sigkill_approved': plan['allow_systemd_sigkill']}


def _target(plan, snapshot, policy):
    runtime_id = snapshot.get('runtime', {}).get('runtimeId')
    runtime_pid = snapshot.get('runtime_process', {}).get('pid')
    if not _text(runtime_id) or type(runtime_pid) is not int or runtime_pid <= 0:
        raise ValueError('runtime identity missing')
    observed = _process(int(policy['MainPID']))
    # Prove ancestry and cgroup correspondence, not executable-name heuristics.
    chain, pid = [], runtime_pid
    while pid != observed['pid']:
        if pid <= 1 or pid in {p['pid'] for p in chain} or len(chain) >= 128:
            raise ValueError('runtime is not service descendant')
        process = _process(pid)
        chain.append(process)
        pid = process['ppid']
    group = policy['ControlGroup']
    for process in [observed, *chain]:
        groups = [line.split(':', 2)[2] for line in
                  (PROC / str(process['pid']) / 'cgroup').read_text().splitlines()]
        if not any(path == group or path.startswith(group + '/') for path in groups):
            raise ValueError('service cgroup mismatch')
        if _process(process['pid']) != process:
            raise ValueError('runtime ancestry changed')
    final_service = _service_state(plan['service'])
    if any(final_service.get(key) != policy[key] for key in SERVICE_FIELDS):
        raise ValueError('service identity or policy changed')
    return {key: observed[key] for key in ('pid', 'start_ticks', 'exe', 'cwd')} | {
        'argv_sha256': hashlib.sha256(json.dumps(observed['argv']).encode()).hexdigest(),
        'runtime_id': runtime_id, 'runtime_pid': runtime_pid}


def inspect(plan, c):
    """Observe service authority, coordinator bindings and pending mail; never mutate."""
    blockers, projects, loaded = [], [], []
    try:
        executable = c['orca']
        if not _text(executable):
            raise ValueError('Orca executable missing')
        if not Path(executable).is_absolute():
            executable = shutil.which(executable, path=os.defpath)
            if not executable or not Path(executable).is_absolute():
                raise ValueError('Orca executable unavailable')
        c['orca'] = executable
    except FAILURES:
        return {'blockers': ['orca_executable_unavailable'], 'target': {},
                'snapshot': {}, 'projects': [], 'actions': []}
    for project in plan['projects']:
        entry = dict(project)
        projects.append(entry)
        try:
            _private(project['config'])
            pc = config.load(project['config'])
            terminal, run_id = pc['coordinator']['terminal'], pc['coordinator']['run']
            if not _text(terminal) or not _text(run_id):
                raise ValueError('coordinator configuration missing')
            unit = 'fleet-' + re.sub(r'[^a-zA-Z0-9_.-]', '-', pc['repo']) + '.service'
            entry.update(terminal=terminal, run=run_id, service=unit)
            loaded.append((entry, pc))
        except FAILURES:
            blockers.append(project['name'] + '_project_invalid')
    try:
        snapshot = orca_capture.collect(c, list(dict.fromkeys(p['service'] for p, _ in loaded)),
                                        run_ids=[p['run'] for p, _ in loaded])
        # Model logs and unrelated process inventory are not restart gates.
        blockers.extend('capture_' + error for error in snapshot.get('errors', [])
                        if error not in ('proc_scan_incomplete', 'owned_omp_incomplete'))
    except FAILURES:
        snapshot = {}
        blockers.append('capture_unavailable')
    for entry, pc in loaded:
        units = [unit for unit in snapshot.get('services', []) if unit.get('unit') == entry['service']]
        if (len(units) != 1 or units[0].get('LoadState') != 'loaded' or
                units[0].get('ActiveState') != 'active' or units[0].get('SubState') != 'running'):
            blockers.append(entry['name'] + '_supervisor_not_running')
        terminals = [t for t in snapshot.get('terminals', []) if t.get('handle') == entry['terminal']]
        if (len(terminals) != 1 or terminals[0].get('connected') is not True or
                terminals[0].get('writable') is not True or terminals[0].get('orphaned') is not False or
                not terminals[0].get('incarnationId') or terminals[0].get('worktreePath') != pc['root'] or
                not isinstance(terminals[0].get('binding'), dict) or
                terminals[0]['binding'].get('id') != entry['run']):
            blockers.append(entry['name'] + '_coordinator_binding_unproven')
        mailbox = snapshot.get('mailboxes', {}).get(entry['run'], {})
        if (mailbox.get('complete') is not True or mailbox.get('run', {}).get('id') != entry['run'] or
                mailbox.get('run', {}).get('coordinator_handle') != entry['terminal']):
            blockers.append(entry['name'] + '_pending_mail_incomplete')
    target = {}
    try:
        policy = _service(plan)
        snapshot['stop_policy'] = policy
        snapshot['service_state'] = policy.copy()
        if policy['SendSIGKILL'] == 'yes' and not policy['sigkill_approved']:
            blockers.append('systemd_sigkill_not_approved')
    except FAILURES:
        policy = None
        blockers.append('service_restart_authority_unproven')
    if policy is not None:
        try:
            target = _target(plan, snapshot, policy)
        except FAILURES:
            blockers.append('target_identity_unproven')
    actions = [
        {'action': 'capture_before', 'description': 'Read-only terminal, supervisor and complete pending Run mail inventory'},
        {'action': 'restart_service', 'argv': RESTART_ARGV.copy(), 'stop_policy': snapshot.get('stop_policy', {})},
        {'action': 'verify_service', 'description': 'Require active service with new PID and runtime identity'},
        {'action': 'capture_after_and_match_survivors',
         'description': 'Require every prior terminal connected, unchanged coordinator bindings, active supervisors and no lost pending message IDs'},
    ]
    for entry in projects:
        actions.append({'action': 'notify_run', 'project': entry['name'], 'run': entry.get('run'),
                        'type': 'question', 'description': 'Send from initiating caller and retain message ID; enqueue is not delivery or read proof'})
    actions.append({
        'action': 'confirm_run_consumption',
        'timeout_seconds': plan.get('consumption_timeout_seconds', 120),
        'default_timeout_seconds': 120, 'poll_interval_seconds': 1,
        'queries': [{'project': entry['name'], 'terminal': entry.get('terminal'),
                     'run': entry.get('run'),
                     'argv': [c['orca'], 'orchestration', 'check', '--terminal', entry.get('terminal'),
                              '--run', entry.get('run'), '--peek', '--json']}
                    for entry in projects],
        'description': 'After native questions, poll only read-only pending Run --peek; require a complete valid inventory with count 0. Retain message-ID and consumption receipts; warn with project/Main/Run for undrained or unknown results without another notification, consuming check or ack.',
    })
    return {'blockers': sorted(set(blockers)), 'target': target, 'snapshot': snapshot,
            'projects': projects, 'actions': actions}

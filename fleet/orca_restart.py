"""External, journaled Orca service restart; never repeat an ambiguous mutation."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import time
import yaml
from . import orca_capture, state
from .commands import CommandError, orca, run
from .config import load
from .orca_restart_preflight import load_plan, inspect, _match_process, _confirm


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _private(path, directory=False):
    path = Path(path)
    info = path.lstat()
    if (path.is_symlink() or info.st_uid != os.getuid() or
            info.st_mode & 0o077 or (not path.is_dir() if directory else not path.is_file())):
        raise ValueError('unsafe restart evidence permissions or ownership')
    return path


def _atomic(path, value):
    """Use Fleet's fsynced atomic replacement, including directory durability."""
    state.write({'_state': str(path.parent)}, path.stem, value)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class _AuthorizationClosed(ValueError):
    pass


def _authorize(plan):
    now = datetime.now(timezone.utc)
    if not (datetime.fromisoformat(plan['execute_after']) <= now <
            datetime.fromisoformat(plan['expires_at'])):
        raise _AuthorizationClosed('execution authorization closed; read-only recovery required')


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
            if self.data.get('plan_sha256') != self.plan_digest:
                raise ValueError('restart plan changed; reconcile original journal, do not replay')
        else:
            self.data = {'schema': 1, 'plan_id': plan['id'], 'plan_sha256': self.plan_digest,
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

    def authorize(self):
        """Revalidate argv-derived bindings, not only a cached window receipt."""
        _authorize(self.plan)
        rows = {}
        for name in ('preflight', 'before'):
            if not self.step(name):
                continue
            document = json.loads(_private(self.directory / (name + '.json')).read_text())
            if self.done(name) and _digest(document) != self.step(name)['receipt']['sha256']:
                raise ValueError('authorization evidence changed')
            snapshot = document.get('snapshot', {}) if name == 'preflight' else document
            boot = snapshot.get('boot_id')
            if boot and boot != (orca_capture.PROC / 'sys/kernel/random/boot_id').read_text().strip():
                raise ValueError('host reboot invalidates authorization evidence')
            for row in snapshot.get('processes', []):
                rows.setdefault((row['pid'], row['start_ticks']), row)
        for name, step in self.data['steps'].items():
            row = step.get('receipt', {}).get('process') if name.startswith('main-') else None
            if row:
                rows.setdefault((row['pid'], row['start_ticks']), row)
        for row in rows.values():
            shape = row.get('argv_shape', [])
            if '--session-dir' not in shape or any(flag in shape for flag in ('--resume', '--continue', '--restore')):
                continue
            liveness = _process_state(row)
            if liveness == 'gone':
                continue
            if liveness != 'live':
                raise ValueError('argv-bound process liveness unknown')
            observed = orca_capture._argv_session(orca_capture.PROC / str(row['pid']))
            if observed is None:
                raise ValueError('live argv session directory no longer proven')
            binding, session = observed
            canonical = [s for s in row['sessions'] if s['path'] == binding['session_path']]
            current = {'identity_stable': True, 'errors': [], 'cwd': binding['cwd'], 'sessions': [session]}
            if (binding['start_ticks'] != row['start_ticks'] or
                    binding['argv_sha256'] != row.get('argv_sha256') or
                    row.get('argv_session') and binding != row['argv_session'] or
                    len(canonical) != 1 or not _same_session({**row, 'sessions': canonical}, current)):
                raise ValueError('argv-bound session authorization changed')
        _authorize(self.plan)


def _service(unit):
    text = run(['systemctl', 'show', unit,
                '--property=MainPID,ActiveState,SubState,ControlGroup,SendSIGKILL,KillMode,TimeoutStopUSec'])
    return dict(row.split('=', 1) for row in text.splitlines() if '=' in row)


def _process_state(row):
    """No missing PID heuristic: classify against the original kernel incarnation."""
    proc = Path('/proc') / str(row['pid'])
    try:
        current = orca_capture._stat(proc)
    except FileNotFoundError:
        return 'gone' if not proc.exists() else 'unknown'
    except (OSError, ValueError, IndexError):
        return 'unknown'
    if current['start_ticks'] != row['start_ticks'] or current['state'] == 'Z':
        return 'gone'
    return 'live'


def _runtime(c):
    try:
        status = orca(c, 'status')
        if status.get('runtime', {}).get('state') == 'ready' and status['runtime'].get('reachable'):
            return status
    except (CommandError, ValueError):
        pass
    return None


def _wait_ready(c, plan, old_runtime):
    deadline = time.monotonic() + plan['timeout_seconds']
    while True:
        service = _service(plan['service'])
        status = _runtime(c)
        if (service.get('ActiveState') == 'active' and int(service.get('MainPID', 0)) > 0
                and status and status['runtime'].get('runtimeId') != old_runtime
                and _process_state(plan['target']) == 'gone'):
            return {'runtime_id': status['runtime']['runtimeId'],
                    'service_pid': int(service['MainPID'])}
        if time.monotonic() >= deadline:
            raise ValueError('restart readiness unverified; inspect service and journal')
        time.sleep(1)


def _stop_receipt(started, unit):
    """Retain only systemd event timing, never journal message text."""
    result = {'stop_duration_seconds': None, 'sigkill_reached': None}
    try:
        text = run(['journalctl', '--unit', unit, '--since', '@' + str(started),
                    '--no-pager', '--output=json',
                    '--output-fields=MESSAGE,MESSAGE_ID,__MONOTONIC_TIMESTAMP,UNIT'])
        events = [json.loads(line) for line in text.splitlines()]
        relevant = [e for e in events if e.get('UNIT') == unit]
        stops = [int(e['__MONOTONIC_TIMESTAMP']) for e in relevant
                 if e.get('MESSAGE_ID') == 'de5b426a63be47a7b6ac3eaac82e2f6f']
        stopped = [int(e['__MONOTONIC_TIMESTAMP']) for e in relevant
                   if e.get('MESSAGE_ID') == '9d1aaa27d60140bd96365438aad20286b']
        if stops and stopped and stopped[-1] >= stops[0]:
            result['stop_duration_seconds'] = (stopped[-1] - stops[0]) / 1_000_000
            result['sigkill_reached'] = any('SIGKILL' in str(e.get('MESSAGE', '')) for e in events)
    except (CommandError, ValueError, KeyError, TypeError):
        pass
    return result


def _current_processes(paths):
    rows, errors, diagnostics = orca_capture._processes(paths)
    if (errors or any(row.get('errors') or not row.get('identity_stable') for row in rows) or
            any(item.get('reason') not in ('non_owned', 'vanished_mid_scan') for item in diagnostics)):
        raise ValueError('current owned-OMP census incomplete; manual reconciliation required')
    return rows


def _restart_service(c, plan, journal):
    step = 'restart'
    if journal.done(step):
        receipt = journal.step(step)['receipt']
        ready = _runtime(c)
        if not ready or ready['runtime'].get('runtimeId') != receipt['runtime_id']:
            raise ValueError('recorded replacement runtime changed; manual reconciliation required')
        return
    attempts = journal.step(step).get('receipt', {}).get('attempts', 0)
    if attempts:
        # A timed-out or interrupted command may have succeeded. Observe first;
        # never restart a live replacement (or the still-live original) again.
        try:
            receipt = _wait_ready(c, plan, plan['target']['runtime_id'])
            previous = journal.step(step)['receipt']
            journal.event(step, 'done', {**receipt, 'attempts': attempts,
                                       'policy': previous.get('policy'),
                                       **_stop_receipt(previous.get('started', time.time()), plan['service'])})
            return
        except ValueError:
            service = _service(plan['service'])
            if (_process_state(plan['target']) != 'gone' or
                    service.get('ActiveState') not in ('inactive', 'failed') or
                    service.get('MainPID') != '0' or _runtime(c)):
                raise ValueError('restart intent ambiguous or process still live; no retry')
    else:
        # Refresh *all* preconditions immediately before the first mutation.
        checked = inspect(plan, c)
        if checked['blockers']:
            raise ValueError('restart preconditions changed; no service restart')
        journal.event('pre_signal', 'done', {'target': checked['target']})
    while attempts < plan['launch_attempts']:
        _authorize(plan)
        if attempts:
            # The down runtime cannot prove current composer/agent idleness.
            # Only a complete, model-free census authorizes recovery (Main ruling).
            rows = _current_processes({project['worktree'] for project in plan['projects']})
            if any(row['kind'] == 'agent' for row in rows):
                raise ValueError('recovery idle state unknown or model agents survive; manual reconciliation required')
        policy = _service(plan['service'])
        if attempts and (_process_state(plan['target']) != 'gone' or
                         policy.get('ActiveState') not in ('inactive', 'failed') or
                         policy.get('MainPID') != '0' or _runtime(c)):
            raise ValueError('recovery service state changed; no retry')
        for path in plan.get('confirmations', {}).values():
            _confirm(plan, path, datetime.now(timezone.utc))
        journal.authorize()
        attempts += 1
        started = time.time()
        journal.event(step, 'intent', {'attempts': attempts, 'started': started, 'policy': policy})
        try:
            # Exact, separately approved security allowlist; no shell, no custom sudo.
            if plan['service'] != 'orca-serve.service':
                raise ValueError('unapproved privileged restart unit')
            run(['sudo', '-n', 'systemctl', 'restart', 'orca-serve.service'],
                timeout=plan['timeout_seconds'])
        except CommandError:
            pass  # Outcome is determined by service/process/runtime proof, not exit code.
        try:
            receipt = _wait_ready(c, plan, plan['target']['runtime_id'])
            journal.event(step, 'done', {**receipt, 'attempts': attempts, 'policy': policy,
                                       **_stop_receipt(started, plan['service'])})
            return
        except ValueError:
            service = _service(plan['service'])
            if (_process_state(plan['target']) != 'gone' or
                    service.get('ActiveState') not in ('inactive', 'failed') or
                    service.get('MainPID') != '0' or _runtime(c)):
                raise ValueError('service restart incomplete or ambiguous; no retry')
    raise ValueError('bounded restart attempts exhausted; external operator required')


def _capture(c, projects, journal, name):
    if journal.done(name):
        path = _private(journal.directory / (name + '.json'))
        result = json.loads(path.read_text())
        if _digest(result) != journal.step(name)['receipt']['sha256']:
            raise ValueError('restart capture changed; manual reconciliation required')
        return result
    result = orca_capture.collect(c, [project['service'] for project in projects],
                                  run_ids=[project['run'] for project in projects])
    for process in result['processes']:
        try:
            process['cgroups'] = (Path('/proc') / str(process['pid']) / 'cgroup').read_text().splitlines()
        except OSError:
            process['cgroups'] = None
    _atomic(journal.directory / (name + '.json'), result)
    # Partial diagnostics are preserved, never promoted to a safety verdict.
    journal.event(name, 'done', {'sha256': _digest(result), 'errors': result['errors']})
    return result


def _terminal_matches(snapshot, old):
    matches = [t for t in snapshot['terminals']
               if t.get('incarnationId') == old.get('incarnationId')
               and t.get('worktreePath') == old.get('worktreePath')]
    if len(matches) != 1 or not old.get('incarnationId'):
        raise ValueError('coordinator terminal incarnation absent or ambiguous; reconcile manually')
    terminal = matches[0]
    if not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable'):
        raise ValueError('coordinator terminal not positively connected and writable')
    return terminal


def _same_session(before, after):
    def identity(session):
        return (session['id'], session.get('model', {}).get('model'),
                session.get('model', {}).get('resolvedModelIsFallback'),
                session.get('thinking', {}).get('thinkingLevel'))
    return (after.get('identity_stable') and not after.get('errors') and
            after.get('cwd') == before.get('cwd') and
            {identity(s) for s in before['sessions']} == {identity(s) for s in after['sessions']} and
            all(s.get('complete') for s in after['sessions']))


def _new_main(c, project, journal):
    name = 'main-' + project['name']
    prior = journal.step(name)
    if prior and prior['status'] not in ('created', 'done'):
        raise ValueError('Main creation intent unresolved; inspect exact terminal, never duplicate')
    if journal.done(name):
        return prior['receipt']['terminal']
    command = project['command']
    directory = Path(command[command.index('--session-dir') + 1])
    if prior:
        receipt = prior['receipt']
        terminal = orca(c, 'terminal', 'show', '--terminal', receipt['terminal'])['terminal']
        if terminal.get('incarnationId') != receipt['incarnationId']:
            raise ValueError('created Main terminal changed; never duplicate')
    else:
        journal.authorize()
        if directory.exists():
            raise ValueError('new Main session directory already exists; possible duplicate')
        journal.event(name, 'intent', {'command_sha256': _digest(command)})
        directory.mkdir(mode=0o700, parents=False)
        # These commands execute inside the NEW terminal, which owns its binding.
        text = shlex.join([c['orca'], 'orchestration', 'run-use', '--id', project['run'], '--json'])
        text += ' && exec ' + shlex.join(command)
        result = orca(c, 'terminal', 'create', '--worktree', 'path:' + project['worktree'],
                      '--title', project['name'] + ' Main', '--command', text)
        terminal = result.get('terminal', {})
        if not terminal.get('handle') or not terminal.get('incarnationId'):
            raise ValueError('Main create receipt incomplete; inspect exact terminal, never duplicate')
        receipt = {'terminal': terminal['handle'], 'incarnationId': terminal['incarnationId'],
                   'session_dir': str(directory), 'worktree': {'path': project['worktree'],
                                                             'id': terminal.get('worktreeId')},
                   'terminal_incarnation_id': terminal['incarnationId']}
        journal.event(name, 'created', receipt)
    deadline = time.monotonic() + journal.data['timeout_seconds']
    while True:
        binding = orca(c, 'orchestration', 'run-current', '--from', terminal['handle'])
        try:
            processes = _current_processes({project['worktree']})
        except ValueError:
            # Metadata may still be appearing during the bounded startup wait.
            # Incomplete evidence remains pending, never permission to accept.
            processes = None
        candidates = ([p for p in processes if p['kind'] == 'agent' and p['cwd'] == project['worktree']]
                      if processes is not None else [])
        if len(candidates) > 1:
            raise ValueError('new Main has competing root agents; receipt retained, reconcile manually')
        agents = []
        for process in candidates:
            if not process['sessions']:
                continue
            try:
                terminal = orca(c, 'terminal', 'show', '--terminal', receipt['terminal'])['terminal']
                _match_process(process, [receipt], [terminal])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            agents.append(process)
        if (binding.get('run') or {}).get('id') == project['run'] and len(agents) == 1:
            receipt['process'] = agents[0]
            journal.event(name, 'done', receipt)
            return terminal['handle']
        if time.monotonic() >= deadline:
            raise ValueError('new Main turn/model binding not proven; receipt retained, do not duplicate')
        time.sleep(1)


def _fresh_main_ready(c, project, before, after, journal):
    """Historical AFTER evidence never authorizes a new process on resume."""
    from .health import shell_ready
    current = orca_capture.collect(c, [project['service']])
    _atomic(journal.directory / ('fresh-' + project['name'] + '.json'), current)
    if (current['boot_id'] != before['boot_id'] or
            current['runtime'].get('runtimeId') != after['runtime'].get('runtimeId')):
        raise ValueError('runtime changed before fresh Main; reconcile manually')
    if (_process_state(project['main_process']) != 'gone' or current['errors'] or
            any(p['kind'] == 'agent' and p['cwd'] == project['worktree']
                for p in current['processes'])):
        raise ValueError('fresh Main census or positive old-process exit unproven')
    terminal = _terminal_matches(current, project['terminal_metadata'])
    if terminal['handle'] != project['terminal']:
        raise ValueError('old Main terminal remapped; reconcile manually')
    for terminal in current['terminals']:
        if terminal.get('worktreePath') != project['worktree']:
            continue
        if (not terminal.get('connected') or not terminal.get('writable') or terminal.get('orphaned') or
                terminal.get('agentIdentity')):
            raise ValueError('old or possible restored Main terminal not positively retired')
        screen = orca(c, 'terminal', 'read', '--terminal', terminal['handle'], '--screen')['terminal']
        if not shell_ready(screen):
            raise ValueError('old or possible restored Main terminal not at a ready shell')
    journal.event('fresh-check-' + project['name'], 'done', {'sha256': _digest(current)})


def _coordinator(c, project, before, after, journal):
    name = 'coordinator-' + project['name']
    created = journal.step('main-' + project['name'])
    if created and not journal.done(name):
        # Receipt recovery only: _new_main cannot create again with a prior step.
        if _process_state(project['main_process']) != 'gone':
            raise ValueError('incumbent exit no longer proven before Main receipt recovery')
        _new_main(c, project, journal)
        created = journal.step('main-' + project['name'])
    if journal.done(name) or created:
        handle = (journal.step(name)['receipt']['terminal'] if journal.done(name)
                  else created['receipt']['terminal'])
        expected = project['terminal_metadata']
        if created:
            if created['status'] != 'done':
                raise ValueError('cached coordinator has unresolved Main receipt')
            expected = {**created['receipt'], 'worktreePath': project['worktree']}
        # Re-read native identity/state; cached AFTER metadata is not authority.
        terminal = _terminal_matches(orca(c, 'terminal', 'list', '--limit', '1000'), expected)
        if terminal['handle'] != handle:
            raise ValueError('cached coordinator terminal remapped')
        binding = orca(c, 'orchestration', 'run-current', '--from', handle)
        if (binding.get('run') or {}).get('id') != project['run']:
            raise ValueError('cached coordinator Run binding changed')
        if project['mode'] == 'shell':
            from .health import shell_ready
            screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
            if terminal.get('agentIdentity') or not shell_ready(screen):
                raise ValueError('cached coordinator shell no longer ready')
        else:
            original = created['receipt']['process'] if created else project['main_process']
            processes = _current_processes({project['worktree']})
            agents = [p for p in processes if p['kind'] == 'agent' and p['cwd'] == project['worktree']]
            same = [p for p in agents if p['pid'] == original['pid']
                    and p['start_ticks'] == original['start_ticks'] and _same_session(original, p)]
            if _process_state(original) != 'live' or len(same) != 1 or len(agents) != 1:
                raise ValueError('cached coordinator Main no longer positively alive')
        if not journal.done(name):
            journal.event(name, 'done', {'terminal': handle, 'previous_terminal': project['terminal']})
        return handle
    old_terminal = project['terminal_metadata']
    if project['mode'] == 'shell':
        terminal = _terminal_matches(orca(c, 'terminal', 'list', '--limit', '1000'), old_terminal)
        from .health import shell_ready
        screen = orca(c, 'terminal', 'read', '--terminal', terminal['handle'], '--screen')['terminal']
        if terminal.get('agentIdentity') or not shell_ready(screen):
            raise ValueError('reserved coordinator shell no longer positively ready')
        binding = orca(c, 'orchestration', 'run-current', '--from', terminal['handle'])
        if (binding.get('run') or {}).get('id') != project['run']:
            raise ValueError('reserved coordinator shell Run binding changed')
        handle = terminal['handle']
    else:
        original = project['main_process']
        if before['boot_id'] != after['boot_id']:
            raise ValueError('host reboot invalidates Main liveness proof; reconcile manually')
        liveness = _process_state(original)
        processes = _current_processes({project['worktree']})
        candidates = [p for p in processes if p['kind'] == 'agent' and p['cwd'] == original['cwd']]
        same = [p for p in candidates if p['pid'] == original['pid']
                and p['start_ticks'] == original['start_ticks'] and _same_session(original, p)]
        if liveness == 'live' and len(same) == 1 and len(candidates) == 1:
            terminal = _terminal_matches(orca(c, 'terminal', 'list', '--limit', '1000'), old_terminal)
            if terminal['handle'] != project['terminal']:
                raise ValueError('surviving Main handle remapped; manual reconciliation required')
            binding = orca(c, 'orchestration', 'run-current', '--from', terminal['handle'])
            if (binding.get('run') or {}).get('id') != project['run']:
                raise ValueError('surviving Main Run binding changed; no impersonation or duplicate')
            handle = terminal['handle']
        elif liveness == 'gone' and not candidates:
            _fresh_main_ready(c, project, before, after, journal)
            handle = _new_main(c, project, journal)
        else:
            raise ValueError('Main surviving/restored attempt unknown; no fresh start')
    journal.event(name, 'done', {'terminal': handle, 'previous_terminal': project['terminal']})
    return handle


def _configure(project, handle, journal):
    name = 'config-' + project['name']
    path = _private(project['config'])
    raw = path.read_bytes()
    config = yaml.safe_load(raw)
    current = config['coordinator']['terminal']
    if config['coordinator']['run'] != project['run']:
        raise ValueError('coordinator Run config changed; preserve and reconcile')
    if journal.done(name):
        if current != handle:
            raise ValueError('completed coordinator config changed; preserve and reconcile')
        return
    previous = journal.step(name)
    if previous and previous['status'] == 'intent':
        expected = previous['receipt']
        if _digest(raw.hex()) not in (expected['before_sha256'], expected['after_sha256']):
            raise ValueError('private config changed during restart; preserve and reconcile')
    elif current != project['terminal']:
        raise ValueError('coordinator config changed since preflight; preserve and reconcile')
    config['coordinator']['terminal'] = handle
    updated = yaml.safe_dump(config, sort_keys=False).encode()
    if current != handle:
        journal.authorize()
        journal.event(name, 'intent', {'before_sha256': _digest(raw.hex()),
                                      'after_sha256': _digest(updated.hex())})
        # Config may contain private token env names/settings: never journal its contents.
        temp = path.with_name('.' + path.name + '.' + journal.data['plan_id'])
        with open(temp, 'xb', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            raise ValueError('private config changed before replacement; preserve and reconcile')
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    journal.event(name, 'done', {'terminal': handle, 'changed': current != handle})


def _install(project, journal):
    name = 'supervisor-' + project['name']
    if journal.done(name):
        if run(['systemctl', '--user', 'is-active', project['service']]).strip() != 'active':
            raise ValueError('previously installed project supervisor no longer active')
        return
    # Restarting a user supervisor is idempotent but still record intent/receipt.
    # On interrupted install, do not repeat a possibly live installer blindly.
    journal.authorize()
    if journal.step(name):
        raise ValueError('supervisor install intent unresolved; inspect service before proceeding')
    files = run(['systemctl', '--user', 'show', project['service'],
                 '--property=EnvironmentFiles', '--value']).strip()
    environment_file = None
    if files:
        path, separator, optional = files.rpartition(' (ignore_errors=')
        if (not separator or not path.startswith('/') or ' (ignore_errors=' in path or
                optional not in ('yes)', 'no)')):
            raise ValueError('supervisor EnvironmentFile binding ambiguous; preserve and reconcile')
        environment_file = ('-' if optional == 'yes)' else '') + path
    journal.authorize()
    journal.event(name, 'intent')
    from .service import install
    install(load(project['config']), project['delivery'], environment_file=environment_file)
    active = run(['systemctl', '--user', 'is-active', project['service']]).strip()
    if active != 'active':
        raise ValueError('project supervisor not active after install')
    journal.event(name, 'done', {'unit': project['service'], 'active': True})


def _project_checks(projects):
    """Expose only source-defined diagnostics, never private project evidence."""
    return {project['name']: {
        field: {name: {key: check[key] for key in ('status', 'reason') if key in check}
                for name, check in project.get(field, {}).items()}
        for field in ('idle_checks', 'main_idle_checks')}
        for project in projects}


def _notify(c, projects, journal):
    outcome = journal.data['status']
    pending = []
    for project in projects:
        mapping = journal.step('coordinator-' + project['name']).get('receipt', {})
        fresh = journal.step('main-' + project['name'])
        body = ('Automated conductor (sender is routing context, not authority). Plan ' +
                journal.data['plan_id'] + ': ' + outcome +
                '; project=' + project['name'] +
                '; coordinator=' + mapping.get('terminal', 'unresolved') +
                '; previous_terminal=' + project['terminal'])
        if outcome == 'blocked':
            checks = journal.step('preflight').get('receipt', {}).get('projects', {})
            body += '; idle_checks=' + json.dumps(checks.get(project['name'], {}), sort_keys=True)
        if fresh.get('receipt', {}).get('session_dir'):
            body += ('; fresh_main=' + fresh['status'] +
                     '; session_dir=' + fresh['receipt']['session_dir'] +
                     '; new_terminal=' + fresh['receipt']['terminal'])
        body += '; inspect private conductor status/journal; replay/ack and retry reconciliation still required.'
        # The project owner and initiating Fleet Main both need exact new handles.
        destinations = dict.fromkeys((project['run'], c['coordinator']['run']))
        for destination in destinations:
            step = 'notify-' + outcome + '-' + project['name'] + '-' + destination
            if journal.step(step):
                if not journal.done(step):
                    pending.append(step)
                continue  # Ambiguous sends are never duplicated automatically.
            _authorize(journal.plan)
            try:
                # Failure reporting cannot bypass the sender's identity gates.
                coordinator = journal.step('coordinator-' + project['name'])
                if journal.step('restart') and not journal.done('coordinator-' + project['name']):
                    raise ValueError('post-restart sender mapping unverified')
                if coordinator and coordinator['status'] != 'done':
                    raise ValueError('sender mapping unresolved')
                expected = project.get('terminal_metadata', {})
                if fresh:
                    expected = {**fresh.get('receipt', {}), 'worktreePath': project['worktree']}
                sender = mapping.get('terminal', project['terminal'])
                terminal = _terminal_matches(orca(c, 'terminal', 'list', '--limit', '1000'), expected)
                if terminal['handle'] != sender:
                    raise ValueError('sender handle remapped')
                binding = orca(c, 'orchestration', 'run-current', '--from', sender)
                if (binding.get('run') or {}).get('id') != project['run']:
                    raise ValueError('sender Run binding changed')
                journal.authorize()
                journal.event(step, 'intent')
                result = orca(c, 'orchestration', 'send', '--from', sender,
                              '--run', project['run'], '--to', 'run:' + destination,
                              '--type', 'status', '--subject', 'Fleet automated Orca restart conductor', '--body', body)
                message_id = result.get('message', {}).get('id')
                if not isinstance(message_id, str) or not message_id:
                    raise ValueError('status enqueue receipt missing')
                journal.event(step, 'done', {'message_id': message_id})
            except _AuthorizationClosed:
                raise
            except (CommandError, OSError, ValueError, KeyError, TypeError):
                if not journal.step(step):
                    journal.event(step, 'blocked', {'reason': 'sender_identity_unverified'})
                pending.append(step)
    return pending


def conduct(c, path, dry_run=False):
    plan = load_plan(path)
    if dry_run:
        proof = inspect(plan, c)
        # Full capture remains private; CLI displays safe metadata/actions only.
        return {'event': 'dry-run', 'plan_id': plan['id'], 'target': proof['target'],
                'runtime': proof['snapshot'].get('runtime'),
                'terminals': [{k: t.get(k) for k in ('handle', 'incarnationId', 'worktreePath', 'binding')}
                              for t in proof['snapshot'].get('terminals', [])],
                'projects': _project_checks(proof['projects']),
                'blockers': proof['blockers'], 'actions': proof['actions'],
                'proc_diagnostics': [d for d in proof['snapshot'].get('proc_diagnostics', [])
                                     if d.get('reason') != 'non_owned']}
    # A service/timer must own this command, not the runtime being restarted.
    if os.environ.get('ORCA_TERMINAL_HANDLE') or not os.environ.get('INVOCATION_ID'):
        raise ValueError('execution requires an external systemd user service, not an Orca terminal')
    try:
        _authorize(plan)
    except _AuthorizationClosed:
        return {'event': 'blocked', 'plan_id': plan['id'],
                'blockers': ['execution_window_closed']}
    directory = Path(plan['state_dir'])
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=False)
    _private(directory, True)
    with state.lock({'_state': plan['state_dir']}, 'conductor'):
        journal = Journal(plan)
        projects = []
        try:
            if not journal.done('preflight'):
                proof = inspect(plan, c)
                _atomic(journal.directory / 'preflight.json', proof)
                if proof['blockers']:
                    diagnostics = {'blockers': proof['blockers'],
                                   'projects': _project_checks(proof['projects'])}
                    journal.event('preflight', 'blocked', diagnostics)
                    _notify(c, [p for p in proof['projects'] if p.get('run')], journal)
                    journal.data['status'] = 'blocked'
                    journal.save()
                    return {'event': 'blocked', **diagnostics, 'journal': str(journal.path)}
                journal.data['timeout_seconds'] = plan['timeout_seconds']
                journal.event('preflight', 'done', {'sha256': _digest(proof)})
            else:
                proof = json.loads(_private(journal.directory / 'preflight.json').read_text())
                if _digest(proof) != journal.step('preflight')['receipt']['sha256']:
                    raise ValueError('preflight evidence changed; manual reconciliation required')
            commands = {p['name']: p['command'] for p in plan['projects'] if p['mode'] == 'main'}
            projects = []
            for entry in proof['projects']:
                project = dict(entry)
                if project['mode'] == 'main':
                    command = commands[project['name']]
                    if _digest(command) != project['command_sha256']:
                        raise ValueError('private Main command differs from approved proof')
                    # Runtime only: never put private prompt/argv back into evidence.
                    project['command'] = command
                projects.append(project)
            journal.data['stage'] = 'before'
            journal.save()
            before = _capture(c, projects, journal, 'before')
            journal.data['stage'] = 'restart'
            journal.save()
            _restart_service(c, plan, journal)
            journal.data['stage'] = 'after'
            journal.save()
            after = _capture(c, projects, journal, 'after')
            if after['runtime'].get('runtimeId') != journal.step('restart')['receipt']['runtime_id']:
                raise ValueError('replacement runtime changed during capture')
            for project in projects:
                journal.data['stage'] = 'coordinator-' + project['name']
                journal.save()
                handle = _coordinator(c, project, before, after, journal)
                journal.data['stage'] = 'config-' + project['name']
                journal.save()
                _configure(project, handle, journal)
                journal.data['stage'] = 'supervisor-' + project['name']
                journal.save()
                if project['mode'] == 'main' or handle != project['terminal']:
                    _install(project, journal)
                elif run(['systemctl', '--user', 'is-active', project['service']]).strip() != 'active':
                    raise ValueError('unchanged shell coordinator supervisor no longer active')
            journal.data['stage'] = 'notifications'
            journal.data['status'] = 'reporting'
            journal.save()
            if _notify(c, projects, journal):
                raise ValueError('status delivery unresolved; manual reconciliation required')
            journal.data['status'] = 'complete'
            journal.save()
            return {'event': 'complete', 'journal': str(journal.path),
                    'runtime_id': after['runtime']['runtimeId']}
        except _AuthorizationClosed:
            return {'event': 'blocked', 'journal': str(journal.path),
                    'blockers': ['execution_window_closed']}
        except (OSError, ValueError, CommandError, KeyError, TypeError) as exc:
            # Never put arbitrary subprocess/exception text in logs or Run messages.
            journal.event('failure', 'failed_manual_reconciliation', {
                'stage': journal.data.get('stage', 'preflight'), 'error_type': type(exc).__name__,
                'guidance': 'Inspect retained step receipts; never repeat an unresolved mutation intent.'})
            try:
                if projects and _runtime(c):
                    _notify(c, projects, journal)
            except _AuthorizationClosed:
                pass  # Expired recovery cannot enqueue new runtime mutations.
            journal.data['status'] = 'failed_manual_reconciliation'
            journal.save()
            return {'event': 'failed_manual_reconciliation', 'journal': str(journal.path)}

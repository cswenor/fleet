"""Explicit guarded fresh launches; reuse only a terminal at a verified shell prompt."""
from contextlib import contextmanager, nullcontext
from collections import deque
from pathlib import Path
import json
import os
import re
import shlex
import time
import yaml
from . import dispatcher, health, snapshot, state
from .commands import CommandError, gh, orca, run


@contextmanager
def _preparation_receipt(c, ref):
    """Keep preparation failures distinct from receipts that may own a session."""
    preparation = {}
    try:
        yield preparation
    except Exception as exc:
        if not preparation:
            raise
        attempted = preparation.get('stage') in ('launch-attempted', 'launched')
        guidance = ('inspect launch and terminal receipts; do not retry blindly' if attempted else
                    'no builder input sent; inspect prepared slot and hooks, then retry fleet launch '
                    'for this ref and slot after admission recovers')
        preparation['safe_state'] = 'inspect-session' if attempted else 'no-builder-input-sent'
        preparation.update(at=time.time(), ref=ref, failure=type(exc).__name__,
                           retry_guidance=guidance)
        preparations = state.read(c, 'launch-preparations', {})
        preparations[preparation['slot']] = preparation
        state.write(c, 'launch-preparations', preparations)
        if isinstance(exc, ValueError):
            raise ValueError(f'{exc}; {guidance} (launch-preparations receipt)') from exc
        if isinstance(exc, CommandError):
            raise CommandError(f'{exc}; {guidance} (launch-preparations receipt)', code=exc.code) from exc
        exc.add_note(guidance + ' (launch-preparations receipt)')
        raise


def _untracked(slot):
    paths = run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], slot, text=False)
    names = [os.fsdecode(path) for path in paths.split(b'\0') if path]
    return names[:50], len(names)


def _rules_after_checkout(c, slot, destination):
    """Resolve the rules file against the target tree plus preserved user files."""
    root = Path(slot).resolve()
    path = root / c['builder']['rules_file']

    def entry(tree, relative):
        data = run(['git', 'ls-tree', '-z', tree, '--', str(relative)], slot, text=False)
        return data.split(b'\t', 1)[0].split() if data else None

    pending = deque(path.parts[1:])
    current = Path(path.anchor)
    links = 0
    while pending:
        part = pending.popleft()
        if part == '..':
            current = current.parent
            continue
        candidate = current / part
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            relative = None
        target = entry(destination, relative) if relative and relative.parts else None
        link = None
        if target:
            mode, kind, oid = target
            if mode == b'120000':
                link = os.fsdecode(run(['git', 'cat-file', 'blob', oid.decode('ascii')],
                                       slot, text=False))
            directory, regular = kind == b'tree', kind == b'blob'
        else:
            old = entry('HEAD', relative) if relative and relative.parts else None
            if old and old[1] != b'tree':
                break  # A removed tracked file/symlink does not survive checkout.
            if candidate.is_symlink():
                link = os.readlink(candidate)
            directory, regular = candidate.is_dir(), candidate.is_file()
            if old and directory:
                directory = bool(run(['git', 'ls-files', '--others', '--directory', '-z',
                                      '--', str(relative)], slot, text=False))
        if link is not None:
            links += 1
            if links > 40:
                break
            target_path = Path(link)
            if target_path.is_absolute():
                current = Path(target_path.anchor)
                parts = target_path.parts[1:]
            else:
                parts = target_path.parts
            pending.extendleft(reversed(parts))
            continue
        if not pending:
            if regular:
                return
            break
        if not directory:
            break
        current = candidate
    raise ValueError('configured project rules_file would be absent after checkout')


def _prepare_branch(c, ref, item, slot, *, dry_run=False):
    """Check preparation without switching a live session's branch during preflight."""
    current = run(['git', 'branch', '--show-current'], slot).strip()
    if not current:
        raise ValueError('launch withheld: slot has detached HEAD')
    if run(['git', 'status', '--porcelain', '--untracked-files=no'], slot).strip():
        raise ValueError('launch withheld: slot has uncommitted tracked work')
    previous = state.read(c, 'launches', {}).get(slot, {})
    if previous.get('ref') == ref or re.match(re.escape(ref.lower()) + r'(?:-|$)', current.lower()):
        if dry_run and c.get('builder', {}).get('rules_file'):
            _rules_after_checkout(c, slot, 'HEAD')
        return current
    run(['git', 'fetch', '--prune', 'origin'], slot)
    # Untracked user files are allowed, unless the target checkout would overwrite them.
    if run(['git', 'rev-list', 'HEAD', '--not', '--remotes=origin'], slot).strip():
        # Squash/rebase publication may outlive the deleted origin task ref.
        head = run(['git', 'rev-parse', 'HEAD'], slot).strip()
        published = gh(c, 'pr', 'list', '--repo', c['repo'], '--state', 'merged',
                       '--head', current, '--limit', '1', '--json', 'headRefOid')
        if not published or published[0].get('headRefOid') != head:
            raise ValueError('launch withheld: current branch has unpushed work')
    slug = re.sub(r'[^a-z0-9]+', '-', item['title'].lower()).strip('-')[:80].rstrip('-')
    base = ref.lower() + ('-' + slug if slug else '')
    refs = set(run(['git', 'for-each-ref', '--format=%(refname)',
                    'refs/heads/', 'refs/remotes/origin/'], slot).splitlines())
    branch = base
    suffix = 1
    while True:
        local = 'refs/heads/' + branch
        remote = 'refs/remotes/origin/' + branch
        target = local if local in refs else remote if remote in refs else None
        # An unused prepared branch can equal main without ever being published.
        # PR state also recognizes squash/rebase merges without guessing ancestry.
        merged = bool(gh(c, 'pr', 'list', '--repo', c['repo'], '--state', 'merged',
                         '--head', branch, '--limit', '1', '--json', 'number'))
        if merged:
            suffix += 1
            branch = f'{base}-{suffix}'
            continue
        if local in refs:
            owner = run(['git', 'for-each-ref', '--format=%(worktreepath)', local], slot).strip()
            if owner and Path(owner).resolve() != Path(slot).resolve():
                raise ValueError('launch withheld: task branch is checked out in another worktree')
        if target and run(['git', 'rev-list', target, '--not', '--remotes=origin'], slot).strip():
            raise ValueError('launch withheld: existing task branch has unpushed work')
        if local in refs and remote in refs:
            if run(['git', 'rev-list', remote, '--not', local], slot).strip():
                raise ValueError('launch withheld: existing task branch is behind or diverged from origin')
        destination = target or 'origin/main'
        run(['git', 'read-tree', '--dry-run', '-m', '-u', 'HEAD', destination], slot)
        if dry_run:
            if c.get('builder', {}).get('rules_file'):
                _rules_after_checkout(c, slot, destination)
            break
        if target is None:
            run(['git', 'switch', '--no-track', '-c', branch, 'origin/main'], slot)
            break
        if local in refs:
            run(['git', 'switch', branch], slot)
        else:
            run(['git', 'switch', '--track', '-c', branch, 'origin/' + branch], slot)
        break
    return branch


def launch(c, ref, slot=None, *, _locked=False):
    if not re.fullmatch(r'[A-Z]+-\d+', ref):
        raise ValueError('expected uppercase Pad reference, e.g. TASK-123')
    with nullcontext() if _locked else state.lock(c, 'launcher'), _preparation_receipt(c, ref) as preparation:
        s = snapshot.collect(c)
        inventory = s.get('inventory', {})
        occupied = {b['path'] for b in inventory.get('builders', []) if health.active(b)}
        slots = c['builder']['slots']
        chosen = str(Path(slot).expanduser().resolve()) if slot else next(
            (p for p in slots if p not in occupied and Path(p).is_dir()
             and not dispatcher.receipt_reasons(c, inventory, p)), None)
        if chosen and chosen not in slots:
            raise ValueError('requested slot is not in builder.slots')
        admission = snapshot.may_start(c, s)
        if s.get('inventory') and not s['inventory'].get('errors'):
            requested = chosen
            admission['reasons'].extend(dispatcher.duplicate_reasons(
                ref, dispatcher.assignments(c, s['inventory']), requested))
            if requested in c['builder']['slots']:
                if any(b['path'] == requested and health.active(b) for b in s['inventory']['builders']):
                    admission['reasons'].append('slot has a live or unconfirmed omp session; finish it first')
                admission['reasons'].extend(dispatcher.receipt_reasons(c, inventory, requested))
            admission['may_start'] = not admission['reasons']
        if not admission['may_start']:
            raise ValueError('launch withheld: ' + '; '.join(admission['reasons']))
        orca(c, 'terminal', 'show', '--terminal', c['coordinator']['terminal'])
        orca(c, 'orchestration', 'run-show', '--id', c['coordinator']['run'])
        if chosen in occupied:
            raise ValueError('slot has a live or unconfirmed omp session; finish it first')
        item = dispatcher.check_launch(c, ref, inventory, chosen)
        existing = chosen is not None and Path(chosen).is_dir()
        shells = []
        skipped = []
        if existing:
            matches = [w for w in inventory['worktrees'] if w['path'] == chosen]
            if len(matches) != 1:
                raise ValueError('slot must map to exactly one Orca worktree')
            w = matches[0]
            ready_shells = [b for b in inventory['builders'] if b['path'] == chosen and b['connected'] and b.get('shell_ready')]
            for shell in ready_shells:
                binding = shell.get('binding', {'state': 'unknown', 'run_id': None})
                if binding['state'] == 'none':
                    shells.append(shell)
                else:
                    skipped.append({'terminal': shell['terminal'], 'binding': binding})
            if len(shells) > 1:
                raise ValueError('slot has multiple ready shells; refusing ambiguous terminal reuse')
        else:
            # Creation only when an explicitly configured slot path is absent. Name must match.
            missing = chosen or next((p for p in slots if not Path(p).exists()), None)
            if not missing:
                raise ValueError('no free configured slot')
            preparation.update(slot=missing, stage='preparation-attempted', action='worktree-create')
            if c['hooks'].get('before_launch'):
                run(c['hooks']['before_launch'], c['root'])
            created = orca(c, 'worktree', 'create', '--repo', 'id:' + c['orca_repo_id'], '--name', Path(missing).name,
                           '--no-parent', '--setup', 'run')['worktree']
            w = {'id': created['id'], 'path': created['path']}
            preparation['worktree'] = w
            if w['path'] != missing:
                raise ValueError('Orca created a different slot path; inspect it before retrying')
        if existing and c['hooks'].get('before_launch'):
            preparation.update(slot=w['path'], worktree=w, stage='preparation-attempted', action='before-launch-hook')
            run(c['hooks']['before_launch'], w['path'])
        preparation.update(slot=w['path'], worktree=w, stage='prepared')
        preparation.update(stage='preparation-attempted', action='task-branch')
        untracked_files, untracked_count = _untracked(w['path'])
        branch = _prepare_branch(c, ref, item, w['path'])
        w = dict(w, branch=branch)
        preparation.update(worktree=w, branch=branch, stage='prepared')
        fresh = snapshot.collect(c)
        admission = snapshot.may_start(c, fresh)
        preparation['admission'] = admission
        if not admission['may_start']:
            raise ValueError('launch withheld after preparation: ' + '; '.join(admission['reasons']))
        if any(b['path'] == w['path'] and health.active(b) for b in fresh['inventory']['builders']):
            raise ValueError('slot has a live or unconfirmed omp session; launch withheld')
        if any(not b.get('incarnation_id') for b in fresh['inventory']['builders'] if b['path'] == w['path']):
            raise ValueError('slot terminal incarnation is unknown; launch withheld')
        before = sorted((b['terminal'], str(b.get('incarnation_id'))) for b in inventory['builders'] if b['path'] == w['path'])
        after = sorted((b['terminal'], str(b.get('incarnation_id'))) for b in fresh['inventory']['builders'] if b['path'] == w['path'])
        if before != after:
            raise ValueError('slot terminal identity changed during preparation; launch withheld')
        dispatcher.check_launch(c, ref, fresh['inventory'], w['path'])
        b = c['builder']
        rules = Path(w['path']) / b['rules_file']
        if not rules.is_file():
            raise ValueError('configured project rules_file is absent in slot')
        untracked_notice = ('Pre-existing untracked files in this slot belong to the user: '
                            'never stage, commit, move or delete them: ' +
                            json.dumps(untracked_files, ensure_ascii=True) +
                            f' ({untracked_count} total; listing {len(untracked_files)})')
        thread_command = shlex.join(['fleet', '--config',
                                     str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                     'thread', 'open', ref, '--slot', w['path']])
        check_command = shlex.join(['fleet', '--config',
                                    str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                    'thread', 'check', ref])
        ready_command = shlex.join(['fleet', '--config',
                                    str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                    'thread', 'ready', 'NEXT'])
        needs_next_command = shlex.join(['fleet', '--config',
                                         str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                         'thread', 'needs-next', ref])
        parked_command = shlex.join(['fleet', '--config',
                                     str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                     'thread', 'parked', ref])
        prompt = f'''Pad-Ref: {ref}
Fresh builder session for {c['repo']} in {w['path']}. Read {rules} first.
Prepared task branch: {branch}. Publish this task from this branch.
{untracked_notice}
Run pad --workspace {shlex.quote(c['pad_workspace'])} item show {ref}; read all handoff comments and continue the exact task.
Never create extra git worktrees. Self-publish according to project rules.
Open the durable task thread before working: {thread_command}
Never bind this builder terminal to an orchestration Run. Keep terminal identity for thread replies and inbox delivery.
Use the following durable command only for ordinary progress/PARK reports, not decisions or FRESH-READY; never use terminal input:
{shlex.quote(c['orca'])} orchestration send --to run:{c['coordinator']['run']} --type status --subject "{ref}: report" --body "<evidence and outcome>"
Decision requests use EXACTLY ONE channel: fleet thread question {ref}, with the same --config as thread open. It sends the native type question to run:{c['coordinator']['run']} itself; NEVER also send a direct orca send/ask/question for the same decision. Follow-ups use reply/status in the same pending native subthread, not a new direct question. Use fleet thread wait {ref} --timeout 600 for replies and fleet thread result {ref} for the final outcome, with the same --config. Ordinary direct reports use --type status only.
After recording a pending decision and finishing unaffected work, you may end your turn. With supervisor delivery and coordinator.builder_wake enabled, Fleet wakes your positively idle empty-composer pane when Main replies. On wake use the same --config for thread check {ref}, then thread wait {ref} --timeout 600 to read and record the decision before continuing; thread check alone does not read replies. Never settle an unanswered decision or send input to your own terminal.
For release-ready, review, CI-failure or result reports that need Main action, use fleet --config CONFIG thread report {ref} --kind release_ready|review_request|ci_failed|result --pr N --head FULL_SHA "text" (choose one kind; same config as thread open). This sends one typed question, not an ordinary status; await its reply with thread wait. Do not also send thread question or a direct question for the same ask. Typed result asks do not settle the task; thread result remains the final settlement.
If Main authorizes continuing after thread result, run fleet --config CONFIG thread reopen {ref} --authorized-by MSG_ID [--reason TEXT] before any new question, using the same config as thread open; never fall back to direct Run questions.
After merge and a later concrete next-piece handoff, settle the primary thread with thread result. Then make {ready_command} [--body TEXT] your last lane action and stop; replace NEXT with the next ref. This is the only FRESH-READY channel, never raw orca send. It requires work merged after this launch and a concrete handoff after that merge, including for the same ref; inherited boundaries and Pad done alone are insufficient. The idempotent ready receipt records the native message ID and its Pad comment; ambiguous sends forbid duplicates. Ready reports do not schedule launches: Main approves the fresh launch.
FRESH-READY sends a native question requesting Main's launch ruling and wakes its decision-filtered wait. Main replies in that native thread; stop after sending, without waiting for that reply. Existing sent ready receipts remain valid; never resend on upgrade.
If the task is complete but no concrete next ref exists, settle with thread result, then use {needs_next_command} as your last lane action and end your turn. It verifies a new merged PR after launch or an explicit pushed-WIP park completion, sends one NEEDS-NEXT-REF question to Main, and never schedules a launch. Do not substitute a status report.
After a successful ready report, end your turn with a final message and do nothing else: no further tool calls, no background jobs, and never a job that waits on your own terminal/idle state or sends input to your own terminal. Main (or the owner) retires the idle session via the documented retirement path.
An inbox send is durable enqueue, NOT a guaranteed new agent turn. While you own an open PR, MUST keep fleet watch-pr PR --timeout 600 armed as an async background command with the same --config as thread open. Process each event and the steering checkpoint below before re-arming after every completion (including timeout/error) until merged or closed. Never rely on retired pr-wait.sh scripts or single-workflow gh run watch alone; never poll with model turns.
Run anything that can exceed a few minutes (test suites, evals, profilers) as an async background job with an explicit timeout sized to the expected runtime plus margin, and process its completion when it wakes you. The long job itself may run longer than 600 seconds; EVERY foreground/background inbox or CI wait segment, including wait-tool calls and watch-pr timeouts, MUST be at most 600 seconds (10 minutes). Bound each wait, not total job runtime. On a background job timeout or no progress, inspect (output size, stack/py-spy, logs) before re-running; a timeout is evidence, not a retry signal. Background jobs are exempt from the stalled alert, so the explicit job timeout is the hang detector.
Between EVERY wait segment and at natural work checkpoints, run: {check_command}
This returns one JSON object per pending Fleet-managed steering message per line (empty output means none), scoped to your current launch/attempt; reading does NOT acknowledge it. Process each message BEFORE acknowledging its token with: {check_command} --ack TOKEN
Do not re-wait before acting on received instructions/events. Also inspect ALL other native inbox message types at natural checkpoints: thread check covers only Fleet-managed steering, does not consume native mail, and does not replace thread wait for decision replies or native inbox inspection for other mail. Acknowledgment records processing, not mere reading. Sender IDs are advisory, not authentication. Priority steering is refused BEFORE side effects: the stock interrupt probe injected text into a running child's PTY, not structured steering, so no safe stock path is proven. Normal steering never uses terminal input. These bounded waits plus actionable checkpoints are event-driven, not repeated model-turn status polling, and override any older foreground-only or longer-wait instruction in the footer.
If Main uses the sanctioned emergency `fleet thread stop {ref} --reason TEXT`, process its journaled STOP through {check_command} before acknowledging the token. Do not resume interrupted work unless Main tells you to. Normal steering and PARK remain cooperative; builders never interrupt other lanes.
On a durable PARK request: finish your current step, commit and push WIP to this task branch, and write a concrete Pad resume-note comment on {ref}. Make {parked_command} --resume-note COMMENT_ID your last lane action, then end your turn: no further tool calls or background jobs, never wait on your own terminal/idle state or send input to your own terminal. It verifies a clean tree and remote branch head, records explicit park completion without a merge, and reports PARKED to Main. Main may retire and reassign the slot; resuming work requires a Main-confirmed fresh session. This replaces older raw-status-only PARK instructions in a project footer.
{b['footer']}
'''
        path = state.directory(c)
        nonce = str(time.time_ns())
        prompt_path = path / ('prompt-' + nonce + '.txt')
        prompt_path.write_text(prompt)
        prompt_path.chmod(0o600)
        overlay = path / ('omp-' + nonce + '.yml')
        overlay.write_text(yaml.safe_dump({'autoResume': False, 'extendedContext': False,
            'codexResets': {'autoRedeem': 'no'}, 'claudeResets': {'autoRedeem': 'no'},
            'retry': {'modelFallback': False, 'usageAwareFallback': False,
                      'usageReservePct': c['quota']['reserve_percent'], 'usageReservePolicy': 'fail-closed',
                      'fallbackChains': {b['resolved_model']: []}},
            'compaction': {'modelThresholds': {b['resolved_model']: 'f200000'}}}))
        overlay.chmod(0o600)
        session_dir = path / ('sessions-' + nonce)
        session_dir.mkdir(mode=0o700)
        argv = ['omp', '--model', b['model'], '--thinking', b['thinking'], '--config', str(overlay),
                '--session-dir', str(session_dir), '@' + str(prompt_path)]
        command = 'cd -- ' + shlex.quote(w['path']) + ' && ' + shlex.join(argv)
        handle = None
        terminal = None
        reason = 'no_ready_shell'
        if shells:
            candidate = shells[0]['terminal']
            terminal = orca(c, 'terminal', 'show', '--terminal', candidate)['terminal']
            latest = orca(c, 'terminal', 'read', '--terminal', candidate, '--screen')['terminal']
            if (not terminal.get('connected') or terminal.get('orphaned') or
                    not terminal.get('writable') or terminal.get('worktreeId') != w['id'] or
                    not shells[0].get('incarnation_id') or terminal.get('incarnationId') != shells[0]['incarnation_id'] or
                    not health.shell_ready(latest)):
                raise ValueError('slot terminal is no longer a ready shell; launch withheld')
            # Recheck immediately before sending: collection is not a reuse lease.
            binding = health.binding(c, candidate)
            if binding['state'] == 'none':
                handle = candidate
            else:
                skipped.append({'terminal': candidate, 'binding': binding})
        if handle is not None:
            reason = 'ready_unbound_shell'
            preparation['stage'] = 'launch-attempted'
            result = orca(c, 'terminal', 'send', '--terminal', handle, '--text', command, '--enter')
        else:
            if skipped:
                reason = 'run_bound' if any(t['binding']['state'] == 'bound' for t in skipped) else 'binding_unknown'
            preparation['stage'] = 'launch-attempted'
            result = orca(c, 'terminal', 'create', '--worktree', 'id:' + w['id'], '--title', ref + ' builder', '--command', command)
        # Persist receipt before any subsequent operation: failure is never permission to relaunch.
        receipt = {'id': nonce, 'at': time.time(), 'ref': ref, 'branch': branch, 'worktree': w,
                   'terminal': handle if handle is not None else result['terminal']['handle'],
                   'terminal_incarnation_id': (terminal if handle is not None else result['terminal']).get('incarnationId'),
                   'terminal_selection': {'action': 'reuse' if handle is not None else 'create',
                                          'reason': reason, 'skipped_terminals': skipped},
                   'session_dir': str(session_dir),
                   'argv': argv, 'resolved_model': b['resolved_model'], 'thinking': b['thinking'],
                   'orca_status_extension': b.get('orca_status_extension'),
                   'untracked_files': untracked_files, 'untracked_count': untracked_count,
                   'receipt': result, 'prompt': str(prompt_path), 'overlay': str(overlay)}
        state.write(c, 'last-launch', receipt)
        launches = state.read(c, 'launches', {})
        launches[w['path']] = receipt
        state.write(c, 'launches', launches)
        preparation['stage'] = 'launched'
        orca(c, 'worktree', 'set', '--worktree', 'id:' + w['id'], '--workspace-status', 'in-progress', '--comment', ref + ': fresh pinned builder launched')
        if c['hooks'].get('after_launch'):
            run(c['hooks']['after_launch'], w['path'])
        return state.read(c, 'last-launch')

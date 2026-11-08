"""Explicit guarded fresh launches; reuse only a terminal at a verified shell prompt."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import re
import shlex
import time
import yaml
from . import dispatcher, health, snapshot, state
from .commands import CommandError, orca, run


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
        dispatcher.check_launch(c, ref, inventory, chosen)
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
        thread_command = shlex.join(['fleet', '--config',
                                     str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                     'thread', 'open', ref, '--slot', w['path']])
        ready_command = shlex.join(['fleet', '--config',
                                    str(c.get('_config') or c.get('_path') or Path(c['root']) / 'fleet.yml'),
                                    'thread', 'ready', 'NEXT'])
        prompt = f'''Pad-Ref: {ref}
Fresh builder session for {c['repo']} in {w['path']}. Read {rules} first.
Run pad --workspace {shlex.quote(c['pad_workspace'])} item show {ref}; read all handoff comments and continue the exact task.
Never create extra git worktrees. Self-publish according to project rules.
Open the durable task thread before working: {thread_command}
Never bind this builder terminal to an orchestration Run. Keep terminal identity for thread replies and inbox delivery.
Use the following durable command only for ordinary progress/PARK reports, not decisions or FRESH-READY; never use terminal input:
{shlex.quote(c['orca'])} orchestration send --to run:{c['coordinator']['run']} --type status --subject "{ref}: report" --body "<evidence and outcome>"
Decision requests use EXACTLY ONE channel: fleet thread question {ref}, with the same --config as thread open. It sends the native type question to run:{c['coordinator']['run']} itself; NEVER also send a direct orca send/ask/question for the same decision. Follow-ups use reply/status in the same pending native subthread, not a new direct question. Use fleet thread wait {ref} for replies and fleet thread result {ref} for the final outcome, with the same --config. Ordinary direct reports use --type status only.
If Main authorizes continuing after thread result, run fleet --config CONFIG thread reopen {ref} --authorized-by MSG_ID [--reason TEXT] before any new question, using the same config as thread open; never fall back to direct Run questions.
After merge and a later concrete next-piece handoff, settle the primary thread with thread result. Then make {ready_command} [--body TEXT] your last lane action and stop; replace NEXT with the next ref. This is the only FRESH-READY channel, never raw orca send. It requires work merged after this launch and a concrete handoff after that merge, including for the same ref; inherited boundaries and Pad done alone are insufficient. The idempotent ready receipt records the native message ID and its Pad comment; ambiguous sends forbid duplicates. Ready reports do not schedule launches: Main approves the fresh launch.
FRESH-READY sends a native question requesting Main's launch ruling and wakes its decision-filtered wait. Main replies in that native thread; stop after sending, without waiting for that reply. Existing sent ready receipts remain valid; never resend on upgrade.
After a successful ready report, end your turn with a final message and do nothing else: no further tool calls, no background jobs, and never a job that waits on your own terminal/idle state or sends input to your own terminal. Main (or the owner) retires the idle session via the documented retirement path.
An inbox send is durable enqueue, NOT a guaranteed new agent turn. While you own an open PR, MUST keep fleet watch-pr PR armed as an async background command with the same --config as thread open. Process each event and re-arm after every completion (including timeout/error) until merged or closed. Never rely on retired pr-wait.sh scripts or single-workflow gh run watch alone; never poll with model turns.
Run anything that can exceed a few minutes (test suites, evals, profilers, CI waits) as an async background job with an explicit timeout sized to the expected runtime plus margin, and process its completion when it wakes you. Never block your turn in a single foreground wait longer than ~10 minutes. On a background timeout or no progress, inspect (output size, stack/py-spy, logs) before re-running; a timeout is evidence, not a retry signal. Background jobs are exempt from the stalled alert, so the explicit timeout is the hang detector.
On a durable PARK request: finish your current step, commit and push WIP to this task branch, write a Pad handoff, report PARKED with evidence to the Run above. After reporting PARKED, end your turn with a final message and do nothing else: no further tool calls, no background jobs, and never a job that waits on your own terminal/idle state or sends input to your own terminal. Main (or the owner) retires the idle session via the documented retirement path (fleet restart for eligible finished work, or manual idle-proof /exit + fleet reconcile). No next task or FRESH-READY; resume only in a Main-confirmed fresh session.
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
        command = 'cd -- ' + shlex.quote(w['path']) + ' && ' + shlex.join(['omp', '--model', b['model'], '--thinking', b['thinking'], '--config', str(overlay), '--session-dir', str(session_dir), '@' + str(prompt_path)])
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
        receipt = {'id': nonce, 'at': time.time(), 'ref': ref, 'worktree': w,
                   'terminal': handle if handle is not None else result['terminal']['handle'],
                   'terminal_incarnation_id': (terminal if handle is not None else result['terminal']).get('incarnationId'),
                   'terminal_selection': {'action': 'reuse' if handle is not None else 'create',
                                          'reason': reason, 'skipped_terminals': skipped},
                   'session_dir': str(session_dir),
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

"""Explicit guarded fresh launches; reuse only a terminal at a verified shell prompt."""
from contextlib import nullcontext
from pathlib import Path
import re
import shlex
import time
import yaml
from . import health, snapshot, state
from .commands import orca, run


def launch(c, ref, slot=None, *, _locked=False):
    if not re.fullmatch(r'[A-Z]+-\d+', ref):
        raise ValueError('expected uppercase Pad reference, e.g. TASK-123')
    with nullcontext() if _locked else state.lock(c, 'launcher'):
        s = snapshot.collect(c)
        admission = snapshot.may_start(c, s)
        if not admission['may_start']:
            raise ValueError('launch withheld: ' + '; '.join(admission['reasons']))
        orca(c, 'terminal', 'show', '--terminal', c['coordinator']['terminal'])
        orca(c, 'orchestration', 'run-show', '--id', c['coordinator']['run'])
        run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', ref, '--format', 'json'], c['root'])
        inventory = s['inventory']
        occupied = {b['path'] for b in inventory['builders'] if health.active(b)}
        slots = c['builder']['slots']
        chosen = str(Path(slot).expanduser().resolve()) if slot else next((p for p in slots if p not in occupied and Path(p).is_dir()), None)
        if chosen and chosen not in slots:
            raise ValueError('requested slot is not in builder.slots')
        if chosen in occupied:
            raise ValueError('slot has a live or unconfirmed omp session; finish it first')
        existing = chosen is not None and Path(chosen).is_dir()
        if existing:
            matches = [w for w in inventory['worktrees'] if w['path'] == chosen]
            if len(matches) != 1:
                raise ValueError('slot must map to exactly one Orca worktree')
            w = matches[0]
            shells = [b for b in inventory['builders'] if b['path'] == chosen and b['connected'] and b.get('shell_ready')]
            if len(shells) > 1:
                raise ValueError('slot has multiple ready shells; refusing ambiguous terminal reuse')
        else:
            # Creation only when an explicitly configured slot path is absent. Name must match.
            missing = chosen or next((p for p in slots if not Path(p).exists()), None)
            if not missing:
                raise ValueError('no free configured slot')
            if c['hooks'].get('before_launch'):
                run(c['hooks']['before_launch'], c['root'])
            created = orca(c, 'worktree', 'create', '--repo', 'id:' + c['orca_repo_id'], '--name', Path(missing).name,
                           '--no-parent', '--setup', 'run')['worktree']
            w = {'id': created['id'], 'path': created['path']}
            if w['path'] != missing:
                raise ValueError('Orca created a different slot path; inspect it before retrying')
        if existing and c['hooks'].get('before_launch'):
            run(c['hooks']['before_launch'], w['path'])
        b = c['builder']
        rules = Path(w['path']) / b['rules_file']
        if not rules.is_file():
            raise ValueError('configured project rules_file is absent in slot')
        prompt = f'''Pad-Ref: {ref}
Fresh builder session for {c['repo']} in {w['path']}. Read {rules} first.
Run pad --workspace {shlex.quote(c['pad_workspace'])} item show {ref}; read all handoff comments and continue the exact task.
Never create extra git worktrees. Self-publish according to project rules.
Use the following durable command to report, not terminal input:
{shlex.quote(c['orca'])} orchestration send --to run:{c['coordinator']['run']} --type status --subject "{ref}: report" --body "<evidence and outcome>"
An inbox send is durable enqueue, NOT a guaranteed new agent turn. Run fleet watch-pr PR as an async background command; never poll with model turns.
On a durable PARK request: finish your current step, commit and push WIP to this task branch, write a Pad handoff, report PARKED with evidence to the Run above, then stop yourself. No next task or FRESH-READY; resume only in a Main-confirmed fresh session.
{b['footer']}
'''
        path = state.directory(c)
        nonce = str(time.time_ns())
        prompt_path = path / ('prompt-' + nonce + '.txt')
        prompt_path.write_text(prompt)
        prompt_path.chmod(0o600)
        overlay = path / ('omp-' + nonce + '.yml')
        overlay.write_text(yaml.safe_dump({'autoResume': False, 'extendedContext': False,
            'retry': {'modelFallback': False, 'usageAwareFallback': False,
                      'usageReservePct': c['quota']['reserve_percent'], 'usageReservePolicy': 'fail-closed',
                      'fallbackChains': {b['resolved_model']: []}},
            'compaction': {'modelThresholds': {b['resolved_model']: 'f200000'}}}))
        overlay.chmod(0o600)
        session_dir = path / ('sessions-' + nonce)
        session_dir.mkdir(mode=0o700)
        command = 'cd -- ' + shlex.quote(w['path']) + ' && ' + shlex.join(['omp', '--model', b['model'], '--thinking', b['thinking'], '--config', str(overlay), '--session-dir', str(session_dir), '@' + str(prompt_path)])
        if existing and shells:
            handle = shells[0]['terminal']
            terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
            latest = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
            if (not terminal.get('connected') or terminal.get('orphaned') or
                    not terminal.get('writable') or terminal.get('worktreeId') != w['id'] or
                    not health.shell_ready('\n'.join(latest.get('tail', [])))):
                raise ValueError('slot terminal is no longer a ready shell; launch withheld')
            result = orca(c, 'terminal', 'send', '--terminal', handle, '--text', command, '--enter')
        else:
            result = orca(c, 'terminal', 'create', '--worktree', 'id:' + w['id'], '--title', ref + ' builder', '--command', command)
        # Persist receipt before any subsequent operation: failure is never permission to relaunch.
        receipt = {'id': nonce, 'at': time.time(), 'ref': ref, 'worktree': w,
                   'terminal': handle if existing and shells else result['terminal']['handle'],
                   'terminal_incarnation_id': (terminal if existing and shells else result['terminal']).get('incarnationId'),
                   'session_dir': str(session_dir),
                   'receipt': result, 'prompt': str(prompt_path), 'overlay': str(overlay)}
        state.write(c, 'last-launch', receipt)
        launches = state.read(c, 'launches', {})
        launches[w['path']] = receipt
        state.write(c, 'launches', launches)
        orca(c, 'worktree', 'set', '--worktree', 'id:' + w['id'], '--workspace-status', 'in-progress', '--comment', ref + ': fresh pinned builder launched')
        if c['hooks'].get('after_launch'):
            run(c['hooks']['after_launch'], w['path'])
        return state.read(c, 'last-launch')

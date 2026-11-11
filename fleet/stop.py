"""One journaled OMP Escape, bounded idle/composer proof, one STOP submission."""
import json
from pathlib import Path
import shlex
import time
import uuid

from . import health, restart, state, steering, threads
from .commands import json_run, orca


def _proof(c, launch, previous=None):
    handle, slot = launch['terminal'], launch['worktree']['path']
    threads._ownership(c, launch, slot, handle)
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if (terminal.get('agentIdentity') != 'omp' or not terminal.get('tabId')
            or not terminal.get('leafId')):
        raise ValueError('stop requires an exact OMP pane')
    inventory = orca(c, 'worktree', 'ps')
    if inventory.get('truncated') or inventory.get('hostScope', {}).get('omittedHostIds'):
        raise ValueError('stop pane inventory incomplete')
    pane = terminal['tabId'] + ':' + terminal['leafId']
    agents = [a for w in inventory['worktrees'] if w.get('worktreeId') == terminal['worktreeId']
              for a in w.get('agents', []) if a.get('paneKey') == pane and not a.get('parentPaneKey')]
    if (terminal.get('incarnationId') != launch.get('terminal_incarnation_id')
            or terminal.get('worktreeId') != launch['worktree']['id']
            or terminal.get('connected') is not True or terminal.get('writable') is not True
            or terminal.get('orphaned')):
        raise ValueError('stop terminal identity changed')
    if len(agents) != 1 or agents[0].get('agentType') != 'omp':
        raise ValueError('stop requires one exact-pane OMP agent')
    logs = list(Path(launch.get('session_dir', '')).glob('*.jsonl')) if launch.get('session_dir') else []
    if len(logs) != 1:
        raise ValueError('stop session log absent or ambiguous')
    events = [json.loads(line) for line in logs[0].read_bytes().splitlines()]
    headers = [event for event in events if event.get('type') == 'session']
    if len(headers) != 1 or headers[0].get('cwd') != slot or not headers[0].get('id'):
        raise ValueError('stop session does not belong to this slot')
    session = headers[0]['id']
    lease = json_run(['omp', 'session', 'lease', '--json', '--', session], c['root'])
    if lease.get('held') != [session] or lease.get('unknown') != []:
        raise ValueError('stop session lease unproven')
    proof = {'session': session, 'path': str(logs[0]), 'pane': pane,
             'process': restart._foreground(c, launch, slot, handle)}
    if previous is not None and proof != previous:
        raise ValueError('stop target process/session/pane changed; input withheld')
    return proof, agents[0]


def _screen(c, handle):
    screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
    if screen.get('source') != 'screen':
        raise ValueError('stop requires a rendered OMP screen')
    text = '\n'.join(screen.get('tail', []))
    return health.omp_ui(text), text


def _idle(agent):
    program = agent.get('programStatus')
    return (agent.get('state') in ('done', 'idle') and not agent.get('interrupted')
            and (program is None or (isinstance(program, dict)
                                    and program.get('state') in ('done', 'idle'))))


def stop(c, ref, reason, timeout=120):
    threads._name(ref)
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
            or any(not char.isprintable() for char in reason)):
        raise ValueError('stop reason must be a non-empty printable single line (at most 2000 characters)')
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('stop timeout must be 1–600 seconds')
    config_path = str(Path(c.get('_config') or c.get('_path') or
                           Path(c['root']) / 'fleet.yml').expanduser().resolve())
    if any(not char.isprintable() for char in config_path):
        raise ValueError('stop config path must be printable on one line')
    check_command = shlex.join(['fleet', '--config', config_path, 'thread', 'check', ref])
    caller = steering._main(c)
    with state.lock(c, 'launcher', operation='thread stop'), state.lock(c, threads._name(ref)):
        ledger, task, launch, attempt = steering._target(c, ref)
        records = attempt.setdefault('steering', {})
        stops = state.read(c, 'stops', {})
        if launch['id'] in stops:
            raise ValueError('stop already journaled for this launch; reconcile it, never blindly retry')
        if any(row.get('status') == 'sending' for row in records.values()):
            raise ValueError('ambiguous steering send; reconcile before stop')
        token = str(uuid.uuid4())
        body = (f'STOP from Main: {reason}. Run `{check_command}` and follow it; '
                'do not resume prior work unless told.')
        receipt = {'token': token, 'kind': 'stop', 'body': body, 'at': time.time(),
                   'status': 'preparing', 'steps': [], 'launch_id': launch['id']}
        records[token] = receipt
        # Launch-wide, not ref/continuation-local: a carried task must never
        # provide another input opportunity after an ambiguous Escape.
        stops[launch['id']] = {'ref': ref, 'token': token, 'at': receipt['at']}
        state.write(c, 'stops', stops)

        def journal(step, **detail):
            receipt['steps'].append({'step': step, 'at': time.time(), **detail})
            receipt['stop_phase'] = step
            state.write(c, threads._name(ref), ledger)

        def input_once(step, text, enter=False):
            _, agent = _proof(c, launch, proof)
            ui, screen_text = _screen(c, launch['terminal'])
            if step == 'escape':
                console = screen_text.splitlines()[-2:]
                if (ui not in ('working', 'idle', 'draft') and not any(
                        '⎋ force-kill · input forwarded to PTY' in line for line in console)):
                    raise ValueError('stop OMP input surface unknown or dialog; input withheld')
            elif not _idle(agent) or ui != ('idle' if enter else 'draft'):
                raise ValueError('stop fresh input proof is not an idle composer; input withheld')
            journal(step + '-intent')
            args = ['terminal', 'send', '--terminal', launch['terminal'], '--text', text]
            if enter:
                args += ['--enter']
            result = orca(c, *args)
            send = result.get('send', {})
            if (send.get('accepted') is not True or
                    (not enter and send.get('bytesWritten') != len(text.encode()))):
                raise ValueError('stop input outcome ambiguous; manual reconciliation required')
            journal(step + '-accepted', receipt=result)
            return result

        journal('prepared')
        try:
            proof, agent = _proof(c, launch)
            journal('identity-proven', proof=proof)
            input_once('escape', '\x1b')
            deadline = time.monotonic() + timeout
            while True:
                _, agent = _proof(c, launch, proof)
                ui, _ = _screen(c, launch['terminal'])
                if _idle(agent) and ui in ('idle', 'draft'):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError('turn did not stop within bounded wait; no second Escape')
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            journal('idle-proven')
            # Raw Ctrl-C clears the entire idle composer, not a child PTY.
            # It is never sent to a working/unknown/dialog surface, nor retried.
            if ui == 'draft':
                _, agent = _proof(c, launch, proof)
                ui, _ = _screen(c, launch['terminal'])
                if not _idle(agent) or ui not in ('idle', 'draft'):
                    raise ValueError('stop composer ceased to be idle; input withheld')
                if ui == 'draft':
                    input_once('clear', '\x03')
                _, agent = _proof(c, launch, proof)
                ui, _ = _screen(c, launch['terminal'])
                if not _idle(agent) or ui != 'idle':
                    raise ValueError('stop composer did not clear; no repeated clear input')
            journal('composer-empty')
            receipt['status'] = 'sending'
            journal('message-intent')
            message = steering.request(c, 'send', '--from', caller, '--to', launch['terminal'],
                                       '--type', 'status', '--thread-id', attempt.get('thread_id', task['id']),
                                       '--subject', ref + ': STOP', '--body', body)['message']
            if not isinstance(message.get('id'), str) or not message['id']:
                raise ValueError('stop steering send ambiguous; reconcile native inbox')
            receipt.update(status='sent', message_id=message['id'])
            journal('message-sent')
            _, agent = _proof(c, launch, proof)
            ui, _ = _screen(c, launch['terminal'])
            if not _idle(agent) or ui != 'idle':
                raise ValueError('stop composer no longer empty; STOP submission withheld')
            result = input_once('submit', body, enter=True)
            journal('submitted', submission=result)
            return dict(receipt)
        except Exception as error:
            journal('refused', error=str(error))
            raise

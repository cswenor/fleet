"""Retire one proven finished idle session, never interrupt or retry uncertain input."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from . import dispatcher, health, launcher, snapshot, state
from .commands import CommandError, gh, json_run, orca
from .process_identity import omp_executable

# Exact observed omp (type, customType) encodings; never allow arbitrary custom events.
POST_TURN_METADATA = frozenset({
    ('credential_pin', None), ('title_change', None), ('custom', 'todo_hud_state'),
    ('model_usage', None),
    ('custom_message', 'eval-state-context'),
})
PROC = Path('/proc')

def _post_turn_metadata(events):
    """Accept only known metadata and adjacent omp 18.18.1 compaction pairs."""
    index = 0
    while index < len(events):
        event = events[index]
        encoding = (event.get('type'), event.get('customType'))
        if (encoding in POST_TURN_METADATA and
                (encoding != ('custom_message', 'eval-state-context') or
                 event.get('display') is False)):
            index += 1
        elif (event.get('type') == 'compaction' and index + 1 < len(events) and
              _compaction_summary(events[index + 1])):
            index += 2
        else:
            return False
    return True


def _compaction_summary(event):
    message = event.get('message', {})
    return (event.get('type') == 'message' and message.get('role') == 'custom' and
            message.get('customType') == 'eval-state-context')


def _final_stop(events):
    messages = [i for i, event in enumerate(events) if event.get('type') == 'message' and
                not (i > 0 and events[i - 1].get('type') == 'compaction' and
                     _compaction_summary(event))]
    if not messages:
        raise ValueError('session has no completed assistant turn')
    last = messages[-1]
    message = events[last]['message']
    if (message.get('role') != 'assistant' or message.get('stopReason') != 'stop' or
            any(part.get('type') == 'toolCall' for part in message.get('content', []))):
        raise ValueError('session has no final assistant stop; turn running or unfinished')
    if not _post_turn_metadata(events[last + 1:]):
        raise ValueError('session has later work or unknown events after final assistant stop')



def _foreground(c, record, slot, handle):
    """Bind the one private launch directory to a live foreground Linux OMP."""
    if not sys.platform.startswith('linux') or not PROC.is_dir():
        raise ValueError('automatic restart unsupported on this platform: requires local Linux /proc '
                         'exact foreground argv/cwd/terminal-routing ownership proof; retirement withheld')
    if not all(record.get(key) for key in ('overlay', 'session_dir', 'prompt')):
        raise ValueError('launch lacks foreground process binding')
    expected = ['--model', c['builder']['model'], '--thinking', c['builder']['thinking'],
                '--config', record['overlay'], '--session-dir', record['session_dir'],
                '@' + record['prompt']]
    extension = c['builder'].get('orca_status_extension')
    permitted = [expected]
    if extension:
        permitted.append(['--extension', extension, *expected])
    owners = []
    for proc in PROC.glob('[0-9]*'):
        try:
            argv = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
            if not argv or Path(argv[0]).name != 'omp' or argv[1:] not in permitted:
                continue
            before = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            # Only the launched process, not a stopped old process or a shell's
            # background group, can own the terminal input we are about to send.
            if before[0] not in ('R', 'S') or before[2] != before[5] or int(before[4]) == 0 or int(before[5]) <= 0:
                continue
            if not omp_executable((proc / 'exe').readlink(), argv[0]) or str((proc / 'cwd').resolve()) != slot:
                continue
            # Inspect only equality of this routing key; never log environments.
            if ('ORCA_TERMINAL_HANDLE=' + handle).encode() not in (proc / 'environ').read_bytes().split(b'\0'):
                continue
            after = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if after[0] not in ('R', 'S') or before[1:6] != after[1:6] or before[19] != after[19]:
                continue
            owners.append({'pid': int(proc.name), 'start': before[19], 'tty': before[4], 'pgrp': before[2],
                           'boot_id': (PROC / 'sys/kernel/random/boot_id').read_text().strip()})
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    if len(owners) != 1:
        raise ValueError('recorded session has no unique matching foreground OMP process; '
                         'argv must match launch exactly, with only the configured Orca status extension permitted; retirement withheld')
    return owners[0]


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is not None:
            return parsed
    except (AttributeError, TypeError, ValueError):
        pass
    raise ValueError('publication/handoff timestamp is missing, malformed, or lacks timezone')


def _merged_prs(c, finished, date_filter):
    """Search is only a prefilter; prove every page arrived before using it."""
    query = '''
    query($search: String!, $cursor: String) {
      search(query: $search, type: ISSUE, first: 100, after: $cursor) {
        issueCount
        pageInfo { hasNextPage endCursor }
        nodes { ... on PullRequest { number title body mergedAt url } }
      }
    }'''
    search = f'repo:{c["repo"]} is:pr is:merged in:title,body "{finished}" merged:{date_filter}'
    prs, numbers, cursors = [], set(), set()
    cursor, total = None, None
    while True:
        args = ['api', 'graphql', '-f', 'query=' + query, '-f', 'search=' + search]
        if cursor is not None:
            args += ['-f', 'cursor=' + cursor]
        try:
            response = gh(c, *args)
        except CommandError as exc:
            raise ValueError('completion PR search API failed; retirement withheld') from exc
        try:
            if response.get('errors'):
                raise ValueError
            page = response['data']['search']
            count, nodes, info = page['issueCount'], page['nodes'], page['pageInfo']
            if type(count) is not int or count < 0:
                raise ValueError
            if count >= 1000:
                raise ValueError('completion PR search reaches GitHub 1000-result cap; retirement withheld')
            if total is None:
                total = count
            if count != total or not isinstance(nodes, list) or len(nodes) > 100:
                raise ValueError
            for pr in nodes:
                number = pr['number']
                if (type(number) is not int or number in numbers or not pr['mergedAt']
                        or not isinstance(pr['title'], str) or not isinstance(pr['body'], str)
                        or not isinstance(pr['url'], str) or not pr['url']):
                    raise ValueError
                _timestamp(pr['mergedAt'])
                numbers.add(number)
                prs.append(pr)
            more, end = info['hasNextPage'], info['endCursor']
            if type(more) is not bool or len(prs) > total:
                raise ValueError
            if not more:
                if len(prs) != total:
                    raise ValueError
                return prs
            if not nodes or len(prs) >= total or not isinstance(end, str) or not end or end in cursors:
                raise ValueError
            cursors.add(end)
            cursor = end
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            if '1000-result cap' in str(exc):
                raise
            raise ValueError('completion PR search incomplete or inconsistent; retirement withheld') from exc


def _launch_time(record):
    started = record.get('at')
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        raise ValueError('restart requires a valid launch timestamp')
    try:
        return datetime.fromtimestamp(started, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('restart requires a valid launch timestamp') from exc


def _finished_completion(c, ref, record):
    """Prove this launch completed, without requiring a next-task handoff."""
    launched = _launch_time(record)
    from .parking import completion
    parked = completion(c, ref, record)
    if parked:
        return parked
    boundary = (launched - timedelta(days=1)).date().isoformat()
    pattern = re.compile(r'(?<![\w-])' + re.escape(ref) + r'(?![\w-])')
    for pr in _merged_prs(c, ref, '>=' + boundary):
        if (pattern.search(pr['title'] + '\n' + (pr['body'] or ''))
                and _timestamp(pr['mergedAt']) > launched):
            return {'finished_ref': ref, 'pr': pr['url']}
    raise ValueError('finished task requires a new merged PR after this launch or a validated park receipt')


def _completion(c, ref, finished, record, *, require_handoff=False):
    launched = _launch_time(record)
    same_ref = ref == finished
    if not same_ref and not require_handoff:
        from .parking import completion
        parked = completion(c, finished, record)
        if parked:
            return parked
    item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', finished, '--format', 'json'], c['root'])
    boundary = (launched - timedelta(days=1)).date().isoformat()
    pattern = re.compile(r'(?<![\w-])' + re.escape(finished) + r'(?![\w-])')
    def matches(prs):
        return [p for p in prs if pattern.search(p['title'] + '\n' + (p['body'] or ''))]
    prs = matches(_merged_prs(c, finished, '>=' + boundary))
    if not prs and not same_ref:
        prs = matches(_merged_prs(c, finished, '<' + boundary))
    if not prs:
        raise ValueError('finished task has no merged PR carrying its Pad ref')
    if same_ref or require_handoff:
        prs = [p for p in prs if _timestamp(p['mergedAt']) > launched]
        if not prs:
            raise ValueError('same-ref restart or ready report requires a new PR merged after this launch')
    fields = item.get('fields', {})
    if isinstance(fields, str):
        fields = json.loads(fields)
    if not same_ref and not require_handoff and fields.get('status') == 'done':
        return {'finished_ref': finished, 'pr': prs[0]['url'], 'pad_status': 'done'}
    comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'comments', ref, '--format', 'json'], c['root'])
    for comment in comments:
        body = comment.get('body', '')
        if pattern.search(body) and re.search(r'\bhandoff\b', body, re.I):
            if same_ref or require_handoff:
                # A bare ref/handoff marker is not a next-piece description.
                tail = pattern.split(body, maxsplit=1)[1]
                tail = re.sub(r'\bhandoff\b', '', tail, flags=re.I).strip(' \t\r\n:;,.!-')
                if not tail:
                    continue
            for pr in prs:
                if _timestamp(comment.get('created_at')) > _timestamp(pr['mergedAt']):
                    return {'finished_ref': finished, 'pr': pr['url'], 'handoff': comment['id']}
    if require_handoff:
        raise ValueError(f'ready report requires a concrete handoff on {ref} after the new merge')
    raise ValueError('finished task is neither Pad done nor handed off after publication on next task')


def _target(c, slot):
    inventory = health.collect(c)
    if inventory['errors']:
        raise ValueError('terminal health incomplete; retirement withheld')
    trees = [w for w in inventory['worktrees'] if w['path'] == slot]
    candidates = [b for b in inventory['builders'] if b['path'] == slot and health.active(b)]
    if len(trees) != 1 or len(candidates) != 1:
        raise ValueError('slot must have exactly one non-shell session; ambiguous or absent target')
    target = candidates[0]
    if not target['connected']:
        raise ValueError('target disconnected or orphaned; retirement withheld')
    # Launcher bypasses ready Run-bound shells without touching them. They cannot
    # compete for the subsequent launch; every other extra terminal stays ambiguous.
    extras = [b for b in inventory['builders'] if b['path'] == slot and b['terminal'] != target['terminal']]
    if any(not (b['connected'] and b.get('shell_ready') and
                b.get('binding', {}).get('state') == 'bound') for b in extras):
        raise ValueError('slot has multiple terminals; retirement withheld')
    return trees[0], target


def _session(record, slot, previous=None):
    directory = record.get('session_dir')
    if not directory:
        raise ValueError('launch record has no session directory; manual reconciliation required')
    logs = list(Path(directory).glob('*.jsonl'))
    if len(logs) != 1:
        raise ValueError('launch session log absent or ambiguous')
    data = logs[0].read_bytes()
    try:
        events = [json.loads(line) for line in data.splitlines()]
    except (ValueError, UnicodeError) as e:
        raise ValueError('session log incomplete or unreadable') from e
    headers = [e for e in events if e.get('type') == 'session']
    if len(headers) != 1 or headers[0].get('cwd') != slot:
        raise ValueError('session log does not belong to this slot')
    _final_stop(events)
    proof = {'path': str(logs[0]), 'id': headers[0]['id'],
             'digest': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    if previous is not None:
        size = previous['size']
        if (proof['path'] != previous['path'] or proof['id'] != previous['id'] or
                len(data) < size or hashlib.sha256(data[:size]).hexdigest() != previous['digest']):
            raise ValueError('retirement evidence changed; input withheld')
        try:
            appended = [json.loads(line) for line in data[size:].splitlines()]
        except (ValueError, UnicodeError) as e:
            raise ValueError('session log incomplete or unreadable') from e
        if not _post_turn_metadata(appended):
            raise ValueError('retirement evidence changed; input withheld')
    return proof


def _terminal_predates_runtime(c, record, tree, handle):
    """Best-effort local chronology for diagnostics only, never input authority."""
    try:
        process = _foreground(c, record, tree['path'], handle)
        fields = (PROC / str(process['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()
        shell = PROC / fields[3]  # The controlling terminal's session leader.
        before = (shell / 'stat').read_text()
        session = before.rsplit(')', 1)[1].split()
        if (session[0] not in ('R', 'S') or session[3] != shell.name or
                session[4] != process['tty'] or
                (shell / 'exe').resolve(strict=True).name not in ('bash', 'zsh', 'sh') or
                ('ORCA_TERMINAL_HANDLE=' + handle).encode()
                not in (shell / 'environ').read_bytes().split(b'\0')):
            return False
        status = orca(c, 'status')
        pid = status.get('app', {}).get('pid')
        if type(pid) is not int or pid <= 0 or status.get('runtime', {}).get('reachable') is not True:
            return False
        runtime = PROC / str(pid) / 'stat'
        runtime_fields = runtime.read_text().rsplit(')', 1)[1].split()
        runtime_after = runtime.read_text().rsplit(')', 1)[1].split()
        shell_after = (shell / 'stat').read_text().rsplit(')', 1)[1].split()
        return (runtime_fields[0] in ('R', 'S') and runtime_after[0] in ('R', 'S') and
                int(session[19]) < int(runtime_fields[19]) and
                shell_after[19] == session[19] and shell_after[1:6] == session[1:6] and
                runtime_after[19] == runtime_fields[19] and
                _foreground(c, record, tree['path'], handle) == process)
    except (CommandError, OSError, ValueError, IndexError, KeyError, TypeError):
        return False


def _idle(c, record, tree, handle, previous=None):
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if (not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable') or
            terminal.get('worktreeId') != tree['id'] or terminal.get('agentIdentity') != 'omp' or
            not record.get('terminal_incarnation_id') or
            terminal.get('incarnationId') != record['terminal_incarnation_id']):
        raise ValueError('target identity disconnected, changed, or unknown')
    screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
    ui = health.omp_ui('\n'.join(screen.get('tail', [])))
    if ui == 'background':
        raise ValueError('background job status visible; retirement withheld')
    if ui == 'draft':
        raise ValueError('OMP composer contains a draft or is ambiguous; leave input untouched')
    if ui != 'idle':
        raise ValueError(
            'no positive idle OMP composer; working, dialog, background job, or stale screen'
            + ('; terminal session predates the current Orca runtime: screen model may be '
               'stale after Orca restart; see docs/MAIN.md#post-restart-clipped-screen. '
               'Agent done/idle alone is insufficient; no retirement input sent'
               if _terminal_predates_runtime(c, record, tree, handle) else ''))
    ps = orca(c, 'worktree', 'ps')
    if ps.get('truncated') or ps.get('hostScope', {}).get('omittedHostIds'):
        raise ValueError('agent status inventory incomplete')
    trees = [w for w in ps['worktrees'] if w['worktreeId'] == tree['id']]
    key = terminal.get('tabId', '') + ':' + terminal.get('leafId', '')
    agents = [a for w in trees for a in w.get('agents', []) if a.get('paneKey') == key and not a.get('parentPaneKey')]
    if (len(agents) != 1 or agents[0].get('agentType') != 'omp' or
            agents[0].get('state') not in ('done', 'idle') or agents[0].get('interrupted') or
            (agents[0].get('programStatus') is not None and
             (not isinstance(agents[0]['programStatus'], dict) or
              agents[0]['programStatus'].get('state') not in ('done', 'idle')))):
        raise ValueError('exact-pane agent is running, interrupted, or unknown')
    proof = _session(record, tree['path'], previous)
    lease = json_run(['omp', 'session', 'lease', '--json', '--', proof['id']], c['root'])
    if lease.get('held') != [proof['id']] or lease.get('unknown') != []:
        raise ValueError('recorded session is no longer positively owned by a live omp process')
    proof['process'] = _foreground(c, record, tree['path'], handle)
    return proof

def _exited_shell(process, handle):
    """Positive same-boot exit and sole foreground shell on the recorded pts."""
    if not process.get('boot_id') or (PROC / 'sys/kernel/random/boot_id').read_text().strip() != process['boot_id']:
        raise ValueError('boot identity changed or unknown')
    old = PROC / str(process['pid']) / 'stat'
    try:
        if old.read_text().rsplit(')', 1)[1].split()[19] == process['start']:
            raise ValueError('bound OMP process is still present')
    except FileNotFoundError:
        pass
    occupants = []
    for proc in PROC.glob('[0-9]*'):
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            continue
        if fields[4] == process['tty']:
            occupants.append((proc, fields))
    if len(occupants) != 1:
        raise ValueError('recorded pts has no unique shell occupant')
    proc, before = occupants[0]
    if (before[0] not in ('R', 'S') or int(before[4]) == 0 or
            before[2] != before[5] or before[2] != proc.name or
            (proc / 'exe').resolve(strict=True).name not in ('bash', 'zsh', 'sh') or
            ('ORCA_TERMINAL_HANDLE=' + handle).encode() not in (proc / 'environ').read_bytes().split(b'\0')):
        raise ValueError('recorded pts is not owned by the foreground POSIX shell')
    after = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    if before[0:6] != after[0:6] or before[19] != after[19]:
        raise ValueError('foreground shell changed during verification')
    return {'pid': int(proc.name), 'start': before[19], 'tty': before[4], 'pgrp': before[2]}



def restart(c, ref, slot, finished_ref):
    if not sys.platform.startswith('linux'):
        raise ValueError('automatic restart unsupported on this platform: exact local Linux process proof required; use verified manual retirement and reconcile')
    if not all(re.fullmatch(r'[A-Z]+-\d+', x) for x in (ref, finished_ref)):
        raise ValueError('restart requires uppercase next and finished Pad refs')
    slot = str(Path(slot).expanduser().resolve())
    if slot not in c['builder']['slots']:
        raise ValueError('requested slot is not in builder.slots')
    with state.lock(c, 'launcher'):
        tree, target = _target(c, slot)
        record = state.read(c, 'launches', {}).get(slot, {})
        if (not record.get('id') or record.get('ref') != finished_ref or
                record.get('terminal') != target['terminal'] or
                record.get('worktree', {}).get('id') != tree['id']):
            raise ValueError('no exact finished-task launch record for slot/terminal; manual reconciliation required')
        retirements = state.read(c, 'retirements', {})
        previous = retirements.get(slot, {})
        if previous.get('launch_id') == record['id']:
            raise ValueError('retirement already attempted for this launch; inspect retirement and launch receipts, never retry blindly')
        proof = _idle(c, record, tree, target['terminal'])
        completion = _completion(c, ref, finished_ref, record)
        # Validate next task and gates before retiring, then again inside normal launch.
        s = snapshot.collect(c)
        # Only this exact proven old launch is exempt; competing receipts still block.
        dispatcher.check_launch(c, ref, s['inventory'], slot, retiring=record)
        s['inventory']['builders'] = [b for b in s['inventory']['builders'] if b['terminal'] != target['terminal']]
        admission = snapshot.may_start(c, s)
        if not admission['may_start']:
            raise ValueError('restart withheld: ' + '; '.join(admission['reasons']))
        fresh_tree, fresh_target = _target(c, slot)
        fresh_proof = _idle(c, record, tree, target['terminal'], previous=proof)
        if (fresh_tree != tree or fresh_target['terminal'] != target['terminal'] or
                fresh_proof['process'] != proof['process']):
            raise ValueError('retirement evidence changed; input withheld')
        proof = fresh_proof
        receipt = {'at': time.time(), 'launch_id': record['id'], 'next_ref': ref,
                   'finished_ref': finished_ref, 'terminal': target['terminal'],
                   'terminal_incarnation_id': record['terminal_incarnation_id'],
                   'session': proof, 'completion': completion, 'stage': 'exit-attempted'}
        retirements[slot] = receipt
        state.write(c, 'retirements', retirements)
        # Journal BEFORE input: transport failure or crash can never authorize a second /exit.
        try:
            receipt['exit_receipt'] = orca(c, 'terminal', 'send', '--terminal', target['terminal'], '--text', '/exit', '--enter')
            receipt['stage'] = 'exit-sent'
            state.write(c, 'retirements', retirements)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                terminal = orca(c, 'terminal', 'show', '--terminal', target['terminal'])['terminal']
                if (not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable') or
                        terminal.get('incarnationId') != record['terminal_incarnation_id'] or terminal.get('worktreeId') != tree['id']):
                    raise ValueError('target identity lost after exit; inspect retirement receipt, no retry')
                screen = orca(c, 'terminal', 'read', '--terminal', target['terminal'], '--screen')['terminal']
                text = '\n'.join(screen.get('tail', []))
                if health.shell_ready(screen):
                    break
                residue = health.shell_reply_residue(text)
                if residue:
                    receipt['stage'] = 'shell-verified-with-residue'
                    state.write(c, 'retirements', retirements)
                    try:
                        shell = _exited_shell(proof['process'], target['terminal'])
                        # Re-read identity, process and exact residue immediately before input.
                        latest = orca(c, 'terminal', 'show', '--terminal', target['terminal'])['terminal']
                        latest_screen = orca(c, 'terminal', 'read', '--terminal', target['terminal'], '--screen')['terminal']
                        if (not latest.get('connected') or latest.get('orphaned') or not latest.get('writable') or
                                latest.get('incarnationId') != record['terminal_incarnation_id'] or
                                latest.get('worktreeId') != tree['id'] or
                                '\n'.join(latest_screen.get('tail', [])) != text or
                                _exited_shell(proof['process'], target['terminal']) != shell):
                            raise ValueError('residue cleanup evidence changed')
                        receipt['residue_cleanup'] = {'stage': 'input-attempted', 'shell': shell, 'reply': residue}
                        state.write(c, 'retirements', retirements)
                        orca(c, 'terminal', 'send', '--terminal', target['terminal'], '--text', '\x15')
                        clean = orca(c, 'terminal', 'read', '--terminal', target['terminal'], '--screen')['terminal']
                        current = orca(c, 'terminal', 'show', '--terminal', target['terminal'])['terminal']
                        clean_text = '\n'.join(clean.get('tail', []))
                        clean_lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
                        expected_prompt = text.splitlines()[-1][:-len(residue)].rstrip()
                        if (not current.get('connected') or current.get('orphaned') or not current.get('writable') or
                                current.get('incarnationId') != record['terminal_incarnation_id'] or
                                current.get('worktreeId') != tree['id'] or
                                not health.shell_ready(clean) or not clean_lines or
                                clean_lines[-1] != expected_prompt or
                                _exited_shell(proof['process'], target['terminal']) != shell):
                            raise ValueError('line-kill did not produce a verified clean prompt')
                        receipt['residue_cleanup']['stage'] = 'clean-prompt-verified'
                        break
                    except (OSError, ValueError, IndexError, RuntimeError) as exc:
                        raise ValueError('residue remains unconfirmed; launch withheld; manually verify and clear '
                                         'the shell line before fleet launch; never retry cleanup or restart') from exc
                time.sleep(0.25)
            else:
                raise ValueError('exit timed out without verified shell; inspect retirement receipt, no retry')
            receipt['stage'] = 'shell-verified'
            state.write(c, 'retirements', retirements)
            receipt['stage'] = 'launch-attempted'
            state.write(c, 'retirements', retirements)
            launched = launcher.launch(c, ref, slot, _locked=True)
        except Exception as exc:
            receipt['failure'] = type(exc).__name__
            receipt['retry_guidance'] = (
                'old session retired; inspect retirement, launch-preparations and launch receipts; '
                'after confirming no replacement session started and admission recovers, retry fleet launch '
                'for next_ref and this slot, not fleet restart'
                if receipt['stage'] == 'launch-attempted' else
                'launch withheld; manually verify and clear the shell line before fleet launch; '
                'never retry cleanup or restart'
                if receipt['stage'] == 'shell-verified-with-residue' else
                'retirement unconfirmed; inspect terminal and retirement receipt; never resend /exit or retry fleet restart')
            state.write(c, 'retirements', retirements)
            if isinstance(exc, ValueError):
                raise ValueError(f"{exc}; {receipt['retry_guidance']}") from exc
            if isinstance(exc, CommandError):
                raise CommandError(f"{exc}; {receipt['retry_guidance']}", code=exc.code) from exc
            exc.add_note(receipt['retry_guidance'])
            raise
        receipt['stage'] = 'launched'
        receipt['next_launch_id'] = launched['id']
        state.write(c, 'retirements', retirements)
        return {'retirement': receipt, 'launch': launched}

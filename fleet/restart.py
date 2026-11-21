"""Retire one proven finished idle session, never interrupt or retry uncertain input."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import time
from . import dispatcher, github, governor, health, launcher, snapshot, state, steering, threads
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
    argv = record.get('argv')
    if argv is None:
        nonce = record.get('id')
        if (not isinstance(nonce, str) or not nonce.isdecimal()
                or Path(record['session_dir']).name != 'sessions-' + nonce
                or Path(record['overlay']).name != 'omp-' + nonce + '.yml'):
            raise ValueError('old launch receipt lacks recorded argv and unique launch artifacts; '
                             'config model/thinking changes cannot be verified safely. Use positively '
                             'verified idle /exit + reconcile + launch; retirement withheld')
        expected = None
        extension = c['builder'].get('orca_status_extension')
    elif (not isinstance(argv, list) or len(argv) < 2 or argv[0] != 'omp'
          or not all(isinstance(arg, str) and arg for arg in argv)):
        raise ValueError('old launch receipt has invalid recorded argv; retirement withheld')
    else:
        expected = argv[1:]
        extension = record.get('orca_status_extension')
    permitted = [expected]
    if extension and expected is not None:
        permitted.append(['--extension', extension, *expected])
    owners = []
    for proc in PROC.glob('[0-9]*'):
        try:
            argv = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
            if not argv or Path(argv[0]).name != 'omp':
                continue
            if expected is None:
                args = argv[1:]
                if extension and args[:2] == ['--extension', extension]:
                    args = args[2:]
                if (len(args) != 9 or args[0] != '--model' or args[2] != '--thinking'
                        or args[4:] != ['--config', record['overlay'], '--session-dir',
                                        record['session_dir'], '@' + record['prompt']]):
                    continue
            elif argv[1:] not in permitted:
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
                         'argv must match the old launch receipt exactly, with only its recorded Orca status extension permitted; retirement withheld')
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
    investigation = _investigation_completion(c, ref, record)
    if investigation:
        return investigation
    boundary = (launched - timedelta(days=1)).date().isoformat()
    pattern = re.compile(r'(?<![\w-])' + re.escape(ref) + r'(?![\w-])')
    for pr in _merged_prs(c, ref, '>=' + boundary):
        if (pattern.search(pr['title'] + '\n' + (pr['body'] or ''))
                and _timestamp(pr['mergedAt']) > launched):
            return {'finished_ref': ref, 'pr': pr['url']}
    raise ValueError('finished task requires a new merged PR after this launch or a validated park receipt')


def _investigation_completion(c, finished, record, evidence=None):
    if not record.get('id'):
        if evidence is not None:
            raise ValueError('no-PR completion requires an exact launch ID')
        return None
    task = state.read(c, threads._name(finished), {}).get(finished, {})
    attempt = task.get('attempts', {}).get(record['id'], {})
    result = attempt.get('result', '')
    explicit_main = evidence is not None
    if not explicit_main:
        marker = (re.search(r'^No-PR:[ \t]*(\S[^\r\n]*)$', result, re.M | re.I)
                  if isinstance(result, str) else None)
        receipt = state.read(c, 'retirements', {}).get(record.get('worktree', {}).get('path'), {})
        recorded = receipt.get('completion', {})
        if marker:
            evidence = marker[1]
        elif (receipt.get('launch_id') == record['id'] and recorded.get('kind') == 'investigation'
              and recorded.get('actor') == c['coordinator']['terminal']
              and recorded.get('result') == result and recorded.get('evidence')):
            evidence = recorded['evidence']
        else:
            return None
    if (not task.get('id') or attempt.get('settled') is not True
            or not result or attempt.get('terminal') != record['terminal']
            or attempt.get('slot') != record['worktree']['path']):
        raise ValueError('no-PR completion requires the exact launch settled thread result; '
                         'record fleet thread result before restarting or reporting readiness')
    item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show',
                     finished, '--format', 'json'], c['root'])
    if dispatcher.fields(item).get('status') != 'done':
        raise ValueError('no-PR completion requires Pad done; finish the investigation on Pad first')
    if not explicit_main and _launch_time({'at': attempt.get('settled_at')}) <= _launch_time(record):
        raise ValueError('no-PR completion requires settlement after this launch')
    return {'kind': 'investigation', 'finished_ref': finished, 'pad_status': 'done',
            'evidence': evidence,
            'actor': c['coordinator']['terminal'] if explicit_main else record['terminal'],
            'at': attempt.get('settled_at'), 'attempt': attempt.get('attempt_id', record['id']),
            'result': result}


def _handoff_after(c, ref, finished, boundaries, *, concrete):
    pattern = re.compile(r'(?<![\w-])' + re.escape(finished) + r'(?![\w-])')
    comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'comments',
                         ref, '--format', 'json'], c['root'])
    for comment in comments:
        body = comment.get('body', '')
        if not (pattern.search(body) and re.search(r'\bhandoff\b', body, re.I)):
            continue
        if concrete:
            tail = pattern.split(body, maxsplit=1)[1]
            tail = re.sub(r'\bhandoff\b', '', tail, flags=re.I).strip(' \t\r\n:;,.!-')
            if not tail:
                continue
        for boundary, proof in boundaries:
            if _timestamp(comment.get('created_at')) > boundary:
                return {**proof, 'handoff': comment['id']}
    return None


def _completion(c, ref, finished, record, *, require_handoff=False):
    launched = _launch_time(record)
    same_ref = ref == finished
    from .parking import completion
    parked = completion(c, finished, record)
    if parked:
        if require_handoff:
            raise ValueError('parked attempts cannot send FRESH-READY; Main must confirm a fresh '
                             'resume with same-ref fleet restart REF --slot SLOT --finished-ref REF')
        return parked
    investigation = _investigation_completion(c, finished, record)
    if investigation:
        if not (same_ref or require_handoff):
            return investigation
        settled = _launch_time({'at': investigation['at']})
        if settled <= launched:
            raise ValueError('no-PR readiness requires settlement after this launch')
        handoff = _handoff_after(c, ref, finished, [(settled, investigation)], concrete=True)
        if handoff:
            return handoff
        raise ValueError(f'no-PR readiness requires a concrete handoff on {ref} after settlement')
    item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', finished, '--format', 'json'], c['root'])
    boundary = (launched - timedelta(days=1)).date().isoformat()
    pattern = re.compile(r'(?<![\w-])' + re.escape(finished) + r'(?![\w-])')
    def matches(prs):
        return [p for p in prs if pattern.search(p['title'] + '\n' + (p['body'] or ''))]
    prs = matches(_merged_prs(c, finished, '>=' + boundary))
    if not prs and not same_ref:
        prs = matches(_merged_prs(c, finished, '<' + boundary))
    if not prs:
        moved = github.repo_move_error(c)
        if moved:
            raise ValueError(moved)
        raise ValueError('finished task has no merged PR carrying its Pad ref; for a completed '
                         'investigation use fleet restart NEXT --slot SLOT --finished-ref FINISHED '
                         '--no-pr-evidence TEXT (Pad done and settled thread result required)')
    if same_ref or require_handoff:
        prs = [p for p in prs if _timestamp(p['mergedAt']) > launched]
        if not prs:
            raise ValueError('same-ref restart or ready report requires a new PR merged after this launch')
    fields = item.get('fields', {})
    if isinstance(fields, str):
        fields = json.loads(fields)
    if not same_ref and not require_handoff and fields.get('status') == 'done':
        return {'finished_ref': finished, 'pr': prs[0]['url'], 'pad_status': 'done'}
    handoff = _handoff_after(c, ref, finished, [
        (_timestamp(pr['mergedAt']), {'finished_ref': finished, 'pr': pr['url']})
        for pr in prs], concrete=same_ref or require_handoff)
    if handoff:
        return handoff
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
        raise ValueError('slot must have exactly one non-shell session; ambiguous or absent target; '
                         'if already retired, inspect retirement/launch receipts and, after fixing admission '
                         'and confirming no replacement started, use fleet launch NEXT --slot PATH, not restart')
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


def _session(record, slot, previous=None, *, stopped=None):
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
    if stopped is None:
        _final_stop(events)
    elif stopped.get('session') != headers[0].get('id'):
        raise ValueError('stop journal belongs to a different session')
    proof = {'path': str(logs[0]), 'id': headers[0]['id'],
             'digest': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    for event in events:
        if event.get('type') == 'model_change':
            proof['model'] = event.get('model')
        elif event.get('type') == 'thinking_level_change':
            proof['thinking'] = event.get('thinkingLevel')
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


def _pre_runtime_shell(c, record, tree, handle, process=None):
    """Prove the controlling shell predates the reachable local Orca process."""
    try:
        if process is None:
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
            return None
        status = orca(c, 'status')
        pid = status.get('app', {}).get('pid')
        if type(pid) is not int or pid <= 0 or status.get('runtime', {}).get('reachable') is not True:
            return None
        runtime = PROC / str(pid) / 'stat'
        runtime_fields = runtime.read_text().rsplit(')', 1)[1].split()
        runtime_after = runtime.read_text().rsplit(')', 1)[1].split()
        shell_after = (shell / 'stat').read_text().rsplit(')', 1)[1].split()
        if (runtime_fields[0] in ('R', 'S') and runtime_after[0] in ('R', 'S') and
                int(session[19]) < int(runtime_fields[19]) and
                shell_after[19] == session[19] and shell_after[1:6] == session[1:6] and
                runtime_after[19] == runtime_fields[19] and
                _foreground(c, record, tree['path'], handle) == process):
            return {'pid': int(shell.name), 'start': session[19], 'tty': session[4], 'pgrp': session[2]}
    except (CommandError, OSError, ValueError, IndexError, KeyError, TypeError):
        pass
    return None


def _children_are(process, expected=()):
    """Check every Linux thread, not just the group leader's children."""
    tasks = PROC / str(process['pid']) / 'task'
    try:
        before = set(tasks.iterdir())
        return (tasks / str(process['pid']) in before
                and sorted(child for task in before for child in (task / 'children').read_text().split())
                == list(expected)
                and set(tasks.iterdir()) == before)
    except OSError:
        return False


def _idle(c, record, tree, handle, previous=None, *, stopped=None, recover_stale=False):
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if (not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable') or
            terminal.get('worktreeId') != tree['id'] or terminal.get('agentIdentity') != 'omp' or
            not record.get('terminal_incarnation_id') or
            terminal.get('incarnationId') != record['terminal_incarnation_id']):
        raise ValueError('target identity disconnected, changed, or unknown')
    screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
    ui = ('unknown' if screen.get('source') == 'screen-unavailable' else
          health.omp_ui('\n'.join(screen.get('tail', []))))
    if ui == 'background':
        raise ValueError('background job status visible; retirement withheld')
    if ui == 'draft':
        raise ValueError('OMP composer contains a draft or is ambiguous; leave input untouched')
    stale_recovery = ui != 'idle' or bool(previous and previous.get('stale_shell'))
    stale_shell = None
    if ui != 'idle':
        stale_shell = _pre_runtime_shell(c, record, tree, handle)
        if not stale_shell:
            raise ValueError('no positive idle OMP composer; working, dialog, background job, or stale screen')
        if not recover_stale or ui != 'unknown' or stopped is not None:
            raise ValueError('no positive idle OMP composer; terminal session predates the current Orca runtime: '
                             'screen model may be stale after Orca restart; '
                             'see docs/MAIN.md#post-restart-clipped-screen; no retirement input sent')
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
    proof = _session(record, tree['path'], previous, stopped=stopped)
    lease = json_run(['omp', 'session', 'lease', '--json', '--', proof['id']], c['root'])
    if lease.get('held') != [proof['id']] or lease.get('unknown') != []:
        raise ValueError('recorded session is no longer positively owned by a live omp process')
    proof['process'] = _foreground(c, record, tree['path'], handle)
    if stopped is not None and stopped.get('process') != proof['process']:
        raise ValueError('stop journal belongs to a different process')
    if stale_recovery:
        shell = _pre_runtime_shell(c, record, tree, handle, proof['process'])
        if not shell:
            raise ValueError('no positive idle OMP composer; pre-restart chronology unverified; '
                             'see docs/MAIN.md#post-restart-clipped-screen')
        steering._main(c)
        if not governor._settled(c, record, tree['path']):
            raise ValueError('stale screen recovery requires the exact settled primary attempt; '
                             'see docs/MAIN.md#post-restart-clipped-screen')
        if health.binding(c, handle)['state'] != 'none':
            raise ValueError('stale screen recovery requires the builder to be positively unbound')
        if not _children_are(proof['process']):
            raise ValueError('stale screen recovery requires a child-free foreground OMP; '
                             'see docs/MAIN.md#post-restart-clipped-screen')
        if (stale_shell is not None and shell != stale_shell or
                previous and previous.get('stale_shell', shell) != shell):
            raise ValueError('controlling shell changed; retirement withheld')
        proof['stale_shell'] = shell
    return proof

def _omp_exited(process):
    if not process.get('boot_id') or (PROC / 'sys/kernel/random/boot_id').read_text().strip() != process['boot_id']:
        raise ValueError('boot identity changed or unknown')
    old = PROC / str(process['pid']) / 'stat'
    try:
        if old.read_text().rsplit(')', 1)[1].split()[19] == process['start']:
            return False
    except FileNotFoundError:
        pass
    return True


def _tty_occupants(tty):
    for proc in PROC.glob('[0-9]*'):
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            continue
        if fields[4] == tty:
            yield proc, fields


def _exited_shell(process, handle):
    """Positive same-boot exit and sole foreground shell on the recorded pts."""
    if not _omp_exited(process):
        raise ValueError('bound OMP process is still present')
    occupants = list(_tty_occupants(process['tty']))
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


def _terminate_omp(c, record, tree, handle, proof, journal):
    """Pin the PID before the last proof; no numeric-PID signal fallback."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise ValueError('pidfd unavailable; see docs/MAIN.md#post-restart-clipped-screen')
    try:
        fd = os.pidfd_open(proof['process']['pid'])
        try:
            latest = _idle(c, record, tree, handle, previous=proof, recover_stale=True)
            if latest['process'] != proof['process']:
                raise ValueError('foreground changed before SIGTERM; retirement withheld')
            _session(record, tree['path'], latest)
            if (_foreground(c, record, tree['path'], handle) != proof['process'] or
                    not _children_are(proof['process'])):
                raise ValueError('child-free foreground proof changed before SIGTERM; retirement withheld')
            shell, omp = proof['stale_shell'], proof['process']
            if ({proc.name for proc, _ in _tty_occupants(omp['tty'])} !=
                    {str(shell['pid']), str(omp['pid'])} or
                    not _children_are(shell, (str(omp['pid']),))):
                raise ValueError('stale pane has other shell children or tty occupants; retirement withheld')
            journal()
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError('could not complete exact-process SIGTERM; see docs/MAIN.md#post-restart-clipped-screen') from exc


def _close_stale_pane(c, tree, record, proof, journal, deadline):
    handle = record['terminal']
    def ready():
        try:
            return (_exited_shell(proof['process'], handle) == proof['stale_shell']
                    and _children_are(proof['stale_shell']))
        except (FileNotFoundError, ValueError):
            return False

    while not ready():
        if time.monotonic() >= deadline:
            raise ValueError('same child-free foreground shell not proven; close withheld')
        time.sleep(0.25)
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if health.binding(c, handle)['state'] != 'none':
        raise ValueError('retired pane is not positively unbound; close withheld')
    if (not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable') or
            terminal.get('incarnationId') != record['terminal_incarnation_id'] or
            terminal.get('worktreeId') != tree['id'] or not ready()):
        raise ValueError('exact unbound shell or pane identity changed; close withheld')
    journal('pane-close-attempted')
    if not ready():
        raise ValueError('child-free foreground shell changed before close; close withheld')
    result = orca(c, 'terminal', 'close', '--terminal', handle)
    closed = result.get('close', {})
    if closed.get('handle') != handle or closed.get('ptyKilled') is not True:
        raise ValueError('exact pane close unconfirmed; launch withheld')
    return result


def relaunch(c, ref, slot, reason, *, keep_uncommitted=False, model=None, thinking=None):
    """Main explicitly replaces an unfinished same-task session, never interrupts."""
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
            or any(not char.isprintable() for char in reason)):
        raise ValueError('relaunch reason must be a non-empty printable single line (at most 2000 characters)')
    steering._main(c)
    return _replace(c, ref, slot, ref, reason=reason, keep_uncommitted=keep_uncommitted,
                    model=model, thinking=thinking)


def restart(c, ref, slot, finished_ref, *, model=None, thinking=None, no_pr_evidence=None):
    if no_pr_evidence is not None:
        if (not isinstance(no_pr_evidence, str) or not no_pr_evidence.strip()
                or len(no_pr_evidence) > 2000 or any(not ch.isprintable() for ch in no_pr_evidence)):
            raise ValueError('no-PR evidence must be a non-empty printable single line (at most 2000 characters)')
        steering._main(c)
    return _replace(c, ref, slot, finished_ref, model=model, thinking=thinking,
                    no_pr_evidence=no_pr_evidence)


def _replace(c, ref, slot, finished_ref, *, reason=None, keep_uncommitted=False,
             model=None, thinking=None, no_pr_evidence=None):
    operation = 'relaunch' if reason is not None else 'restart'
    if not sys.platform.startswith('linux'):
        raise ValueError('automatic restart unsupported on this platform: exact local Linux process proof required; use verified manual retirement and reconcile')
    if not all(re.fullmatch(r'[A-Z]+-\d+', x) for x in (ref, finished_ref)):
        raise ValueError('restart requires uppercase next and finished Pad refs')
    slot = str(Path(slot).expanduser().resolve())
    if slot not in c['builder']['slots']:
        raise ValueError('requested slot is not in builder.slots')
    with state.lock(c, 'launcher', operation=operation), state.slot_lock(
            c, slot, wait_seconds=15, operation=operation):
        launch_config = c
        c = launcher.resolve_model(c, ref, model=model, thinking=thinking)
        tree, target = _target(c, slot)
        record = state.read(c, 'launches', {}).get(slot, {})
        if (not record.get('id') or record.get('ref') != finished_ref or
                record.get('terminal') != target['terminal'] or
                record.get('worktree', {}).get('id') != tree['id']):
            raise ValueError('no exact finished-task launch record for slot/terminal; use the primary '
                             'launch ref as --finished-ref (not its carried ref), or positively verified '
                             'manual retirement + fleet reconcile + fleet launch')
        retirements = state.read(c, 'retirements', {})
        previous = retirements.get(slot, {})
        if previous.get('launch_id') == record['id']:
            raise ValueError('retirement already attempted for this launch; inspect retirement and launch receipts, never retry blindly')
        stopped = None
        if reason is not None:
            stop = state.read(c, 'stops', {}).get(record['id'], {})
            if stop.get('ref'):
                ledger = state.read(c, threads._name(stop['ref']), {})
                attempt = ledger.get(stop['ref'], {}).get('attempts', {}).get(record['id'], {})
                row = attempt.get('steering', {}).get(stop.get('token'), {})
                steps = row.get('steps', [])
                if any(step.get('step') == 'submitted' for step in steps):
                    stopped = next((step.get('proof') for step in steps
                                    if step.get('step') == 'identity-proven'), None)
        idle_options = ({'recover_stale': True} if reason is None else
                        {'stopped': stopped} if stopped is not None else {})
        try:
            proof = _idle(c, record, tree, target['terminal'], **idle_options)
        except ValueError as exc:
            if reason is not None:
                raise ValueError(f"{exc}; run `fleet thread stop {ref}` first") from exc
            raise
        completion = ({'kind': 'relaunch', 'reason': reason} if reason is not None else
                      _investigation_completion(c, finished_ref, record, no_pr_evidence)
                      if no_pr_evidence is not None else _completion(c, ref, finished_ref, record))
        if completion.get('kind') == 'investigation':
            if no_pr_evidence is None:
                steering._main(c)
            completion['actor'] = c['coordinator']['terminal']
        # Validate next task and gates before retiring, then again inside normal launch.
        try:
            s = snapshot.collect(c)
            # Only this exact proven old launch is exempt; competing receipts still block.
            item = dispatcher.check_launch(c, ref, s['inventory'], slot, retiring=record)
            s['inventory']['builders'] = [b for b in s['inventory']['builders'] if b['terminal'] != target['terminal']]
            admission = snapshot.may_start(c, s)
            if not admission['may_start']:
                raise ValueError('; '.join(admission['reasons']))
            launcher.orca(c, 'terminal', 'show', '--terminal', c['coordinator']['terminal'])
            launcher.orca(c, 'orchestration', 'run-show', '--id', c['coordinator']['run'])
            tracked_files = launcher._tracked(slot) if reason is not None else []
            if tracked_files and not keep_uncommitted:
                raise ValueError('uncommitted tracked/staged work: ' + json.dumps(tracked_files) +
                                 '; use --keep-uncommitted to preserve it')
            branch = launcher._prepare_branch(c, ref, item, slot, dry_run=True, **(
                {'keep_uncommitted': True} if keep_uncommitted else {}))
        except (ValueError, CommandError) as exc:
            raise ValueError(f'{operation} preflight withheld: {exc}; old session untouched') from exc
        fresh_tree, fresh_target = _target(c, slot)
        fresh_proof = _idle(c, record, tree, target['terminal'], previous=proof, **idle_options)
        if (fresh_tree != tree or fresh_target['terminal'] != target['terminal'] or
                fresh_proof['process'] != proof['process']):
            raise ValueError('retirement evidence changed; input withheld')
        proof = fresh_proof
        receipt = {'at': time.time(), 'launch_id': record['id'], 'next_ref': ref,
                   'finished_ref': finished_ref, 'terminal': target['terminal'],
                   'terminal_incarnation_id': record['terminal_incarnation_id'],
                   'session': proof, 'completion': completion, 'stage': 'exit-attempted'}
        if completion.get('kind') == 'investigation':
            threads.pad(c, finished_ref, 'Main authorized investigation-only restart:\n' +
                        json.dumps(completion, ensure_ascii=True, sort_keys=True))
            latest = _idle(c, record, tree, target['terminal'], previous=proof, **idle_options)
            latest_tree, latest_target = _target(c, slot)
            if (latest_tree != tree or latest_target['terminal'] != target['terminal']
                    or latest['process'] != proof['process']):
                raise ValueError('retirement evidence changed after Pad recording; input withheld')
            receipt['session'] = proof = latest
        if reason is not None:
            receipt['relaunch'] = {
                'reason': reason, 'at': receipt['at'], 'actor': c['coordinator']['terminal'],
                'old_launch_id': record['id'], 'old_session_id': proof['id'],
                'old_model': proof.get('model'), 'old_thinking': proof.get('thinking'),
                'old_model_source': 'session_events',
                'new_model': c['builder']['resolved_model'],
                'new_thinking': c['builder']['thinking'],
                'new_model_source': c['_model_selection']['model_source'],
                'plan_ref': c['_model_selection']['plan_ref'],
                'branch': branch, 'keep_uncommitted': keep_uncommitted,
                'tracked_files': tracked_files}
            # Pad failure refuses before retirement; the user keeps their live session.
            threads.pad(c, ref, 'Pad-Ref: ' + ref + '\nMain authorized same-task relaunch:\n' +
                        json.dumps(receipt['relaunch'], ensure_ascii=True, sort_keys=True))
            latest = _idle(c, record, tree, target['terminal'], previous=proof, **idle_options)
            latest_tree, latest_target = _target(c, slot)
            if (latest_tree != tree or latest_target['terminal'] != target['terminal'] or
                    latest['process'] != proof['process']):
                raise ValueError('retirement evidence changed after Pad recording; input withheld')
            receipt['session'] = proof = latest
        handle = target['terminal']
        stale_shell = proof.get('stale_shell')
        def journal(stage):
            receipt['stage'] = stage
            retirements[slot] = receipt
            state.write(c, 'retirements', retirements)
        try:
            if stale_shell:
                _terminate_omp(c, record, tree, handle, proof, lambda: journal('sigterm-attempted'))
                receipt['stage'] = 'sigterm-sent'
            else:
                journal('exit-attempted')
                receipt['exit_receipt'] = orca(c, 'terminal', 'send', '--terminal', handle, '--text', '/exit', '--enter')
                receipt['stage'] = 'exit-sent'
            state.write(c, 'retirements', retirements)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if stale_shell:
                    if _omp_exited(proof['process']):
                        break
                    time.sleep(0.25)
                    continue
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
                        receipt['retired'] = True
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
                raise ValueError('exit timed out without verified retirement; inspect retirement receipt, no retry')
            receipt['retired'] = True
            journal('omp-retired' if stale_shell else 'shell-verified')
            if stale_shell:
                receipt['pane_close'] = _close_stale_pane(c, tree, record, proof, journal, deadline)
                journal('pane-closed')
            if reason is not None:
                name = threads._name(ref)
                with state.lock(c, name):
                    ledger = state.read(c, name, {})
                    attempt = ledger.get(ref, {}).get('attempts', {}).get(record['id'])
                    if attempt is not None:
                        attempt.update(settled=True, settled_at=time.time(),
                                       result='Main authorized same-task relaunch: ' + reason,
                                       relaunch=receipt['relaunch'])
                        state.write(c, name, ledger)
            journal('launch-attempted')
            launched = launcher.launch(launch_config, ref, slot, model=model, thinking=thinking,
                                       _locked=True, **(
                {'_keep_uncommitted': True} if keep_uncommitted else {}))
            receipt['next_launch_id'] = launched['id']
            receipt['stage'] = 'launched'
            state.write(c, 'retirements', retirements)
        except Exception as exc:
            if retirements.get(slot) is not receipt:
                raise  # The PID/proof check failed before any signal intent was recorded.
            receipt['failure'] = type(exc).__name__
            receipt['failure_reason'] = snapshot._collection_reason(exc)
            for key in ('command', 'stderr'):
                detail = getattr(exc, key, None)
                if detail:
                    receipt[key] = detail
            receipt['retry_guidance'] = (
                'old session retired; inspect retirement, launch-preparations and launch receipts; '
                'after confirming no replacement session started and admission recovers, retry fleet launch '
                'for next_ref and this slot, not fleet restart'
                if receipt['stage'] in ('shell-verified', 'launch-attempted', 'launched') else
                'launch withheld; manually verify and clear the shell line before fleet launch; '
                'never retry cleanup or restart'
                if receipt['stage'] == 'shell-verified-with-residue' else
                'SIGTERM happened or is unconfirmed; inspect exact process and retirement receipt; '
                'never signal again or retry fleet restart'
                if receipt['stage'] in ('sigterm-attempted', 'sigterm-sent') else
                'retirement unconfirmed; inspect terminal and retirement receipt; never resend /exit or retry fleet restart')
            if stale_shell and receipt.get('retired'):
                receipt['retry_guidance'] = (
                    'Do not repeat restart or the close. Verify the old pane is absent and no replacement started '
                    'before attempting a new launch; see Recovery A in docs/MAIN.md#post-restart-clipped-screen')
                if receipt['stage'] in ('omp-retired', 'pane-close-attempted'):
                    state.write(c, 'retirements', retirements)
                    raise ValueError(f"OMP retired; {receipt['failure_reason']}; {receipt['retry_guidance']}") from exc
            if receipt.get('retired'):
                recovery_argv = [
                    'fleet', '--config',
                    str(c.get('_path') or Path(c['root']) / 'fleet.yml'),
                    'launch', ref, '--slot', slot]
                if model is not None:
                    recovery_argv.extend(['--model', model, '--thinking', thinking])
                recovery = shlex.join(recovery_argv)
                if keep_uncommitted:
                    receipt['retry_guidance'] += (
                        '; preserved tracked work still requires a coordinated relaunch recovery; '
                        'normal launch refuses it, never discard or stash it')
                receipt['recovery_command'] = recovery
                published = state.read(c, 'last-launch', {})
                replacement = (published.get('id') != record['id'] and published.get('ref') == ref and
                               published.get('worktree', {}).get('path') == slot)
                if receipt['stage'] == 'launched' or replacement:
                    receipt['launch_outcome'] = 'replacement-receipt-present'
                    receipt['error'] = (
                        f"restart retired {record['id']} but launch {ref} completion failed: "
                        f"{receipt['failure_reason']}; replacement may already be running; "
                        f"do not retry launch without reconciliation; recover with: {recovery}")
                else:
                    receipt['error'] = (
                        f"restart retired {record['id']} but did not launch {ref}: "
                        f"{receipt['failure_reason']}; recover with: {recovery}")
                    if receipt['stage'] == 'launch-attempted':
                        receipt['launch_outcome'] = 'unconfirmed'
                        receipt['error'] += (
                            '; replacement may already be running; do not retry launch without reconciliation')
                for key in ('command', 'stderr'):
                    if key in receipt:
                        receipt['error'] += f"; {key}: {receipt[key]}"
                state.write(c, 'retirements', retirements)
                message = f"{receipt['error']}; {receipt['retry_guidance']}"
                if isinstance(exc, CommandError):
                    raise CommandError(message, code=exc.code) from exc
                raise ValueError(message) from exc
            state.write(c, 'retirements', retirements)
            if isinstance(exc, ValueError):
                raise ValueError(f"{exc}; {receipt['retry_guidance']}") from exc
            if isinstance(exc, CommandError):
                raise CommandError(f"{exc}; {receipt['retry_guidance']}", code=exc.code) from exc
            exc.add_note(receipt['retry_guidance'])
            raise
        return {'retirement': receipt, 'launch': launched}

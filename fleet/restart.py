"""Retire one proven finished idle session, never interrupt or retry uncertain input."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from . import health, launcher, snapshot, state
from .commands import gh, json_run, orca

# OMP appends these after an otherwise completed conversation turn.
POST_TURN_METADATA = frozenset({'credential_pin', 'title_change'})
PROC = Path('/proc')


def _foreground(c, record, slot, handle):
    """Bind the one private launch directory to a live foreground Linux OMP."""
    if not sys.platform.startswith('linux') or not PROC.is_dir():
        raise ValueError('restart requires local Linux /proc foreground ownership proof; unavailable here')
    if not all(record.get(key) for key in ('overlay', 'session_dir', 'prompt')):
        raise ValueError('launch lacks foreground process binding')
    expected = ['--model', c['builder']['model'], '--thinking', c['builder']['thinking'],
                '--config', record['overlay'], '--session-dir', record['session_dir'],
                '@' + record['prompt']]
    owners = []
    for proc in PROC.glob('[0-9]*'):
        try:
            argv = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
            if not argv or Path(argv[0]).name != 'omp' or argv[1:] != expected:
                continue
            before = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            # Only the launched process, not a stopped old process or a shell's
            # background group, can own the terminal input we are about to send.
            if before[0] not in ('R', 'S') or before[2] != before[5] or int(before[4]) == 0 or int(before[5]) <= 0:
                continue
            if (proc / 'exe').resolve().name != 'omp' or str((proc / 'cwd').resolve()) != slot:
                continue
            # Inspect only equality of this routing key; never log environments.
            if ('ORCA_TERMINAL_HANDLE=' + handle).encode() not in (proc / 'environ').read_bytes().split(b'\0'):
                continue
            after = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if after[0] not in ('R', 'S') or before[1:6] != after[1:6] or before[19] != after[19]:
                continue
            owners.append({'pid': int(proc.name), 'start': before[19], 'tty': before[4], 'pgrp': before[2]})
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    if len(owners) != 1:
        raise ValueError('recorded session has no unique matching foreground OMP process; retirement withheld')
    return owners[0]


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is not None:
            return parsed
    except (AttributeError, TypeError, ValueError):
        pass
    raise ValueError('publication/handoff timestamp is missing, malformed, or lacks timezone')


def _completion(c, ref, finished):
    item = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', finished, '--format', 'json'], c['root'])
    prs = gh(c, 'pr', 'list', '--repo', c['repo'], '--state', 'merged', '--search', finished,
             '--limit', '100', '--json', 'number,title,body,mergedAt,url')
    if len(prs) >= 100:
        raise ValueError('completion PR search truncated; retirement withheld')
    pattern = re.compile(r'(?<![\w-])' + re.escape(finished) + r'(?![\w-])')
    prs = [p for p in prs if p.get('mergedAt') and pattern.search(p.get('title', '') + '\n' + (p.get('body') or ''))]
    if not prs:
        raise ValueError('finished task has no merged PR carrying its Pad ref')
    fields = item.get('fields', {})
    if isinstance(fields, str):
        fields = json.loads(fields)
    if fields.get('status') == 'done':
        return {'finished_ref': finished, 'pr': prs[0]['url'], 'pad_status': 'done'}
    comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'comments', ref, '--format', 'json'], c['root'])
    for comment in comments:
        body = comment.get('body', '')
        if pattern.search(body) and re.search(r'\bhandoff\b', body, re.I):
            for pr in prs:
                if _timestamp(comment.get('created_at')) > _timestamp(pr['mergedAt']):
                    return {'finished_ref': finished, 'pr': pr['url'], 'handoff': comment['id']}
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
    # A second shell would make the subsequent normal launch ambiguous.
    if len([b for b in inventory['builders'] if b['path'] == slot]) != 1:
        raise ValueError('slot has multiple terminals; retirement withheld')
    return trees[0], target


def _session(record, slot):
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
    messages = [i for i, e in enumerate(events) if e.get('type') == 'message']
    if not messages:
        raise ValueError('session has no completed assistant turn')
    last = messages[-1]
    message = events[last]['message']
    if (message.get('role') != 'assistant' or message.get('stopReason') != 'stop' or
            any(part.get('type') == 'toolCall' for part in message.get('content', []))):
        raise ValueError('session has no final assistant stop; turn running or unfinished')
    if any(e.get('type') not in POST_TURN_METADATA for e in events[last + 1:]):
        raise ValueError('session has later work or unknown events after final assistant stop')
    return {'path': str(logs[0]), 'id': headers[0]['id'], 'digest': hashlib.sha256(data).hexdigest()}


def _idle(c, record, tree, handle):
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if (not terminal.get('connected') or terminal.get('orphaned') or not terminal.get('writable') or
            terminal.get('worktreeId') != tree['id'] or terminal.get('agentIdentity') != 'omp' or
            not record.get('terminal_incarnation_id') or
            terminal.get('incarnationId') != record['terminal_incarnation_id']):
        raise ValueError('target identity disconnected, changed, or unknown')
    screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
    text = health.ANSI.sub('', '\n'.join(screen.get('tail', [])))
    # Require OMP's actual idle composer footer, not merely silence or an old title.
    lines = text.splitlines()
    footers = [i for i, line in enumerate(lines) if re.match(r' π >\s*◑', line)]
    if not footers:
        raise ValueError('no positive idle OMP composer; working, dialog, background job, or stale screen')
    # Empty composer is exactly one column-zero border after the footer. Draft
    # continuation rows are indented, even when their text happens to be ╰─.
    if footers[-1] != len(lines) - 2 or not re.fullmatch(r'╰─\s*', lines[-1]):
        raise ValueError('OMP composer contains a draft or is ambiguous; leave input untouched')
    live = '\n'.join(lines[max(0, footers[-1] - 3):])
    if re.search(r'waiting for|background job|permission|approve|deny|ask \d* question', live, re.I):
        raise ValueError('dialog or background job visible; retirement withheld')
    ps = orca(c, 'worktree', 'ps')
    if ps.get('truncated') or ps.get('hostScope', {}).get('omittedHostIds'):
        raise ValueError('agent status inventory incomplete')
    trees = [w for w in ps['worktrees'] if w['worktreeId'] == tree['id']]
    key = terminal.get('tabId', '') + ':' + terminal.get('leafId', '')
    agents = [a for w in trees for a in w.get('agents', []) if a.get('paneKey') == key and not a.get('parentPaneKey')]
    if (len(agents) != 1 or agents[0].get('agentType') != 'omp' or
            agents[0].get('state') not in ('done', 'idle') or agents[0].get('interrupted') or
            agents[0].get('programStatus', {}).get('state') not in ('done', 'idle')):
        raise ValueError('exact-pane agent is running, interrupted, or unknown')
    proof = _session(record, tree['path'])
    lease = json_run(['omp', 'session', 'lease', '--json', '--', proof['id']], c['root'])
    if lease.get('held') != [proof['id']] or lease.get('unknown') != []:
        raise ValueError('recorded session is no longer positively owned by a live omp process')
    proof['process'] = _foreground(c, record, tree['path'], handle)
    return proof


def restart(c, ref, slot, finished_ref):
    if not all(re.fullmatch(r'[A-Z]+-\d+', x) for x in (ref, finished_ref)) or ref == finished_ref:
        raise ValueError('restart requires different uppercase next and finished Pad refs')
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
        completion = _completion(c, ref, finished_ref)
        # Validate next task and gates before retiring, then again inside normal launch.
        json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'show', ref, '--format', 'json'], c['root'])
        s = snapshot.collect(c)
        s['inventory']['builders'] = [b for b in s['inventory']['builders'] if b['terminal'] != target['terminal']]
        admission = snapshot.may_start(c, s)
        if not admission['may_start']:
            raise ValueError('restart withheld: ' + '; '.join(admission['reasons']))
        fresh_tree, fresh_target = _target(c, slot)
        if fresh_tree != tree or fresh_target['terminal'] != target['terminal'] or _idle(c, record, tree, target['terminal']) != proof:
            raise ValueError('retirement evidence changed; input withheld')
        receipt = {'at': time.time(), 'launch_id': record['id'], 'next_ref': ref,
                   'finished_ref': finished_ref, 'terminal': target['terminal'],
                   'terminal_incarnation_id': record['terminal_incarnation_id'],
                   'session': proof, 'completion': completion, 'stage': 'exit-attempted'}
        retirements[slot] = receipt
        state.write(c, 'retirements', retirements)
        # Journal BEFORE input: transport failure or crash can never authorize a second /exit.
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
            if health.shell_ready('\n'.join(screen.get('tail', []))):
                break
            time.sleep(0.25)
        else:
            raise ValueError('exit timed out without verified shell; inspect retirement receipt, no retry')
        receipt['stage'] = 'shell-verified'
        state.write(c, 'retirements', retirements)
        receipt['stage'] = 'launch-attempted'
        state.write(c, 'retirements', retirements)
        launched = launcher.launch(c, ref, slot, _locked=True)
        receipt['stage'] = 'launched'
        receipt['next_launch_id'] = launched['id']
        state.write(c, 'retirements', retirements)
        return {'retirement': receipt, 'launch': launched}

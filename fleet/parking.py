"""Durable pushed-WIP completion; never resend an uncertain native report."""
import json
import os
import re
import time
from pathlib import Path
from . import state, threads
from .commands import json_run, run
from .mailbox import request


def completion(c, ref, launch):
    """Return only a fully recorded park for the current canonical attempt."""
    receipt = state.read(c, 'parks', {}).get(launch.get('id'), {})
    if not receipt:
        return None
    slot = receipt.get('slot')
    authoritative = state.read(c, 'launches', {}).get(slot, {})
    attempt = state.read(c, threads._name(ref), {}).get(ref, {}).get('attempts', {}).get(launch.get('id'), {})
    from .restart import _launch_time
    launched = _launch_time(launch)
    try:
        parked_at = _launch_time({'at': receipt.get('at')})
    except ValueError:
        return None
    if (not launch.get('id') or authoritative.get('id') != launch['id']
            or authoritative.get('ref') != ref or launch.get('ref') != ref
            or slot not in c['builder']['slots']
            or launch.get('worktree', {}).get('path') != slot
            or receipt.get('launch_id') != launch['id'] or receipt.get('ref') != ref
            or not launch.get('terminal_incarnation_id')
            or receipt.get('terminal_incarnation_id') != launch['terminal_incarnation_id']
            or receipt.get('terminal') != launch.get('terminal')
            or authoritative.get('terminal') != launch.get('terminal')
            or authoritative.get('terminal_incarnation_id') != launch['terminal_incarnation_id']
            or parked_at <= launched or not receipt.get('resume_note')
            or not receipt.get('message_id') or receipt.get('recorded') is not True
            or not re.fullmatch(r'[0-9a-fA-F]{40,64}', receipt.get('head', ''))
            or not receipt.get('branch') or receipt.get('branch') != attempt.get('branch')
            or attempt.get('attempt_id', launch['id']) != receipt.get('attempt')
            or attempt.get('terminal') != receipt.get('terminal') or attempt.get('slot') != slot
            or attempt.get('settled') is not True
            or attempt.get('settled_at') != receipt.get('at')
            or attempt.get('result') != receipt.get('body')):
        return None
    return {'finished_ref': ref, 'park': receipt}


def _evidence(c, ref, launch, slot, resume_note, attempt):
    from .restart import _launch_time, _timestamp
    launched = _launch_time(launch)
    actual = str(Path(run(['git', 'rev-parse', '--show-toplevel'], slot).strip()).resolve())
    if actual != str(Path(slot).resolve()):
        raise ValueError('park requires the actual launched git worktree')
    if run(['git', 'status', '--porcelain', '--untracked-files=no'], slot).strip():
        raise ValueError('park requires a clean tracked worktree; commit and push WIP first')
    untracked = run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], slot, text=False)
    tolerated = []
    untracked_count = 0
    for relative in untracked.split(b'\0'):
        if not relative:
            continue
        untracked_count += 1
        if len(tolerated) < 50:
            tolerated.append(os.fsdecode(relative))
    branch = run(['git', 'branch', '--show-current'], slot).strip()
    if not branch or branch != attempt.get('branch'):
        raise ValueError('park requires the local current task branch opened by this attempt')
    head = run(['git', 'rev-parse', 'HEAD'], slot).strip()
    remote_ref = 'refs/heads/' + branch
    remote = run(['git', 'ls-remote', '--exit-code', '--heads', 'origin', remote_ref], slot).splitlines()
    if (not re.fullmatch(r'[0-9a-fA-F]{40,64}', head) or len(remote) != 1
            or remote[0].split() != [head, remote_ref]):
        raise ValueError('park requires HEAD pushed to the actual origin branch head')
    comments = json_run(['pad', '--workspace', c['pad_workspace'], 'item', 'comments', ref,
                         '--format', 'json'], c['root'])
    notes = [row for row in comments if row.get('id') == resume_note]
    if (len(notes) != 1 or not isinstance(notes[0].get('body'), str)
            or not re.search(r'\w', notes[0]['body'])
            or _timestamp(notes[0].get('created_at')) <= launched):
        raise ValueError('park requires the exact meaningful resume-note comment created after launch')
    return branch, head, tolerated, untracked_count


def parked(c, ref, resume_note):
    name = threads._name(ref)
    if not isinstance(resume_note, str) or not resume_note.strip():
        raise ValueError('park requires a resume-note comment ID')
    caller = threads.identity(c)
    with state.lock(c, 'launcher'):
        matches = [(slot, launch) for slot, launch in state.read(c, 'launches', {}).items()
                   if slot in c['builder']['slots'] and launch.get('terminal') == caller]
        if len(matches) != 1 or matches[0][1].get('ref') != ref or not matches[0][1].get('id'):
            raise ValueError('park requires a unique authoritative primary launch')
        slot, launch = matches[0]
        threads._ownership(c, launch, slot, caller)
        with state.lock(c, name):
            ledger = state.read(c, name, {})
            task = ledger.get(ref, {})
            attempt = task.get('attempts', {}).get(launch['id'], {})
            if (not task.get('id') or not attempt.get('recorded')
                    or attempt.get('terminal') != caller or attempt.get('slot') != slot
                    or (attempt.get('authorized_by') and not attempt.get('open_message_id'))):
                raise ValueError('park requires the canonical opened attempt')
            if any(not q.get('reply_recorded') for q in attempt.get('questions', {}).values()):
                raise ValueError('pending decision must be resolved and recorded before parking')
            parks = state.read(c, 'parks', {})
            receipt = parks.get(launch['id'])
            attempt_id = attempt.get('attempt_id', launch['id'])
            if receipt:
                if receipt.get('resume_note') != resume_note or receipt.get('attempt') != attempt_id:
                    raise ValueError('park already recorded for a different resume note or attempt')
                if not receipt.get('message_id'):
                    raise ValueError('ambiguous park send; reconcile native inbox before retrying; never resend')
            else:
                if attempt.get('settled') is not False:
                    raise ValueError('park requires an unsettled canonical attempt')
                branch, head, tolerated, untracked_count = _evidence(
                    c, ref, launch, slot, resume_note, attempt)
                body = (f'Pad-Ref: {ref}\nPARKED\nLaunch: {launch["id"]}\nAttempt: {attempt_id}\n'
                        f'Branch: {branch}\nHEAD: {head}\nResume note: {resume_note}\n'
                        'Committed WIP is pushed; fresh session required for resume.')
                if untracked_count:
                    body += (f'\nUntracked files preserved ({untracked_count} total; '
                             f'listing {len(tolerated)}):\n' +
                             '\n'.join(json.dumps(path, ensure_ascii=True) for path in tolerated))
                receipt = {'launch_id': launch['id'], 'ref': ref, 'slot': slot, 'terminal': caller,
                           'terminal_incarnation_id': launch['terminal_incarnation_id'],
                           'attempt': attempt_id, 'branch': branch, 'head': head,
                           'resume_note': resume_note, 'at': time.time(), 'body': body,
                           'untracked_files': tolerated, 'untracked_count': untracked_count,
                           'status': 'sending'}
                parks[launch['id']] = receipt
                state.write(c, 'parks', parks)
                message = request(c, 'send', '--from', caller, '--to', 'run:' + c['coordinator']['run'],
                                  '--type', 'status', '--thread-id', attempt.get('thread_id', task['id']),
                                  '--subject', ref + ': PARKED', '--body', body)['message']
                receipt.update(message_id=message['id'], status='sent')
                state.write(c, 'parks', parks)
            if not receipt.get('recorded'):
                threads.pad(c, ref, f'Fleet task thread: {attempt.get("thread_id", task["id"])}\n'
                            f'Park report: {receipt["message_id"]}\n{receipt["body"]}')
                receipt['recorded'] = True
                state.write(c, 'parks', parks)
            attempt.update(settled=True, settled_at=receipt['at'], result=receipt['body'])
            state.write(c, name, ledger)
            return dict(receipt)

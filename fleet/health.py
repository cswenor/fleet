"""Read-only Orca inventory and conservative ownership mapping."""
from datetime import datetime
import json
from pathlib import Path
import re
import time
from .commands import orca
from . import state

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
REF = re.compile(r'\b([A-Z]+-\d+)\b', re.I)


def branch(value):
    return (value or '').removeprefix('refs/heads/')


def footer_model(text):
    text = ANSI.sub('', text)
    matches = re.findall(r'[◑◒]\s*([^>\n]+)>', text)
    return matches[-1].strip() if matches else None


def model_matches(actual, expected):
    if actual == expected:
        return True
    # OMP human footer labels: GPT-6.1 Sol vs gpt-6.1-sol.
    normalize = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    return normalize(actual) == normalize(expected.split('/')[-1])


def shell_ready(text):
    """Conservative POSIX prompt receipt; unknown/custom prompts stay occupied."""
    text = ANSI.sub('', re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', text))
    lines = [line.strip() for line in text.replace('\r', '\n').splitlines() if line.strip()]
    return bool(lines and re.fullmatch(r'(?:[\w.-]+@[\w.-]+:[^#$%\n]*|(?:ba|z|da|k)?sh-\d[\d.]*)[#$%]', lines[-1]))


def shell_reply_residue(text):
    """Recognise only a bounded reply body after a supported shell prompt.

    This is not a ready-shell receipt: callers must prove process exit and
    foreground ownership before considering any line-kill input.
    """
    lines = [line for line in text.replace('\r', '\n').splitlines() if line.strip()]
    if not lines or len(lines[-1]) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in lines[-1]):
        return None
    match = re.fullmatch(
        r'((?:[\w.-]+@[\w.-]+:[^#$%\n]*|(?:ba|z|da|k)?sh-\d[\d.]*)[#$%]) '
        r'([0-9]{1,6};[0-9]{1,6}[tR]|[4689];[0-9]{1,6};[0-9]{1,6}t)',
        lines[-1])
    return match[2] if match else None


def active(builder):
    # Only a current connected shell receipt proves the old session is gone.
    return not (builder['connected'] and builder.get('shell_ready', False))


def omp_ui(text):
    """Recognise the current OMP composer/widget, never transcript prose.

    Unknown and nonempty composers remain distinct from positive idle evidence.
    Dialog boxes replace the composer and end at the current UI boundary.
    """
    lines = ANSI.sub('', text).splitlines()
    footers = [i for i, line in enumerate(lines)
               if re.match(r' (?:π|[⠁-⣿].*) >\s*◑', line)]
    footer = footers[-1] if footers else None
    current_footer = (footer is not None and footer + 1 < len(lines) and
                      lines[footer + 1].startswith('╰─') and
                      all(row.startswith('   ') for row in lines[footer + 2:]))
    background = False
    if current_footer:
        status = lines[footer]
        background = bool(re.search(r'(?:^|\s)⚙(?:\s|$)', status))
        if status.startswith(' π >') and not background:
            return ('idle' if footer == len(lines) - 2 and
                    re.fullmatch(r'╰─\s*', lines[-1]) else 'draft')
    # A widget is either the whole bottom UI (Ask), or immediately above the
    # working footer/border (permission). A job never hides a pending approval.
    end = footer if current_footer else len(lines)
    widget = lines[:end]
    if widget and re.fullmatch(r'╰─+╯\s*', widget[-1]):
        for i in range(len(widget) - 2, -1, -1):
            if re.fullmatch(r'╭─ (?:Ask|Allow tool: .+?) ─+╮\s*', widget[i]):
                body = widget[i + 1:-1]
                if body and all(re.fullmatch(r'(?:│.*│|├─+┤)\s*', row) for row in body):
                    return 'dialog'
                break
    if background:
        return 'background'
    return 'working' if current_footer else 'unknown'


def classify(terminal, text, expected, idle_seconds, now):
    text = ANSI.sub('', text)
    live = '\n'.join(text.splitlines()[-12:])
    projection = terminal.get('projection', {}) or {}
    provider = projection.get('provider', {}) or {}
    model = provider.get('model') or footer_model(text)
    flags = []
    if model and not model_matches(model, expected):
        flags.append('wrong_model')
    if not model:
        flags.append('model_unknown')
    if re.search(r'No API key|Retry failed after|rate.limit|quota exceeded|Spend a saved Claude|provider error', live, re.I):
        flags.append('provider_stalled')
    ui = omp_ui(text)
    if ui == 'dialog':
        flags.append('waiting_dialog')
    if not terminal.get('connected', False) or terminal.get('orphaned'):
        flags.append('disconnected')
    last = terminal.get('lastOutputAt')
    idle = max(0, int(now - last / 1000)) if isinstance(last, (int, float)) else None
    if idle is not None and idle > idle_seconds and ui != 'background':
        flags.append('idle')
    return model, flags, idle


def binding(c, handle):
    """Only an explicit native null Run proves a terminal is unbound."""
    unknown = {'state': 'unknown', 'run_id': None}
    try:
        result = orca(c, 'orchestration', 'run-current', '--from', handle)
    except RuntimeError:
        return unknown
    if not isinstance(result, dict) or 'run' not in result:
        return unknown
    current = result['run']
    if current is None:
        return {'state': 'none', 'run_id': None}
    if isinstance(current, dict) and isinstance(current.get('id'), str) and current['id'].strip():
        return {'state': 'bound', 'run_id': current['id']}
    return unknown


def _event_time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('event timestamp has no timezone')
    return stamp.timestamp()


def _progress(c, terminal, slot, now):
    """Require the authoritative launch and one complete, slot-bound session."""
    try:
        record = state.read(c, 'launches', {}).get(slot, {})
        if (not record.get('id') or record.get('terminal') != terminal['handle']
                or not terminal.get('incarnationId')
                or record.get('terminal_incarnation_id') != terminal['incarnationId']
                or record.get('worktree', {}).get('path') != slot
                or not record.get('session_dir')):
            raise ValueError('launch binding unavailable')
        logs = list(Path(record['session_dir']).glob('*.jsonl'))
        if len(logs) != 1:
            raise ValueError('session log absent or ambiguous')
        newest = None
        headers = 0
        with logs[0].open() as stream:
            for line in stream:
                event = json.loads(line)
                # OMP writes a timestamp-free title preamble before the header.
                if event.get('type') == 'title' and 'timestamp' not in event:
                    continue
                if event.get('type') == 'session':
                    headers += 1
                    if event.get('cwd') != slot:
                        raise ValueError('session slot mismatch')
                stamp = _event_time(event['timestamp'])
                newest = stamp if newest is None else max(newest, stamp)
        if headers != 1 or newest is None:
            raise ValueError('session header unavailable')
        # Collection's snapshot clock precedes remote inventory reads. Events
        # appended during those reads are healthy, not future-dated corruption.
        if newest > max(now, time.time()):
            raise ValueError('future event timestamp')
        return {'last_event_at': newest, 'event_age_seconds': max(0, int(now - newest)),
                'launch_id': record['id'], 'task': record.get('ref')}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def add_pr_stalls(c, inventory, github, now):
    """Join only uniquely owned open PRs to the session's last durable progress."""
    for pr in github.get('prs', []):
        if pr['state'] != 'open':
            continue
        handle = owner(inventory, pr['branch'])
        if not handle:
            continue
        builder = next(b for b in inventory['builders'] if b['terminal'] == handle)
        failed_checks = [check for check in pr['checks'] if check.get('conclusion') in
                         ('failure', 'error', 'timed_out', 'cancelled', 'action_required',
                          'startup_failure', 'stale')]
        builder.setdefault('owned_prs', []).append({
            'number': pr['number'], 'head': pr.get('head'),
            'failed_checks': sorted({check['name'] for check in failed_checks}),
            'failure_events': [{key: check.get(key) for key in ('id', 'name', 'completed_at')}
                               for check in failed_checks]})
        progress = builder.get('progress')
        if not progress or builder.get('ui_state') not in ('working', 'idle', 'draft'):
            continue
        failures = []
        for check in failed_checks:
            try:
                finished = _event_time(check['completed_at'])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if (progress['last_event_at'] <= finished <= now
                    and now - finished >= c['builder'].get('stall_after_ci_minutes', 20) * 60):
                failures.append(check['name'])
        if failures and 'stalled' not in builder['flags']:
            builder['flags'].append('stalled')


def collect(c, now=None):
    now = time.time() if now is None else now
    trees = orca(c, 'worktree', 'list', '--repo', 'id:' + c['orca_repo_id'])
    terminals = orca(c, 'terminal', 'list')
    if trees.get('truncated') or terminals.get('truncated'):
        raise ValueError('Orca inventory truncated; refusing partial ownership map')
    trees = [w for w in trees['worktrees'] if w.get('repoId') == c['orca_repo_id'] and not w.get('isArchived')]
    by_id = {w['id']: w for w in trees}
    builders = []
    errors = []
    for t in terminals['terminals']:
        if t.get('worktreeId') not in by_id or t['handle'] == c['coordinator']['terminal']:
            continue
        w = by_id[t['worktreeId']]
        if w['path'] not in c['builder']['slots']:
            continue
        text = ''
        try:
            detail = orca(c, 'terminal', 'read', '--terminal', t['handle'], '--screen')['terminal']
            text = '\n'.join(detail.get('tail', []))
        except RuntimeError:
            errors.append('terminal read unavailable: ' + t['handle'])
        ready = (t.get('connected', False) and not t.get('orphaned') and
                 t.get('writable', False) and shell_ready(text))
        model, flags, idle = classify(t, text, c['builder']['resolved_model'], c['builder']['idle_seconds'], now)
        ui = omp_ui(text)
        progress = None if ready else _progress(c, t, w['path'], now)
        if not ready:
            if progress is None:
                flags.append('progress_unknown')
            elif (ui == 'working' and
                  now - progress['last_event_at'] >= c['builder'].get('stall_minutes', 60) * 60):
                flags.append('stalled')
        if ready:
            model, flags, idle = None, [], None
        terminal_binding = binding(c, t['handle'])
        if terminal_binding['state'] == 'bound':
            flags.append('owner_delivery_broken')
        elif terminal_binding['state'] == 'unknown':
            flags.append('binding_unknown')
        state = 'background' if omp_ui(text) == 'background' else 'active'
        ref = REF.search(branch(w.get('branch'))) or REF.search(t.get('title', ''))
        builders.append({'terminal': t['handle'], 'worktree': w['id'], 'path': w['path'],
                         'branch': branch(w.get('branch')), 'task': ref[1].upper() if ref else None,
                         'title': t.get('title'), 'model': model, 'flags': flags,
                         'idle_seconds': idle, 'state': flags[0] if flags else ('shell' if ready else state),
                         'shell_ready': ready, 'binding': terminal_binding,
                         'incarnation_id': t.get('incarnationId'),
                         'ui_state': ui, 'progress': progress,
                         'connected': t.get('connected', False) and not t.get('orphaned')})
    slots = set(c['builder']['slots'])
    stray = [w['path'] for w in trees if w['path'] not in slots and w['path'] != c['root'] and not w.get('isMainWorktree')]
    return {'builders': builders, 'worktrees': [{'id': w['id'], 'path': w['path'], 'branch': branch(w.get('branch')), 'comment': w.get('comment')} for w in trees],
            'stray_worktrees': stray, 'errors': errors}


def owner(inventory, head):
    trees = [w for w in inventory['worktrees'] if w['branch'] == branch(head)]
    if len(trees) != 1:
        return None
    candidates = [b for b in inventory['builders'] if b['worktree'] == trees[0]['id'] and b['connected'] and active(b)]
    return candidates[0]['terminal'] if len(candidates) == 1 else None

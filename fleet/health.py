"""Read-only Orca inventory and conservative ownership mapping."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
import yaml
from .commands import orca
from . import state

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
REF = re.compile(r'\b([A-Z]+-\d+)\b', re.I)


def branch(value):
    return (value or '').removeprefix('refs/heads/')




def shell_ready(text):
    """Recognise a supported prompt, using screen soft-wrap receipts when present."""
    rows = None
    if isinstance(text, dict):
        rows = text.get('tailRows')
        text = '\n'.join(text.get('tail', []))
    text = ANSI.sub('', re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', text))
    lines = text.replace('\r', '\n').split('\n')
    if rows is not None:
        if (not isinstance(rows, list) or len(rows) != len(lines) or
                any(not isinstance(row, dict) or type(row.get('wrapped')) is not bool for row in rows)):
            return False
        end = len(lines) - 1
        while end >= 0 and not lines[end].strip():
            end -= 1
        if end < 0:
            return False
        start = end
        # xterm isWrapped describes this row continuing the previous row.
        while start > 0 and rows[start]['wrapped']:
            start -= 1
        if rows[start]['wrapped']:
            return False  # The logical line begins outside this screen receipt.
        prompt = ''.join(lines[start:end + 1]).strip()
        return bool(re.fullmatch(r'(?:[\w.-]+@[\w.-]+:[^#$%\n]*|(?:ba|z|da|k)?sh-\d[\d.]*)[#$%]', prompt))
    while lines and not lines[-1].strip():
        lines.pop()
    prompt = ''
    # Screen reads do not always expose terminal width. Bound reconstruction to
    # four contiguous trailing rows; never bridge a blank row or relax the regex.
    for line in reversed(lines[-4:]):
        if not line.strip():
            break
        prompt = line + prompt
        if re.fullmatch(r'(?:[\w.-]+@[\w.-]+:[^#$%\n]*|(?:ba|z|da|k)?sh-\d[\d.]*)[#$%]', prompt.strip()):
            return True
    return False


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
               if re.match(r' (?:π|[⠁-⣿].*) >\s*[⦸○◐-◕◉⟳]', line)]
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

def main_wake_proof(c):
    """Positive own-Run, exact-pane OMP idle proof; never infer idle screenlessly."""
    return _wake_proof(c, c['coordinator']['terminal'], c['coordinator']['run'])


def builder_wake_proof(c, launch, slot):
    """Require the authoritative lane incarnation and an unbound, idle OMP pane."""
    return _wake_proof(c, launch['terminal'], launch=launch, slot=slot)


def _wake_proof(c, handle, run_id=None, *, launch=None, slot=None):
    terminal = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    keys = ('handle', 'incarnationId', 'worktreeId', 'tabId', 'leafId')
    if (terminal.get('handle') != handle or not all(terminal.get(k) for k in keys)
            or terminal.get('connected') is not True or terminal.get('writable') is not True
            or terminal.get('orphaned') or terminal.get('agentIdentity') != 'omp'):
        raise ValueError('Main terminal identity unproven')
    if launch is not None:
        worktree = launch.get('worktree', {})
        if (not launch.get('terminal_incarnation_id')
                or terminal['incarnationId'] != launch['terminal_incarnation_id']
                or not worktree.get('id') or worktree.get('path') != slot
                or terminal['worktreeId'] != worktree['id']):
            raise ValueError('Builder launch identity unproven')
    binding = orca(c, 'orchestration', 'run-current', '--from', handle)
    if launch is not None:
        if 'run' not in binding or binding['run'] is not None:
            raise ValueError('Builder terminal is Run-bound')
    elif (binding.get('run') or {}).get('id') != run_id:
        raise ValueError('Main Run binding unproven')
    processes = orca(c, 'worktree', 'ps')
    if processes.get('truncated') or processes.get('hostScope', {}).get('omittedHostIds'):
        raise ValueError('Main pane inventory incomplete')
    pane = terminal['tabId'] + ':' + terminal['leafId']
    agents = [a for w in processes['worktrees'] if w.get('worktreeId') == terminal['worktreeId']
              for a in w.get('agents', []) if a.get('paneKey') == pane and not a.get('parentPaneKey')]
    if (len(agents) != 1 or agents[0].get('agentType') != 'omp'
            or agents[0].get('state') not in ('idle', 'done') or agents[0].get('interrupted')
            or (agents[0].get('programStatus') is not None
                and agents[0]['programStatus'].get('state') not in ('idle', 'done'))):
        raise ValueError('Main exact pane is not idle')
    screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
    if screen.get('source') == 'screen-unavailable':
        raise ValueError('Main screen unavailable')
    ui = omp_ui('\n'.join(screen.get('tail', [])))
    if ui != 'idle':
        raise ValueError('Main composer state: ' + ui)
    current = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
    if (any(current.get(k) != terminal[k] for k in keys)
            or current.get('connected') is not True or current.get('writable') is not True
            or current.get('orphaned') or current.get('agentIdentity') != 'omp'):
        raise ValueError('Main terminal identity changed')
    return {'terminal': handle, 'incarnation_id': terminal['incarnationId'],
            'worktree': terminal['worktreeId'], 'pane': pane,
            'ui': ui, 'composer': 'empty', 'source': screen.get('source'),
            'screen_sha256': hashlib.sha256(
                '\n'.join(screen.get('tail', [])).encode()).hexdigest()}


def coordinator(c):
    """Read only the configured Main; missing metadata never proves a live agent."""
    handle = c['coordinator']['terminal']
    result = {'terminal': handle, 'incarnation_id': None, 'state': 'unknown',
              'reason': 'terminal observation unavailable', 'evidence': '', 'recovery_hint': ''}
    try:
        inventory = orca(c, 'terminal', 'list')
        terminals = [t for t in inventory['terminals'] if t.get('handle') == handle]
        if (inventory.get('truncated') or inventory.get('hostScope', {}).get('omittedHostIds')
                or len(terminals) > 1):
            return result
        if not terminals:
            return {**result, 'state': 'down', 'reason': 'terminal missing'}
        terminal = terminals[0]
        result['incarnation_id'] = terminal.get('incarnationId')
        if terminal.get('connected') is False or terminal.get('orphaned') is True:
            return {**result, 'state': 'down', 'reason': 'terminal disconnected or orphaned'}
        if terminal.get('connected') is not True or not result['incarnation_id']:
            return result
        screen = orca(c, 'terminal', 'read', '--terminal', handle, '--screen')['terminal']
        text = '\n'.join(screen.get('tail', []))
        if screen.get('source') == 'screen-unavailable':
            text = ''
        screenless_agent = False
        if not text.strip() and terminal.get('agentIdentity') == 'omp':
            processes = orca(c, 'worktree', 'ps')
            pane = (terminal.get('tabId'), terminal.get('leafId'))
            if (all(pane) and terminal.get('worktreeId') and
                    not processes.get('truncated') and
                    not processes.get('hostScope', {}).get('omittedHostIds')):
                agents = [agent for tree in processes['worktrees']
                          if tree.get('worktreeId') == terminal['worktreeId']
                          for agent in tree.get('agents', [])
                          if agent.get('paneKey') == ':'.join(pane)]
                screenless_agent = len(agents) == 1 and agents[0].get('agentType') == 'omp'
        # A replacement during the read cannot borrow the old incarnation's timer.
        current = orca(c, 'terminal', 'show', '--terminal', handle)['terminal']
        if (current.get('incarnationId') != result['incarnation_id'] or
                current.get('connected') is not True or current.get('orphaned')):
            return result
        # Pane inventory is observational only; never substitute it for a screen
        # in shell readiness, idle proof, launch or retirement.
        if (screenless_agent and current.get('agentIdentity') == 'omp' and
                all(current.get(key) == terminal.get(key)
                    for key in ('tabId', 'leafId', 'worktreeId'))):
            return {**result, 'state': 'up', 'reason': 'screen unavailable; omp agent present'}
        if omp_ui(text) != 'unknown':
            return {**result, 'state': 'up', 'reason': 'live omp UI'}
        lines = [line for line in ANSI.sub('', text).splitlines() if line.strip()]
        shell = shell_ready(screen)
        crash_index = next((i for i in range(len(lines) - 1, -1, -1)
                            if re.match(r'^\s*\[(?:Uncaught Exception|Recovery)\]', lines[i])), None)
        if crash_index is not None:
            suffix = lines[crash_index + 1:]
            # Retained transcript is not current crash proof. Only a supported
            # shell or a bounded, recognizable crash/recovery tail establishes it.
            current_crash = shell or all(re.match(
                r'^\s*(?:at\s+\S|Resume this session with\s*$|omp --resume\s+\S|'
                r'Run omp --resume\b)', line) for line in suffix)
            if current_crash:
                hint = next((line for line in reversed(suffix)
                             if re.search(r'(?i)recovery|resume|restart', line)), '')
                return {**result, 'state': 'suspect', 'reason': 'omp crash',
                        'evidence': _screen_evidence(lines[crash_index]),
                        'recovery_hint': _screen_evidence(hint)}
        if shell:
            return {**result, 'state': 'suspect', 'reason': 'bare shell prompt',
                    'evidence': 'supported bare shell prompt'}
        return {**result, 'reason': 'screen state unrecognized'}
    except (RuntimeError, ValueError, KeyError, TypeError):
        return result


def _screen_evidence(line):
    from .commands import _gh_reason
    # Screens are untrusted and may contain shell commands or private credentials.
    line = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', line)
    line = re.sub(r'(?i)\b(password|secret|api[_-]?key|token)\s*[:=]\s*\S+',
                  r'\1=[redacted]', line)
    line = re.sub(r'\b[A-Za-z0-9_./+=-]{32,}\b', '[redacted]', line)
    return _gh_reason(line)[:200]



def classify(terminal, text, session, idle_seconds, now):
    text = ANSI.sub('', text)
    live = '\n'.join(text.splitlines()[-12:])
    session = session or {}
    model = session.get('model')
    flags = []
    if model and (model != session.get('expected_model') or session.get('fallback')
                  or (session.get('expected_thinking') and
                      session.get('thinking') != session['expected_thinking'])):
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
        model, thinking, fallback = None, None, False
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
                if event.get('type') == 'model_change':
                    model = event.get('model')
                    fallback = event.get('resolvedModelIsFallback', False)
                elif event.get('type') == 'thinking_level_change':
                    thinking = event.get('thinkingLevel')
                stamp = _event_time(event['timestamp'])
                newest = stamp if newest is None else max(newest, stamp)
        if headers != 1 or newest is None:
            raise ValueError('session header unavailable')
        # Collection's snapshot clock precedes remote inventory reads. Events
        # appended during those reads are healthy, not future-dated corruption.
        if newest > max(now, time.time()):
            raise ValueError('future event timestamp')
        expected_model = record.get('resolved_model')
        if not expected_model and record.get('overlay'):
            try:
                overlay = yaml.safe_load(Path(record['overlay']).read_text())
                models = overlay['compaction']['modelThresholds']
                if isinstance(models, dict) and len(models) == 1:
                    expected_model = next(iter(models))
            except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
                pass
        return {'last_event_at': newest, 'event_age_seconds': max(0, int(now - newest)),
                'launch_id': record['id'], 'task': record.get('ref'),
                'model': model, 'thinking': thinking, 'fallback': fallback,
                'expected_model': expected_model, 'expected_thinking': record.get('thinking')}
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
        detail = {}
        try:
            detail = orca(c, 'terminal', 'read', '--terminal', t['handle'], '--screen')['terminal']
            text = '\n'.join(detail.get('tail', []))
        except RuntimeError:
            errors.append('terminal read unavailable: ' + t['handle'])
        ready = (t.get('connected', False) and not t.get('orphaned') and
                 t.get('writable', False) and shell_ready(detail))
        ui = omp_ui(text)
        progress = None if ready else _progress(c, t, w['path'], now)
        model, flags, idle = classify(t, text, progress, c['builder']['idle_seconds'], now)
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
                         'thinking': progress.get('thinking') if progress else None,
                         'fallback': progress.get('fallback') if progress else None,
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

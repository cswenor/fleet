"""Read-only Orca inventory and conservative ownership mapping."""
import re
import time
from .commands import orca

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


def active(builder):
    # Only a current connected shell receipt proves the old session is gone.
    return not (builder['connected'] and builder.get('shell_ready', False))


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
    if re.search(r'Ask \d* question|waiting for (?:approval|input)|approve.*deny|permission required', live, re.I):
        flags.append('waiting_dialog')
    if not terminal.get('connected', False) or terminal.get('orphaned'):
        flags.append('disconnected')
    last = terminal.get('lastOutputAt')
    idle = max(0, int(now - last / 1000)) if isinstance(last, (int, float)) else None
    if idle is not None and idle > idle_seconds:
        flags.append('idle')
    return model, flags, idle


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
        if ready:
            model, flags, idle = None, [], None
        ref = REF.search(branch(w.get('branch'))) or REF.search(t.get('title', ''))
        builders.append({'terminal': t['handle'], 'worktree': w['id'], 'path': w['path'],
                         'branch': branch(w.get('branch')), 'task': ref[1].upper() if ref else None,
                         'title': t.get('title'), 'model': model, 'flags': flags,
                         'idle_seconds': idle, 'state': 'shell' if ready else (flags[0] if flags else 'active'),
                         'shell_ready': ready,
                         'incarnation_id': t.get('incarnationId'),
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

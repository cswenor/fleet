"""Project-local critical-path PR designations, independent of GitHub."""
import os
from . import state


_STATE = 'critical-path'


def listed_prs(c):
    return set(state.read(c, _STATE, []))


def manage(c, action, pr):
    if os.environ.get('ORCA_TERMINAL_HANDLE') != c['coordinator']['terminal']:
        raise ValueError('critical-path requires the configured Main terminal identity')
    if action == 'list':
        return sorted(listed_prs(c))
    if pr < 1:
        raise ValueError('critical-path PR number must be positive')
    with state.lock(c, _STATE):
        prs = listed_prs(c)
        if action == 'add':
            prs.add(pr)
        else:
            prs.discard(pr)
        result = sorted(prs)
        state.write(c, _STATE, result)
    return result

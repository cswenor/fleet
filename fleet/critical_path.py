"""Project-local critical-path PR designations, independent of GitHub."""
import os
import stat
from . import state


_STATE = 'critical-path'


def listed_prs(c):
    path = state.directory(c) / f'{_STATE}.json'
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return set()
    if not stat.S_ISREG(mode):
        raise OSError('critical-path.json must be a regular file')
    try:
        prs = state.read(c, _STATE, [])
    except (ValueError, RecursionError) as error:
        raise ValueError('critical-path.json must be a JSON list of positive PR numbers') from error
    if not isinstance(prs, list) or any(type(pr) is not int or pr < 1 for pr in prs):
        raise ValueError('critical-path.json must be a JSON list of positive PR numbers')
    return set(prs)


def manage(c, action, pr):
    if os.environ.get('ORCA_TERMINAL_HANDLE') != c['coordinator']['terminal']:
        raise ValueError('critical-path requires the configured Main terminal identity')
    if action == 'list':
        return sorted(listed_prs(c))
    if pr < 1:
        raise ValueError('critical-path PR number must be positive')
    with state.lock(c, _STATE):
        try:
            prs = listed_prs(c)
        except ValueError:
            prs = set()
        if action == 'add':
            prs.add(pr)
        else:
            prs.discard(pr)
        result = sorted(prs)
        state.write(c, _STATE, result)
    return result

"""Blocking process for async shell completion; no model-turn polling."""
import time
from . import github


def watch(c, number, timeout=14400, interval=60):
    def view():
        return github.collect(c, number)
    before = view()
    if before['prs'][0]['state'] != 'open':
        return {'event': before['prs'][0]['state'], 'pr': number}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        after = view()
        changes = [e for e in github.events(before, after) if e.get('pr') == number]
        if changes:
            return {'pr': number, 'events': changes}
    return {'pr': number, 'event': 'timeout', 'seconds': timeout}

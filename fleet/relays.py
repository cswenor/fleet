"""Read-only relay cleanup assessment; native selective consumption is unavailable."""
from collections import Counter
from datetime import datetime
import math
import os

from . import state
from .mailbox import _binding, request


_LIMIT = 1000


def assess(c, terminal, launch, messages, *, complete):
    """Count only; never expose message bodies or infer safety from a capped page."""
    counts = Counter()
    blockers = Counter()
    start = launch.get('at')
    valid_start = isinstance(start, (int, float)) and math.isfinite(start)
    for message in messages:
        if message.get('read'):
            continue
        sender = ('supervisor' if message.get('from_handle') == c['coordinator']['terminal']
                  else 'other')
        kind = message.get('type', 'unknown')
        try:
            created = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
            before = created.tzinfo is not None and valid_start and created.timestamp() < start
        except (KeyError, TypeError, ValueError, OverflowError):
            before = False
        relay = (sender == 'supervisor' and kind == 'status'
                 and message.get('run_id') == c['coordinator']['run']
                 and message.get('to_handle') == terminal
                 and not message.get('thread_id')
                 and message.get('body', '').startswith(f"[fleet {c['repo']}] "))
        classification = 'stale_relay' if relay and before else 'blocker'
        counts[classification] += 1
        if classification == 'blocker':
            blockers[(sender, kind)] += 1
    return {'event': 'dry-run', 'terminal': terminal, 'read_only': True,
            'complete': complete, 'unread': sum(counts.values()),
            'stale_relays': counts['stale_relay'],
            'blockers': [{'sender_class': sender, 'type': kind, 'count': count}
                         for (sender, kind), count in sorted(blockers.items())],
            'would_be_all_stale': complete and valid_start and counts['blocker'] == 0,
            'mutation_supported': False,
            'reason': 'Native selective mark-read or conditional consume is required; no mail changed.'}


def clear_stale(c, terminal, *, dry_run=False):
    if not dry_run:
        raise ValueError('clear-stale requires --dry-run; native selective consumption is unavailable')
    caller = os.environ.get('ORCA_TERMINAL_HANDLE')
    if caller != c['coordinator']['terminal']:
        raise ValueError('relay assessment requires the configured Main terminal identity')
    _binding(request(c, 'run-current', '--from', caller).get('run'),
             c['coordinator']['run'], caller)
    launches = state.read(c, 'launches', {})
    matching = [launch for slot, launch in launches.items()
                if slot in c['builder']['slots'] and launch.get('terminal') == terminal]
    if len(matching) != 1:
        raise ValueError('relay assessment requires one current configured lane launch')
    # inbox is read-only even for a bound terminal; never call consuming check.
    result = request(c, 'inbox', '--terminal', terminal, '--limit', str(_LIMIT))
    messages = result.get('messages')
    if not isinstance(messages, list):
        raise ValueError('native inbox rows unavailable')
    return assess(c, terminal, matching[0], messages, complete=len(messages) < _LIMIT)

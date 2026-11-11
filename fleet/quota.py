"""Account-pool capacity by quota window; strip identities before persistence."""
import math
import time
from .commands import json_run, run


def collect(c):
    """Refresh unknown cached usage once; never reuse an older passing result."""
    config = {**c['quota'], 'provider': c['builder']['resolved_model'].split('/')[0]}
    command = ['omp', 'usage', '--json', '--provider', config['provider']]
    data = json_run(command, c['root'])
    result = evaluate(data, config, time.time())
    if not result['known']:
        # This invalidates reports, not saved resets or credentials.
        run(['omp', 'usage', 'invalidate', '--provider', config['provider']], c['root'])
        data = json_run(command, c['root'])
        result = evaluate(data, config, time.time())
    return result


def evaluate(data, config, now):
    provider = config['provider']
    reserve = config['reserve_percent']
    reports = [r for r in data.get('reports', []) if r.get('provider') == provider]
    pools = {}
    unknown = not reports
    for report in reports:
        fresh = 0 <= now - report.get('fetchedAt', 0) / 1000 <= config['max_age_seconds']
        if not fresh or not report.get('limits') or report.get('error'):
            unknown = True
        for limit in report.get('limits', []):
            scope = limit.get('scope', {})
            # Tier-specific meters are not shared builder capacity.
            if scope.get('tier'):
                continue
            key = limit.get('window', {}).get('id') or limit.get('id')
            amount = limit.get('amount', {})
            remaining = amount.get('remainingFraction')
            if remaining is None and amount.get('limit'):
                remaining = amount.get('remaining', 0) / amount['limit']
            if not isinstance(remaining, (int, float)) or not math.isfinite(remaining) or not 0 <= remaining <= 1:
                unknown = True
                continue
            pools.setdefault(key, []).append(remaining * 100)
    windows = [{'window': k, 'accounts': len(v), 'remaining_percent': round(sum(v) / len(v), 2)}
               for k, v in sorted(pools.items())]
    unknown = unknown or not windows
    allowed = not unknown and all(w['remaining_percent'] > reserve for w in windows)
    return {'provider': provider, 'reserve_percent': reserve, 'windows': windows,
            'known': not unknown, 'may_start': allowed,
            'reason': f'{provider} usage unavailable or stale' if unknown else ('capacity available' if allowed else 'quota reserve reached')}

"""Observe runtime identity, health and App metadata without exposing credentials."""
from datetime import datetime
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from .review_bot_api import GitHub, _NoRedirect, app_client, verify_pin
from .review_bot_apps import verify_app, verify_installation
from .review_bot_config import validate
from .review_bot_deploy import SERVICES, docker, project_name


_SHA = re.compile(r'[0-9a-f]{40}')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def health(url):
    """Bounded HTTPS diagnostics; never follow redirects to another endpoint."""
    try:
        request = Request(url, headers={'Accept': 'application/json'})
        try:
            response = build_opener(_NoRedirect()).open(request, timeout=10)
        except HTTPError as exc:
            if exc.code != 503:
                raise ValueError('health endpoint unavailable') from None
            response = exc
        with response:
            code = response.code
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('health response exceeds safe limit')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('health response is invalid')
        return code, value
    except (OSError, ValueError):
        raise ValueError('health endpoint unavailable or returned invalid diagnostics') from None


def _containers(c):
    context = c['review_bot']['deployment']['docker_context']
    project = project_name(c)
    identifiers = docker(context, ['container', 'ls', '--all', '--quiet', '--filter',
                                  'label=com.docker.compose.project=' + project]).split()
    if len(identifiers) > 16 or any(not re.fullmatch(r'[0-9a-f]{12,64}', item) for item in identifiers):
        raise ValueError('unexpected deployment container inventory')
    result = {role: [] for role in SERVICES}
    for identifier in identifiers:
        # A Go-template projection avoids ever retrieving Config.Env or full inspect.
        raw = docker(context, ['container', 'inspect', '--format',
                               '{{json (index .Config.Labels "com.docker.compose.service")}} {{json .Image}} {{json .State.Status}} {{json .Config.Image}} {{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}',
                               identifier])
        decoder = json.JSONDecoder()
        values = []
        text = raw.strip()
        while text:
            value, end = decoder.raw_decode(text)
            values.append(value)
            text = text[end:].lstrip()
        if len(values) != 5:
            raise ValueError('invalid container identity response')
        role, image_id, state, reference, runtime_health = values
        if role not in result or not isinstance(image_id, str) or not _DIGEST.fullmatch(image_id):
            raise ValueError('unexpected deployment container identity')
        observed = json.loads(docker(context, ['image', 'inspect', '--format',
                                             '{"digests":{{json .RepoDigests}},"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}', image_id]))
        if not isinstance(observed, dict):
            raise ValueError('invalid image identity response')
        digests = observed.get('digests')
        revision = observed.get('revision')
        pin = c['review_bot'].get('source', {})
        matches = (isinstance(digests, list) and pin.get('image') in digests
                   and reference == pin.get('image') and revision == pin.get('commit'))
        result[role].append({'container': identifier, 'state': state if state in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead') else 'unknown',
                             'image_id': image_id,
                             'digests': [item for item in digests if isinstance(item, str) and re.fullmatch(r'ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}', item)][:16] if isinstance(digests, list) else [],
                             'revision': revision if isinstance(revision, str) and _SHA.fullmatch(revision) else None,
                             'digest_matches_pin': matches,
                             'running': state == 'running',
                             'health': runtime_health if runtime_health in ('starting', 'healthy', 'unhealthy') else 'unknown'})
    return {role: {'instances': rows, 'healthy': len(rows) == 1 and rows[0]['running']
                  and rows[0]['health'] == 'healthy' and rows[0]['digest_matches_pin']}
            for role, rows in result.items()}


def _deliveries(client):
    rows = client.request('GET', '/app/hook/deliveries?per_page=10')
    if not isinstance(rows, list) or len(rows) > 10:
        raise ValueError('webhook delivery metadata unavailable')
    output = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('webhook delivery metadata invalid')
        # No payload-detail endpoint. Even API-provided free text is not echoed.
        item = {}
        for key in ('id', 'installation_id', 'repository_id', 'status_code'):
            value = row.get(key)
            item[key] = value if type(value) is int and value >= 0 else None
        item['redelivery'] = row.get('redelivery') is True
        event = row.get('event')
        item['event'] = event if isinstance(event, str) and re.fullmatch(r'[a-z_]{1,64}', event) else None
        timestamp = row.get('delivered_at')
        item['delivered_at'] = timestamp if isinstance(timestamp, str) and re.fullmatch(r'[0-9T:.+Z-]{1,40}', timestamp) else None
        output.append(item)
    return output


def status(c, *, github=None):
    validate(c)
    bot = c.get('review_bot', {})
    result = {'operation': 'status', 'healthy': False, 'pin_verified': False,
              'containers': None, 'health': None, 'installations': {}, 'deliveries': None, 'blockers': []}
    result['expected_source'] = bot.get('source')
    result['extra_permissions'] = {
        role: bot.get('apps', {}).get(role, {}).get('extra_permissions', {})
        for role in ('review', 'builder')}
    requested_mode = bot.get('deployment', {}).get('mode', 'advisory')
    result['requested_mode'] = requested_mode
    if not bot:
        result['blockers'].append('review_bot is not configured')
        return result
    try:
        if not bot.get('source'):
            raise ValueError()
        verify_pin(bot['source'], github or GitHub.owner())
        result['pin_verified'] = True
    except (ValueError, KeyError):
        result['blockers'].append('immutable release pin is missing, unverifiable or mismatched')
    try:
        result['containers'] = _containers(c)
        if not all(item['healthy'] for item in result['containers'].values()):
            result['blockers'].append('all four singleton containers must be running the pinned digest and revision')
    except (ValueError, KeyError, TypeError):
        result['blockers'].append('container identity is unavailable on the explicitly configured Docker context')
    try:
        code, observed = health(bot['deployment']['health_url'])
        revision = observed.get('revision')
        mode = observed.get('mode')
        effective_mode = mode if mode in ('off', 'advisory', 'approve') else 'unknown'
        degraded = observed.get('mode_degraded', 'unknown')
        result['health'] = {
            'http_status': code, 'ok': observed.get('ok') is True,
            'revision': revision if isinstance(revision, str) and _SHA.fullmatch(revision) else None,
            'mode': effective_mode,
            'requested_mode': requested_mode,
            'mode_mismatch': requested_mode != effective_mode,
            'mode_degraded': degraded if degraded in (None, 'approve→advisory') else 'unknown',
            'merge_lane_disabled': observed.get('mergeLaneEnabled') is False,
            'runner': observed.get('runner') if observed.get('runner') in ('up', 'down') else 'unknown',
            'gateway': observed.get('gateway') if observed.get('gateway') in ('up', 'down') else 'unknown'}
        if (code != 200 or observed.get('ok') is not True or revision != bot.get('source', {}).get('commit')
                or mode != requested_mode or degraded is not None
                or observed.get('mergeLaneEnabled') is not False
                or observed.get('runner') != 'up' or observed.get('gateway') != 'up'):
            result['blockers'].append('health readiness, exact revision or requested/native mode is unknown, degraded or mismatched')
    except (ValueError, KeyError, TypeError):
        result['blockers'].append('HTTPS health diagnostics unavailable')
    for role in ('review', 'builder'):
        try:
            client = app_client(bot['apps'][role], role)
            verify_app(c, role, client)
            installation = verify_installation(c, role, client)
            result['installations'][role] = {'verified': True, 'installation_id': installation}
            if role == 'review':
                try:
                    result['deliveries'] = _deliveries(client)
                    repository = (github or GitHub.owner()).request('GET', '/repos/' + c['repo'])
                    if (not isinstance(repository, dict) or repository.get('full_name', '').lower() != c['repo'].lower()
                            or type(repository.get('id')) is not int or repository['id'] < 1):
                        raise ValueError('target delivery identity unavailable')
                    relevant = [row for row in result['deliveries']
                                if row['repository_id'] == repository['id'] and row['installation_id'] == installation]
                    if any(type(row['id']) is not int or row['id'] < 1
                           or not row['delivered_at'].endswith(('Z', '+00:00')) for row in relevant):
                        raise ValueError('target delivery timestamp or identity unavailable')
                    latest = max(relevant, key=lambda row: (
                        datetime.fromisoformat(row['delivered_at'].replace('Z', '+00:00')), row['id']))
                    if type(latest['status_code']) is not int or not 200 <= latest['status_code'] < 300:
                        result['blockers'].append('latest verified repository/installation webhook delivery is unsuccessful')
                except (ValueError, KeyError, TypeError, AttributeError):
                    result['blockers'].append('successful verified repository/installation webhook delivery evidence unavailable')
        except (ValueError, KeyError, TypeError):
            result['installations'][role] = {'verified': False}
            result['blockers'].append(role + ' App selected-only-this-repository installation is unverifiable')
    result['healthy'] = not result['blockers'] and result['pin_verified']
    return result

"""Reviewable, additive repository rules; never infer replacement or bypass."""
import base64
import copy
import difflib
import hashlib
import json
from urllib.parse import quote

import yaml

from . import state
from .review_bot_api import APIError, GitHub, app_client, verify_pin
from .review_bot_apps import prerequisites, verify_app, verify_installation
from .review_bot_files import read_config, save_config


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2) + '\n'


def _pages(github, path):
    rows = []
    seen = set()
    for page in range(1, 101):
        values = github.request('GET', path + ('&' if '?' in path else '?') + f'per_page=100&page={page}')
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError('incomplete repository ruleset discovery')
        for value in values:
            if not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] in seen:
                raise ValueError('unstable repository ruleset discovery')
            seen.add(value['id'])
        rows.extend(values)
        if len(values) < 100:
            return rows
    raise ValueError('repository ruleset discovery exceeded its bounded pagination')


def _body(value):
    return {key: copy.deepcopy(value[key]) for key in
            ('name', 'target', 'enforcement', 'bypass_actors', 'conditions', 'rules') if key in value}


def _required_checks(c):
    bot = c.get('review_bot', {})
    app_id = bot.get('apps', {}).get('review', {}).get('id')
    checks = list(bot.get('ruleset', {}).get('required_checks', []))
    if not bot.get('ruleset', {}).get('merge_queue', False):
        checks.append({'context': 'codex-review', 'integration_id': app_id})
    checks.append({'context': 'app-approval', 'integration_id': app_id})
    return checks


def desired_ruleset(c, existing=None):
    bot = c.get('review_bot', {})
    config = bot.get('ruleset', {})
    app_id = bot.get('apps', {}).get('review', {}).get('id')
    before = _body(existing) if existing else None
    if before:
        if 'bypass_actors' not in before:
            raise ValueError('ruleset bypass visibility is unknown; operator authentication needs write access to the ruleset')
        if (existing.get('source_type') != 'Repository'
                or existing.get('source', '').lower() != c['repo'].lower()
                or before.get('target') != 'branch'
                or before.get('conditions') != {'ref_name': {'include': ['refs/heads/main'], 'exclude': []}}):
            raise ValueError('replacement requires a repository-owned ruleset protecting only literal refs/heads/main')
    rules = copy.deepcopy(before.get('rules', [])) if before else []
    indexed = {}
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('type'), str) or rule['type'] in indexed:
            raise ValueError('existing ruleset contains unsupported or duplicate rules')
        indexed[rule['type']] = rule
    pull = indexed.get('pull_request', {'type': 'pull_request', 'parameters': {}})
    pull_parameters = pull.get('parameters', {})
    approval_count = pull_parameters.get('required_approving_review_count', 0)
    fleet_owned = (before is not None
                   and existing.get('id') == config.get('id')
                   and before.get('name') == config.get('name', 'fleet-review-bot')
                   and type(app_id) is int and app_id > 0)
    if approval_count and not (approval_count == 1 and fleet_owned):
        raise ValueError('refusing to reduce an existing approval count outside the selected Fleet-owned ruleset; choose a separate additive ruleset')
    pull_parameters.setdefault('require_code_owner_review', False)
    pull_parameters.setdefault('required_review_thread_resolution', False)
    pull_parameters.update(required_approving_review_count=0, dismiss_stale_reviews_on_push=True)
    if fleet_owned:
        pull_parameters['require_last_push_approval'] = False
    else:
        pull_parameters.setdefault('require_last_push_approval', False)
    pull['parameters'] = pull_parameters
    required = indexed.get('required_status_checks', {'type': 'required_status_checks', 'parameters': {}})
    parameters = required.get('parameters', {})
    checks = parameters.get('required_status_checks', [])
    if not isinstance(checks, list):
        raise ValueError('existing ruleset has invalid required status checks')
    desired = _required_checks(c)
    by_context = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get('context'), str) or check['context'] in by_context:
            raise ValueError('existing ruleset has ambiguous required status check producers')
        by_context[check['context']] = copy.deepcopy(check)
    if config.get('merge_queue', False) and 'codex-review' in by_context:
        if not fleet_owned or by_context['codex-review'].get('integration_id') != app_id:
            raise ValueError('refusing to remove codex-review outside the selected Fleet-owned App-bound ruleset')
        del by_context['codex-review']
    for check in desired:
        previous = by_context.get(check['context'])
        if previous and previous.get('integration_id') not in (None, check['integration_id']):
            raise ValueError('refusing to replace an existing required-check producer; use an additive ruleset')
        by_context[check['context']] = copy.deepcopy(check)
    parameters.update(required_status_checks=list(by_context.values()), strict_required_status_checks_policy=True)
    required['parameters'] = parameters
    replacements = {'pull_request': pull, 'required_status_checks': required}
    if config.get('merge_queue', False):
        queue = copy.deepcopy(indexed.get('merge_queue', {
            'type': 'merge_queue', 'parameters': {
                'check_response_timeout_minutes': 60, 'grouping_strategy': 'ALLGREEN',
                'max_entries_to_build': 1, 'max_entries_to_merge': 1,
                'min_entries_to_merge': 1, 'min_entries_to_merge_wait_minutes': 0}}))
        queue['parameters'].update(
            merge_method=c.get('publication', {}).get('merge_method', 'squash').upper(),
            grouping_strategy='ALLGREEN')
        replacements['merge_queue'] = queue
    elif 'merge_queue' in indexed:
        if not fleet_owned:
            raise ValueError('refusing to remove a merge queue outside the selected Fleet-owned ruleset')
        rules = [rule for rule in rules if rule['type'] != 'merge_queue']
    output = [replacements.pop(rule['type'], rule) for rule in rules]
    output.extend(replacements.values())
    return {'name': config.get('name', 'fleet-review-bot'), 'target': 'branch', 'enforcement': 'active',
            'bypass_actors': [], 'conditions': {'ref_name': {'include': ['refs/heads/main'], 'exclude': []}},
            'rules': output}


def verify_capability(c, github):
    try:
        return _pages(github, f'/repos/{c["repo"]}/rulesets?includes_parents=true')
    except APIError as exc:
        if exc.status == 403:
            raise ValueError('GitHub403: repository rulesets are unavailable or forbidden; resolve private-repository plan capability before creating Apps') from None
        raise


def verify_live_ruleset(c, github=None, *, require_publication=False):
    """Read authoritative protection, including visible empty bypass actors."""
    bot = c.get('review_bot', {})
    config = bot.get('ruleset', {})
    apps = bot.get('apps', {})
    if not bot.get('source') or not config.get('id') or not config.get('required_checks'):
        raise ValueError('auto-merge blocked: explicit Review Bot pin, ruleset.id and App-bound project CI are required')
    for role in ('review', 'builder'):
        if not all(apps.get(role, {}).get(key) for key in ('id', 'slug', 'installation_id')):
            raise ValueError(f'auto-merge blocked: verified {role} App receipts are required')
    if require_publication:
        publication = c.get('publication', {})
        if (publication.get('mode') != 'auto_merge'
                or publication.get('review_app') != apps['review']['slug']
                or publication.get('hold_label', 'hold') != 'hold'):
            raise ValueError('auto-merge blocked: publication must select the installed Review App and its hold label')
    github = github or GitHub.owner()
    root = f'/repos/{c["repo"]}'
    repository = github.request('GET', root)
    if (not isinstance(repository, dict)
            or repository.get('full_name', '').lower() != c['repo'].lower()
            or repository.get('default_branch') != 'main'
            or repository.get('allow_auto_merge') is not True):
        raise ValueError('auto-merge blocked: verify repository identity, main default branch and repository auto-merge enablement')
    if not any(row['id'] == config['id'] for row in verify_capability(c, github)):
        raise ValueError('auto-merge blocked: explicit ruleset is not attached to this repository')
    live = github.request('GET', root + f'/rulesets/{config["id"]}')
    if (not isinstance(live, dict) or live.get('id') != config['id']
            or live.get('source_type') != 'Repository'
            or live.get('source', '').lower() != c['repo'].lower()
            or live.get('target') != 'branch' or live.get('enforcement') != 'active'
            or live.get('conditions') != {'ref_name': {'include': ['refs/heads/main'], 'exclude': []}}
            or live.get('bypass_actors') != []):
        raise ValueError('auto-merge blocked: ruleset must be repository-owned, active, main-only and visibly have no bypass actors; missing bypass visibility is unknown')
    rules = {}
    remote_rules = live.get('rules')
    if not isinstance(remote_rules, list):
        raise ValueError('auto-merge blocked: live ruleset rules are unavailable')
    for rule in remote_rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('type'), str) or rule['type'] in rules:
            raise ValueError('auto-merge blocked: live ruleset has invalid or ambiguous rules')
        parameters = rule.get('parameters', {})
        if not isinstance(parameters, dict):
            raise ValueError('auto-merge blocked: live rule parameters are unavailable')
        rules[rule['type']] = parameters
    pull = rules.get('pull_request', {})
    if (type(pull.get('required_approving_review_count')) is not int
            or pull['required_approving_review_count'] < 0):
        raise ValueError('auto-merge blocked: an explicit nonnegative human approval count is required')
    parameters = rules.get('required_status_checks', {})
    if parameters.get('strict_required_status_checks_policy') is not True:
        raise ValueError('auto-merge blocked: strict required checks must be enforced')
    checks = {}
    required_checks = parameters.get('required_status_checks')
    if not isinstance(required_checks, list):
        raise ValueError('auto-merge blocked: required check producers are unavailable')
    for check in required_checks:
        if not isinstance(check, dict) or not isinstance(check.get('context'), str) or check['context'] in checks:
            raise ValueError('auto-merge blocked: required check producers are ambiguous')
        checks[check['context']] = check.get('integration_id')
    expected = _required_checks(c)
    if any(checks.get(check['context']) != check['integration_id'] for check in expected):
        raise ValueError('auto-merge blocked: project CI and required Review App checks must require their exact configured Apps')
    if config.get('merge_queue', False):
        queue = rules.get('merge_queue', {})
        method = c.get('publication', {}).get('merge_method', 'squash').upper()
        if queue.get('merge_method') != method or queue.get('grouping_strategy') != 'ALLGREEN':
            raise ValueError('auto-merge blocked: configured native merge queue method and ALLGREEN grouping must be enforced')
        if 'codex-review' in checks:
            raise ValueError('auto-merge blocked: codex-review is not published on native merge-group heads; reapply the queue-mode ruleset')
    elif 'merge_queue' in rules:
        raise ValueError('auto-merge blocked: live merge queue differs from configured non-queue mode')
    return {'verified': True, 'ruleset_id': config['id'], 'branch': 'main',
            'review_app_id': apps['review']['id'], 'required_checks': expected}


def _matches_requested(actual, expected):
    """GitHub may add default parameters and reorder unordered rule arrays."""
    if isinstance(expected, dict):
        return (isinstance(actual, dict)
                and all(key in actual and _matches_requested(actual[key], value)
                        for key, value in expected.items()))
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for value in expected:
            match = next((index for index, candidate in enumerate(remaining)
                          if _matches_requested(candidate, value)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    return type(actual) is type(expected) and actual == expected


def _queue_workflow_warnings(c, github):
    """Best-effort diagnostics only; unreadable workflows never block planning."""
    root = f'/repos/{c["repo"]}/contents/'
    try:
        rows = github.request('GET', root + '.github/workflows?ref=main')
        if not isinstance(rows, list):
            return []
    except ValueError:
        return []
    contexts = {check['context'] for check in c['review_bot']['ruleset'].get('required_checks', [])}
    warnings = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get('path', '')
        if (row.get('type') != 'file' or not isinstance(path, str)
                or not path.startswith('.github/workflows/')
                or not path.endswith(('.yml', '.yaml'))):
            continue
        try:
            content = github.request('GET', root + quote(path, safe='/') + '?ref=main')
            if content.get('encoding') != 'base64':
                continue
            workflow = yaml.load(base64.b64decode(content['content']).decode('utf-8'), Loader=yaml.BaseLoader)
            if not isinstance(workflow, dict) or not isinstance(workflow.get('jobs'), dict):
                continue
            names = {job.get('name', key) for key, job in workflow['jobs'].items()
                     if isinstance(job, dict) and isinstance(job.get('name', key), str)}
            matched = contexts & names
            triggers = workflow.get('on', {})
            if isinstance(triggers, (str, list, dict)) and 'merge_group' not in (
                    [triggers] if isinstance(triggers, str) else triggers):
                if matched:
                    warnings.append(f'{path}: configured CI {", ".join(sorted(matched))} lacks a merge_group trigger; native queue checks will not run')
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, yaml.YAMLError):
            continue
    return warnings


def ruleset(c, *, apply=False, github=None):
    github = github or GitHub.owner()
    bot = c.get('review_bot', {})
    config = bot.get('ruleset', {})
    root = f'/repos/{c["repo"]}'
    repository = github.request('GET', root)
    if not isinstance(repository, dict) or repository.get('full_name', '').lower() != c['repo'].lower():
        raise ValueError('repository identity unavailable')
    result = {'operation': 'ruleset', 'apply': apply, 'repository': c['repo'],
              'repository_auto_merge_enabled': repository.get('allow_auto_merge') is True,
              'existing': None, 'desired': None, 'diff': '', 'blockers': []}
    if config.get('branch', 'main') != 'main' or repository.get('default_branch') != 'main':
        result['blockers'].append('The current bot requires the main default branch')
    existing = None
    try:
        live = _pages(github, root + '/rulesets?includes_parents=true')
        selected = config.get('id')
        if selected is not None:
            if not any(row['id'] == selected for row in live):
                raise ValueError('the explicit ruleset ID is not attached to this repository')
            existing = github.request('GET', root + f'/rulesets/{selected}')
            if not isinstance(existing, dict) or existing.get('id') != selected:
                raise ValueError('explicit repository ruleset could not be verified')
        elif any(row.get('name') == config.get('name', 'fleet-review-bot') for row in live):
            result['blockers'].append('An existing same-name ruleset requires an explicit ruleset.id; no replacement is inferred')
    except APIError as exc:
        if exc.status != 403:
            raise
        result['blockers'].append('GitHub403: repository rulesets are unavailable or forbidden; private-repository plan capability must be resolved, never treated as no rules')
    try:
        desired = desired_ruleset(c, existing)
    except ValueError as exc:
        result['blockers'].append(str(exc))
        desired = None
    result['existing'] = _body(existing) if existing else None
    result['desired'] = desired
    app_id = bot.get('apps', {}).get('review', {}).get('id')
    result['approval authority'] = f'app-approval (Review App {app_id}), human approval count 0'
    if config.get('merge_queue', False):
        result['queue approval authority'] = (
            'app-approval subsumes codex-review: every member requires a non-dismissed '
            'exact-head Review App APPROVE, issued only after passing review; '
            'codex-review is not published on synthetic merge-group heads')
        result['warnings'] = _queue_workflow_warnings(c, github)
    if not app_id:
        result['blockers'].append('Verified Review App id/slug/installation receipts are required before --apply')
    if not config.get('required_checks'):
        result['blockers'].append('Configure at least one project CI context and its integration_id')
    if not bot.get('source'):
        result['blockers'].append('An explicit version/commit/image pin is required before --apply')
    if desired:
        result['diff'] = ''.join(difflib.unified_diff(_json(result['existing']).splitlines(True),
                              _json(desired).splitlines(True), fromfile='live', tofile='proposed'))
    digest = hashlib.sha256(_json({'repo': c['repo'], 'id': config.get('id'),
                                  'before': result['existing'], 'after': desired}).encode()).hexdigest()
    result['plan_digest'] = digest
    if not apply:
        if not result['blockers']:
            state.write(c, 'review-bot-ruleset-plan', {'digest': digest})
        return result
    if result['blockers']:
        raise ValueError('; '.join(result['blockers']))
    if repository.get('permissions', {}).get('admin') is not True:
        raise ValueError('ruleset --apply requires repository administration authority')
    with state.lock(c, 'review-bot-install', operation='Review Bot ruleset application'):
        receipt = state.read(c, 'review-bot-ruleset-plan', {})
        if receipt.get('digest') != digest:
            raise ValueError('run ruleset --dry-run and review its current diff before --apply; live/config changes invalidate the plan')
        prerequisites(c)
        verify_pin(bot['source'], github)
        for role in ('review', 'builder'):
            app = bot['apps'][role]
            if not all(app.get(key) for key in ('id', 'slug', 'installation_id')):
                raise ValueError(f'verified {role} App receipts required before ruleset --apply')
            client = app_client(app, role)
            verify_app(c, role, client)
            verify_installation(c, role, client)
        selected = config.get('id')
        # Preserve a durable create intent across an ambiguous POST response.
        pending = state.read(c, 'review-bot-ruleset-create')
        if selected is None and pending:
            raise ValueError('ruleset creation is unconfirmed; reconcile live rules and record the explicit ID, never retry blindly')
        # Remote verification can take time; never overwrite a newer protection
        # document observed after the reviewed plan was admitted under our lock.
        refreshed_inventory = _pages(github, root + '/rulesets?includes_parents=true')
        if selected is not None:
            if not any(row['id'] == selected for row in refreshed_inventory):
                raise ValueError('ruleset attachment changed; review a fresh dry-run before apply')
            refreshed = github.request('GET', root + f'/rulesets/{selected}')
            if (not isinstance(refreshed, dict) or refreshed.get('id') != selected
                    or _body(refreshed) != result['existing']):
                raise ValueError('live protection changed during verification; review a fresh dry-run before apply')
        elif any(row.get('name') == desired['name'] for row in refreshed_inventory):
            raise ValueError('a same-name ruleset appeared during verification; reconcile its explicit ID before apply')
        document, encoded = read_config(c)
        if document.get('review_bot') != bot:
            raise ValueError('Review Bot configuration changed during verification; reload and review a fresh dry-run before apply')
        if selected is None:
            state.write(c, 'review-bot-ruleset-create', {'digest': digest})
        response = github.request('PUT' if selected else 'POST',
                                  root + (f'/rulesets/{selected}' if selected else '/rulesets'), desired)
        if (not isinstance(response, dict) or type(response.get('id')) is not int
                or response['id'] < 1 or (selected is not None and response['id'] != selected)):
            raise ValueError('ruleset mutation unconfirmed; inspect live protection before retrying')
        confirmed = github.request('GET', root + f'/rulesets/{response["id"]}')
        if not isinstance(confirmed, dict) or not _matches_requested(_body(confirmed), desired):
            raise ValueError('ruleset post-apply verification differs; inspect live protection, never assume enforcement')
        document['review_bot'].setdefault('ruleset', {})['id'] = response['id']
        save_config(c, document, encoded)
        (state.directory(c) / 'review-bot-ruleset-create.json').unlink(missing_ok=True)
        result['ruleset_id'] = response['id']
    return result

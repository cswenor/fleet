"""Validation for the opt-in, per-project Review Bot installation."""
import re
from pathlib import Path
from urllib.parse import urlsplit


REPOSITORY = r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
SLUG = r'[a-z0-9]+(?:-[a-z0-9]+)*'


def _mapping(value, fields, name):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise ValueError(f'{name} must be a mapping with only documented fields')
    return value


def _text(value, name, pattern=None):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or (pattern is not None and not re.fullmatch(pattern, value))):
        raise ValueError(f'invalid {name}')


def _positive(value, name):
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        raise ValueError(f'{name} must be a positive JavaScript-safe integer')


def _url(value, name, path):
    _text(value, name)
    parsed = urlsplit(value)
    try:
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path == path and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f'{name} requires an HTTPS URL ending in {path}, without credentials or query')


def validate(c):
    """Bootstrap receipts/pin may be absent; mutation commands require them."""
    if 'review_bot' not in c:
        return
    bot = _mapping(c['review_bot'], ('source', 'apps', 'webhook_url', 'ruleset', 'deployment'), 'review_bot')
    source = bot.get('source')
    if source is not None:
        source = _mapping(source, ('repository', 'version', 'commit', 'image'), 'review_bot.source')
        if set(source) != {'repository', 'version', 'commit', 'image'}:
            raise ValueError('review_bot.source requires repository, version, commit and image together')
        _text(source['repository'], 'review_bot.source.repository', REPOSITORY)
        _text(source['version'], 'review_bot.source.version', r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
        _text(source['commit'], 'review_bot.source.commit', r'[0-9a-f]{40}')
        _text(source['image'], 'review_bot.source.image',
              re.escape('ghcr.io/' + source['repository'].lower() + '@sha256:') + r'[0-9a-f]{64}')
    paths = []

    def private_path(value, name):
        _text(value, name)
        path = Path(value)
        if not path.is_absolute() or path.resolve().is_relative_to(Path(c['root']).resolve()):
            raise ValueError(f'{name} requires an absolute path outside the project checkout')
        paths.append((path.resolve(), name))

    apps = bot.get('apps', {})
    _mapping(apps, ('owner', 'owner_type', 'review', 'builder'), 'review_bot.apps')
    if 'owner' in apps:
        _text(apps['owner'], 'review_bot.apps.owner', r'[A-Za-z0-9][A-Za-z0-9-]*')
        if apps['owner'].lower() != c['repo'].split('/')[0].lower():
            raise ValueError('private review_bot Apps must belong to the project repository owner')
    if 'owner_type' in apps and apps['owner_type'] not in ('organization', 'user'):
        raise ValueError('review_bot.apps.owner_type must be organization or user')
    for role in ('review', 'builder'):
        if role not in apps:
            continue
        app = _mapping(apps[role], ('name', 'slug', 'id', 'installation_id', 'env_file'), f'review_bot.apps.{role}')
        if app.get('id') is not None and not app.get('slug'):
            raise ValueError(f'review_bot.apps.{role}.id requires its verified slug')
        if app.get('installation_id') is not None and not app.get('id'):
            raise ValueError(f'review_bot.apps.{role}.installation_id requires its App id')
        for key, value in app.items():
            name = f'review_bot.apps.{role}.{key}'
            if key in ('id', 'installation_id'):
                _positive(value, name)
            elif key == 'slug':
                _text(value, name, SLUG)
            elif key == 'env_file':
                private_path(value, name)
            else:
                _text(value, name, r'[A-Za-z0-9][A-Za-z0-9 ._-]{2,33}')
    for key in ('id', 'slug', 'installation_id'):
        first, second = apps.get('review', {}).get(key), apps.get('builder', {}).get(key)
        if first is not None and first == second:
            raise ValueError(f'Review and Builder Apps require separate {key} receipts')
    if 'webhook_url' in bot:
        _url(bot['webhook_url'], 'review_bot.webhook_url', '/github')
    rules = bot.get('ruleset', {})
    _mapping(rules, ('id', 'name', 'branch', 'required_checks', 'merge_queue'), 'review_bot.ruleset')
    if 'id' in rules:
        _positive(rules['id'], 'review_bot.ruleset.id')
    if 'name' in rules:
        _text(rules['name'], 'review_bot.ruleset.name', r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}')
    if 'branch' in rules and rules['branch'] != 'main':
        raise ValueError('review_bot.ruleset.branch currently supports main only')
    if 'merge_queue' in rules and type(rules['merge_queue']) is not bool:
        raise ValueError('review_bot.ruleset.merge_queue must be a boolean')
    checks = rules.get('required_checks', [])
    if not isinstance(checks, list):
        raise ValueError('review_bot.ruleset.required_checks must be a list')
    contexts = set()
    for check in checks:
        _mapping(check, ('context', 'integration_id'), 'review_bot.ruleset.required_checks entry')
        if set(check) != {'context', 'integration_id'}:
            raise ValueError('each required check needs context and integration_id')
        _text(check['context'], 'required check context')
        _positive(check['integration_id'], 'required check integration_id')
        if check['context'] in contexts or check['context'] in ('app-approval', 'codex-review'):
            raise ValueError('project CI contexts must be unique and cannot replace Review App checks')
        contexts.add(check['context'])
    deploy = bot.get('deployment', {})
    _mapping(deploy, ('backend', 'docker_context', 'directory', 'health_url', 'control_env_file',
                     'broker_env_file', 'gateway_env_file', 'runner_config_dir', 'broker_auth_dir'),
             'review_bot.deployment')
    if 'backend' in deploy and deploy['backend'] != 'compose':
        raise ValueError('review_bot.deployment.backend must be compose')
    if 'docker_context' in deploy:
        _text(deploy['docker_context'], 'review_bot.deployment.docker_context', r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
    if 'health_url' in deploy:
        _url(deploy['health_url'], 'review_bot.deployment.health_url', '/health')
        if 'webhook_url' in bot and urlsplit(deploy['health_url']).netloc != urlsplit(bot['webhook_url']).netloc:
            raise ValueError('Review Bot health and webhook must use the same HTTPS origin')
    for key in ('directory', 'control_env_file', 'broker_env_file', 'gateway_env_file',
                'runner_config_dir', 'broker_auth_dir'):
        if key in deploy:
            private_path(deploy[key], f'review_bot.deployment.{key}')
    for index, (path, name) in enumerate(paths):
        for previous, _ in paths[:index]:
            if path == previous:
                raise ValueError(f'{name} must not share another credential/state path')

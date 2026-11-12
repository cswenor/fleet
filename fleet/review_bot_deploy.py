"""Explicit-context Compose plans and guarded, private deployment execution."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import tempfile

from .review_bot_config import validate
from .review_bot_api import GitHub, app_client, verify_pin
from .review_bot_apps import verify_app, verify_installation
from .service_env import read_environment
from .review_bot_files import private_parent
from .review_bot_ruleset import verify_live_ruleset

SERVICES = ('control', 'runner', 'broker', 'gateway')
_LIMIT = 1024 * 1024

# Kernel bind roots retain source identity when Docker dereferences host symlinks.
# Include nested mounts: a vault submount inside reviewed work is also unsafe.
_BIND_ISOLATION = r'''
function isolatedMounts(text) {
  const decode = value => value.replace(/\\([0-7]{3})/g, (_, octal) => String.fromCharCode(parseInt(octal, 8)));
  const below = (path, parent) => path === parent || path.startsWith(parent === '/' ? '/' : parent + '/');
  const mounts = text.trim().split('\n').map(line => {
    const fields = line.split(' ');
    return { device: fields[2], root: decode(fields[3] || ''), target: decode(fields[4] || '') };
  });
  const names = ['instance', 'auth', 'config'];
  const groups = names.map(name => mounts.filter(mount => below(mount.target, '/probe/' + name)));
  if (groups.some((group, index) => !group.some(mount => mount.target === '/probe/' + names[index]) ||
      group.some(mount => !/^\d+:\d+$/.test(mount.device) || !mount.root.startsWith('/')))) return false;
  for (let first = 0; first < groups.length; first++) {
    for (let second = first + 1; second < groups.length; second++) {
      for (const left of groups[first]) for (const right of groups[second]) {
        if (left.device === right.device && (below(left.root, right.root) || below(right.root, left.root))) return false;
      }
    }
  }
  return true;
}
'''


def project_name(c):
    return 'fleet-review-' + hashlib.sha256(c['repo'].lower().encode()).hexdigest()[:16]


def docker(context, args, *, input=None, anonymous=False):
    """Never expose daemon errors, credentials, or unbounded subprocess output."""
    if not isinstance(context, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', context):
        raise ValueError('an explicit Docker context is required')
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'DOCKER_CONFIG', 'SSH_AUTH_SOCK', 'XDG_RUNTIME_DIR')}
    command = ['docker', '--context', context, *args]
    if anonymous:
        endpoint = docker(context, ['context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}']).strip()
        if not endpoint.startswith(('unix://', 'ssh://')):
            raise ValueError('anonymous pull requires an existing local or SSH Docker context')
        command = ['docker', '--host', endpoint, *args]
    try:
        with ExitStack() as stack:
            if anonymous:
                environment['DOCKER_CONFIG'] = stack.enter_context(tempfile.TemporaryDirectory(prefix='fleet-public-pull-'))
            process = stack.enter_context(subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment))
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                payload = memoryview(input.encode() if input is not None else b'')
                if payload:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                else:
                    process.stdin.close()
                output = bytearray()
                total = 0
                deadline = time.monotonic() + 120
                try:
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ValueError('Docker operation timed out')
                        for key, _ in selector.select(remaining):
                            if key.data == 'stdin':
                                written = os.write(key.fd, payload[:65536])
                                payload = payload[written:]
                                if not payload:
                                    selector.unregister(key.fileobj)
                                    key.fileobj.close()
                                continue
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > _LIMIT:
                                raise ValueError('Docker output exceeded the safe limit')
                            if key.data == 'stdout':
                                output.extend(chunk)
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
                    raise ValueError('Docker failed, timed out or exceeded its safe output limit') from None
            if process.returncode:
                raise ValueError('Docker operation failed; inspect privately on the selected context')
            return output.decode('utf-8')
    except (OSError, UnicodeError):
        raise ValueError('Docker is unavailable or returned an invalid response') from None


def topology(c):
    """JSON-safe intent only; never a claim about a running deployment."""
    bot = c.get('review_bot', {})
    deployment = bot.get('deployment', {})
    image = bot.get('source', {}).get('image')
    directory = deployment.get('directory')
    services = {}
    for role in SERVICES:
        services[role] = {
            'image': image, 'user': '10010:10010', 'read_only': True,
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'restart': 'unless-stopped', 'pids_limit': 4096 if role == 'runner' else 256,
            'mem_limit': '8g' if role == 'runner' else '2g',
            'tmpfs': ['/tmp:rw,noexec,size=64m,uid=10010,gid=10010'],
            'environment': {'PI_NATIVES_DIR': '/opt/omp-natives'},
        }
    services['control'].update(command=['node', '/opt/pr-review-bot/bot/server.mjs'],
                               networks=['review', 'egress', 'ingress'])
    services['runner'].update(command=['node', '/opt/pr-review-bot/bot/runner.mjs'], networks=['review'])
    services['broker'].update(command=['bash', '/opt/pr-review-bot/bin/broker-serve.sh', '0.0.0.0:8765'],
                              networks=['broker', 'egress'])
    services['gateway'].update(command=['bash', '/opt/pr-review-bot/bin/gateway-serve.sh', '0.0.0.0:4000'],
                               networks=['review', 'broker', 'egress'])
    for role, endpoint in {'control': '8080/ready', 'runner': '8081/health',
                           'broker': '8765/v1/healthz', 'gateway': '4000/healthz'}.items():
        services[role]['healthcheck'] = {
            'test': ['CMD', 'node', '-e', 'fetch("http://127.0.0.1:' + endpoint
                     + '",{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))'],
            'interval': '10s', 'timeout': '3s', 'retries': 12}
    services['gateway']['depends_on'] = {'broker': {'condition': 'service_healthy'}}
    services['control']['depends_on'] = {role: {'condition': 'service_healthy'} for role in ('runner', 'gateway')}
    services['control']['environment'].update(
        GITHUB_REPOSITORY=c['repo'], REVIEW_BOT_MODE=deployment.get('mode', 'advisory'), MERGE_LANE_ENABLED='false',
        MERGE_BATCH_ENABLED='false', RUNNER_URL='http://runner:8081', GATEWAY_URL='http://gateway:4000',
        STATE_ROOT='/state', WORK_ROOT='/work', SESSIONS_ROOT='/sessions', PORT='8080')
    services['gateway']['environment'].update(HOME='/var/lib/omp', OMP_AUTH_BROKER_URL='http://broker:8765')
    services['broker']['environment']['HOME'] = '/var/lib/omp'
    services['runner']['tmpfs'].append('/home/review:rw,exec,size=256m,uid=10010,gid=10010')
    def bind(source, target, readonly=False):
        return {'type': 'bind', 'source': source, 'target': target, 'read_only': readonly,
                'bind': {'create_host_path': False}}
    if directory:
        root = Path(directory)
        services['control']['volumes'] = [bind(str(root / part), '/' + part) for part in ('state', 'work', 'sessions')]
        services['runner']['volumes'] = [bind(str(root / 'work'), '/work', True), bind(str(root / 'sessions'), '/sessions')]
        services['gateway']['volumes'] = [bind(str(root / 'gateway'), '/var/lib/omp')]
    if deployment.get('runner_config_dir'):
        services['runner'].setdefault('volumes', []).append(bind(deployment['runner_config_dir'], '/opt/pr-review-bot/config', True))
    if deployment.get('broker_auth_dir'):
        services['broker']['volumes'] = [bind(deployment['broker_auth_dir'], '/var/lib/omp')]
    return {'name': project_name(c), 'services': services,
            'networks': {'review': {'internal': True}, 'broker': {'internal': True}, 'egress': {},
                         'ingress': {'external': True, 'name': deployment.get('ingress_network')} }}


def plan(c):
    validate(c)
    bot = c.get('review_bot', {})
    deployment = bot.get('deployment', {})
    blockers = []
    if not bot.get('source'):
        blockers.append('configure a verified immutable release source/image pin')
    for key in ('backend', 'docker_context', 'directory', 'health_url', 'control_env_file',
                'runner_env_file', 'broker_env_file', 'gateway_env_file', 'runner_config_dir',
                'broker_auth_dir', 'ingress_network'):
        if not deployment.get(key):
            blockers.append('configure review_bot.deployment.' + key)
    for role in ('review', 'builder'):
        app = bot.get('apps', {}).get(role, {})
        if not all(app.get(key) for key in ('name', 'slug', 'id', 'installation_id', 'env_file')):
            blockers.append('complete verified ' + role + ' App creation/installation receipts')
    if not bot.get('webhook_url'):
        blockers.append('configure public HTTPS webhook ingress')
    return {'operation': 'deploy', 'apply': False, 'ready': False, 'blockers': blockers,
            'target_paths_verified': False, 'compose': topology(c), 'prerequisites': [
                'Separate owner/Main host rollout authorization is required before --apply.',
                'Existing HTTPS reverse proxy must join ingress and route the configured origin to control:8080; no ports are published.',
                'Target-host instance directories and broker logins must already exist, owned by uid 10010; runner configuration must be read-only.',
                'Live release, selected-only-this-repository Apps, private environment files and Docker context/network are verified on apply.',
                'Approval mode requires verified live protection; deployment never authorizes rollout or calibration.']}


def _probe_target(c):
    """Same-image nonroot probe; target paths are not client-side filesystem paths."""
    bot, deployment = c['review_bot'], c['review_bot']['deployment']
    mounts = {
        'instance': deployment['directory'],
        'auth': deployment['broker_auth_dir'],
        'config': deployment['runner_config_dir'],
    }
    args = ['run', '--rm', '--interactive', '--pull', 'never', '--network', 'none',
            '--read-only', '--user', '10010:10010', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '32', '--memory', '128m']
    for name, path in mounts.items():
        if ',' in path:
            raise ValueError('target bind paths cannot contain commas')
        args += ['--mount', 'type=bind,src=' + path + ',dst=/probe/' + name + ',readonly,bind-propagation=rprivate,bind-recursive=readonly']
    args += [bot['source']['image'], 'node', '--input-type=module']
    script = r'''
import { lstatSync, realpathSync, accessSync, readFileSync, constants } from 'node:fs';
''' + _BIND_ISOLATION + r'''
let valid = false;
try {
  valid = isolatedMounts(readFileSync('/proc/self/mountinfo', 'utf8'));
  for (const name of ['instance', 'instance/state', 'instance/work', 'instance/sessions', 'instance/gateway', 'auth']) {
    const path = '/probe/' + name;
    const info = lstatSync(path);
    if (!info.isDirectory() || realpathSync(path) !== path || info.uid !== 10010 || info.gid !== 10010 ||
        (info.mode & 0o777) !== 0o700) valid = false;
  }
  const config = lstatSync('/probe/config');
  if (!config.isDirectory() || config.uid === 10010 || (config.mode & 0o022)) valid = false;
  for (const name of ['reviewer-models.yml', 'reviewer-overlay.yml']) {
    const path = '/probe/config/' + name;
    const info = lstatSync(path);
    if (!info.isFile() || info.uid === 10010 || (info.mode & 0o022)) valid = false;
    accessSync(path, constants.R_OK);
  }
} catch { valid = false; }
process.stdout.write(valid ? 'valid' : 'invalid');
process.exitCode = valid ? 0 : 1;
'''
    # Pull is an apply-only mutation, required before the networkless probe.
    try:
        docker(deployment['docker_context'], ['image', 'pull', bot['source']['image']], anonymous=True)
    except ValueError:
        raise ValueError('anonymous public pinned-image pull failed; verify public availability and the selected Docker context privately') from None
    if docker(deployment['docker_context'], args, input=script).strip() != 'valid':
        raise ValueError('target-host private directories or read-only runner configuration are unsafe')


def deploy(c, *, apply=False, github=None):
    result = plan(c)
    bot = c.get('review_bot', {})
    deployment = bot.get('deployment', {})
    result['mode'] = deployment.get('mode', 'advisory')
    if result['mode'] == 'approve':
        try:
            result['protection'] = verify_live_ruleset(c, github)
        except ValueError as exc:
            result['blockers'].append('approve deployment blocked: ' + str(exc))
    if not apply:
        return result
    if result['blockers']:
        raise ValueError('deployment prerequisites incomplete: ' + '; '.join(result['blockers']))
    context = deployment['docker_context']
    # Context inspection is projected: never read TLS material or arbitrary metadata.
    endpoint = docker(context, ['context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}']).strip()
    if not endpoint.startswith(('unix://', 'ssh://')):
        raise ValueError('deployment requires an existing local or SSH Docker context')
    github = github or GitHub.owner()
    verify_pin(bot['source'], github)
    for role in ('review', 'builder'):
        client = app_client(bot['apps'][role], role)
        verify_app(c, role, client)
        verify_installation(c, role, client)
    network = docker(context, ['network', 'inspect', deployment['ingress_network'], '--format', '{{.Driver}}']).strip()
    if network != 'bridge':
        raise ValueError('ingress requires an existing Docker bridge network')
    envs = {}
    for role in SERVICES:
        path = deployment[role + '_env_file']
        envs[role] = read_environment(path)
        private_parent(path)
    allowed = {
        'runner': {'OMP_GATEWAY_TOKEN'}, 'broker': {'OMP_AUTH_BROKER_TOKEN'},
        'gateway': {'OMP_GATEWAY_TOKEN', 'OMP_AUTH_BROKER_TOKEN'},
        'control': {'GITHUB_APP_ID', 'GITHUB_APP_CLIENT_ID', 'GITHUB_APP_PRIVATE_KEY_B64', 'GITHUB_WEBHOOK_SECRET',
                    'OMP_GATEWAY_TOKEN', 'TYPESAFE_API_KEY', 'REVIEW_BOT_LOGIN', 'REVIEW_BOT_USER_ID',
                    'BUILDER_BOT_LOGIN', 'BUILDER_BOT_USER_ID', 'REVIEW_APPROVERS', 'REVIEW_MODEL',
                    'REVIEW_THINKING', 'REVIEW_CONCURRENCY', 'RETENTION_DAYS', 'RECONCILE_INTERVAL_MS'},
    }
    for role, values in envs.items():
        if set(values) - allowed[role] or not all(values.values()):
            raise ValueError(role + ' environment contains unexpected or empty authority/configuration fields')
    required = {'control': {'GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY_B64', 'GITHUB_WEBHOOK_SECRET',
                             'OMP_GATEWAY_TOKEN', 'TYPESAFE_API_KEY', 'REVIEW_BOT_LOGIN', 'REVIEW_BOT_USER_ID',
                             'BUILDER_BOT_LOGIN', 'BUILDER_BOT_USER_ID', 'REVIEW_APPROVERS'},
                'runner': allowed['runner'], 'broker': allowed['broker'], 'gateway': allowed['gateway']}
    if any(required[role] - envs[role].keys() for role in SERVICES):
        raise ValueError('per-service environment is missing required runtime fields')
    if (any(envs[role]['OMP_GATEWAY_TOKEN'] != envs['control']['OMP_GATEWAY_TOKEN'] for role in ('runner', 'gateway'))
            or envs['broker']['OMP_AUTH_BROKER_TOKEN'] != envs['gateway']['OMP_AUTH_BROKER_TOKEN']):
        raise ValueError('per-instance runtime authentication does not match between services')
    review = read_environment(bot['apps']['review']['env_file'])
    if any(envs['control'].get(key) != review.get(key) for key in ('GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY_B64', 'GITHUB_WEBHOOK_SECRET')):
        raise ValueError('control credentials differ from the verified Review App credentials')
    _probe_target(c)
    compose = topology(c)
    for role in SERVICES:
        compose['services'][role]['environment'] = {**envs[role], **compose['services'][role]['environment']}
    def literal(value):
        if isinstance(value, str):
            return value.replace('$', '$$')
        if isinstance(value, dict):
            return {key: literal(item) for key, item in value.items()}
        if isinstance(value, list):
            return [literal(item) for item in value]
        return value
    # Compose reads private JSON over stdin; no secret files, argv or logs are emitted.
    if result['mode'] == 'approve':
        result['protection'] = verify_live_ruleset(c, github)
    docker(context, ['compose', '--project-name', project_name(c), '--file', '-', 'up', '--detach', '--pull', 'never'],
           input=json.dumps(literal(compose)))
    result.update(apply=True, deployed=True, target_paths_verified=True)
    result['prerequisites'].append('Deployment requested; use status for observed runtime readiness.')
    return result

"""Install one non-privileged foreground supervisor per configured project."""
from pathlib import Path
import http.client
import os
import re
import shutil
import sys
import time
from .commands import run


def install(c, deliver=False):
    name = 'fleet-' + re.sub(r'[^a-zA-Z0-9_.-]', '-', c['repo']) + '.service'
    result = _install(name, ['--config', c['_path'], 'serve'] + ([] if deliver else ['--no-deliver']),
                      c['root'], 'Agent fleet supervisor (' + c['repo'] + ')')
    return {**result, 'delivery_enabled': deliver, 'config': c['_path']}


def install_central(c, environment_file):
    environment_file = Path(environment_file).expanduser().resolve()
    if not environment_file.is_file() or environment_file.stat().st_mode & 0o077:
        raise ValueError('central EnvironmentFile must exist and be private (chmod 600)')
    result = _install('fleet-central.service', ['--config', c['_path'], 'central-serve'],
                      str(Path(c['_path']).parent), 'Agent fleet central status', str(environment_file))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(c['host'], c['port'], timeout=1)
        try:
            connection.request('GET', '/')
            if connection.getresponse().status == 200:
                return result
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.1)
    raise ValueError('central service did not become ready; inspect fleet-central.service')


def _install(unit_name, arguments, directory, description, environment_file=None):
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    units = config_home / 'systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    executable = shutil.which('fleet')
    if executable is None:
        # Source checkout install: keep code at this stable path. No system Python mutation.
        executable = str(Path.home() / '.local/bin/fleet')
        Path(executable).parent.mkdir(parents=True, exist_ok=True)
        package_root = str(Path(__file__).resolve().parent.parent)
        Path(executable).write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {package_root!r})\nfrom fleet.cli import main\nsys.exit(main())\n')
        Path(executable).chmod(0o755)
    def quote(s, expand_dollars=False):
        if any(ch in s for ch in ('\n', '\r', '\0')):
            raise ValueError('service values must not contain control characters')
        result = s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
        return '"' + (result.replace('$', '$$') if expand_dollars else result) + '"'
    # Inherit only executable search paths, never the caller environment or provider tokens.
    paths = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    args = [executable] + arguments
    working_directory = directory.replace('%', '%%')
    if any(ch in working_directory for ch in ('\n', '\r', '\0')):
        raise ValueError('invalid service working directory')
    environment_directive = ''
    if environment_file:
        if any(ch in environment_file for ch in ('\n', '\r', '\0')):
            raise ValueError('invalid service EnvironmentFile path')
        # EnvironmentFile is one literal path, not an ExecStart argv or Environment assignment.
        environment_directive = 'EnvironmentFile=' + environment_file.replace('%', '%%')
    unit = f'''[Unit]
Description={description}
After=default.target

[Service]
Type=simple
WorkingDirectory={working_directory}
ExecStart={' '.join(quote(a, expand_dollars=True) for a in args)}
Environment={quote('PATH=' + paths)}
{environment_directive}
Restart=on-failure
RestartSec=15
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
'''
    (units / unit_name).write_text(unit)
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', unit_name])
    run(['systemctl', '--user', 'restart', unit_name])
    run(['systemctl', '--user', 'is-active', '--quiet', unit_name])
    return {'unit': unit_name}

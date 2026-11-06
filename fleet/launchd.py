"""macOS user LaunchAgents; no system daemon or privilege escalation."""
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import time
from .commands import CommandError, run
from .service_env import read_environment


def target(unit):
    if not re.fullmatch(r'[a-zA-Z0-9_.@-]+\.service', unit):
        raise ValueError('invalid service unit')
    return unit.removesuffix('.service')


def status(unit):
    label = target(unit)
    text = run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'])
    # Never return raw print output: it can include environment values.
    fields = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\t(state|pid|runs|last exit code) = ([a-zA-Z0-9 -]+)', line)
        if match:
            fields[match[1]] = match[2].strip()
    pid = fields.get('pid', '')
    return {'unit': unit, 'label': label, 'state': fields.get('state', 'unknown'),
            'pid': int(pid) if pid.isdecimal() else None}


def install(unit, arguments, directory, environment_file=None):
    label = target(unit)
    if environment_file:
        environment_file = str(Path(environment_file).expanduser().absolute())
        read_environment(environment_file)  # validate now AND at every process start
    executable = shutil.which('fleet')
    if executable is None:
        executable = str(Path.home() / '.local/bin/fleet')
        Path(executable).parent.mkdir(parents=True, exist_ok=True)
        package_root = str(Path(__file__).resolve().parent.parent)
        Path(executable).write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {package_root!r})\nfrom fleet.cli import main\nsys.exit(main())\n')
        Path(executable).chmod(0o755)
    argv = [executable, *arguments]
    if environment_file:
        argv = [sys.executable, str(Path(__file__).with_name('service_env.py').resolve()), environment_file, *argv]
    agent = {'Label': label, 'ProgramArguments': argv, 'WorkingDirectory': directory,
             'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')},
             'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
             'ThrottleInterval': 15, 'Umask': 0o077}
    agents = Path.home() / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / (label + '.plist')
    # Private even though the plist contains no token values.
    with open(path, 'wb', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW, 0o600)) as stream:
        os.fchmod(stream.fileno(), 0o600)
        plistlib.dump(agent, stream)
    domain = f'gui/{os.getuid()}'
    try:
        status(unit)
    except CommandError:
        # Missing job is normal on first install. Bootstrap must still succeed;
        # a transport/domain failure cannot become a successful installation.
        pass
    else:
        run(['launchctl', 'bootout', domain + '/' + label])
    run(['launchctl', 'bootstrap', domain, str(path)])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = status(unit)
        if current['state'] == 'running' and current['pid'] and current['pid'] > 0:
            return {'unit': unit, 'label': label, 'plist': str(path)}
        time.sleep(0.1)
    raise ValueError('LaunchAgent did not become running with a PID; inspect launchctl state privately')

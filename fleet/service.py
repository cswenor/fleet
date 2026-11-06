"""Install one non-privileged foreground supervisor per configured project."""
from pathlib import Path
import os
import re
import shutil
import sys
from .commands import run
from . import state


def install(c, deliver=False):
    unit_name = 'fleet-' + re.sub(r'[^a-zA-Z0-9_.-]', '-', c['repo']) + '.service'
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
    def quote(s):
        if any(ch in s for ch in ('\n', '\r', '\0')):
            raise ValueError('service values must not contain control characters')
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'
    # Inherit only executable search paths, never the caller environment or provider tokens.
    paths = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    args = [executable, '--config', c['_path'], 'serve'] + ([] if deliver else ['--no-deliver'])
    working_directory = c['root'].replace('%', '%%')
    if any(ch in working_directory for ch in ('\n', '\r', '\0')):
        raise ValueError('invalid service working directory')
    unit = f'''[Unit]
Description=Agent fleet supervisor ({c['repo']})
After=default.target

[Service]
Type=simple
WorkingDirectory={working_directory}
ExecStart={' '.join(quote(a) for a in args)}
Environment={quote('PATH=' + paths)}
Restart=on-failure
RestartSec=15
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
'''
    (units / unit_name).write_text(unit)
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', '--now', unit_name])
    run(['systemctl', '--user', 'is-active', '--quiet', unit_name])
    return {'unit': unit_name, 'delivery_enabled': deliver, 'config': c['_path']}

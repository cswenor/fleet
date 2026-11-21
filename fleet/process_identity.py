"""Kernel executable-link recognition; callers must prove process ownership separately."""
from pathlib import Path
from os import getpid
import re


_OMP_BACKUP = re.compile(r'omp\.[0-9]+\.[0-9]+\.[0-9]+\.bak \(deleted\)')


def omp_executable(executable, argv0):
    """Recognize stock OMP or its deleted self-updater backup plus OMP argv0."""
    name = Path(executable).name
    return (name.removesuffix(' (deleted)') == 'omp' or
            bool(_OMP_BACKUP.fullmatch(name) and Path(argv0).name == 'omp'))


def local_pid_namespace(proc):
    """Identify the PID view only if procfs uses syscall-local PID numbers."""
    try:
        for line in (proc / 'self/status').read_text().splitlines():
            if line.startswith('NSpid:'):
                # The first entry belongs to the procfs mount, the last to us.
                # Require a single entry even if two namespaces assigned the same number.
                if line.split()[1:] == [str(getpid())]:
                    return str((proc / 'self/ns/pid').readlink())
                break
    except OSError:
        pass
    return None


def exited(process, proc=Path('/proc')):
    """Prove a recorded PID/start pair no longer exists in its recorded view."""
    pid, start, boot = process.get('pid'), process.get('start'), process.get('boot_id')
    namespace = process.get('pid_namespace')
    if type(pid) is not int or pid <= 0 or not isinstance(start, str) or not start.isdecimal() or not boot:
        return False
    try:
        if ((proc / 'sys/kernel/random/boot_id').read_text().strip() != boot
                or namespace is not None and local_pid_namespace(proc) != namespace):
            return False
    except OSError:
        return False
    try:
        return (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19] != start
    except FileNotFoundError:
        return True
    except (OSError, IndexError):
        return False

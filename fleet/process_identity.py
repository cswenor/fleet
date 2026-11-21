"""Kernel executable-link recognition; callers must prove process ownership separately."""
from pathlib import Path
import re


_OMP_BACKUP = re.compile(r'omp\.[0-9]+\.[0-9]+\.[0-9]+\.bak \(deleted\)')


def omp_executable(executable, argv0):
    """Recognize stock OMP or its deleted self-updater backup plus OMP argv0."""
    name = Path(executable).name
    return (name.removesuffix(' (deleted)') == 'omp' or
            bool(_OMP_BACKUP.fullmatch(name) and Path(argv0).name == 'omp'))


def exited(process, proc=Path('/proc')):
    """Prove a recorded PID/start pair no longer exists on the same boot."""
    pid, start, boot = process.get('pid'), process.get('start'), process.get('boot_id')
    namespace = process.get('pid_namespace')
    if (type(pid) is not int or pid <= 0 or not isinstance(start, str) or not start.isdecimal()
            or not boot or not namespace):
        return False
    try:
        if ((proc / 'sys/kernel/random/boot_id').read_text().strip() != boot
                or str((proc / 'self/ns/pid').readlink()) != namespace):
            return False
    except OSError:
        return False
    try:
        return (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19] != start
    except FileNotFoundError:
        return True
    except (OSError, IndexError):
        return False

"""Kernel executable-link recognition; callers must prove process ownership separately."""
from pathlib import Path
import re


_OMP_BACKUP = re.compile(r'omp\.[0-9]+\.[0-9]+\.[0-9]+\.bak \(deleted\)')


def omp_executable(executable, argv0):
    """Recognize stock OMP or its deleted self-updater backup plus OMP argv0."""
    name = Path(executable).name
    return (name.removesuffix(' (deleted)') == 'omp' or
            bool(_OMP_BACKUP.fullmatch(name) and Path(argv0).name == 'omp'))

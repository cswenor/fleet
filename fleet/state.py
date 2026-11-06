"""Private state and advisory single-writer locks on Linux user services."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile


def directory(c):
    path = Path(c['_state'])
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError('unsafe fleet state directory')
    path.chmod(0o700)
    return path


def read(c, name, default=None):
    path = directory(c) / (name + '.json')
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(c, name, value):
    path = directory(c)
    fd, temp = tempfile.mkstemp(dir=path, prefix='.')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path / (name + '.json'))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(c, name):
    with (directory(c) / (name + '.lock')).open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ValueError(f'fleet {name} already running') from e
        yield

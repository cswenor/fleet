"""Private state and advisory single-writer locks on Linux user services."""
from contextlib import contextmanager
import fcntl
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


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
    if name.startswith('threads-') and not name.endswith('-holder'):
        from .threads import compact_ledger
        compact_ledger(c, value)
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


class LauncherBusy(ValueError):
    """Lock contention is retryable, not an ownership/admission failure."""


def slot_lock(c, slot, wait_seconds=0, *, operation='slot terminal lease'):
    """Terminal-mutation lease; interactive callers may wait, supervisors never do."""
    return lock(c, 'slot-' + hashlib.sha256(os.fsencode(slot)).hexdigest(),
                wait_seconds=wait_seconds, operation=operation, display_name=f'slot {slot}')


def _busy(c, name, display_name=None):
    if name not in ('launcher', 'launch-publication') and not name.startswith(('threads-', 'thread-wait-', 'slot-')):
        return ValueError(f'fleet {name} already running')
    holder = read(c, name + '-holder', {}) or {}
    if name.startswith('slot-'):
        return LauncherBusy(
            f"{display_name or name} busy: held by pid {holder.get('pid', 'unknown')} "
            f"({holder.get('op', 'unknown')}) since {holder.get('start', 'unknown')}; retry in ~60s")
    if name.startswith(('threads-', 'thread-wait-')):
        return LauncherBusy(
            f"thread busy: held by pid {holder.get('pid', 'unknown')} "
            f"({holder.get('op', 'unknown')}) since {holder.get('start', 'unknown')}; retry")
    if name == 'launch-publication':
        return LauncherBusy(
            f"launch publishing; retry (held by pid {holder.get('pid', 'unknown')} "
            f"({holder.get('op', 'unknown')}) since {holder.get('start', 'unknown')})")
    return LauncherBusy(
        f"launcher busy: held by pid {holder.get('pid', 'unknown')} "
        f"({holder.get('op', 'unknown')}) since {holder.get('start', 'unknown')}; retry in ~60s")


@contextmanager
def lock(c, name, wait_seconds=None, *, operation=None, display_name=None):
    with (directory(c) / (name + '.lock')).open('a') as f:
        if wait_seconds is None:
            wait_seconds = 15 if name.startswith(('threads-', 'thread-wait-')) else 0
        if name == 'launcher':
            wait_seconds = min(wait_seconds, 15)
        elif name == 'launch-publication':
            wait_seconds = min(wait_seconds, 5)
        elif name.startswith(('threads-', 'thread-wait-', 'slot-')):
            wait_seconds = min(wait_seconds, 15)
        deadline = time.monotonic() + wait_seconds if wait_seconds > 0 else None
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as e:
                if deadline is None:
                    raise _busy(c, name, display_name) from e
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _busy(c, name, display_name) from e
                time.sleep(min(0.1, remaining))
        write(c, name + '-holder', {
            'pid': os.getpid(), 'op': operation or name,
            'start': datetime.now(timezone.utc).isoformat()})
        try:
            yield
        finally:
            (directory(c) / (name + '-holder.json')).unlink(missing_ok=True)

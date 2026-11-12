"""Atomic, private installer writes; never overwrite an existing credential file."""
import os
import stat
import tempfile
from pathlib import Path

import yaml


def private_parent(path):
    path = Path(path)
    parent = path.parent.resolve()
    if any((directory / '.git').exists() or (directory / '.git').is_symlink()
           for directory in (parent, *parent.parents)):
        raise ValueError('private installation files must not be inside a Git checkout')
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('installation files require an owned private parent directory with mode 0700')
    return parent / path.name


def _read_private(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('project installation config requires an owned regular file with mode 0600')
            return stream.read()
    except OSError:
        raise ValueError('private installation file unavailable or unsafe') from None


def read_config(c):
    path = private_parent(c['_path'])
    encoded = _read_private(path)
    try:
        document = yaml.safe_load(encoded)
    except yaml.YAMLError:
        raise ValueError('invalid private project config') from None
    if not isinstance(document, dict) or document.get('repo') != c['repo']:
        raise ValueError('private project config changed; reload before applying')
    return document, encoded


def _temporary(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.fleet-install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def create_environment(path, values):
    path = private_parent(path)
    lines = []
    for key, value in values.items():
        value = str(value)
        if not value or any(char.isspace() or char in '\"\'\0' for char in value):
            raise ValueError('App credential cannot be stored in the literal private environment format')
        lines.append(f'{key}={value}\n')
    temporary = _temporary(path, ''.join(lines).encode())
    try:
        # link is an atomic no-replace promotion; a concurrent file always wins.
        os.link(temporary, path, follow_symlinks=False)
    except OSError:
        raise ValueError('App credential destination exists or cannot be written; never retry App creation blindly') from None
    finally:
        temporary.unlink(missing_ok=True)


def save_config(c, document, expected):
    path = private_parent(c['_path'])
    temporary = _temporary(path, yaml.safe_dump(document, sort_keys=False).encode())
    try:
        if _read_private(path) != expected:
            raise ValueError('private project config changed; preserve it and reload before applying')
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)
    return path.read_bytes()

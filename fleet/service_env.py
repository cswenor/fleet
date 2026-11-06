"""Internal launchd entry point: private literal environment, never a shell."""
import os
from pathlib import Path
import re
import stat
import sys


def read_environment(path):
    """Open once without following a symlink; validate the opened file before reading."""
    try:
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('service environment requires an owned regular file with mode 0600')
            result = {}
            for number, line in enumerate(stream, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                key, sep, value = line.partition('=')
                if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                    raise ValueError(f'invalid service environment line {number}')
                if value[:1] in ('"', "'"):
                    if len(value) < 2 or value[-1] != value[0] or value[0] in value[1:-1]:
                        raise ValueError(f'invalid service environment line {number}')
                    value = value[1:-1]
                elif any(ch.isspace() or ch in ('"', "'") for ch in value):
                    raise ValueError(f'invalid service environment line {number}')
                if any(ch in value for ch in ('\0', '\r', '\n')):
                    raise ValueError(f'invalid service environment line {number}')
                result[key] = value
            return result
    except (OSError, UnicodeError):
        raise ValueError('service environment unavailable or unsafe') from None


def main():
    # argv contains only a file path and executable/CLI arguments, never values.
    try:
        if len(sys.argv) < 4:
            raise ValueError('invalid service environment invocation')
        values = read_environment(sys.argv[1])
        os.execve(sys.argv[2], sys.argv[2:], {**os.environ, **values})
    except (OSError, ValueError) as exc:
        # OSError can contain filenames; only parser's sanitized messages are safe.
        print(str(exc) if isinstance(exc, ValueError) else 'service executable unavailable', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

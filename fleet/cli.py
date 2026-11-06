import argparse
import json
import sys
from .config import load
from .commands import CommandError
from . import snapshot


def main():
    parser = argparse.ArgumentParser(prog='fleet')
    parser.add_argument('--config', default='fleet.yml')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('status', 'may-start'):
        p = sub.add_parser(cmd)
        p.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        c = load(args.config)
        s = snapshot.collect(c)
        if args.command == 'may-start':
            result = snapshot.may_start(c, s)
            print(json.dumps(result))
            return 0 if result['may_start'] else 1
        print(json.dumps(s, indent=2) if args.json else snapshot.render(s))
        return 0 if not s['errors'] else 1
    except (ValueError, OSError, CommandError) as e:
        print(f'fleet: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

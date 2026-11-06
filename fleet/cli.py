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
        p.add_argument('--drill-usage-file', help='test-only quota fixture; disables central push')
    p = sub.add_parser('serve')
    p.add_argument('--once', action='store_true')
    p.add_argument('--no-deliver', action='store_true')
    p.add_argument('--drill-usage-file', help='test-only quota fixture; disables central push')
    p = sub.add_parser('launch')
    p.add_argument('ref')
    p.add_argument('--slot')
    p.add_argument('--drill-usage-file', help='test-only additional launch withhold, never bypasses real quota')
    p = sub.add_parser('restart')
    p.add_argument('ref')
    p.add_argument('--slot', required=True)
    p.add_argument('--finished-ref', required=True)
    p = sub.add_parser('watch-pr')
    p.add_argument('number', type=int)
    p.add_argument('--timeout', type=int, default=14400)
    p.add_argument('--interval', type=int, default=60)
    p = sub.add_parser('wait')
    p.add_argument('--timeout', type=int, default=900, help='checkpoint after 1–1800 seconds')
    p.add_argument('--ack-run', help='acknowledge a processed Run delivery before waiting')
    p.add_argument('--terminal', help='caller identity when ORCA_TERMINAL_HANDLE is unavailable')
    p = sub.add_parser('install-service')
    p.add_argument('--deliver', action='store_true', help='enable durable inbox messages')
    sub.add_parser('central-serve')
    p = sub.add_parser('central-install-service')
    p.add_argument('--environment-file', required=True, help='private systemd EnvironmentFile with project tokens')
    args = parser.parse_args()
    try:
        if args.command in ('central-serve', 'central-install-service'):
            from . import central
            c = central.load(args.config, require_tokens=args.command == 'central-serve')
            if args.command == 'central-serve':
                central.serve(c)
            else:
                from .service import install_central
                print(json.dumps(install_central(c, args.environment_file)))
            return 0
        c = load(args.config)
        if getattr(args, 'drill_usage_file', None):
            c['_drill_usage_file'] = args.drill_usage_file
        if args.command == 'wait':
            from .mailbox import wait
            result = wait(c, args.timeout, args.ack_run, args.terminal)
            print(json.dumps(result))
            return {'messages': 0, 'timeout': 3, 'error': 2}[result['event']]
        if args.command == 'serve':
            from .supervisor import serve
            serve(c, not args.no_deliver, args.once)
            return 0
        if args.command == 'launch':
            from .launcher import launch
            print(json.dumps(launch(c, args.ref, args.slot), indent=2))
            return 0
        if args.command == 'restart':
            from .restart import restart
            print(json.dumps(restart(c, args.ref, args.slot, args.finished_ref), indent=2))
            return 0
        if args.command == 'watch-pr':
            if args.timeout < 1 or args.interval < 1 or args.number < 1:
                raise ValueError('watch-pr arguments must be positive')
            from .watcher import watch
            print(json.dumps(watch(c, args.number, args.timeout, args.interval)))
            return 0
        if args.command == 'install-service':
            from .service import install
            print(json.dumps(install(c, args.deliver)))
            return 0
        s = snapshot.collect(c)
        if args.command == 'may-start':
            result = snapshot.may_start(c, s)
            print(json.dumps(result))
            return 0 if result['may_start'] else 1
        print(json.dumps(s, indent=2) if args.json else snapshot.render(s))
        return 0 if not s['errors'] else 1
    except (ValueError, OSError, CommandError) as e:
        prefix = 'DRILL ' if getattr(args, 'drill_usage_file', None) else ''
        print(f'{prefix}fleet: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

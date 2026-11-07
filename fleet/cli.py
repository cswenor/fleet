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
    p = sub.add_parser('next', help='propose a ready task and verified slot; never launches')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('launch')
    p.add_argument('ref')
    p.add_argument('--slot')
    p.add_argument('--drill-usage-file', help='test-only additional launch withhold, never bypasses real quota')
    p = sub.add_parser('orca-restart', help='external journaled Orca service restart')
    p.add_argument('--plan', required=True)
    p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('restart')
    p.add_argument('ref')
    p.add_argument('--slot', required=True)
    p.add_argument('--finished-ref', required=True)
    p = sub.add_parser('reconcile', help='Main records positive exit evidence for an exact launch')
    p.add_argument('--slot', required=True)
    p.add_argument('--launch', required=True, dest='launch_id')
    p.add_argument('--evidence', required=True, help='non-secret positive exit evidence; absence is not exit')
    p = sub.add_parser('carry', help='Main approves a prerequisite ref on an exact live launch')
    p.add_argument('--slot', required=True)
    p.add_argument('--launch', required=True, dest='launch_id')
    p.add_argument('--ref', required=True)
    p.add_argument('--evidence', required=True, help='non-secret explicit Main approval')
    p = sub.add_parser('watch-pr')
    p.add_argument('number', type=int)
    p.add_argument('--timeout', type=int, default=14400)
    p.add_argument('--interval', type=int, default=60)
    p = sub.add_parser('wait')
    p.add_argument('--timeout', type=int, default=900, help='checkpoint after 1–1800 seconds')
    p.add_argument('--wake-on', default='all',
                   help='comma-separated question,escalation,urgent,status,all; urgent aliases question; deliveries stay whole')
    p.add_argument('--ack-run', help='acknowledge a processed Run delivery before waiting')
    p.add_argument('--terminal', help='caller identity when ORCA_TERMINAL_HANDLE is unavailable')
    p = sub.add_parser('thread', help='Pad-backed terminal-builder task threads')
    commands = p.add_subparsers(dest='thread_command', required=True)
    t = commands.add_parser('open')
    t.add_argument('ref')
    t.add_argument('--slot')
    t = commands.add_parser('reopen', help='continue a settled attempt with recorded Main authorization')
    t.add_argument('ref')
    t.add_argument('--authorized-by', required=True, help='Main authorization message ID')
    t.add_argument('--reason', default='')
    t = commands.add_parser('question')
    t.add_argument('ref')
    t.add_argument('--body', required=True)
    t.add_argument('--subject')
    t = commands.add_parser('wait')
    t.add_argument('ref')
    t.add_argument('--timeout', type=int, default=900)
    t = commands.add_parser('result')
    t.add_argument('ref')
    t.add_argument('--body', required=True)
    t = commands.add_parser('ready', help='report the next handoff after settling the current attempt')
    t.add_argument('ref', help='next Pad reference')
    t.add_argument('--body', default='')
    p = sub.add_parser('install-service')
    p.add_argument('--deliver', action='store_true', help='enable durable inbox messages')
    p.add_argument('--environment-file', help='private service token file (macOS: literal KEY=value)')
    sub.add_parser('central-serve')
    p = sub.add_parser('central-install-service')
    p.add_argument('--environment-file', required=True, help='private service token file (macOS: literal KEY=value)')
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
        if args.command == 'orca-restart':
            from .orca_restart import conduct
            result = conduct(c, args.plan, args.dry_run)
            print(json.dumps(result, indent=2))
            return 0 if result['event'] == 'complete' or (
                result['event'] == 'dry-run' and not result['blockers']) else 2
        if getattr(args, 'drill_usage_file', None):
            c['_drill_usage_file'] = args.drill_usage_file
        if args.command == 'thread':
            from . import threads
            if args.thread_command == 'open':
                result = threads.open_thread(c, args.ref, args.slot)
            elif args.thread_command == 'reopen':
                result = threads.reopen(c, args.ref, args.authorized_by, args.reason)
            elif args.thread_command == 'question':
                result = threads.question(c, args.ref, args.body, args.subject)
                result = {**result, 'sent': True,
                          'notice': 'Question sent to the configured Run; no separate direct send '
                                    'is needed. Use fleet thread wait for the reply; follow up '
                                    'with reply/status in this same native subthread, not a new question.'}
            elif args.thread_command == 'wait':
                result = threads.wait(c, args.ref, args.timeout)
            elif args.thread_command == 'ready':
                result = threads.ready(c, args.ref, args.body)
            else:
                result = threads.result(c, args.ref, args.body)
            print(json.dumps(result))
            return 3 if result.get('event') == 'timeout' else 0
        if args.command == 'wait':
            from .mailbox import wait
            result = wait(c, args.timeout, args.ack_run, args.terminal, args.wake_on)
            print(json.dumps(result))
            return {'messages': 0, 'timeout': 3, 'error': 2}[result['event']]
        if args.command == 'serve':
            from .supervisor import serve
            serve(c, not args.no_deliver, args.once)
            return 0
        if args.command == 'next':
            from .dispatcher import propose, render
            result = propose(c)
            print(json.dumps(result, indent=2) if args.json else render(result))
            return 0 if result['proposal'] else 1
        if args.command == 'reconcile':
            from .dispatcher import reconcile
            print(json.dumps(reconcile(c, args.slot, args.launch_id, args.evidence), indent=2))
            return 0
        if args.command == 'carry':
            from .dispatcher import carry
            print(json.dumps(carry(c, args.slot, args.launch_id, args.ref, args.evidence), indent=2))
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
            print(json.dumps(install(c, args.deliver, args.environment_file)))
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

import argparse
import json
import sys
from .config import load
from .commands import CommandError
from . import snapshot, state


def main():
    parser = argparse.ArgumentParser(prog='fleet')
    parser.add_argument('--config', default='fleet.yml')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('status', 'may-start'):
        p = sub.add_parser(cmd)
        p.add_argument('--json', action='store_true')
        p.add_argument('--drill-usage-file', help='test-only quota fixture; disables central push')
        p.add_argument('--debug', action='store_true', help='report GitHub request and 304 counts')
    p = sub.add_parser('serve')
    p.add_argument('--once', action='store_true')
    p.add_argument('--no-deliver', action='store_true')
    p.add_argument('--drill-usage-file', help='test-only quota fixture; disables central push')
    p.add_argument('--debug', action='store_true', help='report GitHub request and 304 counts per cycle')
    p = sub.add_parser('next', help='propose a ready task and verified slot; never launches')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('launch')
    p.add_argument('ref')
    p.add_argument('--slot')
    p.add_argument('--drill-usage-file', help='test-only additional launch withhold, never bypasses real quota')
    p = sub.add_parser('orca-restart', help='restart Orca from the initiating Main and verify preservation',
                       description='Journal one restart, preserve terminals/Run mail, and enqueue owner questions. '
                                   'Exit 0 only for verified completion or an unblocked read-only dry-run; otherwise 2.')
    p.add_argument('--plan', required=True, help='absolute path to private version-2 restart plan')
    p.add_argument('--dry-run', action='store_true', help='read-only preflight, exact actions and BEFORE capture')
    p = sub.add_parser('restart')
    p.add_argument('ref')
    p.add_argument('--slot', required=True)
    p.add_argument('--finished-ref', required=True)
    p = sub.add_parser('relaunch', help='Main replaces an idle unfinished session of the same task')
    p.add_argument('ref')
    p.add_argument('--slot', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--keep-uncommitted', action='store_true',
                   help='preserve tracked/staged WIP and identify it in the fresh builder prompt')
    p = sub.add_parser('reconcile', help='Main records positive exit evidence for an exact launch')
    p.add_argument('--slot', required=True)
    p.add_argument('--launch', required=True, dest='launch_id')
    p.add_argument('--evidence', required=True, help='non-secret positive exit evidence; absence is not exit')
    p = sub.add_parser('park-reconcile', help='Main records evidence to resolve an exact active park next cycle')
    p.add_argument('--launch', required=True, dest='launch_id')
    p.add_argument('--evidence', required=True, help='non-secret explicit Main reconciliation evidence')
    p = sub.add_parser('carry', help='Main approves a prerequisite ref on an exact live launch')
    p.add_argument('--slot', required=True)
    p.add_argument('--launch', required=True, dest='launch_id')
    p.add_argument('--ref', required=True)
    p.add_argument('--evidence', required=True, help='non-secret explicit Main approval')
    p = sub.add_parser('watch-pr')
    p.add_argument('number', type=int)
    p.add_argument('--timeout', type=int, default=14400)
    p.add_argument('--interval', type=int, default=60)
    p.add_argument('--terminal', help='caller identity when ORCA_TERMINAL_HANDLE is unavailable')
    p.add_argument('--include-seen', action='store_true', help='re-emit an already returned initial event')
    p.add_argument('--debug', action='store_true', help='report GitHub request and 304 counts per poll')
    p = sub.add_parser('relays', help='read-only stale relay assessment; never consumes mail')
    relay_commands = p.add_subparsers(dest='relay_command', required=True)
    p = relay_commands.add_parser('clear-stale', help='assess a configured lane; cleanup is unavailable')
    p.add_argument('--terminal', required=True)
    p.add_argument('--dry-run', action='store_true', required=True)
    p = sub.add_parser('wait', help='wait for Run mail; recover read-only API outages for up to 30s',
                       description='Exit 0: messages; 3: timeout; 4: runtime recovered, rearm without replaying an ambiguous ack; 2: error/runtime_unavailable. Recovery is bounded to 30s; consuming checks are never retried.')
    p.add_argument('--timeout', type=int, default=900, help='checkpoint after 1–1800 seconds')
    p.add_argument('--wake-on', default='all',
                   help='comma-separated question,escalation,urgent,builder,status,all; builder holds relay-only deliveries until selected mail or timeout; deliveries stay whole')
    p.add_argument('--ack-run', help='acknowledge a processed Run delivery before waiting')
    p.add_argument('--terminal', help='caller identity when ORCA_TERMINAL_HANDLE is unavailable')
    p.add_argument('--interval', type=float, default=3, help='builder peek cadence, 1–30 seconds')
    p.add_argument('--loop', action='store_true', help='rearm internally on timeout/recovery; never repeat an ack')
    p.add_argument('--save', metavar='PATH', help='atomically save the full JSON result with mode 0600')
    p.add_argument('--summary', action='store_true', help='print delivery blocks with full bodies by default instead of JSON')
    p.add_argument('--bodies', action=argparse.BooleanOptionalAction, default=None,
                   help='print every full message body (default on with --summary)')
    p.add_argument('--legacy-history', action='store_true',
                   help='include read-only terminal history in body output (always retained in saved JSON)')
    p.add_argument('--auto-ack', action='store_true',
                   help='ack the previous fully printed/saved delivery on the next invocation')
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
    t = commands.add_parser('report', help='send a typed action request; await Main via thread wait')
    t.add_argument('ref')
    t.add_argument('--kind', required=True,
                   choices=('release_ready', 'review_request', 'ci_failed', 'result'))
    t.add_argument('--pr', type=int, required=True)
    t.add_argument('--head', required=True, help='full commit SHA')
    t.add_argument('text')
    t = commands.add_parser('wait')
    t.add_argument('ref')
    t.add_argument('--timeout', type=int, default=900)
    t = commands.add_parser('result')
    t.add_argument('ref')
    t.add_argument('--body', required=True)
    t = commands.add_parser('ready', help='report the next handoff after settling the current attempt')
    t.add_argument('ref', help='next Pad reference')
    t.add_argument('--body', default='')
    t = commands.add_parser('needs-next', help='request a next ref after completed task settlement')
    t.add_argument('ref')
    t = commands.add_parser('parked', help='settle pushed WIP with a durable Pad resume note')
    t.add_argument('ref')
    t.add_argument('--resume-note', required=True, help='Pad resume comment ID on this task')
    t = commands.add_parser('send', help='Main sends durable steering without terminal input')
    t.add_argument('ref')
    t.add_argument('--body', required=True)
    t.add_argument('--priority', action='store_true', help='refused until safe stock steering is available')
    t = commands.add_parser('check', help='show unacknowledged task steering, one JSON line per message')
    t.add_argument('ref')
    t.add_argument('--ack', help='acknowledge a steering token only after processing it')
    commands.add_parser('pending', help='Main lists unacknowledged and ambiguous steering per lane')
    t = commands.add_parser('stop', help='Main interrupts one proven OMP lane; never blindly retry')
    t.add_argument('ref')
    t.add_argument('--reason', required=True)
    t.add_argument('--timeout', type=int, default=120, help='bounded idle wait, 1–600 seconds')
    p = sub.add_parser('install-service')
    p.add_argument('--deliver', action='store_true', help='enable durable inbox messages')
    p.add_argument('--environment-file', help='private service token file (macOS: literal KEY=value)')
    sub.add_parser('central-serve')
    p = sub.add_parser('central-install-service')
    p.add_argument('--environment-file', required=True, help='private service token file (macOS: literal KEY=value)')
    args = parser.parse_args()
    try:
        if args.command == 'wait' and args.summary and not args.save:
            raise ValueError('--summary requires --save to preserve complete mail payloads')
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
        if getattr(args, 'debug', False):
            c['_github_debug'] = True
        if args.command == 'orca-restart':
            from .orca_restart import conduct
            result = conduct(c, args.plan, args.dry_run)
            print(json.dumps(result, indent=2))
            return 0 if result['event'] == 'complete' or (
                result['event'] == 'dry-run' and not result['blockers']) else 2
        if getattr(args, 'drill_usage_file', None):
            c['_drill_usage_file'] = args.drill_usage_file
        if args.command == 'relays':
            from .relays import clear_stale
            print(json.dumps(clear_stale(c, args.terminal, dry_run=args.dry_run)))
            return 0
        if args.command == 'thread':
            from . import threads
            if args.thread_command == 'stop':
                from .stop import stop
                print(json.dumps(stop(c, args.ref, args.reason, args.timeout)))
                return 0
            if args.thread_command in ('send', 'check', 'pending'):
                from . import steering
                if args.thread_command == 'send':
                    result = steering.send(c, args.ref, args.body, args.priority)
                elif args.thread_command == 'pending':
                    result = steering.pending(c)
                else:
                    result = steering.check(c, args.ref, args.ack)
                    for message in result['messages']:
                        print(json.dumps(message))
                    return 0
                print(json.dumps(result))
                return 0
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
            elif args.thread_command == 'report':
                result = threads.report(c, args.ref, args.kind, args.pr, args.head, args.text)
            elif args.thread_command == 'wait':
                result = threads.wait(c, args.ref, args.timeout)
            elif args.thread_command == 'ready':
                result = threads.ready(c, args.ref, args.body)
            elif args.thread_command == 'needs-next':
                result = threads.needs_next(c, args.ref)
            elif args.thread_command == 'parked':
                from . import parking
                result = parking.parked(c, args.ref, args.resume_note)
            else:
                result = threads.result(c, args.ref, args.body)
            print(json.dumps(result))
            return 3 if result.get('event') == 'timeout' else 0
        if args.command == 'wait':
            from .mailbox import run_wait
            return run_wait(c, args)
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
        if args.command == 'park-reconcile':
            from .governor import reconcile
            print(json.dumps(reconcile(c, args.launch_id, args.evidence), indent=2))
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
        if args.command == 'relaunch':
            from .restart import relaunch
            print(json.dumps(relaunch(c, args.ref, args.slot, args.reason,
                                      keep_uncommitted=args.keep_uncommitted), indent=2))
            return 0
        if args.command == 'watch-pr':
            if args.timeout < 1 or args.interval < 1 or args.number < 1:
                raise ValueError('watch-pr arguments must be positive')
            from .watcher import watch
            print(json.dumps(watch(c, args.number, args.timeout, args.interval,
                                   terminal=args.terminal, include_seen=args.include_seen)))
            return 0
        if args.command == 'install-service':
            from .service import install
            print(json.dumps(install(c, args.deliver, args.environment_file)))
            return 0
        s = snapshot.collect(c)
        if c.get('_github_debug'):
            print(json.dumps({'github_requests': s.get('github_requests', {})}), file=sys.stderr)
        if args.command == 'may-start':
            result = snapshot.may_start(c, s)
            print(json.dumps(result))
            return 0 if result['may_start'] else 1
        print(json.dumps(s, indent=2) if args.json else snapshot.render(s))
        return 0 if not s['errors'] else 1
    except (ValueError, OSError, CommandError) as e:
        if args.command == 'watch-pr':
            print(json.dumps({'pr': args.number, 'event': 'error', 'error': str(e),
                              'endpoint': getattr(e, 'endpoint', None)}))
            return 2
        prefix = 'DRILL ' if getattr(args, 'drill_usage_file', None) else ''
        print(f'{prefix}fleet: {e}', file=sys.stderr)
        return 4 if isinstance(e, state.LauncherBusy) else 2


if __name__ == '__main__':
    sys.exit(main())

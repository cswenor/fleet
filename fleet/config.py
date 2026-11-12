"""Validated, project-local configuration. No provider credentials belong here."""
from pathlib import Path
import os
import math
import re
import shutil
import sys
import yaml

STATUS_DEFAULTS = {'not_started': ['open'], 'in_progress': ['in-progress'],
                   'terminal': ['done', 'cancelled']}

URGENT_EVENTS = ('provider_stalled', 'idle', 'wrong_model', 'stray_worktree',
                 'merge_group_failure', 'pr_stuck', 'stalled', 'main_down')

THINKING_LEVELS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'auto')


def validate_model_policy(builder):
    allowed = builder.setdefault('allowed_models', {})
    if not isinstance(allowed, dict):
        raise ValueError('builder.allowed_models must map provider/model IDs to thinking levels')
    for model, levels in allowed.items():
        if (not isinstance(model, str) or not re.fullmatch(r'[^\s/]+/[^\s/]+', model)
                or not isinstance(levels, list) or not levels
                or any(not isinstance(level, str) or level not in THINKING_LEVELS for level in levels)
                or len(set(levels)) != len(levels)):
            raise ValueError('invalid builder.allowed_models model or thinking levels')
    plans = builder.setdefault('plan_models', {})
    if not isinstance(plans, dict):
        raise ValueError('builder.plan_models must be a mapping')
    for ref, selection in plans.items():
        if not isinstance(ref, str) or not re.fullmatch(r'PLAN-\d+', ref):
            raise ValueError('builder.plan_models keys must be PLAN refs')
        if (not isinstance(selection, dict) or set(selection) != {'model', 'thinking'}
                or not isinstance(selection['model'], str)
                or not isinstance(selection['thinking'], str)
                or not model_allowed(builder, selection['model'], selection['thinking'])):
            raise ValueError(f'invalid or nonallowlisted builder.plan_models entry for {ref}')


def model_allowed(builder, model, thinking):
    return ((model, thinking) == (builder['resolved_model'], builder['thinking'])
            or thinking in builder.get('allowed_models', {}).get(model, []))


def validate_statuses(statuses, *, allow_no_progress=False):
    if not isinstance(statuses, dict) or set(statuses) != set(STATUS_DEFAULTS):
        raise ValueError('pad.statuses requires not_started, in_progress and terminal')
    seen = set()
    for category, names in statuses.items():
        if (not isinstance(names, list) or
                (not names and not (allow_no_progress and category == 'in_progress')) or
                any(not isinstance(n, str) or not n.strip() for n in names)):
            raise ValueError('pad.statuses categories must be non-empty lists of status names')
        if seen.intersection(names) or len(set(names)) != len(names):
            raise ValueError('pad.statuses categories must be disjoint without duplicates')
        seen.update(names)
    return statuses


def load(path):
    path = Path(path).expanduser().resolve()
    c = yaml.safe_load(path.read_text())
    if not isinstance(c, dict) or c.get('version') != 1:
        raise ValueError('fleet.yml requires version: 1')
    for key in ('repo', 'root', 'orca_repo_id', 'pad_workspace', 'builder', 'coordinator'):
        if not c.get(key):
            raise ValueError(f'missing config: {key}')
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', c['repo']):
        raise ValueError('repo must be owner/name')
    c['root'] = str(Path(c['root']).expanduser().resolve())
    if not Path(c['root']).is_dir():
        raise ValueError('root must be an existing checkout')
    pad = c.setdefault('pad', {})
    if not isinstance(pad, dict):
        raise ValueError('pad must be a mapping')
    if 'statuses' in pad:
        validate_statuses(pad['statuses'])
    b = c['builder']
    for key in ('model', 'resolved_model', 'rules_file', 'slots'):
        if not b.get(key):
            raise ValueError(f'missing builder.{key}')
    if '/' not in b['resolved_model']:
        raise ValueError('builder.resolved_model must include provider/model')
    b.setdefault('thinking', 'medium')
    b.setdefault('max', len(b['slots']))
    b.setdefault('orca_status_extension', os.environ.get('ORCA_OMP_STATUS_EXTENSION'))
    extension = b['orca_status_extension']
    if extension is not None and (not isinstance(extension, str) or not Path(extension).is_absolute()):
        raise ValueError('builder.orca_status_extension must be an absolute path or null')
    b.setdefault('footer',
        'Run anything that can exceed a few minutes (test suites, evals, profilers, CI waits) as an async background job with an explicit timeout sized to the expected runtime plus margin, and process its completion when it wakes you. Never block your turn in a single foreground wait longer than ~10 minutes. On a background timeout or no progress, inspect (output size, stack/py-spy, logs) before re-running; a timeout is evidence, not a retry signal. Background jobs are exempt from the stalled alert, so the explicit timeout is the hang detector. '
        'This session is already fresh for the assigned task. Do not signal FRESH-READY for an inherited handoff or boundary. Pick the next piece by plan priority, usually the same feature; the same ref is allowed. Never switch to an unrelated task just to get a different ref. Only after publishing your NEXT piece of work (merged after your launch) and writing its concrete next-piece handoff after that merge, settle the primary thread with fleet --config CONFIG thread result REF, then make fleet --config CONFIG thread ready NEXT [--body TEXT] your last lane action and stop. Same-ref readiness requires the newer merge and later concrete handoff on that ref; Pad done alone is insufficient. Never raw-send FRESH-READY. Ready sends a native question for Main to rule on launch in-thread; stop after sending without waiting for the reply. Never resend an existing ready receipt. Never resume context or silently switch provider/model. While you own an open PR, MUST keep fleet watch-pr PR armed as an async background command; process each event and re-arm after every completion (including timeout/error) until merged or closed. Never rely on retired pr-wait.sh scripts or single-workflow gh run watch alone; never poll with model turns. Decision requests use EXACTLY ONE channel: fleet thread question. It sends the native type question to run:<coordinator.run> itself; never also send a direct orca send/ask/question for the same decision. Follow-ups use reply/status in the same pending native subthread, not a new direct question. Ordinary progress/PARK reports may still use raw native send with --type status. If Main authorizes continuing after thread result, run fleet --config CONFIG thread reopen REF --authorized-by MSG_ID before any new question; never fall back to direct Run questions. Use the same config as thread open. '
        'After reporting PARKED or successfully sending thread ready, end your turn with a final message and do nothing else: no further tool calls, no background jobs, and never a job that waits on your own terminal/idle state or sends input to your own terminal. Main (or the owner) retires the idle session via the documented retirement path (fleet restart for eligible finished work, or manual idle-proof /exit + fleet reconcile).')
    b.setdefault('idle_seconds', 2700)
    b.setdefault('alert_recovery_cycles', 2)
    if type(b['alert_recovery_cycles']) is not int or b['alert_recovery_cycles'] < 1:
        raise ValueError('builder.alert_recovery_cycles must be a positive integer')
    for key, default in (('ready_report_minutes', 15), ('stall_minutes', 60),
                         ('stall_after_ci_minutes', 20)):
        b.setdefault(key, default)
        minutes = b[key]
        if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
            raise ValueError(f'builder.{key} must be a positive finite number')
    if b['thinking'] not in THINKING_LEVELS:
        raise ValueError('invalid builder.thinking')
    validate_model_policy(b)
    if not isinstance(b['max'], int) or b['max'] < 1:
        raise ValueError('builder.max must be positive')
    if not isinstance(b['slots'], list) or not all(isinstance(s, str) for s in b['slots']):
        raise ValueError('builder.slots must be paths')
    b['slots'] = [str(Path(s).expanduser().resolve()) for s in b['slots']]
    q = c.setdefault('quota', {})
    q['provider'] = b['resolved_model'].split('/')[0]
    q.setdefault('reserve_percent', 10)
    q.setdefault('max_age_seconds', 900)
    q.setdefault('park_timeout_seconds', 1800)
    q.setdefault('unknown_alert_cycles', 3)
    if type(q['unknown_alert_cycles']) is not int or q['unknown_alert_cycles'] < 1:
        raise ValueError('quota.unknown_alert_cycles must be a positive integer')
    if not isinstance(q['park_timeout_seconds'], (int, float)) or not 0 < q['park_timeout_seconds'] <= 86400:
        raise ValueError('quota.park_timeout_seconds must be positive and at most 86400')
    if not 0 <= q['reserve_percent'] <= 100 or q['max_age_seconds'] <= 0:
        raise ValueError('invalid quota thresholds')
    d = c.setdefault('disk', {})
    d.setdefault('path', c['root'])
    d.setdefault('min_free_gb', 10)
    if d['min_free_gb'] < 0:
        raise ValueError('disk.min_free_gb must be nonnegative')
    s = c.setdefault('status', {})
    s.setdefault('url', '')
    s.setdefault('token_env', '')
    s.setdefault('push_seconds', 300)
    c.setdefault('interval_seconds', 60)
    if c['interval_seconds'] < 1 or s['push_seconds'] < 1:
        raise ValueError('intervals must be positive')
    github = c.setdefault('github', {})
    if not isinstance(github, dict):
        raise ValueError('github must be a mapping')
    github.setdefault('parked_labels', ['parked'])
    if (not isinstance(github['parked_labels'], list) or
            any(not isinstance(label, str) or not label.strip() for label in github['parked_labels'])):
        raise ValueError('github.parked_labels must be a list of nonempty label names')
    publication = c.setdefault('publication', {})
    if (not isinstance(publication, dict) or
            set(publication) - {'mode', 'review_app', 'hold_label', 'merge_method'}):
        raise ValueError('publication must map mode, review_app, hold_label and merge_method')
    publication.setdefault('mode', 'manual')
    publication.setdefault('review_app', '')
    publication.setdefault('hold_label', 'hold')
    publication.setdefault('merge_method', 'squash')
    if publication['mode'] not in ('manual', 'auto_merge'):
        raise ValueError('publication.mode must be manual or auto_merge')
    app = publication['review_app']
    if (not isinstance(app, str) or
            (app and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', app)) or
            (publication['mode'] == 'auto_merge' and not app)):
        raise ValueError('publication.review_app must be a GitHub App slug, required in auto_merge')
    label = publication['hold_label']
    if not isinstance(label, str) or not label.strip():
        raise ValueError('publication.hold_label must be a nonempty label name')
    if publication['merge_method'] not in ('merge', 'squash', 'rebase'):
        raise ValueError('publication.merge_method must be merge, squash or rebase')
    urgent = c.setdefault('urgent', {})
    if not isinstance(urgent, dict):
        raise ValueError('urgent must be a mapping')
    urgent.setdefault('events', list(URGENT_EVENTS))
    urgent.setdefault('pr_stuck_minutes', 45)
    events = urgent['events']
    if (not isinstance(events, list) or
            any(not isinstance(event, str) or event not in URGENT_EVENTS for event in events) or
            len(set(events)) != len(events)):
        raise ValueError('urgent.events must be a list of distinct supported event names')
    minutes = urgent['pr_stuck_minutes']
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('urgent.pr_stuck_minutes must be a positive finite number')
    for key in ('terminal', 'run'):
        if not c['coordinator'].get(key):
            raise ValueError(f'missing coordinator.{key}')
    coordinator = c['coordinator']
    coordinator.setdefault('expected_worktree_globs', [])
    patterns = coordinator['expected_worktree_globs']
    if (not isinstance(patterns, list) or
            any(not isinstance(pattern, str) or not pattern.strip() or
                not Path(pattern).is_absolute() for pattern in patterns)):
        raise ValueError('coordinator.expected_worktree_globs must be a list of nonempty absolute path globs')
    coordinator.setdefault('liveness_minutes', 5)
    coordinator.setdefault('unknown_alert_cycles', 3)
    coordinator.setdefault('wake', True)
    coordinator.setdefault('builder_wake', True)
    coordinator.setdefault('wake_after_seconds', 120)
    coordinator.setdefault('wake_cooldown_seconds', 600)
    coordinator.setdefault('unwakeable_after_seconds', 900)
    if type(coordinator['wake']) is not bool:
        raise ValueError('coordinator.wake must be a boolean')
    if type(coordinator['builder_wake']) is not bool:
        raise ValueError('coordinator.builder_wake must be a boolean')
    for key in ('wake_after_seconds', 'wake_cooldown_seconds', 'unwakeable_after_seconds'):
        value = coordinator[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'coordinator.{key} must be a positive finite number')
    if type(coordinator['unknown_alert_cycles']) is not int or coordinator['unknown_alert_cycles'] < 1:
        raise ValueError('coordinator.unknown_alert_cycles must be a positive integer')
    minutes = coordinator['liveness_minutes']
    if type(minutes) not in (int, float) or not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('coordinator.liveness_minutes must be a positive finite number')
    escalation = coordinator.get('escalation_run')
    if escalation is not None and (not isinstance(escalation, str) or
                                   not re.fullmatch(r'run_[A-Za-z0-9_-]+', escalation)):
        raise ValueError('coordinator.escalation_run must be a Run ID or null')
    c.setdefault('hooks', {})
    for name, argv in c['hooks'].items():
        if name not in ('before_launch', 'after_launch', 'event') or not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError('hooks must be argv lists: before_launch, after_launch, event')
    c['orca'] = (c.get('orca') or os.environ.get('ORCA_CLI_COMMAND') or
                 shutil.which('orca' if sys.platform == 'darwin' else 'orca-ide'))
    if not c['orca']:
        raise ValueError('set orca to the absolute Orca CLI path')
    c['_path'] = str(path)
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    c['_state'] = str(base / 'fleet' / c['repo'].replace('/', '--'))
    return c

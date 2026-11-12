"""Fail-closed publication admission and Builder-App-only native arming."""
from datetime import datetime
import os
import re
import subprocess

from .review_bot_api import APIError, GitHub, app_client
from .review_bot_apps import verify_app, verify_installation
from .review_bot_ruleset import verify_live_ruleset
from .github import _queued
from .commands import CommandError


# Match the Review App's merge-gate semantics, including quoted/nested items,
# continuation lines and gates hidden in Markdown comments or code blocks.
_GATE = re.compile(
    r'^[ \t]*(?:(?:>|[-*+][ \t]|\d{1,9}[.)][ \t])[ \t]*)*'
    r'(?:[-*+]|\d{1,9}[.)])[ \t]+\[([ xX])\]'
    r'(?:[ \t]+|[ \t]*\r?\n[ \t>]*)gate:([^\r\n]*)', re.I | re.M)


def guard(c):
    """Obtain fresh operator-visible protection, never a cached authorization."""
    if c.get('publication', {}).get('mode') != 'auto_merge':
        return {'required': False, 'verified': True}
    try:
        receipt = verify_live_ruleset(c, require_publication=True)
        if not isinstance(receipt, dict) or receipt.get('verified') is not True:
            raise ValueError('unverified protection')
        return {**receipt, 'required': True}
    except ValueError:
        return {'required': True, 'verified': False,
                'reason': 'auto-merge blocked: live ruleset is unverified; run review-bot ruleset --dry-run with operator auth and bypass visibility'}


def _pull(client, c, pr):
    pull = client.request('GET', f'/repos/{c["repo"]}/pulls/{pr}')
    if (not isinstance(pull, dict) or pull.get('number') != pr
            or pull.get('base', {}).get('repo', {}).get('full_name', '').lower() != c['repo'].lower()
            or pull.get('head', {}).get('repo', {}).get('full_name', '').lower() != c['repo'].lower()
            or pull.get('base', {}).get('ref') != 'main'):
        raise ValueError('arming withheld: live PR repository or main base is unavailable or mismatched')
    return pull


def _eligible(client, c, pr, head):
    pull = _pull(client, c, pr)
    if (pull.get('state') != 'open' or pull.get('merged') is not False
            or pull.get('draft') is not False or pull.get('head', {}).get('sha') != head):
        raise ValueError('arming withheld: PR must be open, non-draft and at the exact requested head')
    labels = pull.get('labels')
    if (not isinstance(labels, list)
            or any(not isinstance(label, dict) or not isinstance(label.get('name'), str) for label in labels)
            or any(label['name'].lower() == 'hold' for label in labels)):
        raise ValueError('arming withheld: hold label present or label visibility unavailable')
    body = pull.get('body')
    if 'body' not in pull or (body is not None and not isinstance(body, str)):
        raise ValueError('arming withheld: PR Gate visibility unavailable')
    if any(match[1] == ' ' for match in _GATE.finditer(body or '')):
        raise ValueError('arming withheld: PR has an unchecked Gate')
    # Per-author effective human review, not the globally latest review. Comments
    # and pending reviews do not withdraw an earlier request for changes.
    effective = {}
    for page in range(1, 101):
        reviews = client.request('GET', f'/repos/{c["repo"]}/pulls/{pr}/reviews?per_page=100&page={page}')
        if not isinstance(reviews, list):
            raise ValueError('arming withheld: human review visibility unavailable')
        for review in reviews:
            if not isinstance(review, dict) or not isinstance(review.get('user'), dict):
                raise ValueError('arming withheld: human review visibility unavailable')
            user = review['user']
            if user.get('type') == 'Bot':
                continue
            if user.get('type') != 'User' or type(user.get('id')) is not int:
                raise ValueError('arming withheld: human review identity unavailable')
            status = review.get('state')
            if status not in ('APPROVED', 'DISMISSED', 'CHANGES_REQUESTED', 'COMMENTED', 'PENDING'):
                raise ValueError('arming withheld: human review state unavailable')
            if status in ('APPROVED', 'DISMISSED', 'CHANGES_REQUESTED'):
                try:
                    submitted = datetime.fromisoformat(review['submitted_at'].replace('Z', '+00:00')).timestamp()
                    review_id = review['id']
                    if type(review_id) is not int:
                        raise ValueError()
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    raise ValueError('arming withheld: human review ordering unavailable') from None
                order = (submitted, review_id)
                prior = effective.get(user['id'])
                if prior is None or order > prior[0]:
                    effective[user['id']] = (order, status)
        if len(reviews) < 100:
            break
    else:
        raise ValueError('arming withheld: human review visibility incomplete')
    if any(status == 'CHANGES_REQUESTED' for _, status in effective.values()):
        raise ValueError('arming withheld: human changes-requested veto is active')

def _observed(c, pr, head, pull):
    queue = None
    if pull.get('state') == 'open' and pull.get('merged') is not True:
        try:
            entry = _queued(c, pr, details=True)
            if entry is not None and (not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']):
                raise ValueError('invalid queue entry')
            queue = entry is not None
        except (CommandError, ValueError, KeyError, TypeError):
            pass
    auto = pull.get('auto_merge')
    enabled_by = auto.get('enabled_by') if isinstance(auto, dict) else None
    actor = enabled_by.get('login') if isinstance(enabled_by, dict) else None
    if actor is not None and (not isinstance(actor, str) or not re.fullmatch(r'[A-Za-z0-9_\[\]-]+', actor)):
        actor = None
    state = ('merged' if pull.get('merged') is True else
             'closed' if pull.get('state') == 'closed' else
             'queued' if queue is True else 'armed' if isinstance(auto, dict) else
             'not_armed' if queue is False and auto is None and 'auto_merge' in pull else 'unknown')
    return {'operation': 'arm', 'repository': c['repo'], 'pr': pr, 'head': head,
            'observed_head': pull.get('head', {}).get('sha'),
            'state': state, 'arming_actor': actor, 'merge_queue': queue}


def arm(c, pr, head):
    """Reverify protection and live PR, then arm using a separate Builder token.

    Independent PASS / exact-head Main RELEASE remain builder/Main governance,
    not fabricated receipts. Pending CI and App approval are enforced by GitHub.
    """
    if type(pr) is not int or pr < 1:
        raise ValueError('PR must be a positive integer')
    if not isinstance(head, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', head):
        raise ValueError('head must be a full 40-hex commit SHA')
    head = head.lower()
    policy = c.get('publication', {})
    if policy.get('mode') != 'auto_merge':
        raise ValueError('arming requires explicit publication.mode=auto_merge')
    method = policy.get('merge_method', 'squash')
    if method not in ('merge', 'squash', 'rebase'):
        raise ValueError('invalid publication merge method')
    owner = GitHub.owner()
    protection = verify_live_ruleset(c, owner, require_publication=True)
    if not isinstance(protection, dict) or protection.get('verified') is not True:
        raise ValueError('arming withheld: live protection unverified')
    builder = app_client(c['review_bot']['apps']['builder'], 'builder')
    verify_app(c, 'builder', builder)
    installation = verify_installation(c, 'builder', builder)
    issued = builder.request('POST', f'/app/installations/{installation}/access_tokens',
                             {'repositories': [c['repo'].split('/')[1]],
                              'permissions': {'contents': 'write', 'pull_requests': 'write'}})
    token = issued.get('token') if isinstance(issued, dict) else None
    if not isinstance(token, str) or not token or any(char.isspace() for char in token):
        raise ValueError('Builder installation token unavailable')
    scoped = GitHub(token)
    observed = None
    try:
        # Authentication/installation checks can take time; protection must still
        # be authoritative immediately before arming. GitHub's native rules
        # remain the merge authority; the gh head precondition is not a lock.
        _eligible(owner, c, pr, head)
        protection = verify_live_ruleset(c, owner, require_publication=True)
        if not isinstance(protection, dict) or protection.get('verified') is not True:
            raise ValueError('arming withheld: live protection unverified')
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
               if key in os.environ}
        env.update(GH_TOKEN=token, GH_HOST='github.com', GH_PROMPT_DISABLED='1')
        failed = False
        try:
            result = subprocess.run(
                ['gh', 'pr', 'merge', str(pr), '--repo', c['repo'], '--auto',
                 '--match-head-commit', head, '--' + method], cwd=c['root'], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=60, check=False)
            failed = result.returncode != 0
        except (OSError, subprocess.SubprocessError):
            failed = True
        observed = _observed(c, pr, head, _pull(owner, c, pr))
        observed['command_succeeded'] = not failed
        if observed['observed_head'] != head:
            observed['blockers'] = ['PR head changed during arming; inspect native auto-merge and queue state before any retry or push']
        elif failed and observed['state'] not in ('merged', 'closed'):
            observed['blockers'] = ['Builder auto-merge command failed or timed out; observed native state is not proof this command succeeded; inspect before retrying']
        elif observed['state'] not in ('armed', 'queued', 'merged', 'closed'):
            observed['blockers'] = ['GitHub did not confirm auto-merge arming; inspect native PR state before retrying']
        if observed['state'] not in ('merged', 'closed') and observed['merge_queue'] is None:
            observed.setdefault('blockers', []).append('Native queue visibility unavailable; inspect before retrying or pushing')
        return observed
    finally:
        try:
            scoped.request('DELETE', '/installation/token')
        except APIError:
            if observed is None:
                raise ValueError('Builder token revocation unconfirmed; inspect native PR state before retrying') from None
            observed.setdefault('blockers', []).append(
                'Builder token revocation unconfirmed; inspect native PR state and credential cleanup before retrying')

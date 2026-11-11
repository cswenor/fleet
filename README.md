# Fleet

Per-project Orca + omp fleet supervision. Generic project configuration; no model
calls in collection or event diffing. Requires Python 3.11+, PyYAML 6, `gh`, `omp`,
and a running Orca runtime with its CLI.

## Fleet's own project

Fleet dogfoods this CLI: committed `fleet.yml`, `.pad.toml` (`fleet` workspace),
and `.omp/config.yml`/`.omp/AGENTS.md` define the installation and agent policy.
Start with [docs/MAIN.md](docs/MAIN.md); [docs/ROADMAP.md](docs/ROADMAP.md) is the
single source of truth for all remaining PLAN-2685 work and acceptance.
[docs/orca-feature-map.md](docs/orca-feature-map.md) records built-ins, live facts
and limitations. Fleet Pad owns PLAN-9 and process lessons DOC-19; elsewhere's
PLAN-2685 remains historical. Main coordinates, builders use `fleet launch`;
fresh independent read-only unadvised Sol review then CI are the two PR gates.
The committed host binding has no credentials; private supervisor config is
`~/.config/fleet/fleet.yml`, token in a mode-0600 EnvironmentFile.

## Adopting fleet in a project

Copy [fleet.example.yml](fleet.example.yml) and adopt
[the decision-rights template](templates/AGENTS.decision-rights.md) in the project's
AGENTS instructions. Fill its project, Main Run, approval-record, reviewer, CI and
dispatch bindings; fleet's filled example is not another project's configuration.
Point `builder.rules_file` at those instructions. Keep project-specific policy in
AGENTS/config, not fleet code. Builders own task-internal implementation, Main
owns scope/public contracts, and the owner retains budget/reset/rollout authority.
Record owner approval before saved-reset spend; self-merge only after independent
review and green final-head CI. No review bot or automatic dispatch is assumed.

Install the generic [Fleet workflow skill](.omp/skills/fleet/SKILL.md) and stock
review overlay into each adopted project. The skill contains the complete
onboarding checklist, Main/builder workflow, publication gates, deployment policy
and restart recovery; it uses project bindings rather than fleet's host names.

## Install and inspect

```sh
pipx install .
# Inspect existing target files first; preserve project-local customization.
mkdir -p /path/to/project/.omp/skills/fleet
cp .omp/skills/fleet/SKILL.md /path/to/project/.omp/skills/fleet/SKILL.md
cp .omp/unadvised-review.yml /path/to/project/.omp/unadvised-review.yml
cp -n fleet.example.yml /path/to/project/fleet.yml
# Fill project policy, private config/env, Pad workspace, Run and configured slots.
cd /path/to/project
fleet status
fleet status --json
fleet may-start --json
```

Without pipx, use a virtual environment and `pip install .`. On Debian install
`python3-venv` first. To run from source with system PyYAML:
`python3 -m fleet.cli --config /absolute/path/fleet.yml status`.

Run the copies from the reviewed Fleet source checkout. On update, repeat only
the skill/overlay copies after inspecting local differences; never overwrite
configured `fleet.yml`, project instructions or private settings. Commit both
copied files in the target project. Copies pin policy to the adopted version;
no moving symlinks, installer code or public skill registry are required.
`omp skill` manages registry packages, `omp plugin` manages plugins, and
`orca skills install/update` manages bundled Orca skills; none is needed for this
plain project-local skill plus separate config overlay.

Registry publication is planned but unavailable until the owner obtains Stencil
registry access (TASK-43). Version metadata `0.1.0` does not mean a package has
been published. Use the copy procedure above; no registry scope or install spec
is assigned yet. Do not publish to a registry without owner approval.
Keep the copied skill and overlay byte-identical. If the target formatter would
rewrite them, exclude just these vendored files using its existing vendor-ignore
convention; continue checking all project-authored policy and configuration.

Prove stock discovery with a **fresh** omp session inside the target checkout:
ask how a Fleet builder requests a decision and publishes safely, without naming
the skill. Retain the discovered skill read and answer. Configure effective reset
auto-redemption to `no` for both providers before sessions start (see the skill);
existing running sessions may retain old settings. Then complete the private
config, Run binding, supervisor and actual delivery checks in the skill.
Installing policy files does not start services, bind a builder Run, authorize
rollout, or prove central ingestion.

`fleet.yml` version 1 is illustrated in `fleet.example.yml`. `builder.model` is
the launch selector, including `@role` if desired; `resolved_model` is the exact
provider/model expected at runtime. `thinking` is explicit and defaults to medium.
Slots are absolute checkout paths. The main checkout is not a stray lane.
Only unique normalized PR branch → Orca worktree → connected omp terminal mappings
have owners. Ambiguous mappings never select the first terminal.

Status contains last-24h PR timestamps/time-to-merge, default-branch commits,
merge queue/recent merge-group failures, builder models/state, quota, and disk.
Unknown collectors are explicit errors, not zero activity. Model detection uses
Orca provider projection when present, otherwise the OMP footer from the current
rendered screen, not capped output history. Unreadable current evidence stays
unknown; launch configuration or last-restart history never proves the current
model, thinking level, or model pinning after a restart.

Quota aggregates selected-provider account capacity by shared window, excluding
tier-specific limits. Each window must remain above the reserve percentage.
Missing/stale usage fails closed. Account identities, provider metadata and
credentials are never persisted in the snapshot. `may-start` exits 1 when quota,
disk, builder cap, or incomplete collection prevents a launch.
Usage is evaluated against the clock **after** `omp usage` completes, not the
snapshot's start time: a report fetched during collection is not future-dated.
An unknown cached result triggers one provider-scoped `omp usage invalidate`
and one re-read, each bounded by the command timeout (60 seconds). If still
unknown/erroring, admission stays closed; known reserve results never retry.
Invalidation refreshes reports only; it does not spend saved resets.

With **known** usage at/below reserve, `serve` requests cooperative PARK of one
lowest-priority lane (Pad critical > high > medium > low > unresolvable).
Ties park the most recently launched lane; missing matching timestamps withhold
only unresolved ties and alert Main. `launches.json`, keyed by absolute slot,
records launch ID/time/ref/worktree/handle/incarnation and a unique private
`session_dir`; this is launch evidence, not proof of model resolution or exit.
Unknown/stale usage withholds starts immediately but **never parks**. Main is
alerted after `quota.unknown_alert_cycles` consecutive unknown supervisor cycles
(positive integer, default 3); known usage resets the persisted streak. Quota
collection errors use the same debounce; other collection/health errors and
known reserve alerts remain immediate.
Launches whose current canonical task-thread attempt is already settled are
excluded from PARK selection; a reopened unsettled continuation is eligible again.
The owner-builder inbox, coordinator Run and Pad task receive the request.
The builder finishes its step, commits/pushes WIP, records a Pad resume note and
reports via `thread parked REF --resume-note COMMENT_ID`, then ends its turn:
no further tool calls, no background jobs, and never a job that waits on its own
terminal/idle state or sends input to its own terminal. Main (or the owner)
retires the idle session via the [documented retirement path](docs/MAIN.md#guarded-retirement):
`fleet restart` for eligible finished work, or manual idle-proof `/exit` +
`fleet reconcile`. Fleet never injects exit/interrupt/kill input for PARK.
An exact connected terminal at a verified shell prompt permits re-evaluation;
absence/disconnection never does. `quota.park_timeout_seconds` (default 1800,
maximum 86400) escalates once without authorizing another park.
Builder delivery is bound to the selected launch record and terminal incarnation;
changed/unverifiable identity alerts Main and blocks further parking. Final
live identity/readiness validation and enqueue hold the launcher lock to prevent
Fleet reuse races. Run/Pad copies retry independently even after builder exit.
Requests and recipient receipts survive supervisor restarts and dedupe within
an episode; recovery reports parked/outstanding tasks. Relaunch is always
Main-confirmed and fresh. Terminal-only inboxes have Orca's lifetime limitation;
the Run/Pad copies preserve the request after terminal closure. Send is enqueue,
not proof the builder woke or read it. Saved resets require an explicit owner
Pad DECISION **before** spending; Fleet invokes no reset/spend command.

An obsolete active park becomes `superseded` only when `launches.json` records a
newer launch in the same slot and `retirements.json` proves the exact parked
launch/terminal/incarnation reached a verified shell before that replacement.
Changed/unknown identity alone still blocks. Each new reserve episode blocked
by an older unresolved park emits one ordinary status relay.

Main can queue explicit non-secret reconciliation evidence without stopping the
supervisor:

```sh
fleet --config /private/project/fleet.yml park-reconcile --launch EXACT_LAUNCH_ID \
  --evidence 'exact old attempt retired; supporting non-secret evidence'
```

Only the configured Main terminal and exact active park launch ID are accepted;
empty evidence refuses. The CLI returns a receipt ID and states that it takes
effect on the next supervisor cycle. Requests are append-only in private
`park-reconciliations.json`; retries for the same park return the same receipt.
The supervisor records `consumed` or `ignored` under
`governor.reconciliations[receipt_id]` in `supervisor.json`, never deletes requests,
and reports each outcome once. A stale receipt never clears a later park, even
for the same launch. Resolved requests move from `active` to `parked`: status
`parked` means verified shell exit, `superseded` verified guarded replacement,
and `reconciled` explicit Main evidence. This command neither stops a builder
nor reconciles launch-slot admission; `fleet reconcile` remains separate.

Builder launch overlays and the stock `.omp/unadvised-review.yml` review overlay
pin `codexResets.autoRedeem: 'no'` and `claudeResets.autoRedeem: 'no'`, preventing
native automatic spending even when inherited user settings allow it.
An explicitly approved manual spend remains a separate owner-authorized action.

Test/drill only: `status`, `may-start`, `serve`, and `launch` accept
`--drill-usage-file PATH` containing the `omp usage --json` shape. The flag is
never loaded from config/environment. Drill delivery bodies/subjects start
`DRILL`, central push is disabled, and launch must pass **both real usage and
the fixture** (a fixture cannot permit a real-quota refusal). Use an isolated
`XDG_STATE_HOME`, config, scratch Run and owned scratch terminals: a drill still
sends real inbox/Pad messages. Never point a drill at live builders.

## Development

```sh
python3 -m unittest discover -s tests -v
```

## Supervisor and launch

```sh
fleet serve --once --no-deliver  # collect/baseline without inbox delivery
fleet serve                    # foreground, durable inbox relay enabled
fleet install-service          # starts user service, delivery OFF
fleet install-service --deliver
fleet next --json              # proposal only; Main confirms every launch
fleet launch TASK-123 --slot /path/to/slot-a
fleet restart TASK-124 --slot /path/to/slot-a --finished-ref TASK-123
fleet watch-pr 42               # run with the agent's async shell facility
```

From source, `python3 -m fleet.cli --config /absolute/project/fleet.yml
install-service` also installs `~/.local/bin/fleet` pointing at this checkout.
Keep that checkout at a stable path and install system PyYAML first. Service name:
`fleet-owner-project.service`. Inspect with `systemctl --user status` and
`journalctl --user -u fleet-owner-project.service`. Reinstalling applies changed
arguments by restarting only this supervisor, never its builders.
Installation fails unless the user service is active; a successful unit-file
write or `enable --now` alone is not startup proof.
The service survives terminal exit; host reboot requires a user manager at boot
(ask the host administrator to enable lingering if necessary).

### macOS host (TASK-36)

macOS installation and actual Mac/Orca runtime acceptance are complete; see
[TASK-36 runtime receipts](docs/ROADMAP.md#task-36--macos-host-support) and the
[platform audit](docs/macos-audit.md). Runner tests or these scratch receipts
are not rollout approval. Linux keeps its systemd backend.

Use Python >=3.11, PyYAML, `omp`, authenticated `gh`/`pad`, and the installed
Orca CLI. Set an absolute `orca:` config path or `ORCA_CLI_COMMAND` when it is
not on PATH. Automatic discovery uses `orca` on macOS, `orca-ide` on Linux
(never Linux's bare `orca` screen reader). Preserve these executables in the
installing shell's PATH; launchd inherits only that PATH, not caller tokens.
Use actual Mac checkout/lane paths, not the committed Linux host binding.
Set reusable zsh lanes to a supported explicit prompt, e.g.
`PROMPT='%n@%m:%~%% '`. Themes with prompt hooks may overwrite this assignment;
an owned scratch lane can use `exec zsh -f` before setting it, without changing
user dotfiles. Unknown/custom prompts remain occupied, never guessed idle.

```sh
fleet --config /absolute/project/fleet.yml install-service \
  --environment-file /absolute/private/project.env
# Add --deliver only after Main's real receiving wait is proven.
launchctl print gui/$(id -u)/fleet-owner-project
# Stop only this approved supervisor (not Orca or any builders):
launchctl bootout gui/$(id -u)/fleet-owner-project
```

Installation writes `~/Library/LaunchAgents/fleet-owner-project.plist`, uses
stock `launchctl bootstrap`, and requires `state = running` plus a positive PID.
Reinstall bootouts only that label, waits up to ten seconds for its registration
to disappear, then bootstraps its replacement; timeout refuses replacement.
No system daemon, sudo, or login domain fallback. A logged-in GUI user domain is required. The LaunchAgent starts
at user login, not before login. `launchctl print` may disclose environment
values: inspect privately, never paste its raw output. `central-install-service`
uses `fleet-central` and additionally retains HTTP readiness.

The optional supervisor `--environment-file` and required central file contain
literal `KEY=value` lines, blank lines or full-line `#` comments. Matching outer
single/double quotes are removed; no escaping, inline comments, multiline values,
shell expansion or execution. Unquoted values cannot contain whitespace/quotes.
For example, `$HOME` remains literal. The macOS loader checks an owned regular
file, mode **exactly 0600**, with no final symlink at install and every startup;
malformed lines report only their number. Plists contain the file path, never
token values. Use the same literal subset in Linux systemd EnvironmentFiles;
existing Linux units/drop-ins and default installation output are unchanged.
Keep token files outside Git. State still uses `XDG_STATE_HOME` / `~/.local/state`;
no implicit migration to Library paths.

Automatic `fleet restart` on macOS **refuses**: stock ps/lsof metadata has not
proved equivalent exact argv/foreground/terminal-routing ownership. Main must
positively verify manual retirement and reconcile the exact receipt; no forced
exit or retry. `python3 -m fleet.orca_capture` saves a private **partial** snapshot
with native Orca metadata and allowlisted LaunchAgent state, null boot ID and
explicit process/boot evidence errors (exit 1). It neither crashes on missing
procfs nor claims absent processes exited. It is not a Mac restart GO.

Each host adoption still requires owner-approved actual Mac proof: service
active/reinstall, status/may-start, fresh launch with actual Sol-medium events,
safe restart refusal/manual reconciliation, partial capture and real wait delivery.


One service per project, private state under
`$XDG_STATE_HOME/fleet/owner--project` (default `~/.local/state`). Atomic snapshots,
pending events and successful per-recipient delivery receipts survive process
restarts. Supervisor/launcher locks prevent duplicate local instances. Delivery
is at-least-once across a crash between Orca accepting a send and the local
receipt write; no exactly-once guarantee. Read-only runs retain pending events
until delivery is enabled. On first sighting, DIRTY, failing checks, and merged/
closed PRs notify immediately; healthy initial state stays quiet. This includes
terminal PRs in the recent 24h history. Subsequent identical snapshots are quiet.
Alerts dedupe unchanged content until recovery; content changes and recurrence
alert again. Consecutive identical GitHub relay content dedupes per transition
stream (A→B→A still notifies); failed recipient deliveries remain pending.
Failure reports contain recipient plus sanitized Orca code, never error bodies.
An ambiguous/missing owner gets a coordinator-only report; unavailable
inventory retains owner delivery for a later successful lookup. Health and
collector errors never restart or stop lanes.

Urgent supervisor relays use native type `question` and subject prefix
`[urgent] `; Orca has no native `urgent` type. `urgent.events` defaults to eight
kinds: `provider_stalled`, `idle`, `wrong_model`, `stray_worktree`,
`merge_group_failure`, `pr_stuck`, `stalled`, `main_down`; set it to `[]` to disable urgent upgrades.
Other relays remain `status`. `urgent.pr_stuck_minutes` is a positive finite
number, default 45. The persisted PR timer starts on first observation and resets
on state, head, merge-state, checks or review progress, not comments or
`updated_at`. Draft PRs and PRs carrying any `github.parked_labels` name
(case-insensitive, default `["parked"]`) produce no stuck alerts or queued stuck
relays. Their timers are cleared; the full interval starts on the first observed
ready, unparked state. `parked_labels: []` disables label exclusion, not draft
exclusion. Other PR events are unchanged. Urgency is advisory and unauthenticated,
never authority or permission to restart, stop, merge or deploy.

Each cycle observes `coordinator.terminal` for Main liveness.
`main_down` is immediate for a missing, disconnected or orphaned terminal.
A supported bare shell prompt or current `[Uncaught Exception]` / `[Recovery]`
line must persist for `coordinator.liveness_minutes` (positive finite, default 5).
A recognized live omp UI takes precedence over old crash text. Missing incarnation,
unrecognized screen, truncated inventory or read failure reports nonurgent
`main_unknown` after `coordinator.unknown_alert_cycles` consecutive unknown cycles
(positive integer, default 3), never permission to restart. An up, suspect or down
observation, or a new Main identity, resets this streak. Unknown observations reset
an unconfirmed shell/crash timer but do not clear an already reported down occurrence.
Live UI clears it; a new incarnation starts a new observation window.
Alerts include terminal, observed minutes down, bounded sanitized crash evidence
and recovery hint when present; arbitrary screen contents are not copied.
Unchanged occurrences dedupe across supervisor restarts. Both kinds go to the
project Run and optional `coordinator.escalation_run` (a bare Run ID such as
`run_abc`, not `run:run_abc`); identical destinations receive one copy.
Configure an independently attended escalation Run: alerting only the dead Main's
own Run cannot ensure anyone notices. Delivery means durable enqueue, not wake/read.

`coordinator.wake` (boolean, default true) is a narrow Main-only input exception:
read-only peek of its own Run, actionable unread age ≥ `wake_after_seconds` (default 120)
and positive bound-Run/exact-pane idle OMP + empty composer proof permit a fixed
inbox reminder. No working/background UI, draft, dialog, unknown
identity or unavailable screen is permitted. Null restored `programStatus` needs
idle/done agent state, no interrupt and every remaining proof. Actionable means
native question/escalation: the decision/urgent classes in `fleet wait`. Status-only
backlogs never wake or relay. Unsafe states relay nonurgent
`main_unread_unwakeable` to the escalation Run only after the oldest actionable
message reaches `unwakeable_after_seconds` (default 900), once per actionable set.
`wake_cooldown_seconds` (default 600) limits each actionable set; all three durations
must be positive finite numbers. New status messages change neither age nor
cooldown/relay dedupe. A valid capped 100-row oldest-first prefix can still wake
Main for observed actionable mail; restart capture remains incomplete at that cap. Attempts are
journaled before input in `supervisor.json` → `main_wakes`, with metadata and
screen hash, not raw screen contents. Attempt/signature lists retain 200 entries.
`--no-deliver` never inputs; peeks never consume or acknowledge Main mail.

`coordinator.builder_wake` (boolean, default true) separately permits a fixed
decision reminder for a current, open builder attempt. Native status mail must
match its unique pending-question subthread, configured Main sender/Run,
builder recipient and `Re: <question subject>`; unread/read state alone is not
reply evidence. The lane must match the authoritative launch's worktree and
terminal incarnation, remain unbound to any Run, and pass the same exact-pane
idle/empty-composer proof. `--no-deliver` never inputs.
The reminder requires `thread check`, then `thread wait --timeout 600` with the
supervisor's absolute, printable loaded config path (shell-quoted); an unproven
path refuses input rather than using a default project.
`supervisor.json` → `builder_wakes` journals before input and retains one attempt
per (question ID, reply ID) for the current launch, including unknown sends;
there is no automatic retry. `wake_cooldown_seconds` also limits different
decisions in the same lane. Unsafe proof older than `unwakeable_after_seconds`
from the reply emits one ordinary `builder_reply_unwakeable` status to Main.
No interrupt, restart, settlement or deployment permission is added.

After recording a pending decision and completing unaffected work, a builder
may end its turn. With delivery and builder wake enabled, Fleet wakes its
positively idle pane when Main replies. On wake, run `thread check REF`, then
`thread wait REF --timeout 600` with the original `--config` to record the
decision on Pad before continuing; `thread check` alone does not read replies.

Health alert identities include worktree, terminal handle, terminal incarnation
and flag. Recovery requires `builder.alert_recovery_cycles` consecutive absent
cycles (positive integer, default 2); a one-cycle flicker does not resend.
Changed content or a new terminal incarnation still alerts immediately.
Existing worktree/flag ledger signatures migrate only when current content
matches, avoiding an upgrade duplicate burst without hiding changed evidence.

`stalled` uses the newest event timestamp in the authoritative launch's dedicated
OMP session JSONL, never terminal output age (spinners repaint continuously).
Working UI with no event for `builder.stall_minutes` (default 60) alerts.
A uniquely owned open PR in working/idle/draft UI also alerts after a terminal
failed/cancelled check is `builder.stall_after_ci_minutes` old (default 20), if
no session event followed that failure. Both settings are per-project config
and require positive finite minutes. Background UI (the live async-job ⚙
indicator) counts as progress and is exempt from both stall rules, even for
long quiet jobs. Check-run completion/status update timestamps are retained
locally for this comparison.
Missing, ambiguous, unreadable or mismatched launch/session evidence produces
nonurgent `progress_unknown`; a verified ready shell needs no session progress.
Reports include task, owned PRs, failed checks, JSONL event age and UI state.
Changing age alone does not re-alert; recovery and new evidence do. These are
read-only coordinator alerts, not an interrupt, a nudge, or proof of deadlock.


Idle alerts are omitted only when the exact current launch/terminal incarnation
has a canonical thread result and its Pad task is done, or a post-publication
handoff is verified on a different next item. Next refs are discovered from the
result and finished-item comments; publication/handoff proof uses the retirement
convention. Missing/unreadable evidence keeps the idle alert. Positive completion
is memoised per launch in supervisor process memory until replacement/restart;
negative/unknown results retry each cycle. Raw health flags and all other alerts
are unchanged. The supervisor never writes Pad to establish completion.

Outside the verified idle merge/park lane-free case described below, a settled
canonical result with a verified next-ref handoff but no ready report retains the
nonurgent `settled, no ready report` status once per launch.
`builder.ready_report_minutes` is a positive finite number, default 15, measured
from settlement. For legacy attempts without a settlement timestamp, the timer
starts at the first verified handoff observation and persists in supervisor state.
A native ready message ID proves enqueue; absence of an ID is not proof of
reporting. This fallback neither writes Pad nor schedules a launch.

Coordinator messages target `run:<id>`; owning builders receive durable terminal
inbox messages, **not terminal keystrokes**. The service uses the explicitly
configured coordinator handle as message sender; its body identifies fleet.
After an Orca restart, refresh the coordinator handle/run configuration.
Builder handles are re-listed every cycle, never stored as durable identities.
Terminal-only builder mailboxes expire when their terminal closes; the Run inbox
is durable across terminal replacement. Fleet never promises replay into a new session.
Inbox enqueue does not prove the agent woke or read it. `watch-pr` first reports
an already-DIRTY, failing, merged or closed PR immediately, unless that caller
already received its current initial-event key. Receipts are private Fleet state,
scoped to caller terminal, repository and PR. `ORCA_TERMINAL_HANDLE` takes precedence
over the `--terminal HANDLE` fallback; without either, initial events always return.
`--include-seen` forces initial re-emission. A new head, PR update or failed-check
occurrence returns again. Live changes still wake normally; returned live failures
also record their current initial diagnosis to avoid an immediate re-arm duplicate.
Otherwise it blocks on only the selected PR (including merge-group failures naming
it) and exits on changes, timeout, or API failure, so async shell completion can
wake a builder without polling model turns. Check-failure subjects name the failing checks.
Initial merge-group failures are omitted only when a newer, completely resolved
group with a distinct synthetic head SHA also includes this PR, or a synthetic
merge's second parent proves it tested an older PR head. Ordering uses creation
time, falling back to run ID only when
timestamps are missing. Unknown ordering, membership or lineage retains the failure.
Pending-group membership reads are bounded newest-first and stop at a proven
successor; later live failures still wake immediately. Supervisor events are unchanged.
A later pending/successful workflow on the same group cannot hide its failed workflow.
Head checks from a cancelled or completed non-success workflow run are excluded
when a newer run of that same workflow on that same head exists. Workflow history
enrichment reads at most three pages of 100 runs per head per collection, only
when a cancelled/completed non-success check exists. Process-local memoization
reuses unchanged head/check-suite/conclusion evidence (up to 512 head entries);
changed check status/linkage invalidates it. Healthy/pending-only heads add no reads.
Unknown/ambiguous check linkage or unavailable history retains checks. Failed or
truncated enrichment is not memoized, allowing recovery on the next collection.
URL linkage requires the HTTPS GitHub authority and cannot override a conflicting
check suite. A replacement that fails remains actionable; legacy statuses are unchanged.

### GitHub read budget and token permissions

Fleet uses stock `gh` authentication; supervisors and builder installation tokens
can have separate rate-limit pools. Do not infer one token's budget from another.
REST GETs revalidate each page with `If-None-Match`; HTTP 304 reuses its cached
body and does not consume GitHub's authenticated primary rate limit. `watch-pr`
keeps a bounded process-local cache; snapshot/supervisor collection persists a
private, credential-fingerprinted cache (256 pages / 16 MiB) across cycles.
Only the fingerprint, validators, response bodies and budget metadata are stored,
never credentials or arbitrary headers. Changing credentials discards the cache.
GraphQL reads remain uncached and use their separate GitHub budget.

PR watches read PR/check data first. Cold polls discover historical group failures;
unchanged nonqueued polls then omit group history unless an own-group or unresolved
pending/failed group needs tracking. Queue entry and head/check/PR changes refresh it. Hour-bucketed
history URLs enable revalidation while results retain the exact rolling 24-hour cutoff.
Existing group and workflow supersession rules are unchanged.

Below 10% REST remaining, or on a rate-limit 403/429, GitHub reads defer until reset
(at most one hour; secondary limits honor `Retry-After`). Permission-denied 403s
are not rate exhaustion. Watch sleep respects its timeout. Supervisors continue
terminal/quota/disk collection and emit one nonurgent `github budget low` report
per reset window instead of repeated collection errors; unavailable GitHub visibility
still withholds launches. `watch-pr --debug` prints per-poll request/304 counts to
stderr; `serve --debug` includes per-cycle counts in its report. `status --debug`
and `may-start --debug` expose the same counters without printing credentials.

GitHub Apps need repository access and the following read permissions:

| Data | GitHub App permission | If unavailable |
|---|---|---|
| PRs, reviews, review comments, merge queue | Pull requests: read | Required PR reads fail; optional queue reads become unknown |
| Issue comments | Issues: read | Required; fail loudly |
| Check runs | Checks: read | Required; fail loudly |
| Commit statuses | Commit statuses: read | Required; fail loudly |
| Commits, synthetic group comparisons | Contents: read | Required commit reads fail; unresolved group lineage retains failures |
| Actions workflow/group history | Actions: read | Optional; 403/404 retains failing checks and reports unknown history |

Optional queue/group/workflow permission errors never certify a healthy result:
checks remain actionable without supersession proof, and known group failures are
retained. Watches emit sanitized unknown-data diagnostics and continue; supervisors
relay unknown optional data without urgency. Required failures return watch error
JSON with a sanitized endpoint path (no query) and bounded, credential-redacted
`gh` stderr. Keep token EnvironmentFiles private; never copy another project's
installation token to diagnose a lane.

While owning an open PR, builders **MUST** keep `fleet --config CONFIG watch-pr N`
armed as an async background command. Process each event and re-arm after every
completion, including timeout/error, until the PR merges or closes. Fix reported
failures even when re-arming suppresses their unchanged initial event; quiet inboxes
are not success. Never rely on retired `pr-wait.sh` scripts or a single-workflow
`gh run watch` alone.

Run anything that can exceed a few minutes (test suites, evals, profilers,
CI waits) as an async background job with an explicit timeout sized to the
expected runtime plus margin, and process its completion when it wakes you.
Never block your turn in a single foreground wait longer than ~10 minutes.
On a background timeout or no progress, inspect (output size, stack/py-spy,
logs) before re-running; a timeout is evidence, not a retry signal.
Background jobs are exempt from the `stalled` alert, so the explicit timeout
is the hang detector.

Failed native merge groups resolve all constituent PRs by walking the synthetic
first-parent merge chain between the queue branch's base SHA and run head SHA.
Side-branch commits are not group members. Proven immutable membership is cached;
unresolved membership is explicit in the coordinator report rather than guessed.
Every known member gets its owning-builder event and per-PR watcher wake,
including non-leading PRs. Later membership recovery notifies newly resolved members.
Initial and subsequent watcher events use the same exact-PR filter. Unknown
membership (`pr: null`) remains a coordinator diagnostic, not a per-PR wake;
partially resolved groups wake only their known members. An unrelated historical
failure cannot end a watch or accompany its selected PR's initial diagnostic.

### Inbox wait

Run `fleet wait --wake-on question,escalation,urgent,builder --timeout 600 --loop --save ~/.local/state/fleet/main-inbox.json --summary --auto-ack` using Main's
**async background shell** from
its own Orca terminal. The caller must already be bound to the configured
`coordinator.run` (`orca orchestration run-use --id RUN` from that terminal).
An unbound caller or different Run fails closed before any wait or acknowledgment.
Caller identity comes from `ORCA_TERMINAL_HANDLE`; `--terminal HANDLE` is an
explicit fallback only when that environment variable is absent, not permission
to borrow another terminal's identity. The configured coordinator terminal is
never used as the caller.

The save parent must exist. `--loop` continues across timeout/rearm checkpoints
until messages or a real error; omit it for existing single-shot exit behavior.
Use a bounded async-job deadline. `--save PATH` atomically replaces the complete
JSON result (0600) after each result, including errors. `--summary` defaults to
`--bodies`: delimited blocks show every ID, sender, type/classification, subject,
typed kind/PR/head and full body, including relays. Read-only legacy terminal
history is hidden unless `--legacy-history` is supplied; saved JSON retains it unchanged.
Process all blocks before rearming; no separate saved-file read is needed.
Unusable save destinations fail before consuming mail. A later save failure
returns exit 2 with full JSON on stdout (even with `--summary`) and `saveError`;
preserve it because the native batch may already be acknowledged.
`--summary` requires `--save`; otherwise Fleet refuses before consuming mail.
`--no-bodies` selects compact triage and is incompatible with `--auto-ack`.
`--auto-ack` stores a delivery only after full stdout flush and successful save
(when requested), then submits it once at the next invocation. Native
auto-handled deliveries are never acknowledged again. Explicit `--ack-run`
overrides the stored ID. Ack intent is cleared before native I/O: ambiguous
outcomes require reconciliation, not automatic replay.
Each CLI invocation holds an exclusive crash-safe flock per Run+caller.
Concurrent consumers refuse immediately (exit 2, Run and owning PID).
Private 0600 lock/ack files live under `$XDG_STATE_HOME/fleet/waits`
(default `~/.local/state/fleet/waits`), shared across config paths.
Interrupted native request metadata is retained in `rearmEvents`; reconcile
unknown outcomes separately. Looping cannot recover a possibly auto-handled
batch lost with its native response.

Current Orca routes BOTH `send --to run:RUN` and `send --to BOUND_TERMINAL` into
that Run. Native-only wake selections use one blocking `check --wait`.
`--wake-on KINDS` accepts `question`, `escalation`, `urgent`, `builder`, `status`,
or `all` (default `all`). `urgent` aliases native `question`; supervised worker
escalations remain selectable. Main uses `question,escalation,urgent,builder`
to wake on builder reports/decisions without routine supervisor status churn.
Stock native waits lack sender/class predicates: builder-selected waits classify
read-only `check --peek` rows every 3 seconds (`--interval` 1–30) within the
single async process, then consume one whole native batch when a class matches.
A native 100-row peek without a match fails explicitly (`builder_peek_incomplete`),
not silently ignoring an unseen tail. `all` and native-only selections use native blocking waits.
An explicit `--ack-run` (or previous `--auto-ack` receipt) submits one immediate
native check/ack. Empty or relay-only results continue class-gated peeking for
the remaining timeout; no ack is repeated and no held unprinted mail is acked.
Auto-handled runtimes combine held and selected deliveries in one output.
Explicit-ack runtimes return the held whole batch on selected arrival or timeout,
leaving selected mail queued for the next invocation after printing/saving.
Held mail survives errors and loop checkpoints. Every returned Delivery stays whole.
Builder mode defers relay-only backlog/replay until selected mail or timeout;
a last pending peek then permits one whole batch.
Empty peeks time out without consumption. Read-only APIs expose no active-Delivery
metadata, so speculative early checks cannot safely preserve immediate replay.

Builder actions use `fleet thread report TASK-N --kind release_ready --pr N
--head FULL_SHA "text"`, choosing `release_ready`, `review_request`, `ci_failed`
or `result`. This sends a native question with `payload.fleet_report`
(`ref`, `kind`, `pr`, `head`), summarized as kind/PR/head. Use `thread wait`
for its reply; pending/ambiguous-question guards apply. Typed result requests
do not settle the attempt: use `thread result` for final settlement.
The supplied head must equal the authoritative local task slot's current Git
HEAD; mismatches refuse before sending or recording a question.
Terminal-control and Unicode format characters are escaped visibly in bodies;
printable text/newlines remain complete and saved JSON retains original bytes.
One JSON document returns `event`, `runId`, `terminal`, `batches`, and
`legacyTerminalInbox`. Each batch contains `source: "run"`, the native
`deliveryId`, every message in that FIFO batch, and `replayed`.
Messages carry `classification`: `builder_decision_request` for decision requests;
`supervisor_urgent` for configured-coordinator messages with `[fleet ` body prefix
and `[urgent]` subject; `supervisor_relay` for other coordinator `[fleet ` relays;
or `builder_report`. Urgent supervisor questions are purely a wake mechanism,
never builder decisions: they carry `replyExpected: false` and
`triageNote: "no reply expected"`. Decisions sort first, urgent relays next,
stably; per-source and aggregate `counts` cover every displayed row and bucket,
including legacy history. These are presentation changes, not filtered
consumption: process every row; acknowledgment semantics come from native Orca.
Classification is advisory triage, **not authentication or authority**: Orca
allows local callers to supply `--from`. Labels never grant authority, suppress,
auto-ack or downgrade a message; inspect every original payload.

Process **all** message types. Reply to actual decision requests in-thread with
`orca orchestration reply --id MESSAGE_ID --body 'answer'`; urgent supervisor
questions do not request replies. After processing all output, re-arm the
recommended command unchanged; `--auto-ack` handles explicit-ack runtimes.
Manual recovery can override it with `--ack-run DELIVERY_ID`.

Fleet discovers stock `--full` support from each orchestration verb's `--help`,
cached in memory. It requests full records when advertised. Failed or ambiguous
help refuses before any consuming check or mutation; only complete successful
help establishes that the flag is unsupported. It never retries a mutation to
probe flags and still rejects incomplete delivery schemas.
Orca 1.4.242 keeps explicit whole-batch acknowledgment and unchanged output:
unacked deliveries replay; an explicit ack applies only to that Run batch.

Orca 1.4.251 auto-handles consuming checks. When native `deliveryAcknowledged`
is present, Fleet preserves it and emits `nativeAutoAck` (true/false) in the batch
and result. True adds `acknowledgmentStatus: "already acknowledged natively"`;
re-arm **without** `--ack-run`. This flag comes from native evidence, not version
sniffing. `acknowledged` is only the native echo, not proof Fleet performed an ack;
an empty timeout may omit auto-ack metadata. Use `--ack-run` for legacy unacked
batches only. The local ack receipt is not a replay store.

Auto-handled batches retain `nativeRequestId` when native mutation metadata is
available. On 1.4.251, `orca orchestration request-show --request ID --full --json`
returns the recorded outcome; replay the **original** native check command with
the same arguments plus `--retry-request ID --full --json` to recover the same
delivery, not newer mail. Real Mac replay preserved its delivery/message IDs.
Retain that request ID privately before processing. If a crash loses it between
native auto-ack and recording/processing, Fleet cannot guarantee recovery;
starting another wait does not replay that handled batch. Native absent/pruned
request evidence is not proof the check never consumed mail.
Exit **0** / `event: "messages"` means delivery; exit **3** /
`event: "timeout"` is an empty checkpoint, not failure or worker death.
Exit **2** / `event: "error"` is a binding, runtime, transport or diagnostic
failure, never an empty checkpoint. Inspect any returned batches even on error;
auto-handled batches may already be acknowledged despite a later diagnostic error.
Only explicit-ack runtimes replay until acknowledgment. Shared Orca runtime
restart is not proven.

Switch Main's receiving wait only in the coordinated deployment window after
the deployed native runtime supports the selected wake types and background
wake/replay/ack behavior has been proved. Shared-checkout updates can deploy
immediately; merge alone is not rollout approval. Builders remain unbound to Runs.

Shared-runtime restart uses `fleet orca-restart --plan PATH [--dry-run]`, not
`fleet restart` (which retires one proven finished builder). TASK-13's live
2026-11-10 service restart preserved all 15 terminal handles and omp processes.
TASK-80 therefore restarts and verifies; it never relaunches Mains, rebinds Runs,
rewrites project configs or reinstalls supervisors.

Main supplies a private version-2 plan containing only ID, state directory,
`service: orca-serve.service` and `{name, config}` project entries, plus optional
bounded restart/consumption timeouts and approval of the existing systemd SIGKILL policy.
See the [restart runbook](docs/orca-feature-map.md#task-80-restart-runbook-restart-verify-report)
for the exact schema and authority checks. Old version-1 launch/session/idle/
handoff fields are rejected, not silently ignored.

```sh
python3 -m fleet.cli --config /home/dev/.config/fleet/fleet.yml orca-restart \
  --plan /private/fleet/restart-plan.yml --dry-run
```

Dry run is read-only and prints the BEFORE capture, blockers and planned actions.
Only Main executes without `--dry-run`, from its own configured Run-bound terminal,
after owner approval and cross-project notice. No timer or external transient
service is needed. Builders never execute a live shared restart.
Keep all Main inbox waits running across the restart; do not ask them to go
quiet. Run the conductor asynchronously, with a bounded deadline including its
result-consumption window. `fleet wait` recovers transport outages for up to
30 seconds. An ambiguous consuming request is never automatically replayed:
`rearm` (exit 4) tells Main to inspect retained native receipts and re-arm;
`runtime_unavailable` (exit 2) reports persistent outage.

The conductor journals BEFORE/AFTER metadata privately (0700 directories, 0600
files), verifies a new active service PID/runtime, every prior terminal handle
connected, coordinator Run bindings and active supervisors. Automatic per-Run
pending-message comparison reports lost and new IDs; loss or incomplete evidence
prevents success. Capture uses read-only `check --peek`, never check/ack consumption;
fewer than 100 validated rows proves completeness, while capped/malformed data
blocks. History count is informational. Concurrent consumption can look like loss;
Mains must reconcile native Delivery receipts separately.

Outcome notifications use native `question` from the initiating Main identity
to every project Run, retaining message-ID receipts. Enqueue is not proof of
wake/read. Missing/ambiguous receipts or a timed-out restart require manual
reconciliation, not duplicate sends or restarts. Resume only the unchanged plan
after inspecting its private journal; never delete state to force retry.
After sending results, read-only polling confirms complete zero-pending Run
inventories, or names undrained/unknown Mains in `attention_required` after
`consumption_timeout_seconds` (default 120, range 1..600). Empty pending is not
human approval. Main must nudge flagged recipients through an attended channel.
An empty post-restart screen needs safe operator redraw, never automatic builder
input. Manually exited lanes still require Main's positive-exit `fleet reconcile`
receipt before reuse; restart does not clear unresolved launches.

Every result includes unfiltered, **read-only** terminal history diagnostics
(requested limit 1000, subject to Orca's history cap), or an explicit diagnostic
error. This is not another Delivery and has no `--ack-terminal` or local ack.
Mail sent before binding can remain there unread; it does not wake the Run.
Unbound terminal-only native checks mark mail read immediately, without
replayable delivery IDs, and those mailboxes expire when the terminal closes.
Inspect/reconcile pre-binding history separately; never silently consume it to
simulate reliable delivery. Enqueue alone still does not prove owner wake.

### Terminal-builder task threads

Use the same project `--config` on every command:

```sh
fleet --config CONFIG thread open TASK-123 --slot /path/to/slot-a
fleet --config CONFIG thread question TASK-123 --body 'Proposal, risk, decision needed'
fleet --config CONFIG thread wait TASK-123 --timeout 600  # async background shell
fleet --config CONFIG thread result TASK-123 --body 'Published PR, review/CI receipts, outcome'
fleet --config CONFIG thread ready NEXT --body 'Concrete next-piece handoff'
```

Main sends steering with `fleet --config CONFIG thread send TASK-123 --body TEXT`.
Between every wait segment (at most 600 seconds, including background waits),
builders run `fleet --config CONFIG thread check TASK-123`. Each pending
Fleet-managed message is one JSON line. Act before re-waiting, then acknowledge
with `thread check TASK-123 --ack TOKEN`. Reading alone does not clear it.
Main's `thread pending` lists unacknowledged or ambiguous sends per current lane
with age in seconds. These commands do not consume native mail or replace
decision replies / all-type native inbox checks. Sender IDs are advisory.
`thread send --priority` refuses before side effects: the installed stock
interrupt path injects text into a child PTY and sends Ctrl-C, not safe steering.
Normal sends never type into a terminal. Ambiguous sends remain journaled and
block further sends on that attempt until manually reconciled; never resend.

For an emergency only, configured Run-bound Main may run
`fleet --config CONFIG thread stop TASK-123 --reason TEXT [--timeout 120]`.
It proves the exact OMP process/pane/session lease, journals one bare Escape,
waits for idle, clears a draft only in an idle composer, proves it empty, and
submits one fixed STOP line with an absolute shell-quoted `--config` check
command. The instruction remains unacknowledged in
`thread check` / `thread pending`. Unknown identity/screen or timeout refuses;
an existing stop journal forbids blind retries. Terminal acceptance does not
prove consumption. See [the stop runbook](docs/MAIN.md#emergency-lane-stop-task-89-source-only).

`open` requires the caller's authoritative launch receipt and records Pad ref,
canonical task thread ID, attempt, actual Git branch and reusable slot together.
That task ID survives fresh attempts; each question has a unique native subthread
to isolate late replies from earlier questions/attempts. A different Pad task gets
a different canonical thread. Session/terminal IDs are disposable; no supervised
Task/Dispatch or `worker_done` is fabricated.

If Main authorizes continuing after `thread result`, run
`fleet --config CONFIG thread reopen REF --authorized-by MSG_ID [--reason TEXT]`
before any new question; never fall back to direct Run questions. This requires
the latest attempt to be settled and the same authoritative launch, unbound
terminal, live incarnation and worktree. It archives that attempt and creates a
linked continuation with a new attempt ID/native primary subthread. Main receives
a native status naming the prior attempt, supplied authorization ID and reason;
Pad records the continuation and message ID. The ID records Main's authorization,
not authenticated proof: obtain and record the actual ruling before calling.
Repeating that ID returns the same current continuation; a different ID on an
unsettled attempt refuses. A known send retries only its Pad recording; ambiguous
sends require reconciliation, never resending or bypassing via `open`.
Question/wait/result use the continuation; ready still requires settlement and
a merge after the original launch plus a later concrete handoff. Supervisor
completion/ready-report caches distinguish continuations on the same launch.


Decision requests use **exactly one channel**: `fleet thread question`. It sends
the native type `question` to `run:<coordinator.run>` itself. Never also send a
direct `orca send`/`ask`/question for the same decision. Success JSON includes
`sent: true`, the native `message_id`, unique `thread_id` subthread and a `notice`
that no separate direct send is needed. A pending question refuses with its
message ID and subthread (an ambiguous send has no known message ID).
Follow-ups use reply/status in that same pending native subthread, not a new
direct question. Ordinary progress/PARK reports may still raw-send with native
type `status`; final FRESH-READY must use `thread ready`, never raw `orca send`.
Main answers the emitted question message
ID with native `orchestration reply --id`. Native `ask`, including resume, requires
a Dispatch and cannot serve terminal builders. One background `thread wait` process reads
non-consuming terminal history every two seconds (requested 1000, native cap
applies), caches **all observed message types** privately, and copies only the
question, matching Main decision and final outcome to Pad. It returns only after
the decision's Pad write succeeds. Retry the same wait after a timeout/restart or
Pad failure; an already-cached decision needs no native history. Exit 3 is timeout.
Each task has its own private ledger/lock, so slow native or Pad I/O in one lane
cannot abort a different task's background wait. Same-task operations remain
serialized; the launcher prevents duplicate active task attempts.

This does not promise delivery after an **unobserved** reply's terminal closes or
history is evicted: retain the terminal until the wait copies the reply to Pad.
No native history is consumed/acknowledged. Ambiguous sends fail closed against
resending: reconcile the recorded unique subthread with Main/native history
before any operator repair. `result` refuses unresolved decisions and settles the
attempt; old/settled receipts cannot authorize a new task. Ordinary new tasks
require a fresh omp session; the explicit prerequisite exception follows below.

#### Final FRESH-READY receipt

After merge, write the concrete next-piece Pad handoff, settle the primary
thread with `thread result`, then make
`fleet --config CONFIG thread ready NEXT [--body TEXT]` the last lane action and
stop. `NEXT` is the proposed next Pad ref, requesting Main's launch decision. The caller must
be the unbound terminal of the current authoritative primary launch, with its
canonical attempt already settled; a carried task result alone is insufficient.
Before a new send, its live incarnation and worktree routing must still match the
launch receipt, and the terminal must be connected, writable and non-orphaned.
Ready verifies a PR carrying the current ref merged strictly after this launch
and a concrete handoff on NEXT written strictly after that merge. Same-ref readiness retains
those newer-merge/later-handoff guards, including a non-empty next-piece
description after the ref; Pad done alone and inherited boundaries do not qualify.

Ready sends one native `question` to Main so decision-filtered waits wake, and saves an idempotent receipt with
`next_ref`, native `message_id`, timestamp and Pad-recording state. It then writes
a Pad comment containing the message ID. Retrying the same known-ID receipt
does not send again; if only the Pad write failed, retry the same command to
finish that comment. An ambiguous send without a message ID refuses duplicate
sends: reconcile the receipt with Main/native history before operator repair,
never substitute raw `orca send`. The ID proves durable enqueue, not wake/read
or approval. Ready does not schedule, restart or launch anything; Main still
approves the fresh launch.
Existing sent `status` receipts remain valid and are never resent on upgrade.
Main processes FRESH-READY as a `builder_decision_request` and replies in its
native thread with the launch ruling. After a successful ready report, the builder
ends its turn with a final message and does nothing else: no further tool calls
or background jobs, never a job that waits on its own terminal/idle state or sends
input to its own terminal. It does not wait for the reply or arrange self-exit;
Main (or the owner) retires the idle session via the documented retirement path.

#### Finished without a next ref; explicit park completion

If the current primary attempt is settled but no concrete next ref exists, use
`fleet --config CONFIG thread needs-next REF` as the last lane action, then end
the turn. It verifies a PR carrying REF merged after this launch, or a recorded
explicit park completion. It sends `NEEDS-NEXT-REF` as one native question to
Main, classified as `builder_decision_request`, without inventing a handoff or
scheduling a launch. The receipt deduplicates per launch; a known-ID Pad failure
can be retried without resending, while an ambiguous native send requires
reconciliation. A launch cannot send both ready and needs-next reports.

On a durable PARK/reassign request, commit and push WIP and write a concrete
resume-note comment on the current task. Run
`fleet --config CONFIG thread parked REF --resume-note COMMENT_ID` as the last
lane action, then end the turn. It requires the current opened primary attempt,
unchanged live terminal ownership, no pending decisions, a clean tracked worktree,
and local HEAD equal to the actual origin branch head (not a tracking cache).
Non-ignored untracked files are preserved regardless of age. Receipts/report
bodies list up to 50 relative paths and the total count (`untracked_files`,
`untracked_count` in the receipt). Launch refuses tracked changes, not untracked files.
The exact Pad comment must exist on REF and be newer than the launch.
The command reports PARKED, records the park receipt and settles the attempt;
no merged PR or fake done status is required. Main can use guarded different-ref
restart to reassign the slot, or same-ref restart to confirm a fresh resume on
the parked branch without a merge. PARKED attempts cannot send FRESH-READY.
All exact retirement and admission proofs remain.

The supervisor requests a next task once per launch for a positively idle OMP
or supported shell with canonical settlement and verified merge/park completion,
unless a ready/needs-next report already exists. Its `Lane free: … needs next task`
question replaces generic idle/missing-ready relays for that completed launch.
Blocked, stalled and unproven idle states never qualify; this is not dispatch.
Supervisor and builder share the same launch-level pre-send journal under the
launcher lock. A later `needs-next` returns the existing supervisor message ID;
uncertain native responses block both channels until reconciliation, never retry
the question automatically.


Main may approve a prerequisite in the existing session (Pad relations alone
never authorize it). Record the decision on Pad, then from the configured Main
terminal run:

```sh
fleet --config /private/project/fleet.yml carry --slot /path/to/slot-a \
  --launch EXACT_ID --ref TASK-124 --evidence 'Explicit Main prerequisite approval'
```

`carry` requires the exact current live launch, matching terminal/incarnation,
an unsettled primary thread and non-empty approval evidence. Unknown/settled
launches, the primary ref itself, another owner's claim and non-Main callers
refuse. Repeating the same approval preserves its original receipt. This is an
identity policy check, not authentication against local environment spoofing.
The builder opens `thread open TASK-124 --slot /path/to/slot-a` with the same
config. Its separate canonical task thread shares the launch attempt, not its
question subthreads or settlement: `result TASK-124` settles only TASK-124.
`next`, `launch` and `restart` retain the carried claim while the launch is
unresolved, even after the carried result. Verified launch exit/retirement or
Main reconciliation releases ownership; a replacement launch never inherits
approval. Restart's OLD/NEXT logic still uses the primary launch ref. No native
transport or supervised Dispatch changes.


Builder terminals must remain **unbound**. Health exposes binding as `none`,
`bound` plus Run ID, or `unknown`; bound slot terminals are
`owner_delivery_broken`, including ready shells. Use separate owned scratch
terminals/Run/config/state for probes, never the live coordinator identity.

### Main-confirmed ready queue

`next` proposes one not-started task whose nearest ancestor PLAN is active, ordered
by priority (`critical`, `high`, `medium`, `low`, unset) then oldest creation time and ref.
It queries the configured workspace's task list and dependency edges: Pad's
`project ready` currently favors in-progress work and is not an open-task queue.
Every task and configured slot includes exclusion reasons. Incoming blockers must
be terminal under the workspace status mapping; unknown/unreadable dependency
state excludes the task. Both `next` and explicit `launch` walk at most 20 parent
links to the nearest PLAN, stopping there even when inactive. Missing/unreadable
parents, cycles and depth overflow refuse with a reason.
Actual lane Git branches, leading Orca card refs, active builders and unresolved
launch receipts prevent duplicate-task proposals. A card claims only a ref at
its start, optionally after non-word decoration (Unicode punctuation, symbols
or whitespace, including emoji/keycap sequences), then a status word (`open`,
`ready`, `working`, `in-progress`, `blocked`, `done`, `completed`, `parked`,
`cancelled`). Thus `: TASK-2` claims TASK-2; `note: TASK-2` does not. Later prose
refs are mentions, not assignments; a card with no
leading ref claims nothing. Duplicate diagnostics identify `card (leading ref)`.
Branch-derived refs and carried/unresolved launch ownership are unchanged.
Only one connected, verified
shell with no other live/unknown terminal makes a proposed slot free. Quota,
disk, cap and incomplete health can withhold all proposals. Exit 0 means a
proposal, 1 means none, 2 means collection/command failure; `--json` is structured.
Not-before fields are shown when present; Main applies scheduling/scope/budget
constraints, including dates expressed only in prose.

Task states come from `pad collection list --format json` in `pad_workspace`:
the tasks collection's status `default` is not-started (`open` or `todo`, for
example), `terminal_options` supplies terminal states, and only an exact
`in-progress` option authorizes continuation. Other options (including `blocked`)
and unknown states remain ineligible. If collection schema discovery is unavailable,
Fleet defaults to `open` / `in-progress` / `done,cancelled`; malformed schema refuses.
For other vocabularies set a complete `pad.statuses` mapping with `not_started`,
`in_progress` and `terminal`, each a non-empty, disjoint list of names (see
`fleet.example.yml`). This explicit override takes precedence over schema discovery.
`next` excludes in-progress tasks; only an explicit confirmed launch can continue them.

There are **no automatic starts**. Main inspects `next`, writes the Pad brief,
and explicitly runs `launch REF --slot PATH`. Same-ref recovery (the slot's last
launch ref matches, or its current branch starts with the exact task ref) keeps
the existing branch and committed/unpushed WIP: no fetch or switch, even if
the Pad title changed. Tracked modifications and staged changes withhold launch.
Non-ignored untracked user files are preserved; the launch receipt records the
first 50 relative paths and total count, and the prompt forbids staging, committing,
moving or deleting them. A target checkout that conflicts with them refuses.
For different-ref launches the launcher fetches origin and refuses
detached or unpushed current work before switching. It creates
`task-<n>-<title-slug>` from `origin/main`, or reuses that exact
unmerged, pushed task branch. A remote-only task branch is checked out locally;
behind/diverged local task branches refuse for manual reconciliation.
Merged names (including GitHub squash/rebase merges) are preserved and skipped
with numeric suffixes (`-2`, `-3`, …). No stash, reset or clean is used.
The prepared branch is recorded in the launch receipt and builder prompt;
`thread open` refuses a different actual branch for that primary launch.
Only the leading task ref in a branch claims ownership; task refs mentioned in
the title slug do not assign those other tasks.
Launch re-reads Pad state/dependencies and ownership under the shared launcher
lock, rechecks admission and terminal identity after preparation hooks, and
refuses duplicate live/unresolved task attempts or occupied slots. An explicit
in-progress continuation is allowed only after the previous attempt's exit is
positively reconciled; stale receipts or missing terminals never prove exit.
Branches/leading card refs in the selected slot may name the task being confirmed;
the same claim in another slot blocks it. `next` never reserves or mutates Pad,
Git, or Orca state; its proposal can become stale before confirmation.
Initial admission precedes hooks or slot creation. If later admission/identity
rechecks fail, Fleet preserves the prepared slot and records a slot-keyed
`launch-preparations.json` failure receipt with stage, safe state and retry
guidance. Before builder input, recovery is inspect hooks/slot and retry
`launch`; after a launch attempt, inspect terminal/launch receipts first.
These preparation receipts are diagnostics, not proof of session ownership.

An unresolved launch receipt withholds its **whole slot**, even for a different
next task. Exit proof is an exact terminal/incarnation at a verified shell, a
matching `restart` retirement receipt that reached shell verification, or an
explicit Main reconciliation. Never infer exit from an absent/replaced terminal.
From the configured Main terminal only:

```sh
fleet reconcile --slot /path/to/slot-a --launch EXACT_LAUNCH_ID \
  --evidence 'final stop observed; shell verified before terminal was closed'
```

This records launch ID, slot, actor, time and supplied evidence in private
`reconciliations.json`; it does not stop a process or launch anything. Live,
unknown/disconnected or incomplete slot health refuses reconciliation. Supply
only observed, non-secret evidence. Wrong/stale IDs cannot clear another attempt.

Launch checks fresh quota, free disk, active-session cap, and slot occupancy.
A connected terminal at a verified shell prompt is not an active builder.
An existing slot reuses its single verified **unbound** ready shell after fresh
readiness/identity/binding checks; bound or unknown-binding shells are bypassed
for a new terminal in the same slot. The launch receipt records
`terminal_selection` and skipped bindings. No `/exit` is sent or old context resumed.
Live, unknown, and ambiguous sessions remain blocked. Prompt detection supports standard
`user@host:path$` / `bash-version$` POSIX prompts; custom prompts fail closed.
Shell reuse requires a nonempty terminal incarnation, unchanged in the
post-preparation inventory and final `terminal show` immediately before input.
Disconnected/orphaned sessions and unknown terminal identities still occupy their
configured slot and count toward admission until positively reconciled. A stale
shell screen is not a connected, writable shell receipt. Disconnected terminals
are never chosen as PR message owners.
Without a ready shell it starts a **new terminal**. If a configured slot is absent,
Orca creates a top-level
worktree with that basename and validates the resulting path. Repo setup hooks
run only on creation. A private prompt includes the Pad ref/handoff instruction,
rules file and configured footer. Private prompt/overlay files remain for the
session lifetime; remove them after the owning terminal exits.
The default/example footer identifies the current task as already fresh.
Pick the next piece by plan priority, usually the same feature; the same ref is
allowed. Never switch to an unrelated task just to get a different ref.
`FRESH-READY` requires publishing this session's NEXT piece of work (merged after
its launch), then writing a concrete next-piece handoff after that merge; never
signal it for an inherited handoff. After `thread result` settlement, use
`fleet --config CONFIG thread ready NEXT [--body TEXT]` as the last lane action,
never raw-send readiness. Keep project-local footer overrides consistent.

The launch overlay disables auto-resume, extended context, and model fallback;
sets reserve policy fail-closed and an empty pinned-model fallback chain; and
sets `compaction.modelThresholds[resolved_model]` to `f200000` (fixed trigger,
not a scaled window; see the
[OMP threshold contract](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/config/compaction-threshold.ts)).
The launch receipt is saved before later hooks/metadata updates: an error after
terminal creation is **not** permission to launch again. Inspect `last-launch.json`.
No automatic retries, auto-dispatch from Pad, merge actions, or worktree cleanup.

`restart NEXT --slot PATH --finished-ref OLD` is the guarded replacement for
manually exiting a finished builder. Main still confirms the next task/launch.
Before `/exit`, restart checks quota/disk/cap and the next task's status, plan,
dependencies, duplicate assignments and competing receipts. Only the exact
proven retiring launch receipt and its matching slot/worktree/terminal/incarnation
active-builder claim are exempt from that preflight. Keep OLD's branch intact
while its session is live. After verified retirement, normal launch prepares
NEXT's task branch; no branch switch occurs before `/exit`.
Other terminals, slots and receipts still block.
When OLD differs from NEXT, an explicit `thread parked` completion receipt
qualifies without a merge; otherwise OLD must have a merged PR carrying its exact
Pad ref and either Pad status `done` or a post-publication handoff on NEXT.
When OLD equals NEXT, a fully recorded valid park receipt for the current launch
also qualifies without a merge. Otherwise Pad `done` is insufficient: require a PR merged strictly
after the retiring launch receipt's timestamp, and a handoff comment on that
same ref strictly after the merge, with a non-empty next-piece description after
the ref (not just a bare handoff marker). Publication/handoff ordering uses
timezone-aware timestamps; missing, malformed or timezone-less evidence refuses.

Retirement requires one connected writable non-shell OMP terminal, bound to OLD
by `launches.json`'s exact slot/handle/incarnation record. Extra terminals block
except verified connected **Run-bound ready shells**, which launch already
bypasses and retirement never touches. Unknown/unbound extra shells still block.
Each launch gets a private `sessions-<id>` directory. Its single session log must
belong to the slot, still have a live OMP session lease, and end with an assistant
`stopReason=stop`. Only top-level `credential_pin` / `title_change` and
`type=custom, customType=todo_hud_state` UI snapshots may follow; all other
event encodings refuse. The exact pane must report idle/done, with a current idle,
**empty** OMP composer and no dialog/background-job indicator. Single/multiline
drafts (including draft text resembling UI borders) refuse without clearing or
submitting them. A stale working screen after a completed turn still refuses;
silence, titles and elapsed time never prove readiness. Older launches without
this binding require manual reconciliation.

Restart currently requires **local Linux `/proc`** evidence: one non-stopped
foreground OMP process with the exact launch argv (including private session
directory, overlay and prompt), slot cwd and matching terminal routing key.
PID/start identity is rechecked before input. Only `ORCA_TERMINAL_HANDLE`
equality is inspected in memory; no environment contents are logged or retained.
One optional **leading** `--extension PATH` pair is accepted only when PATH
exactly equals `builder.orca_status_extension` (absolute path or `null`).
If omitted, that setting defaults to `ORCA_OMP_STATUS_EXTENSION` from Fleet's
own environment, never the target process environment. Unset/null allows only
the plain launch form; arbitrary, repeated or reordered extra arguments refuse.
Native exact-pane idle/done evidence is required in either form; missing or
unknown projection refuses rather than inferring status from the extension.
Missing/inaccessible `/proc`, non-Linux hosts, changed argv/config, absent routing
keys and ambiguous process ownership refuse. Restart is unavailable there;
manual retirement requires independent reconciliation, never a force bypass.

Under the launcher lock, restart rechecks evidence and admission. A changed log
is accepted only when its previously checked bytes are unchanged and all appended
events are allowlisted post-turn metadata; the fresh idle/pane/lease/process checks
must still pass. Rewrites, truncation, new turns and unknown events withhold input.
The rechecked proof is journaled as `exit-attempted` in the slot-keyed
`retirements.json` **before** sending `/exit`
once, then waits up to 15 seconds between bounded Orca calls for a standard
POSIX shell prompt in the same terminal incarnation. Only then does it invoke
normal guarded launch, including fresh quota/disk/cap/occupancy checks.
Receipts record exit acceptance, shell verification and the next launch ID.
No Esc, interrupt, force mode or automatic retries exist. Unknown/disconnected,
ambiguous, unfinished, changed evidence, dialog and timeout states fail closed.
Any attempted retirement blocks another restart of that launch ID; inspect
`retirements.json`, `launches.json` and `last-launch.json` and reconcile the
actual terminal before any manual recovery. A failed fresh launch leaves the
verified shell and receipts intact; it is not permission to repeat retirement.
Restart excludes only the proven retiring terminal from the pre-exit cap check.
Post-exit failures retain durable recovery guidance: confirm no replacement
started, then retry `launch NEXT --slot PATH`, never repeat `restart`/`/exit`.

If exit leaves exactly a terminal-reply body after the prompt, restart may send
**one Ctrl-U (0x15), without Enter**, never arbitrary cleanup input. Recognition
is limited to one two-number `t`/`R` body or a three-number `t` size reply whose
first field is 4, 6, 8 or 9; fields contain 1–6 ASCII digits and separators are
semicolons, with no extra whitespace or text. Before input it proves the
recorded OMP PID/starttime is gone on the same boot, the same-incarnation pts has
exactly one process, and that process is a foreground bash/zsh/sh with matching
terminal routing. It rechecks identity/screen/process evidence and journals
`residue_cleanup.stage=input-attempted` before sending. A fresh exact clean prompt
and unchanged shell proof are mandatory before normal launch. Any mismatch or
failed cleanup leaves `shell-verified-with-residue`, withholds launch and requires
manual verification/line clearing; never repeat Ctrl-U or restart. Ordinary
health/admission/launch still accept only clean prompts, not residue.

Optional hooks `before_launch`, `after_launch`, and `event` are argv lists, run
without a shell in the project checkout (launch hooks use the slot when known).
Their output is discarded and only redacted command failures are reported.
Treat fleet.yml and hooks as trusted local code.

Snapshot push is optional. `status.url` must be HTTPS and `token_env` names an
environment variable, never a token in YAML. Unset URL/token silently skips it;
endpoint failure leaves local supervision running. Push on snapshot change and
at least every `push_seconds`; redirects are refused to protect Authorization.
For a user service, set the token through a private systemd `EnvironmentFile`
drop-in; never commit it or dump the service environment.

TASK-2687 verification: real disposable PR fail/pass, inline review and close
events delivered to owning builder and coordinator inbox; blocking PR watcher
woke on a real status; fresh session model/thinking recorded by OMP; wrong model,
reserve warning and delivery dedupe exercised. No Orca restart was tested.

## Central read-only status

The central app adds no runtime dependency: stdlib HTTP/HTML and SQLite, using the
same snapshot version 1 as the daemon. `/` lists every configured project;
`/project/owner%2Fproject` adds commit links and recent snapshot history. Both
show last-24h PR counts/timestamps/time-to-merge, default-branch commits, queue,
merge-group failures, builder task/PR/model/state, pooled quota and free disk.
Missing collectors are unknown, not zero activity. Closed counts include merged
PRs. Read-only pages refresh every 60 seconds; no launch/merge/control endpoints.
PR links include feature titles as well as numbers; older supervisors must be
upgraded before their next snapshot can supply titles. Merge durations use
readable units such as `23m`, `2h 14m`, or `1d 3h`. Dates, snapshot times and
history render in the viewing browser's timezone with a visible zone label;
without JavaScript the page retains a readable UTC fallback.

### Production central: the PaaS

Per-project supervisors remain user-systemd services on their own machines.
Only the central web app is deployed through the PaaS. Create a private-repository
**Docker Compose** application using `/compose.yml`, with a read-only deploy key.
Do not assign a public/autogenerated domain: the Compose backend publishes only
on host loopback. Keep the `snapshots` named volume across deployments.

Set runtime-only variables in the PaaS (never build arguments):

- `FLEET_PROJECT_TOKEN`: the private token shared with the first supervisor.
- `FLEET_SELF_TOKEN`: optional second-project token for fleet's own supervisor;
  required when `cswenor/fleet: FLEET_SELF_TOKEN` is in the central allowlist.
- `FLEET_PROJECT_BETA_TOKEN`: distinct Project Beta ingestion token; required
  when `example-org/project-beta: FLEET_PROJECT_BETA_TOKEN` is in the allowlist.
- `FLEET_CENTRAL_CONFIG`: multiline/literal YAML. The approved three-project
  allowlist below requires all three runtime tokens; apply Project Beta's entry
  only after coordinated deployment GO:

```yaml
version: 1
host: 0.0.0.0
behind_proxy: true
port: 8787
database: /data/snapshots.sqlite3
projects:
  example-org/project-alpha: FLEET_PROJECT_TOKEN
  cswenor/fleet: FLEET_SELF_TOKEN
  example-org/project-beta: FLEET_PROJECT_BETA_TOKEN
```

For more projects, add distinct token environment names to this mapping, and add
each matching runtime variable to `compose.yml` and the PaaS. Registration is
allowlisted: the first authenticated snapshot populates the configured project's
card; a token cannot invent another project. Enable the PaaS environment-value
redaction and keep these variables runtime-only. Disable preview deployments.
Some the PaaS releases persist encoded SSH-key setup commands in hidden deployment
records. Hidden is not redacted. On affected releases, keep automatic deployments
off; after each manual rollout, sanitize only that application's sensitive stored
command fields and rotate any key whose material was stored, or upgrade to a
release that redacts those commands. Do this before sharing deployment logs.
Runtime project tokens must stay out of both image layers and deployment logs.

The image runs as UID/GID `10001:10001` with a read-only root, writable `/tmp`,
and persistent SQLite under `/data`. New named volumes inherit its ownership;
pre-existing bind mounts must be writable by UID 10001. A packaged health check
requires HTTP 200. `FLEET_CENTRAL_PORT` optionally changes the host's loopback port
(default 8787), not the container port. Keep wildcard binding inside the private
container network; `behind_proxy: true` is explicit permission, not network ACLs.

Expose the host's loopback backend through tailnet HTTPS as below. Upgrade with a
the PaaS deployment of the tested main branch; never replace the volume. During
migration, preserve the old SQLite database with its backup API, verify production
ingestion/read access, then retire only the old central service/frontend. Do not
stop or move the per-project supervisors.

Project Beta's completed Linux onboarding and exact production acceptance
receipts are recorded in the [TASK-17 roadmap section](docs/ROADMAP.md#task-17--phase-4-install-elsewhere).

### Optional standalone central: systemd

Copy `central.example.yml` to a private configuration directory. Map each repo to
a distinct environment-variable name, **not a token**. Generate independent
random tokens locally (at least 32 ASCII characters), store them in a `chmod 600`
systemd EnvironmentFile, and give each daemon only its own token:

```sh
mkdir -p ~/.config/fleet
umask 077
python3 -c 'import secrets; print("FLEET_PROJECT_TOKEN="+secrets.token_urlsafe(48)); print("FLEET_OTHER_TOKEN="+secrets.token_urlsafe(48))' > ~/.config/fleet/central.env
cp central.example.yml ~/.config/fleet/central.yml
# Edit project names and token env names; keep host 127.0.0.1 behind Tailscale Serve.
fleet --config ~/.config/fleet/central.yml central-install-service \
  --environment-file ~/.config/fleet/central.env
systemctl --user status fleet-central.service
```

Re-run the same installer after upgrading the fleet package (or fast-forwarding
the stable source checkout). It reloads/restarts only central; the original
`install-service` command still installs per-project supervisors. Source installs
keep using the stable checkout selected by `~/.local/bin/fleet`. For foreground
operation, load the private environment without tracing and run `fleet --config
/absolute/central.yml central-serve`.
The central installer waits up to 10 seconds for the local HTTP page to respond
successfully; an active-but-not-ready service is an installation failure.

### Connect project supervisors

Expose the loopback backend on the **tailnet only**, with Tailscale HTTPS Serve:

```sh
# Choose a free tailnet HTTPS port, avoiding existing host services.
sudo tailscale serve --bg --yes --https=8443 http://127.0.0.1:8787
```

Use the printed `https://host.tailnet.ts.net:8443/snapshots` as each daemon's
`status.url`, and its matching environment name as `status.token_env`. Load that
daemon's token through a private systemd EnvironmentFile drop-in, then reload/
restart its supervisor. Do not use Funnel or expose the backend publicly.
Tailscale Serve handles certificate renewal and binds the external frontend to
the Tailscale interface; viewing trusts tailnet membership/ACLs, not a browser
bearer credential. Direct host binding is restricted to loopback/Tailscale IPv4;
wildcard container binding requires the explicit private-proxy configuration above.

`POST /snapshots` checks per-project bearer identity, version, types, timestamps
and a 2 MiB body limit. Unknown/private fields are discarded before storage;
HTML is escaped and links limited to HTTP(S). Request headers/bodies and tokens
are never logged. A token cannot submit another repo's state. Keep all tokens,
SQLite files and project configs outside the repository.

Private SQLite state defaults to `$XDG_STATE_HOME/fleet/central/snapshots.sqlite3`
(otherwise `~/.local/state`). History is pruned on ingestion to 48 hours and at
most 1,000 snapshots per configured project; the latest is always retained.
Late/replayed older snapshots do not replace newer collection state. Drill-down
shows up to 50 history timestamps. Projects with no snapshot stay visible as
never received; collection or receipt older than `stale_seconds` is marked stale/
unreachable (last known data remains visible). Default 600s allows two daemon
push intervals. This indicates missing freshness, not proof of a network cause.
An unavailable app never disables local supervision; failed pushes retry on
subsequent daemon cycles. Back up SQLite with the service stopped or SQLite's
backup API, not a partial live file copy.

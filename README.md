# Fleet

Per-project Orca + omp fleet supervision. Generic project configuration; no model
calls in collection or event diffing. Requires Python 3.11+, PyYAML 6, `gh`, `omp`,
and a running Orca runtime with its CLI.

## Install and inspect

```sh
pipx install .
cp fleet.example.yml /path/to/project/fleet.yml
# Fill in the checkout, Orca repo id, coordinator run/terminal, and lane slots.
cd /path/to/project
fleet status
fleet status --json
fleet may-start --json
```

Without pipx, use a virtual environment and `pip install .`. On Debian install
`python3-venv` first. To run from source with system PyYAML:
`python3 -m fleet.cli --config /absolute/path/fleet.yml status`.

`fleet.yml` version 1 is illustrated in `fleet.example.yml`. `builder.model` is
the launch selector, including `@role` if desired; `resolved_model` is the exact
provider/model expected at runtime. `thinking` is explicit and defaults to medium.
Slots are absolute checkout paths. The main checkout is not a stray lane.
Only unique normalized PR branch → Orca worktree → connected omp terminal mappings
have owners. Ambiguous mappings never select the first terminal.

Status contains last-24h PR timestamps/time-to-merge, default-branch commits,
merge queue/recent merge-group failures, builder models/state, quota, and disk.
Unknown collectors are explicit errors, not zero activity. Model detection uses
Orca provider projection when present, otherwise the latest OMP footer; it is not
proof of thinking level or model pinning after a restart.

Quota aggregates selected-provider account capacity by shared window, excluding
tier-specific limits. Each window must remain above the reserve percentage.
Missing/stale usage fails closed. Account identities, provider metadata and
credentials are never persisted in the snapshot. `may-start` exits 1 when quota,
disk, builder cap, or incomplete collection prevents a launch.

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
fleet launch TASK-123 --slot /path/to/slot-a
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

One service per project, private state under
`$XDG_STATE_HOME/fleet/owner--project` (default `~/.local/state`). Atomic snapshots,
pending events and successful per-recipient delivery receipts survive process
restarts. Supervisor/launcher locks prevent duplicate local instances. Delivery
is at-least-once across a crash between Orca accepting a send and the local
receipt write; no exactly-once guarantee. Read-only runs retain pending events
until delivery is enabled. On first sighting, DIRTY, failing checks, and merged/
closed PRs notify immediately; healthy initial state stays quiet. This includes
terminal PRs in the recent 24h history. Subsequent identical snapshots are quiet.
Alerts dedupe until recovery; a recurrence alerts again. Failed sends remain
pending. An ambiguous/missing owner gets a coordinator-only report; unavailable
inventory retains owner delivery for a later successful lookup. Health and
collector errors never restart or stop lanes.

Coordinator messages target `run:<id>`; owning builders receive durable terminal
inbox messages, **not terminal keystrokes**. The service uses the explicitly
configured coordinator handle as message sender; its body identifies fleet.
After an Orca restart, refresh the coordinator handle/run configuration.
Builder handles are re-listed every cycle, never stored as durable identities.
Terminal-only builder mailboxes expire when their terminal closes; the Run inbox
is durable across terminal replacement. Fleet never promises replay into a new session.
Inbox enqueue does not prove the agent woke or read it. `watch-pr` first reports
an already-DIRTY, failing, merged or closed PR immediately. Otherwise it blocks
on only the selected PR (plus relevant project merge-group failures) and exits
on changes, timeout, or API failure, so async shell completion can wake a builder
without polling model turns.

Failed native merge groups resolve all constituent PRs by walking the synthetic
first-parent merge chain between the queue branch's base SHA and run head SHA.
Side-branch commits are not group members. Proven immutable membership is cached;
unresolved membership is explicit in the coordinator report rather than guessed.
Every known member gets its owning-builder event and per-PR watcher wake,
including non-leading PRs. Later membership recovery notifies newly resolved members.

Launch checks fresh quota, free disk, active-session cap, and slot occupancy.
A connected terminal at a verified shell prompt is not an active builder.
An existing slot reuses its single ready shell terminal after a fresh readiness/
identity check; no `/exit` is sent and no old context is resumed. Live, unknown,
and ambiguous sessions remain blocked. Prompt detection supports standard
`user@host:path$` / `bash-version$` POSIX prompts; custom prompts fail closed.
Without a ready shell it starts a **new terminal**. If a configured slot is absent,
Orca creates a top-level
worktree with that basename and validates the resulting path. Repo setup hooks
run only on creation. A private prompt includes the Pad ref/handoff instruction,
rules file and configured footer. Private prompt/overlay files remain for the
session lifetime; remove them after the owning terminal exits.
The default/example footer identifies the current task as already fresh.
`FRESH-READY` is for a **different next task**, only after publishing the new
session's next piece of work, never for the inherited handoff. Keep project-local
footer overrides consistent with this boundary to avoid restart loops.

The launch overlay disables auto-resume, extended context, and model fallback;
sets reserve policy fail-closed and an empty pinned-model fallback chain; and
sets `compaction.modelThresholds[resolved_model]` to `f200000` (fixed trigger,
not a scaled window; see the
[OMP threshold contract](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/config/compaction-threshold.ts)).
The launch receipt is saved before later hooks/metadata updates: an error after
terminal creation is **not** permission to launch again. Inspect `last-launch.json`.
No automatic retries, auto-dispatch from Pad, merge actions, or worktree cleanup.

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
bearer credential. The app refuses wildcard/public bind addresses; direct HTTP
on a configured Tailscale IPv4 address is also supported behind a TLS proxy.

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

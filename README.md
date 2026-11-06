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
`journalctl --user -u fleet-owner-project.service`. To change a running unit,
reinstall, then `systemctl --user restart fleet-owner-project.service`.
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
until delivery is enabled. Initial GitHub collection establishes a baseline, not
a replay of historical PR events. Alerts dedupe until recovery; a recurrence
alerts again. Failed sends remain pending. An ambiguous/missing owner gets a
coordinator-only report. Health and collector errors never restart or stop lanes.

Coordinator messages target `run:<id>`; owning builders receive durable terminal
inbox messages, **not terminal keystrokes**. The service uses the explicitly
configured coordinator handle as message sender; its body identifies fleet.
After an Orca restart, refresh the coordinator handle/run configuration.
Builder handles are re-listed every cycle, never stored as durable identities.
Terminal-only builder mailboxes expire when their terminal closes; the Run inbox
is durable across terminal replacement. Fleet never promises replay into a new session.
Inbox enqueue does not prove the agent woke or read it. `watch-pr` blocks on
only the selected PR (plus project merge-group failures) and exits on changes,
terminal PR state, timeout, or API failure, so an async shell completion can wake
the builder without polling model turns.

Launch checks fresh quota, free disk, builder cap, and slot occupancy. It starts
a **new terminal**; it never types `/exit` or resumes another session. An existing
connected omp terminal makes a slot occupied, even if idle. Finish and close it
explicitly first. If a configured slot is absent, Orca creates a top-level
worktree with that basename and validates the resulting path. Repo setup hooks
run only on creation. A private prompt includes the Pad ref/handoff instruction,
rules file and configured footer. Private prompt/overlay files remain for the
session lifetime; remove them after the owning terminal exits.

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

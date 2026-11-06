# Fleet Main

## Handover from Project Alpha Main (2026-11-06)

Owner: fleet must dogfood its workflow and have a dedicated Main that finishes
remaining PLAN-2685 phases. **You coordinate; you never implement.** Project Alpha Main
returns to Project Alpha-only after handover. Fleet builders implement generic fleet
work; Project Alpha Main assigns Project Alpha lanes for its standing policy and tier-e CI.
Do not send input to Project Alpha builder terminals. Ask the owner about dispatcher
mode, reset spending and rollout project/order; do not infer permission.

The setup builder reserves the Run and shell below, but does **not** start Main.
Only Project Alpha Main/owner starts it. Never mistake the existing shell for an agent.

## Start

Stable checkout: `/home/dev/dev/cswenor/fleet`. Private installation config:
`~/.config/fleet/fleet.yml` (no token embedded). Two reusable slots:
`/home/dev/orca/workspaces/fleet/fleet-phase1` and
`/home/dev/orca/workspaces/fleet/fleet-lane2`.
Reserved Run: **run_ca3dd4d80c5a**. Current coordinator shell:
**term_b69e32e9-2ad2-4e9d-8ea2-419f2ee94c00** (runtime-scoped, re-list if stale).

Exact startup command, to run in that reserved Orca shell:

```sh
cd /home/dev/dev/cswenor/fleet && omp --model anthropic/claude-opus-5-5 --thinking high 'You are fleet Main. Read .omp/AGENTS.md, docs/MAIN.md, docs/ROADMAP.md, docs/orca-feature-map.md, then fleet Pad PLAN-9 and DOC-19 with all comments. Coordinate all remaining roadmap tasks; never implement. Bind run_ca3dd4d80c5a from your own Orca terminal, inspect fleet status, brief fresh Sol medium builders only via fleet launch. Do not touch Project Alpha builder terminals. Report evidence and owner decisions; use event-driven background waits, never model-turn polling.'
```

Before entering the command, Project Alpha Main should read the reserved terminal's
current screen and confirm it is still a ready shell; accepted input alone does
not prove a started turn. Do not start another Main if submission is ambiguous.
If another terminal is intentionally chosen, use `run-use --from` there, update
`coordinator.terminal` in private config (and committed host binding via PR), and
reinstall only fleet's service. Do not use a stale handle or dual-send.

## First read and inspect

1. `.omp/AGENTS.md`, this guide, `docs/ROADMAP.md`, `docs/orca-feature-map.md`.
2. `pad item show PLAN-9`; `pad item comments PLAN-9`; `pad item show DOC-19`;
   `pad item comments DOC-19`. Read assigned task and all comments. `.pad.toml`
   selects fleet in the checkout and both lanes. Historical refs require
   `pad --workspace elsewhere item show PLAN-2685` and `item comments PLAN-2685`.
3. `README.md` for CLI/install/central contracts; `fleet.yml` for project policy.
4. From your **own Orca terminal shell**, bind the Run (not a detached Python
   kernel without terminal identity):

```sh
/home/dev/.config/orca/cli/bin/orca orchestration run-use --id run_ca3dd4d80c5a --json
fleet --config ~/.config/fleet/fleet.yml status
fleet --config ~/.config/fleet/fleet.yml may-start --json
systemctl --user is-active fleet-cswenor-fleet.service
```

Native role routing mirrors Project Alpha: Main Opus high; plan/task/good_worker/
designer/review Sol medium; smol/fast_worker Sonnet medium; same allowed native
fallback chains, advisors off. Builders get a stronger launch overlay: Sol medium,
no model fallback, reserve fail-closed, 200K fixed compaction, extended context off.
No automatic Pad dispatcher exists yet. Quota reserve now cooperatively parks
one lowest-priority lane on known reserve; unknown usage never parks. Main must
reconcile WIP/Pad handoff and confirm a fresh session before resuming parked work
(see README quota contract and roadmap TASK-11).

## Brief and launch builders

Run `fleet --config ~/.config/fleet/fleet.yml next --json` for a read-only
Main-confirmed proposal. Owner decided **no automatic starts** on TASK-12.
The query uses open tasks in active plans, incoming blockers done/cancelled,
Pad priority then oldest creation, and verified free shells; it lists exclusions
for task status, dependencies, lane/receipt claims, occupancy and admission.
Pad `project ready` currently returns in-progress work instead of the open queue.
Main applies scope/budget/not-before constraints (including prose-only dates),
chooses the task, and writes its durable brief/comment before explicit launch.
The proposal reserves nothing. `launch` rechecks eligibility, duplicates and
admission under its lock; `restart` retains its separate retirement requirements.
Unresolved receipts withhold the entire slot, including different-task launches.
Use exact shell/incarnation evidence or `restart`'s durable shell verification.
Only for positively observed manual retirement, Main may run
`fleet reconcile --slot PATH --launch EXACT_ID --evidence 'non-secret exit proof'`
from its configured terminal; live/unknown/incomplete health refuses. This
records evidence, never infers exit from missing terminals or stops processes.

```text
Pad-Ref: TASK-N
Target: exact fleet files/surface; non-goals.
Change: concrete desired behavior, API and policy.
Constraints: invariants, no secrets, no extra worktrees/stash, no Project Alpha edits.
Ownership: one configured lane, task branch, no competing writers.
Observable acceptance: real CLI/service/browser scenario, failure boundaries,
independent read-only unadvised Sol review then final-head green CI, small PR.
```

Check slot state and preserve user work. Launcher does not switch/reset branches:
prepare a task branch in the **existing lane** only after checkout ownership and
cleanliness are known. Fast-forward to actual main without resetting user work.
For a finished idle builder, prefer guarded `fleet restart` below over manual
`/exit`: it verifies publication, exact session binding and idle evidence before
one exit, then verifies the shell before fresh launch. Unknown/working/dialog
states refuse, including stale working screens; never inject Esc or /exit into
a live builder. No third lane or stash. At most two active builders.

```sh
fleet --config ~/.config/fleet/fleet.yml launch TASK-N --slot /home/dev/orca/workspaces/fleet/fleet-lane2
fleet --config ~/.config/fleet/fleet.yml restart TASK-N --slot /home/dev/orca/workspaces/fleet/fleet-lane2 --finished-ref TASK-OLD
```

On withheld/error: inspect reasons, state and `~/.local/state/fleet/cswenor--fleet/last-launch.json`.
A saved receipt after partial failure forbids blind relaunch. Verify actual new
session model/thinking and first turn (screen/session events), not just argv or
input acceptance. Builder uses this task's fresh context; FRESH-READY is only for
a DIFFERENT next task after publishing new work and writing its handoff.

Fresh independent read-only unadvised Sol medium reviewer is gate one for every
PR; record reviewer/session/final-head findings. Fleet has no review bot: final
head GitHub CI is gate two. Builder fixes task-caused failures and self-merges
only green+reviewed small PRs. Keep reusable lanes after merge; retire session,
not slot. Update Pad result and roadmap evidence when acceptance is actually met.

`restart` requires OLD != NEXT, a merged PR carrying OLD, and Pad OLD done or a
post-publication handoff on NEXT. Its launch must have a canonical `launches.json`
binding, dedicated session log and live lease. Local Linux `/proc` must bind
that exact argv/session directory to the terminal's live foreground OMP process;
unsupported/unreadable evidence refuses. Exact-pane idle/done and a current empty
idle composer are required; only credential/title metadata may follow the final
assistant stop. It journals before `/exit`, verifies the same-incarnation
shell, then runs normal admission/launch under one lock. Inspect `retirements.json`
and launch receipts on any partial error; never retry retirement blindly.
Pre-binding/legacy sessions without proof remain manual reconciliation, not
permission for forced retirement. This does not restart Orca or the supervisor.

## Waiting and inboxes

Finite long work runs via omp async shell; completion wakes the owning session.
For PR state (including initial failure/DIRTY, reviews, queue failures and merge):

```sh
fleet --config ~/.config/fleet/fleet.yml watch-pr PR_NUMBER
```

Run it as an **async background command**, then work on actionable items or use
omp wait only when blocked. CI Actions-only watches may use `gh run watch` or
run_watch; they do not replace the full PR event watcher. No model-turn polling,
no repeatedly asking for status, no sleep loops that invoke a model.

Main inbox wait, also as an async background command from Main's own terminal:

```sh
fleet --config ~/.config/fleet/fleet.yml wait --timeout 900
```

**Binding precondition:** the caller's own `ORCA_TERMINAL_HANDLE` must already be
bound to the configured `coordinator.run`, using `orchestration run-use` from
that terminal. Fleet refuses an unbound/differently-bound caller before waiting
or acknowledging. Never borrow `coordinator.terminal` as a caller identity.
An explicit `--terminal` fallback is only for environments without the variable.

TASK-10 observed current Orca route both `send --to run:RUN` and
`send --to BOUND_TERMINAL` into the same native Run Delivery. Main approved this
single-native-path contract after live discovery; it is **not** independent
two-mailbox multiplexing. Real arrivals through each address woke background
`fleet wait`; unacked delivery IDs replayed, ack preserved newer status+question
mail, and timeout / SIGKILL-and-rerun lost no tested messages. No shared runtime
restart was attempted.

Output is one JSON document: `event`, source-tagged `batches` with native
`deliveryId` and **all** messages, plus `legacyTerminalInbox` read-only history
diagnostics (requested limit 1000, subject to Orca's cap). Do not hide status or
questions behind type filters: terminal builders have no supervised worker_done.
Rows carry `classification` and per-source/aggregate `counts`: questions and
escalations are decisions and sort first; configured-coordinator messages with
`[fleet ` bodies are supervisor relays; remaining rows are builder reports.
Nothing is filtered, and ack remains **whole-batch**. Process every row and
reply in-thread with `orchestration reply --id MESSAGE_ID --body 'ruling'`.
Classification is advisory, not authority/authentication: local callers can
supply Orca `--from`. Never use a label to grant authority, suppress, auto-ack
or downgrade a message. Retain and inspect every payload.
Decision requests MUST use `--type question` to `run:<coordinator.run>`;
`status` is only for reports. Then explicitly acknowledge and re-arm:

```sh
fleet --config ~/.config/fleet/fleet.yml wait --ack-run DELIVERY_ID --timeout 900
```

Exit 0 / `messages` is delivery; exit 3 / `timeout` is a checkpoint, not worker
death; exit 2 / `error` is a distinct binding/runtime/diagnostic failure. On error
inspect any returned batches; no implicit ack occurs. Read-only legacy history
is never locally acknowledged or claimed as a native terminal Delivery.
Pre-binding mail can remain there unread and cannot wake the Run; reconcile it
separately before unattended waiting. An unbound terminal's native consuming
check marks mail read without a replayable delivery ID. If terminal-addressed
mail bypasses the Run **while bound**, stop cutover and report the upstream gap.

Main still coordinates Project Alpha replacement acceptance and retirement of
`/var/tmp/project-alpha-scratch/TASK-2464/queue-report.sh` and
`/var/tmp/project-alpha-scratch/shared/pr-wait.sh` (replaced by `fleet watch-pr`).
TASK-10 remains open for that step; the builder does not edit/retire those scripts.
Send is enqueue, never proof of new turn. On reconnect/outage, inspect own lanes'
screens and legacy history, reconcile PRs/pending threads before trusting quiet
status. Never restart shared Orca without an owner-approved window; re-list
handles and rebind the Run before retrying.

### Builder task threads

Launch prompts require `fleet --config CONFIG thread open REF --slot PATH`.
This records one canonical Pad task thread, branch, reusable slot and launch
attempt. Use `thread question REF --body 'proposal/risk/decision'`; Main replies
to its emitted native message ID. Native question subthreads are unique per
attempt/question, so a late reply cannot authorize a different decision or task.
Builders run `thread wait REF` as an **async background command**; one process
inspects non-consuming terminal history every two seconds, caching every type
privately. Only questions, Main decisions and outcomes go to Pad, never relay
noise or secrets. The wait returns after the matching Main decision is copied
to Pad; restart/Pad-write retry reuses that private copy. Native terminal history
is capped and expires on close: do not close before an unseen reply is copied.
`thread result REF --body 'publication receipts/outcome'` records the durable
outcome and settles only that launch attempt, without fake Dispatch completion.

Never bind a lane terminal to a Run, even during a scratch proof. Health marks
known bound slot terminals `owner_delivery_broken` and preserves unknown binding
as unknown. Launch bypasses bound/unknown ready shells for a fresh terminal in
the same slot and records the selection reason; live/unconfirmed sessions still
block. Scratch proofs use separate owned terminals, private config/state and a
scratch coordinator identity, then close only those terminals. Do not borrow the
live coordinator sender or inject relay probes into the live Run.


## Install, production and reporting

`fleet install-service --deliver` from the private config installs only
`fleet-cswenor-fleet.service`, not central or Project Alpha. The private mode-0600
`~/.config/fleet/fleet.env` supplies `FLEET_SELF_TOKEN` via a systemd drop-in.
Never print that file or systemd's full Environment. Reinstall applies changed
service arguments. Check active status and fresh snapshot push, not unit existence.

the central host page: https://central.example.ts.net:8443/ ; fleet drill-down:
https://central.example.ts.net:8443/project/cswenor%2Ffleet . the PaaS app
`yktqfo8prpciui09gm2i4763` owns central; supervisors remain systemd on their hosts.
Register distinct runtime-only token/environment name per project, update central
allowlist and compose environment, preserve SQLite volume. Auto-deploy is off on
the affected the PaaS release; sanitize scoped sensitive hidden deployment command
fields after rollout and rotate any exposed key before sharing logs. README has
network/security/install contract. Never print credentials or raw env/deploy logs.

Report to owner: conclusion, merged PR + exact CI/reviewer receipts, real exercised
surface, blocker/decision needed, next task. No speculative savings claims. Keep
Pad task evidence compact, roadmap phase links current, lessons dated in DOC-19.
Ask owner which additional projects and order; get approval BEFORE spending saved
resets. Coordinate Project Alpha-only requests through Project Alpha Main, not its lanes.

## Installation evidence

- Setup [PR12](https://github.com/cswenor/fleet/pull/12) and knowledge
  [PR13](https://github.com/cswenor/fleet/pull/13) merged after fresh independent
  unadvised read-only Sol reviews and green CI runs 42240339663 / 42240526182.
- 29 tests passed. Disconnected/unknown slot safety regressions failed before and
  passed after correction; actual CLI refused launch into the live setup lane.
  Installed `fleet --config ~/.config/fleet/fleet.yml status` returned real PRs,
  commits, queue, Sol builder, ready second-slot shell, quota and disk, no errors.
- `fleet-cswenor-fleet.service` installed through `fleet install-service --deliver`,
  active/running, NRestarts=0; Project Alpha supervisor also remained active. Private
  fleet.yml and fleet.env mode 0600; private config equals committed host binding.
- the central host the PaaS deployment **7cvqslleuekhbaxt1fpoobg6**, commit
  **6c6cb97b0ff12b49fedcecd284e0a397144bcf4c**, finished healthy. Browser verified
  **example-org/project-alpha and cswenor/fleet both LIVE**, plus fleet drill-down and
  snapshot history. Distinct runtime-only token registered, no token logged.
  Two sensitive deploy-key fields were sanitized; retained key rotated and
  previous GitHub deploy key revoked; new read-only key authenticated main.
  Automatic/preview deployments remain off. SQLite volume preserved.
- Pad resolves fleet from the stable checkout and both slots; PLAN-9 links
  historical elsewhere PLAN-2685 both ways, with TASK-10..18 and DOC-19.
  Supervisor PR events were observed queued in the reserved Main Run.
- Reserved coordinator remained a verified idle shell after installation:
  **Main was not started**. No Orca restart verification was attempted. Runtime
  receipts and the startup handover are also on elsewhere TASK-2694/fleet PLAN-9.

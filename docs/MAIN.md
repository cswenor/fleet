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
No automatic Pad dispatcher or active-lane quota pausing exists yet.

## Brief and launch builders

Use fleet Pad ready queue (`pad project ready`; discover installed command help
if schema differs). Pick an eligible roadmap task within owner-approved scope.
Write the durable task brief/comment first; launcher tells builder to read it.

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
prepare a task branch in the **existing lane** only after the old session exits
and checkout ownership/cleanliness is known. Fast-forward to actual main without
resetting user work. Never create a third lane, stash or inject /exit into a live
builder. At most two active builders. Launch only:

```sh
fleet --config ~/.config/fleet/fleet.yml launch TASK-N --slot /home/dev/orca/workspaces/fleet/fleet-lane2
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

Main Run wait, also as an async background command from Main's own terminal:

```sh
/home/dev/.config/orca/cli/bin/orca orchestration check --run run_ca3dd4d80c5a --wait --timeout-ms 900000 --json
```

Do **not** restrict wake to worker_done: fleet terminal builders and supervisor
send `status`, questions/escalations may also arrive. Returned Delivery is whole
FIFO batch even with a type wake filter. Process every message, reply in-thread
with `orchestration reply --id MESSAGE_ID --body 'ruling' --json`, then acknowledge
its deliveryId on the next check (`--ack DELIVERY_ID`). Re-arm in background;
timeout is a checkpoint, not worker death. Never invent worker_done for terminal
builders with no active Dispatch.

**Known gap:** a Run wait is not a terminal mailbox wait. Require fleet reports
and questions to target `run:run_ca3dd4d80c5a`; at each natural checkpoint inspect
terminal mail separately:

```sh
/home/dev/.config/orca/cli/bin/orca orchestration inbox --terminal term_b69e32e9-2ad2-4e9d-8ea2-419f2ee94c00 --json
```

This is honest interim Run-only blocking coverage, **not** a claimed solution for
unattended terminal-addressed mail. Make TASK-10 the first wake-capability brief:
one fleet wait covering both paths, real two-path wake and ack/replay proof.
Do not claim terminal delivery wakes the Run, and do not retire Project Alpha's existing
zero-model script watcher until that acceptance. The existing
`/var/tmp/project-alpha-scratch/TASK-2464/queue-report.sh` is Project Alpha-specific (reads
its terminals and queue), not a fleet command to run against fleet. Its retirement
and `/var/tmp/project-alpha-scratch/shared/pr-wait.sh` replacement are coordinated with
Project Alpha Main. Fleet Main must not silently inherit their project-specific code.

Send is enqueue, never proof of new turn. On reconnect/outage, inspect own lanes'
current screens and both inbox surfaces, reconcile PRs and pending threads before
trusting quiet status. Never restart shared Orca without owner-approved window;
handles may change, restored_unconfirmed is not permission to retry.

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

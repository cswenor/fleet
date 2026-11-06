# Orca + omp feature map

Cleaned project-owned edition of the 2026-11-06 TASK-2464 research. Historical
research was documentation-only; live findings below supersede its proposed
architecture. Audit receipts below contain only non-secret evidence identifiers.
Policy/status/remaining acceptance live in [ROADMAP.md](ROADMAP.md), not here.

## Evidence hierarchy

1. Actual runtime/session evidence proves execution, resolved model and thinking.
2. Selected CLI's version-matched `skills get orca-cli` / `skills get orchestration`
   documents contracts; use `--reference` for gated details and `--help` for flags.
3. Historical guide archive: `/var/tmp/project-alpha-scratch/TASK-2464/orca-docs/`.
4. omp settings/CLI/session/async/broker/extension/RPC documentation informed the
   initial research; consult installed version before relying on optional APIs.

Select CLI once: ORCA_CLI_COMMAND if set, otherwise orca-dev in a dev context,
otherwise orca-ide on Linux outside managed terminals. Never blindly run bare
`orca` there (screen-reader collision). Do not switch builds after a failure.

## Built-ins versus fleet glue

| Need | Orca / omp built-in | Fleet glue and boundary |
|---|---|---|
| Fresh pinned task session | omp --model / --thinking, project roles/config, --config overlay | `fleet launch`: explicit Sol medium, fresh context, no fallback/auto-resume, 200K compaction. Guard quota/disk/cap/slot; private prompt and launch receipt. |
| Supervised resource lifecycle | worker-start/list/show/read/stop/abandon/retain/release; Task/Dispatch; retry-request/request-show | **Not builder launch policy today**: preamble publication conflict and bare-Sol high. Preserve docs as capability, not permission. |
| Identity | Worktree ID, Run/Task/Dispatch/request IDs; Pad-ref convention | `fleet thread open` links canonical Pad thread/ref, actual branch, reusable slot and launch attempt. Native question subthreads isolate late answers; no fabricated terminal-builder Task/Dispatch. |
| Main event wait | orchestration check --wait, FIFO Delivery replay until ack | `fleet wait` requires caller bound to configured Run; current Orca routes both Run and bound-terminal addresses there. Read-only legacy history diagnostics; Project Alpha cutover remains TASK-10. |
| Builder waiting | Async bash completion; Actions run_watch; terminal wait tui-idle/exit | `fleet watch-pr` covers reviews, initial failure/DIRTY, merge-group failures and merged/closed, not only Actions. |
| Durable communication | send to run/dispatch/terminal; ask/reply; checkpoint check/ack | Send enqueues only, no guaranteed turn. Use in-thread replies; do not report terminal launch as supervised worker_done. |
| Health/liveness | worker-list projection.liveness/attention/nextAction; PTY observation separately; terminal screen | Fleet collects terminal/worktree inventory, current model, provider/dialog/idle flags and strays. Unknown is not stopped. Never auto-destroy lanes. |
| PR ownership/events | Worktree linkedPR/linkedItems, branch metadata | gh REST/GraphQL for PR/check/review/queue/commit state; unique branch->worktree->active terminal only. Coordinator-only on ambiguity; durable dedupe receipts. |
| Budget | omp usage, reserve policy, empty fallback chains; broker account pooling | Aggregate windows without identities; fail-closed new-start gate. Known reserve cooperatively parks one lowest-priority lane with identity-bound delivery, exit-gated re-evaluation and recovery reporting (TASK-11). Unknown usage never parks; saved resets require prior recorded owner approval, and Fleet never spends them. |
| Ready queue/slots | Pad ready/dependencies; Orca Run does not schedule | Explicit `fleet launch`, two slots. Main-confirmed now; owner must decide automatic dispatcher. No duplicate/unknown-slot launch. |
| Per-project setup | .omp/config.yml, .pad.toml; Orca repo setup/optional orca.yaml recipes | fleet.yml + one supervisor/project; systemd user install, private state/env. No global watcher. |
| Cross-project central | No native multi-project status feed documented | Per-project outbound HTTPS snapshot -> the central host/the PaaS read-only SQLite app, tokens per project, tailnet view. Local supervision survives outage. |
| Disk | OS free-space accounting; omp gc only handles omp storage | Fleet reports reserve and blocks starts; no destructive cache cleanup. |

## TASK-31 stock-first lifecycle audit (2026-11-06)

**Verdict: retain the policy composition; no equivalent replacement proved.**
Orca **1.4.242**, omp **18.17.0**, source baseline `01153fba` plus the merged
TASK-32 review/reset overlay changes present in this checkout. This audit covers
standalone terminal builders, not supervised Dispatches. A successful stock
primitive is not proof it implements Fleet's surrounding admission/ownership policy.

### Function-level decisions

| Fleet function / contract | Stock candidate | Live evidence below | Verdict and risk |
|---|---|---|---|
| `launcher.launch`, `_preparation_receipt`: guarded fresh task, overlay, dedicated session directory, journaled ambiguity | `agent launch --agent omp --model … --effort medium`; `worktree create --agent` | L1 rejects effort; L2 launches actual **high**, not medium; L3 lacks overlay/session pins | **Keep**. Stock launch does deliver Pad-Ref without a supervised preamble, but does not satisfy thinking/overlay contract. Admission, slot identity, hooks and failure receipts remain project composition. |
| `restart.restart`, `_target`, `_idle`, `_session`, `_foreground`, `_completion`: published finished task, exact foreground session, empty composer, one exit, verified shell | `terminal wait --for tui-idle/exit`, `worktree ps`, supervised `worker-release` | R1 reports idle while PTY runs; R2 exit wait times out; H1 sees exact-pane done | **Keep**. Idle is not publication, empty draft, foreground ownership or fresh-task admission. Release belongs to settled supervised workers, not these builders. TASK-34 separately owns restart race/self-claim corrections; keeping composition is not a safety certification of current code. |
| `mailbox.wait`, `request`, `delivery`, `classify`: bound caller, validated whole Delivery, diagnostic legacy history, decision-first presentation | `orchestration check --wait/--ack`, `run-current`, `inbox` | M1 replay/ack and M2 real blocking wake succeed | **Keep thin wrapper**, already using stock delivery. Native replay/wait must not be reimplemented. Wrapper adds configured-Run ownership/error contract and legacy diagnostics; classification never grants authority or filters ack. |
| `threads.identity/open_thread/current/question/wait/result`, Pad recording: task/attempt identity and durable decisions | `orchestration ask`, `send --thread-id`, `reply --id`, `inbox` | T1 ask rejects standalone terminal; T2 send/reply preserves subthread and unread history | **Keep**. Native send/reply already carries messages; no Dispatch means native blocking ask is unavailable. Stock inbox has no Pad copy or authoritative Fleet attempt mapping. History polling stays in one background process; eviction/terminal-close durability remains limited. |
| `dispatcher.unresolved/receipt_reasons/reconcile`, `slot_reasons`: unresolved launch fences and explicit Main reconciliation | `agent launch --retry-launch`, `worker-list` liveness, `terminal show`, `run-current` | L2 returns operationId; H1/H2 expose terminal versus supervised inventory; H3 proves binding | **Keep**. Stock operation replay is useful only for a launch performed through that API. Empty supervised inventory cannot retire a standalone Fleet launch receipt or authorize reconciliation. No live receipts were reconciled in this audit. |
| `health.collect/binding/active/owner`, `classify/shell_ready/footer_model/model_matches/branch`: complete slot inventory and conservative owner mapping | `terminal list/show/read --screen`, `worktree list/ps`, `worker-list` | H1 standalone agent appears in ps/show; H2 supervised list empty; H3 explicit null binding | **Keep composition** over native inventory. PTY connected, agent done, shell ready and supervised liveness are different facts. Titles/footer models are diagnostics, not ownership proof; actual session events remain stronger. |
| `orca_capture.collect`, `_processes/_session/_stat/_pick`: private cross-restart metadata projection | `status`, `terminal list`, `run-current/run-show`, `inbox`, `worker-list` | C1 real collection reports incomplete proc scan and unavailable read-only Delivery identity | **Keep**. Stock supplies runtime/mailbox metadata, not the local proc/session/systemd join or safe redaction. Capture is sequential and observational, not idle proof; no runtime restart was performed. |

### Commands and observed receipts

In this section `O` means `/home/dev/.config/orca/cli/bin/orca`; `W` is this
existing configured lane (`path:/home/dev/orca/workspaces/fleet/fleet-lane2`).
`P` denotes the owned standalone probe terminal; `C` the separately created
scratch coordinator terminal; `R` its scratch Run `run_692229535c5c`.
These labels are explanatory, not shell setup instructions or reusable handles.
Both scratch terminals were closed with native `ptyKilled: true` receipts.
No extra worktree, builder-terminal Run binding, sudo, runtime restart or shared
checkout deployment occurred.

- **L1 — requested effort:** `O agent launch --agent omp --worktree W --model
  openai-codex/gpt-6.1-sol --effort medium --surface terminal --prompt
  '<Pad-Ref: TASK-31; read-only probe>' --json` returned `ok:false`,
  `invalid_argument`, **“Agent omp model openai-codex/gpt-6.1-sol does not
  support effort medium.”** No successful launch receipt was returned.
- **L2 — actual execution:** same command without `--effort` returned
  `outcome.kind:terminal`, `prompt.outcome:handed-to-terminal`, operationId
  `1793989811326-6f1568bdf7d64ec6aa132caba0891745`. Screen displayed the
  Pad-Ref prompt and `STOCK-LAUNCH-PROBE` response. Open session log
  `01a1b220-1f92-77ca-8587-bfd3716b241b` recorded
  `model_change.model:openai-codex/gpt-6.1-sol`,
  `resolvedModelIsFallback:false`, then
  `thinking_level_change.thinkingLevel:high`, `configured:null`.
  No tools were used by the probe. Thus fresh model/prompt delivery works,
  but bare model launch still does not prove Sol medium.
- **L3 — policy injection boundary:** `O agent launch --help` and
  `O worktree create --help` expose model/effort and replay, not arbitrary
  omp `--config` or `--session-dir`. Inspection of the probe's local process
  arguments found no `--thinking`, `--config`, `--session-dir` or `--resume`.
  This does **not** prove every inherited setting unsafe: no-fallback,
  fail-closed reserve, fixed 200K, no-auto-resume and no-auto-reset equivalents
  are **unverified for this launch**, not inferred from its successful response.
  `worktree create --agent` was **not executed**: creating extra worktrees is
  forbidden. Shared help semantics alone do not prove runtime equivalence.
- **R1/R2 — idle versus exit:** `O terminal wait --terminal P --for tui-idle
  --timeout-ms 60000 --json` returned `satisfied:true`, `status:running`,
  `exitCode:null`. `--for exit --timeout-ms 1000` on that idle agent returned
  `ok:false`, `timeout`. `tui-idle --timeout-ms 1000` on scratch shell C also
  timed out. Neither primitive replaces positive same-incarnation shell proof
  after OMP `/exit`; no Fleet restart was attempted.
- **M1 — native FIFO:** `O orchestration send --from P --to run:R --type
  question --thread-id task31-audit-native … --json` enqueued
  `msg_a85b3c6ad37a`. `check --terminal C --run R --json` returned
  `delivery_d21903e327f1`; repeating returned the same ID, `replayed:true`.
  After processing the question and reply, `check … --ack
  delivery_d21903e327f1 --wait --timeout-ms 1` returned acknowledged ID,
  `count:0`, `timedOut:true`.
- **M2 — native blocking wake:** started `O orchestration check --terminal C
  --run R --wait --timeout-ms 10000 --json` in a subprocess, then sent status
  to **C's terminal address**. Send normalized destination to `run:R`;
  blocked check returned `msg_77eb29d9bf8b` in
  `delivery_4a0a90a26b79`, `timedOut:false`. Explicit ack returned timeout
  with zero messages. This proves the stock wait path, not an agent turn.
- **T1/T2 — terminal questions:** `O orchestration ask --from P --to run:R
  --question '<scratch question>' --timeout-ms 1 --json` returned
  `dispatch_inactive`: **“ask requires an active supervised Dispatch.”**
  `O orchestration reply --from C --id msg_a85b3c6ad37a --body
  '<scratch decision>' --json` instead created `msg_f32e31c3c8b9`,
  addressed to P, type status, same `task31-audit-native` thread.
  `O orchestration inbox --terminal P --limit 10 --json` retained that reply,
  `read:0`. This exercised native transport only, not Fleet's Pad-copy path.
- **H1/H2/H3 — inventory:** `O terminal show --terminal P --json` reported
  connected/writable/non-orphaned OMP with an incarnation ID.
  `O worktree ps --json` showed three live terminals in this lane (builder,
  probe, scratch shell), `terminalVerdict:live`; probe's exact pane had
  `state:done`, `programStatus.state:done`. `O orchestration worker-list
  --run R --json` returned `workers:[]`, page total 0, `hasMore:false`,
  scope source `flag`, despite that live standalone agent. `O orchestration
  run-current --from P --json` and the builder's own identity both returned
  explicit `run:null`. Supervised listing is not standalone inventory.
- **C1 — actual capture:** invoked `orca_capture.collect(load(private_config),
  ['fleet-cswenor-fleet.service'])` from a throwaway Python cell. The native
  queries listed in its table row produced a ready 1.4.242 runtime, 15
  terminals and 17 process rows; own supervisor active/running, NRestarts 0.
  Collection honestly retained `proc_scan_incomplete` and
  `native_delivery_identity:unavailable_read_only_api`. Only sanitized
  aggregates were emitted; the host snapshot was not committed.

No production replacement is justified by these receipts. A future upstream
OMP launch effort/config/session interface or standalone blocking question API
needs a new real probe and Main approval before removing the corresponding glue.

## Orca capabilities and caveats

### Placement and launch

- `worktree create` creates tracked checkout; `--no-parent` is independent lineage,
  not a Git base choice. Existing worktree selectors are exact full repo::path IDs.
  `agent launch` starts in an existing checkout; launch outcome may be chat or
  terminal depending on settings, and receipts distinguish prompt journal/input
  from proven started turn. Do not duplicate an ambiguous launch; inspect receipt.
- Supervised `worker-start --task/--spec --worktree --agent omp --model` supports
  existing or new placement, fresh agent terminal by default, and alias selectors.
  omp effort is unsupported by worker-start; role resolution can supply thinking.
  No arbitrary omp --config/--profile argv on that supervised API.
- Phase 0 actual bare Sol selected **high**, @good_worker **medium**. Both effective
  receipts echoed requested model/alias and effort=null. Actual omp model_change
  and thinking_level_change events, not worker prose, established resolution.
- Injected worker preamble prohibits posting to GitHub/other channels, requires
  checkpoint checks, heartbeat cadence, ask, exactly one worker_done with both
  Task/Dispatch IDs and outcome, then idle. This conflicts with self-publication;
  do not paste it into fleet terminal builders or manufacture Dispatch authority.
- `dispatch --inject` leaves an operator-owned process unsupervised. Dry-run
  `--return-preamble` creates no attempt. Tracking rows alone do not supervise.
- `fleet launch` validates ready shell identity immediately before reuse; no /exit
  injection. `fleet restart NEXT --slot PATH --finished-ref OLD` separately
  retires only a published/handed-off, exactly bound idle session: one journaled
  /exit, same-incarnation verified shell, then normal launch under one lock.
  Unknown/working/dialog/disconnected evidence refuses; no forced interrupt.
  Missing configured slot may create one Orca top-level worktree; no ad hoc
  side worktrees allowed.

### Lifecycle and recovery

- Run = durable namespace/coordinator inbox, not scheduler. Task = work; Dispatch
  = one authoritative attempt. Successful valid worker_done settles both.
- worker-list projection.liveness is fleet verdict: live / unverifiable / exited;
  worker-show observation is PTY status, not equivalent. Attention and nextAction
  guide recovery; page cursor results before claiming complete inventory.
- restored_unconfirmed, stale status, host unavailable or absent observations do
  not authorize stop, release or retry. Release only accepted settled worker;
  abandon fences ownership without killing; stop acts only on proven owned PTY.
- Retry of failed/stopped attempt repeats task, retry-of, explicit placement,
  agent/model; placement is not inherited. Lost mutations use request replay;
  three failed attempts circuit-break. Runtime restart alone is not failed attempt.
- Terminal handles are runtime-scoped. Re-list after restart/stale-handle; use
  replacement only, never dual-send. Rebind Run, update private supervisor config,
  reconcile possible live workers. Actual restart/model/handle/Run replay semantics
  remain **unverified**, not solved by fresh launch evidence.

#### TASK-13 restart runbook (phase A; phase B not exercised)

The latest owner authorization permits **one unattended shared Orca restart**
through an approved external conductor; it supersedes the owner-present,
immediate-final-go and owner-manual-stop/start requirements recorded earlier.
Scheduled window: **2026-11-07 14:00 UTC**; Main must give Project Alpha Main the
cross-project notice by **13:00 UTC** (at least 60 minutes). This builder
assignment is **rehearsal only**: no shared restart, production scheduling or
rollout. Publishing the tooling still requires independent review, CI and
Main's announcement confirmation. The rehearsals below do not establish
shared-runtime restart acceptance.

Orca 1.4.242 has no documented isolated profile startup recipe or quit command;
`serve --data-root` is stale-lock recovery, not isolation. The approved target
is the stock **system** unit `orca-serve.service`, not an executable launch or
generic signal. The sole sudo mutation exception is exactly
`sudo -n systemctl restart orca-serve.service`; read-only unit show/status is
permitted. No other sudo, direct process signal, second server, lock recovery,
stash or extra worktree is authorized.

**Before scheduling**

1. Project Alpha Main prevents new build/cohort/CG jobs, finishes or safely stops
   current work, persists Pad handoffs and confirms all eight lanes safe/idle.
   Fleet Main does the same for fleet lanes. Unknown/disconnected is not idle;
   no interrupt injection. Keep both project supervisors running.
2. Deploy TASK-22 with both Mains informed. Each Main writes a durable project
   handoff with exact fresh-Main startup argv, Run ID, task/PR/WIP evidence and
   unresolved attempts. Start a fresh Main only after proving its incumbent
   exited and no restored attempt exists; reconcile survivors without duplicate
   launches. Builders are not automatically relaunched.
3. Builders require explicit launch records, actual cmdline `--session-dir`,
   readable session JSONL, stable PID/start ticks and complete actual
   model/thinking events. Manually started Mains may instead use the exact
   configured coordinator handle plus BEFORE-captured PID/start ticks, cwd,
   argv shape and session file, with complete model/thinking. These explicit
   Main records are not launch receipts or guesses from title/cwd. Missing/
   unknown bindings block; project Mains safely retire or replace unverifiable
   legacy sessions first, never kill live work.
   Fleet lane readiness requires exact launch-bound positive idle proof or a
   positively verified shell, not merely absence of activity.
4. Save the last observed `fleet wait` receipt privately, mode 0600: native
   Delivery ID, message IDs and read/ack state only; explicitly record no
   outstanding Delivery when applicable. Keep known delivered-unacked and
   pending IDs for replay comparison without discarding/acking real work.
   No bodies, subjects, payloads, prompts or credentials. Orca 1.4.242's read-only
   API cannot recover native unacked Delivery identity; inbox `read` is not ack.
5. Main supplies the approved private plan at exactly
   `/home/dev/.local/state/fleet/orca-restart/plan.yml`, pins the reviewed fleet
   checkout, verifies a live user systemd manager and uses an **external shell**
   that survives Orca exit. Plan files are 0600 in private 0700 directories;
   journal/capture directories are 0700 and their files 0600. Do not commit or
   publish the plan, confirmations, session records, journal or full snapshots.

**Private plan schema (version 1)**

The following is a schema example, **not an executable approved plan**. Every
`<...>` value (including integer placeholders) is an operator input obtained
from current verified evidence; do not invent commands, runtime IDs or PIDs.
Repeat `projects` and `session_records` to cover all projects and every Main
and builder. All filesystem paths are absolute; IDs/project names are safe
slugs. Unknown keys are rejected. There is no launch/environment schema.

```yaml
version: 1
id: "<approved-safe-slug>"
state_dir: "/<private-absolute-journal-directory>"
execute_after: "2026-11-07T14:00:00+00:00"
expires_at: "<approved-timezone-qualified-ISO-expiry>"
service: orca-serve.service
allow_systemd_sigkill: true # Approved by ruling msg_dc4d07132417; default false.
target:
  pid: <integer-exact-target-pid>
  start_ticks: <integer-proc-start-ticks>
  exe: "/<exact-proc-executable>"
  argv: ["<exact-target-argv0>", "<each-exact-argument>"]
  cwd: "/<exact-target-cwd>"
  runtime_id: "<current-status-runtime-id>"
  runtime_pid: <integer-current-status-runtime-pid>
projects:
  - name: "<project-safe-slug>"
    mode: main
    config: "/<project-private-fleet-config>"
    handoff: "/<durable-project-main-handoff>"
    worktree: "/<existing-project-main-worktree>"
    command: ["<approved-fresh-main-argv0>", "<each-exact-argument>"]
    delivery: false # Operator chooses approved supervisor delivery policy.
  - name: project-beta
    mode: main
    config: "/home/dev/.config/fleet/project-beta.yml"
    worktree: "/home/dev/dev/example-org/project-beta"
    handoff: "/home/dev/.local/state/fleet/cswenor--project-beta/main-handoff.md"
    command: [] # Unresolved template; fill the exact approved fresh argv privately.
    delivery: false # Operator input, not a rollout recommendation.
session_records:
  - "/<canonical-launches-json-or-explicit-main-record-json>"
confirmations:
  project_alpha_idle: "/<private-project-alpha-idle-confirmation-json>"
  fleet_idle: "/<private-fleet-idle-confirmation-json>"
timeout_seconds: 60 # Optional; integer 1..300, default 60.
launch_attempts: 2 # Optional; integer 1..3, default 2.
```

Each confirmation is JSON with exactly the operator's plan ID and fresh,
timezone-qualified timestamps, for example this **non-executable template**:

```json
{"plan_id":"<approved-safe-slug>","confirmed_at":"<ISO-time>","expires_at":"<ISO-time>","idle":true}
```

Both confirmations must be unexpired and less than one hour old at execution;
they may precede `execute_after` so Mains can confirm and become idle before
the external timer fires. Their expiry cannot exceed the plan expiry. A timer
is not an idle confirmation.

Main ruling `msg_1bd7a113d407` defines `fleet_idle` as attesting safe-idle for
both fleet and Project Beta Main/lane work; Fleet Main coordinates Email for
this restart. The existing per-project native/process idle gates still run for
Email too. No third confirmation field or gate waiver was introduced.

A launch-bound Main
record includes `session_dir`, `terminal`, `worktree: {path, id}` and
`terminal_incarnation_id` with normal launch identity fields. The approved
alternative for a manually started Main is an explicit JSON record with
`terminal`, `pid`, `start_ticks`, `session_path`, `worktree: {path, id}` and
`terminal_incarnation_id`. Its exact configured coordinator handle must match
the BEFORE capture's process/cwd/argv-shape/session-file evidence; survival
requires the same boot/PID/start/session. This exception does not loosen
builder launch/session-directory requirements. Handoffs must be nonempty,
private (0600), and modified no earlier than T-30 minutes, never future-dated;
all three Mains refresh them at T-15. Validate each project's config with fleet's
normal config loader, terminal bindings and readable handoffs.

Refresh manual records with explicit `session_id` and `boot_id`; changed values
are refused rather than inferred from a title or working directory alone.

Main ruling `msg_65510e97c8bc` permits FD-less discovery only through the exact
live revalidated argv `--session-dir`: absolute, process-real-uid-owned, no
symlink component, no group/other write. Exactly one canonical root must have
the recorded session ID, matching cwd and complete actual model/thinking.
Its header timestamp must follow that process's start; prior/future logs refuse.
No default directory guessing. `--resume`, `--continue` and `--restore` retain
FD-only binding, including the legacy resumed Project Alpha Main. Capture, preflight
and mutation authorization share the same validator; cached argv/directory/
session evidence is revalidated before every live mutation.

Approved project bindings: Project Alpha config
`/home/dev/.config/fleet/project-alpha.yml`, Run `run_9629bd883de6`, handoff
`/var/tmp/project-alpha-scratch/TASK-2464/main-handoff.md`; Fleet handoff
`/home/dev/.local/state/fleet/cswenor--fleet/main-handoff.md`; project-beta
Run `run_c018565b177d` now has a Main, started by the owner at 22:05Z on
2026-11-06. The private plan treats all three projects as Main mode. Main
relayed Email's handoff path
`/home/dev/.local/state/fleet/cswenor--project-beta/main-handoff.md`,
to be written by Email Main at T-15, mode 0600 and subject to the same freshness gates.
Email's exact fresh startup argv comes from its `docs/fleet.md` Future Main
startup command. Main supplied Project Alpha's exact private startup command and
approved only adding `--session-dir` immediately before each unchanged prompt.
Fresh directories belong under each project's own private fleet state (`main-sessions-<UTC>`),
created 0700 only if a fresh Main is positively required. The journal and
durable messages to both the project Run and initiating Fleet Main record
the new handle and session directory. The private plan remains **pending**:
future timing, current process receipts, handoff freshness, idle confirmations
and every legacy builder's launch evidence must all pass before scheduling.

The observed stock unit has `KillMode=mixed`, `SendSIGKILL=yes` and a 90-second
stop timeout. Final ruling **`msg_dc4d07132417` approves this existing policy**
and bounded recovery; `allow_systemd_sigkill: true` may record that approval.
Default false still fails closed. No unit edits are authorized. Record actual
`TimeoutStopSec`, restart duration, whether systemd reached SIGKILL, and PTY
scopes outside the service unit; do not infer those scopes stopped with it.
The conductor never sends SIGKILL/custom signals itself or changes kill policy.

**Read-only preflight and external conductor**

CLI contract: `fleet orca-restart --plan PATH [--dry-run]` (global `--config`
selects the primary project). First run the dry run externally:

```sh
fleet --config /home/dev/.config/fleet/fleet.yml orca-restart \
  --plan /home/dev/.local/state/fleet/orca-restart/plan.yml --dry-run
```

Dry run is strictly read-only: no journal creation, waiting/scheduling, stop,
launch, terminal/config/service updates or mail consumption. It reports
blockers rather than bypassing missing confirmations, timing or evidence.
Execution also refuses before `execute_after` or after `expires_at`; it never
waits until the appointed time. External systemd scheduling provides that wait.
Preflight checks the exact same-user target `/proc` executable, complete argv,
cwd and start ticks against the plan, plus status runtime ID/PID. The target
must be the runtime PID or its verified ancestor (the host's AppImage CLI
`serve` parent may own the runtime node), never a process-name match. Reports
retain only safe target identity and argv SHA256, not arbitrary observed argv.
Every owned model-bearing OMP must have complete stable evidence. Capture
errors other than the explicit `proc_scan_incomplete` diagnostic block;
`owned_omp_incomplete` always blocks. List unreadable entries explicitly;
never waive an unreadable same-user executable by mutable process name.
Strict capture also recognizes the observed self-updater's deleted
`omp.<digits>.<digits>.<digits>.bak` executable only with argv0 `omp`.
It retains the exact executable link and revalidates that link, command bytes,
start ticks and cwd; preflight still requires the exact session/record/handle.
Mutable process names never justify an exclusion. Main approved this capture
fix in `msg_bc52baf85deb`; the analogous builder-retirement fix is separate
TASK-39, not included here.

Only after Main's verification, announcement confirmation and approved values,
the authorized operator may schedule the conductor outside Orca. This is an
operator template, **not a builder instruction to execute**: replace the
checkout placeholder with the pinned reviewed absolute path. Use one unique
unit for this plan; do not add `Restart=always` or an automatic retry timer.

```sh
systemd-run --user --unit=fleet-orca-restart-2026-11-07 \
  --on-calendar='2026-11-07 14:00:00 UTC' \
  --property=UMask=0077 \
  --working-directory=/<APPROVED-REVIEWED-FLEET-CHECKOUT> \
  /usr/bin/python3 -m fleet.cli \
  --config /home/dev/.config/fleet/fleet.yml orca-restart \
  --plan /home/dev/.local/state/fleet/orca-restart/plan.yml
```

The timer launches a transient **service**, not an Orca terminal/scope; it
survives loss of the coordinating terminal. Do not export provider credentials
or Orca terminal identity. Preserve timer/service invocation ID, result and
sanitized output privately. At execution, the conductor must revalidate all
gates, capture BEFORE, journal intent durably, recheck exact target and stock
unit identity, then issue only `sudo -n systemctl restart orca-serve.service`.
A timeout is not permission for another restart. Bounded recovery may repeat
that same exact command only after positive old-process exit **and** a
failed/inactive unit with MainPID=0, subject to `launch_attempts`. Every attempt
revalidates both idle confirmations. Down-runtime recovery additionally requires
a complete current owned-OMP census with no surviving model-bearing agents;
survivors, unreadable entries or unknown actors retain manual state, never retry.
Main accepted this reduced availability in `msg_e654aa00fe74`; filesystem-only
completed-turn/lease proof is not an alternative. No custom launch, `sudo start`,
escalation or unbounded loop. Capture AFTER before Main/config mutations.
Follow the recovery sequence below.

The private journal records plan-bound progress and before/after evidence
across conductor failure. Resume uses the **same unchanged plan, ID and state
directory** with the same CLI only after inspecting the journal. Never delete
state or use a new ID to force a rerun. An ambiguous stop/launch/terminal-create
intent is not permission to retry: reconcile actual process/runtime/terminal
identity manually first. In particular, terminal creation accepted before a
receipt was durably recorded requires manual reconciliation, not another Main.
Refused/expired plans retain evidence; Main resolves the blocker explicitly.
Fresh-Main authorization never reuses persisted AFTER evidence: immediately
before creation it takes another live capture, checks unchanged boot/runtime,
positive incumbent exit, no competing agent and a complete current census.
The exact old terminal must still be connected/writable, same handle/incarnation,
explicitly agent-free and at a positively ready shell; every other terminal
in that Main worktree must also be positively agent-free/ready. Missing, closed
without a retained positive closure proof, disconnected, orphaned or unknown
terminals require manual reconciliation, never a duplicate. Retain the private
`fresh-<project>.json` receipt. Partial proc-scan evidence can be reported by
preflight but cannot authorize automatic fresh-Main creation.
Shell-only coordinators also require a fresh `run-current` match, not just a
connected ready prompt. Cached coordinator receipts are revalidated against
current terminal/Run/shell or exact live Main evidence before continuing;
past success cannot authorize a later stale cutover. Required announcements must return durable message IDs;
failed, missing or ambiguous receipts make the outcome manual reconciliation,
never `complete`. Persisted send intents are not retried automatically.
Recorded Main creation is recovered without another create: verify the recorded
handle/incarnation, current Run and exact sole live model process, then finish
the coordinator receipt. An intent without a creation receipt remains manual.
Fresh-Main and preflight checks share canonical-log validation: exactly one root
JSONL, complete actual model/thinking headers, and only descendant helper logs.
Nested helpers are accepted; foreign logs and ambiguous canonical roots refuse.
Every listed supervisor must be loaded/active/running at preflight; unchanged
shell coordinators and cached completed installs also recheck live activity
before completion. A historical active receipt never repairs or hides failure.
Reinstallation preserves the effective single `EnvironmentFiles` path and its
optional/missing-file flag from systemd, without reading credential values.
Multiple or unrecognized bindings require manual reconciliation before intent;
the installer must not silently drop credentials while leaving a service active.
Authorization is rechecked on every execution/resume and before each new live
mutation, not inherited from a completed preflight or restart. Outside the
window, return existing `blocked` / `execution_window_closed` output and leave
recovery read-only; no fresh Main, config cutover, install or enqueue. Cached
config receipts also require current terminal and Run equality; changed owner
routing is preserved and requires reconciliation.
Private startup argv/prompts remain only in the operator input plan. Public
actions and persisted preflight carry a command digest and allowlisted metadata;
execution hydrates argv in memory from the same digest-bound private plan,
without mutating the stored proof. Never copy the plan into reports or logs.
External sends use the same explicit `--from`/`--run` routing convention as the
existing supervisor: the verified post-reconciliation project handle, or its
preflight configured handle before mapping. Messages identify the automated
conductor; sender text is routing context, never authority.
Main approved this convention in `msg_96e2c57e8824`: status-only automated text
to listed project Runs; binding must be verified before pre-restart sends and
post-restart sends require the freshly verified mapping. Failed/ambiguous sends
remain manual and are not retried. Probes must use scratch Runs.
Every enqueue rechecks the sender's current incarnation, worktree, connectivity,
handle and Run. Post-restart reporting requires a completed coordinator mapping.
If identity is unproven, retain a blocked local receipt only; never borrow a
configured or cached handle to report the failure of its own identity checks.
Surviving Mains and reserved shells also require current terminal inventory;
survivor session metadata is re-read from current processes, never saved AFTER.
Both initial and cached Main reconciliation require a complete current census
and exactly one model-bearing candidate in that Main's root. An original survivor
does not resolve a competing restored agent; partial evidence remains manual.
Positively foreign-user `non_owned` diagnostics are outside the owned census,
not incompleteness; unreadable/unknown ownership still blocks.
A created Main may wait boundedly for startup metadata to become complete;
it is accepted only with a sole same-root agent and the shared process/native
terminal proof: exact argv/session directory, current PID/start, process
`ORCA_TERMINAL_HANDLE`, terminal incarnation/worktree/connectivity and cgroup.
Competing agents refuse immediately; incomplete or wrong-terminal evidence at
timeout retains the created receipt.

**2026-11-06 conductor rehearsal evidence (source checkout, no shared restart)**

- Real external dry-run unit `fleet-task13-conductor-dry-final-1793993960`,
  invocation `ace6add5d06c4ecdb767925bf591ce12`, exited **2 as required**:
  exact service parent PID 4110451/start ticks 34577681 and runtime
  `0bce356b-d517-4353-bfb0-4ffbd1ddda22`/Orca 1.4.242 matched. It listed
  16 current terminals, the exact privileged restart, capture, conditional Main
  start/bind, config, supervisor and durable-report actions. Private output:
  `/home/dev/.local/state/fleet/orca-restart/dry-run.json` (0600).
- The actual operator draft is `.../orca-restart/plan.yml` (0600, parent 0700).
  The exact supplied Project Alpha command/handoff and current PID10290 receipt are
  filled; future timing, T-15 private handoffs, idle confirmations, stale Fleet
  Main identity and unmapped legacy sessions still block. `command: []` remains
  a supported draft, never executable. No journal or runtime mutation occurs
  during dry-run.
- Upgraded-process external capture invocation
  `a936ebd269804bad92f7c3b054ca2fea` listed 15 terminals/27 OMP processes
  and honestly retained `proc_scan_incomplete` (exit1). It now includes
  Project Alpha Main PID10290/start34651824, exact deleted updater executable,
  session `01a16375-b15b-706b-8750-41fa6ddcf32f`, complete Opus/high evidence;
  the private manual record matches its current terminal/session/cgroup.
- Updated private-plan CLI dry-run invocation
  `2f4efcbe19174818a4b46d569cff2523` listed 15 handles and both conditional
  fresh-Main commands, plus project/initiating-Run report destinations. Exit2
  correctly retained nine blockers (future window, handoff/config prerequisites,
  both idle confirmations/bindings and unproven/unmapped sessions), with no
  unresolved startup-command blocker. Private output: `.../dry-run-updated.json`.
- Owned disposable **user** service `fleet-task13-restart-drill-1793993960`
  exercised the conductor restart/journal/resume path: sleep PID 2410269 exited,
  replacement PID 2410278 became active; resuming issued no second restart
  (one total). The throwaway adapter redirected only the privileged command
  and readiness probe to this scratch unit; it did not test Orca readiness,
  system-service privileges, PTY survival or shared-runtime replay. Scratch
  service stopped afterward; no Orca signal/restart occurred.
- Real `terminal create --command printf` in the existing lane returned
  `term_3d2f18f1-e31c-4e9b-a836-0644dba6cad1`; screen read proved the marker
  executed and shell returned. The owned terminal was closed (`ptyKilled=true`).
  This proves terminal creation, not a fresh Main or binding after restart.
- Fresh-creation guard exercised actual native read-only capture: runtime
  unchanged, 15 handles/27 OMP, `proc_scan_incomplete`; it correctly refused
  authorization, retaining `.../orca-restart/fresh-guard-smoke/fresh-fleet.json`
  (0600). No Main creation was invoked.
- The reserved project-beta shell's real current Run ownership was verified
  read-only: `term_585ef494-543b-4a24-b1c3-b886aa1c7a42` owns
  `run_c018565b177d`; only a private local receipt changed. Repeating through
  the cached-receipt path also revalidated the real native terminal, Run and
  ready shell. Stock shell frames omit `agentIdentity` rather than supplying
  explicit null; that incidental representation is not a standalone idle proof.
- A real external report without terminal identity failed
  `no_active_sender_terminal` (invocation
  `618dbad2b3034cf897accb8140095904`), exposing a required stock CLI prerequisite.
  After adding explicit sender/run context, external invocation
  `bb883caf3cba491db4ef3d367739d1be` successfully enqueued the actual assigned
  builder's tooling-only status report (`msg_64a5b634e1b5`), using its **own**
  sender handle, not borrowing a Main. No binding/restart/config/service
  mutation occurred; this proves transport, not delivery wake or permission to
  use a project's coordinator identity. This incorrectly targeted the live Main
  Run; do not repeat. Main subsequently approved the production convention above.
- Real external unit `fleet-task13-supervisor-dry-1793993960` exercised the new
  supervisor gates: exit 2, the same nine operator blockers, no supervisor
  blockers and no restart. Private `dry-run-supervisors.json` retains output.
- Read-only receipt-recovery smoke used an existing Project Alpha Main and a retired
  own sleep process as the incumbent fixture, not an actual new Main. Two root
  models were observed; recovery required manual reconciliation before cutover.
  Private `receipt-recovery-smoke/proof.json` records the limits; no model,
  terminal, binding, config or service was changed. Cached shell proof also passed.
- External expired-window unit `fleet-task13-expired-resume-v2-1793993960`
  returned exit 2 / `blocked` / `execution_window_closed`, created no state
  directory, and left stock service PID/start/restart metadata unchanged.
  Private `expired-resume-proof-v2.json` retains the result; throwaway plan removed.
- Real project-beta config cached-receipt proof accepted matching routing and
  refused simulated expected-Run/handle mismatches without changing live bytes.
  Private `cached-config-smoke/proof.json` records this read-only check.
- File-based session-binding API smoke accepted canonical+nested fixture logs,
  refused a foreign log and second root JSONL, then removed fixtures. Private
  `nested-helper-proof.json` records this; no actual Main/model was launched.
- Redacted external dry-run unit `fleet-task13-redacted-dry-1793993960` exited 2
  with the same nine blockers and zero known private-prompt matches in stdout;
  Main actions contain digests, not argv (`dry-run-redacted.json`, private).
  A review caught raw Main commands in earlier actions/proof. All 24 owned JSON
  rehearsal artifacts were checked; three old dry-run files were sanitized to
  retain digests. Twelve journal records from the three affected owned units had
  no exact known private-prompt matches. No logs were printed/deleted; this is
  scoped evidence, not a blanket no-exposure or credential-rotation claim.
- Earlier source's read-only survivor reconciliation preserved the Project Alpha Main's
  original handle with fresh process/session/native/Run proof. An adapter-only
  simulated disconnection refused a coordinator receipt. Private
  `survivor-current-proof-smoke/proof.json` records both; no restart/native writes.
- Actual replacement Fleet Main PID 3634706/start 123908689/session
  `01a1b2b0-d53f-7025-a865-a174ecdd2355` passed canonical argv discovery and
  process/native/Run matching; the private manual record was refreshed.
  Live argv revalidation authorized a private fixture config cutover and
  refused a simulated expected-digest mismatch without writing that fixture.
  `argv-session-current-smoke/proof.json` retains the limits; no live config,
  binding, service or model was changed.
- Real native sender verification refused a deliberately mismatched expected
  Run before enqueue (`sender-current-proof-smoke/proof-v2.json`). No real Run
  or terminal was modified.
- External `fleet-task13-argv-dry-1793993960` returned exit 2 with 16 handles
  and ten blockers, including owned-model incompleteness. A narrower process
  diagnosis observed unknown agent PID 4145120 with no session/launch flags;
  it was not excluded or stopped. `dry-run-argv-bound.json` remains private,
  with zero known private-prompt matches. This is NO-GO, not a restart receipt.
- Recovery authorization smoke refused an expired confirmation within an open
  plan window and separately refused the real current census with surviving
  agents. Both retained attempt 1 with zero privileged calls. Backend-down
  readiness/service state was adapted, not a real Orca outage; only the expiry
  case used an empty-census adapter. Private
  `retry-authorization-proof-smoke/proof.json` retains those limits.
- Final-source survivor reconciliation refused the real incomplete census,
  which included two same-root agents (10290, 3880293), without a coordinator
  receipt or new Main. A throwaway census adapter presenting only those two
  complete real rows also refused the collision despite a matching original.
  `survivor-collision-proof-smoke/{proof,complete-census-adapter-proof}.json`
  retains the distinction; no native/config/service mutations.
- A scratch proc-directory view of real `/proc/1` proved positively foreign
  ownership is excluded without treating unknown ownership as foreign.
  `foreign-ownership-proof-smoke/proof.json` retains the scope limit: this is
  not complete-host or restart authorization, and changed no native/service state.
- Created-receipt acceptance using the existing Project Alpha terminal as a private
  fixture retained `created` on actual partial census and on an adapter presenting
  both complete real root rows. Native calls were identity/Run reads only.
  `new-main-acceptance-proof-smoke/proof.json` records the fixture limits;
  no real Main or terminal was created and no config/service/enqueue changed.
- Updated private Email Main record verified PID 751081/start 124254263/session
  `01a1b2e5-903f-72b8-91ec-7908dcad7687`, actual Opus 5.5/high/no fallback,
  canonical argv-directory binding and the real native terminal/Run.
  `email-main-binding-proof.json` contains metadata only. External unit
  `fleet-task13-three-main-dry-1793993960` returned exit 2 with three Main
  actions and twelve blockers, including Email idle/handoff prerequisites.
  `dry-run-three-mains.json` has zero known private-prompt matches; no execution
  state directory was created, and no service, binding or live config changed.
- Created-receipt binding smoke used the actual Email Main's process, logs,
  environment/cgroup and native identity/Run reads: matching terminal reached
  `done`, while an adapted different handle retained `created`.
  `process-terminal-acceptance-proof-smoke/proof.json` records that a
  complete-census adapter narrowed evidence to one actual row; this is not a
  complete-host GO or a fresh Main launch. Only private fixture journals changed.
- Real owned scratch user-service reinstall reproduced an active process losing
  its synthetic EnvironmentFile marker before the fix; after the fix a new PID
  loaded the marker and remained active. Stock installation/systemd were real,
  while the consumer executable/config were private fixtures, not a real
  supervisor or credential. `supervisor-environment-preservation-proof.json`
  retains metadata only; the unit was stopped/disabled/removed and throwaways deleted.
- 242 regression tests pass; eighteen independent-review findings are resolved.
  Regressions cover restored Main, wrong-terminal fresh-Main acceptance,
  ambiguous terminals, changed live sessions/argv, unsafe FD-less directories,
  recorded session IDs, routing/config changes, receipt recovery, supervisor
  continuity, expiry, nested helpers, ambiguous sends and private prompt absence.
  Failed Phase-A units were reset as requested; diagnostic
  units retain honest failures. Final review/CI/announcement receipts on Pad/PR.

**External capture, before and after**

Run from that external shell, using distinct new output directories and unit
names for each capture. Historical BEFORE-only example for this host (substitute
`after` only after an authorized restart; no restart command is embedded):

```sh
systemd-run --user --unit=fleet-orca-before --wait --pipe \
  --property=UMask=0077 \
  --working-directory=/home/dev/orca/workspaces/fleet/fleet-phase1 \
  /usr/bin/python3 -m fleet.orca_capture \
  --config /home/dev/.config/fleet/fleet.yml \
  --out /var/tmp/fleet-orca-before \
  --service fleet-example-org-project-alpha.service
```

The transient **service**, not an Orca terminal process/scope, performs capture.
No provider credentials or Orca terminal identity need to be exported into it.
`--out` must have an existing parent and must not already exist; directory 0700,
`snapshot.json` 0600. Exit 0 means no top-level collection errors, **not** safe
to restart; exit 1 retains partial evidence with errors, exit 2 means capture
failed. Per-process `errors` and session `complete` must also be inspected.
Transient service exit after capture is expected; preserve its invocation ID
and result. Never commit the full host snapshot; share only reviewed projections.

Capture includes runtime/version, handles/incarnations/worktree paths, title
SHA256 + extracted Pad refs, read-only per-terminal Run bindings, all same-user
local OMP PID/start ticks/boot ID/foreground state, safe argv-shape flags,
open session logs' latest actual model/thinking events, Run metadata, inbox
message metadata and supervisor state. No raw titles, transcript text, previews,
argument values, environments or message contents. Process/session mappings
are observational (cwd/open descriptors), not terminal ownership or idle proof.
Known internal `daemon_broker` / `text_predict` / `js_eval_process` workers have
no model session; other missing logs/processes stay unknown. Deleted-but-live
executable links are inspected without resolving their removed on-disk path.
Per-entry `proc_diagnostics` distinguishes foreign owners, zombies and unreadable
entries with PID/known UID credentials/field/errno. Process names never establish
non-OMP executable identity; unreadable same-user entries make capture partial.
User scope comes from `/proc/PID/status` real/effective/saved/filesystem UIDs,
never directory ownership (non-dumpable same-user processes can appear root-owned).
Unreadable/malformed credentials stay incomplete. A process that exits between
stat and executable lookup requires a second matching-start zombie observation
or disappearance before it is classified as exited-mid-scan.
Collection is sequential; a changed runtime ID invalidates a cross-restart
capture. Inbox/terminal caps and missing host coverage are reported incomplete.
Phase B acceptance (Main ruling `msg_88fdc4be4ae4`): every model-bearing OMP
session, including launch-recorded builders and Mains, must have PID/start ticks,
argv shape, cwd, session binding and actual model/thinking. Every incomplete
entry must be listed explicitly; partial status alone is not a failure.
Read-only capture never escalates privileges. The conductor's separately
approved stock-unit restart exception above does not authorize builder sudo.

**After the conductor's one authorized restart**

1. Preserve the external AFTER capture taken **before** rebind/relaunch; compare
   runtime ID/version and handles against BEFORE, keeping both originals.
2. Check every incumbent Main for survival using the same boot/PID/start ticks,
   session and exact configured coordinator handle/incarnation. Restored or
   remapped Main handles require **manual reconciliation**, even with the same
   incarnation and Run; the engine refuses automatic remapping. Reconcile
   survivors; **never start a duplicate Main**. Fresh start requires
   positive old-process exit, no captured restored attempt for that session/cwd,
   and the old terminal positively at a ready shell or explicitly closed in
   inventory. Ambiguous preserved/restored terminals block.
   Only then use the project's approved fresh command/handoff in its existing
   worktree, reconcile terminal bindings, bind its coordinator Run, update its
   private coordinator/lane config and reinstall its own supervisor with the
   approved delivery setting. project-beta `mode: shell` only reconciles a
   same-incarnation handle and reinstalls its supervisor if config changed:
   no handoff, command or new Main. A fleet config update is not an Project Alpha
   update. No builder relaunch, extra worktree or dual-send to stale handles.
   Mains inspect receipts and reconcile before new work. Any failure or
   ambiguous terminal intent stops automation.
3. Reconcile every possible surviving attempt, including `restored_unconfirmed`.
   Same boot + PID + start ticks is survival evidence; a changed/missing PID is
   not proof no other/restored attempt exists. Read actual model/thinking events
   and positive liveness/session evidence. No blind retry, stop or auto-resume.
   Only a positively reconciled eligible retry may use a fresh explicit
   placement/model/thinking pin and retry-of where applicable.
4. From Main's rebound terminal, exercise normal `fleet wait` replay. Compare
   native Delivery/message IDs to the saved receipt, process all types, reply
   in-thread, then explicitly ack the whole batch once. Verify pending newer
   messages remain deliverable. Capture receipt metadata privately; no message
   payloads in public evidence. Re-arm the async wait; enqueue alone is not wake.
5. Publish a dated before/after table with actual commands/receipt IDs and
   GO/NO-GO **per dimension**: model/thinking, process survival vs fresh resume,
   replacement handle mapping, Run rebinding, outstanding replay/ack and
   supervisor continuity. Missing evidence is UNVERIFIED/NO-GO, not inferred GO.
   A lost or duplicated message, ambiguous live attempt, wrong model/thinking,
   failed rebind or changed runtime during capture blocks rollout/retries.

Phase A dry run: external unit `fleet-task13-before-dry2-1793985794.service`,
invocation `241a262c21144a8e8f23059be75f63cb`, captured Orca 1.4.242 /
runtime `0bce356b-d517-4353-bfb0-4ffbd1ddda22`, 13 terminals and 14 local OMP
processes. Both project supervisors active/running, NRestarts=0; private output
permissions 0700/0600. Exit 1 honestly reports `proc_scan_incomplete`; some
processes lack open session metadata and remain unknown. Actual builder session
`01a1b1e2-d572-70bb-9107-50f73ade72fa` recorded Sol medium, no fallback.
No restart, handle replacement, post-restart rebind or replay was exercised.

Completeness follow-up (Main ruling): the first generic scan error conflated
foreign-user executable EACCES, same-user non-dumpable systemd/sd-pam/sshd,
zombie Orca executable ENOENT and vanished entries. Sessionless OMP rows were
internal workers, not missing agent sessions. Resolving an executable symlink
also incorrectly skipped live binaries whose on-disk executable was deleted;
use the kernel link's basename instead. No raw argv/environment was retained.
External unit `fleet-task13-complete-before2-1793985794.service`, invocation
`52acd8c64d8c48e4916805cc976acd66`, historically exits **0**: 17 same-user OMP processes
(9 agents with complete actual model/thinking metadata, 7 daemon brokers,
1 text-predict worker), no process errors. Per-entry exclusions: 539 foreign
owners; executable EACCES for known non-OMP services at PIDs 1248/1249/3582181;
zombie executable ENOENT at PIDs 4110530/4110532. Both supervisors remain
active/running, NRestarts=0. These are BEFORE-only observations, not restart GO.
Historical credential-based capture: unit `fleet-task13-statusuid-final-1793985794`,
invocation `08ba668f56624b74b80eb212daff4abc`, exits 0 with 17 OMP processes
and no collection errors. 143 tests pass, including process-credential vs
directory-owner mismatch, unknown credentials and the observed-zombie race.
Those exit-0 classifications are superseded: independent review found that
mutable process names cannot prove non-OMP identity. The final tool leaves all
unreadable same-user executables partial, never suppressing them by name.
Final strict external BEFORE unit `fleet-task13-strict-final-1793985794.service`
exits **1**, honestly partial: 13 terminals, 19 OMP processes (7 complete agents,
1 agent without an open session log, 8 daemon brokers, 1 text-predict worker,
2 JS-eval workers). PID 2523586 lacks session metadata; PIDs
1248/1249/3582181 have same-user UID credentials and executable EACCES.
Every entry is explicit in the private snapshot; none is silently waived.
143 tests pass, including mutable-name rejection. This BEFORE capture is not
phase B acceptance: obtain complete model-bearing session evidence before GO.


### Inbox and wake

- `check --wait` blocks without model polling, keepalives on stderr; consuming
  check uses caller terminal identity and bound Run. Process delivery then ack;
  unacked FIFO batches replay and can hide newer mail. Read all message types at
  checkpoints; filtering only worker_done/escalation/question misses fleet status.
- TASK-10 proved current Orca redirects a bound terminal address into its Run.
  `fleet wait` uses that one native blocking path, requires the caller's own
  configured Run binding, and includes read-only terminal history diagnostics.
  Unbound terminal checks mark mail read without replayable delivery IDs;
  pre-binding legacy mail is not another ackable batch and cannot wake the Run.
  This is not independent mailbox multiplexing. Stop cutover if bound-terminal
  mail bypasses the Run; shared-runtime restart remains unverified.
- `send` is durable enqueue. Nudge/wake is best-effort; preamble explicitly says
  nothing interrupts the agent. Settled dispatch rejects late sends. Async command
  completion is the usable guaranteed owner wake mechanism in omp.
- `ask` blocks durably; resume same message ID after timeout instead of duplicating.
  Coordinator `reply --id` closes the original question thread from its own Orca
  terminal identity. Terminal input is neither thread acknowledgment nor reply.
- Terminal builders use `fleet thread question` plus async `thread wait`, not
  native `ask` (even resume requires a Dispatch). One process reads terminal
  history non-destructively, caching all types and writing the matched decision
  to Pad before return. Canonical task thread survives attempts; per-question
  native subthreads prevent old decisions crossing fresh task boundaries.
  Unseen replies are not durable after terminal close/history eviction.
- `fleet wait` sorts question/escalation decisions first with classification
  and counts, without dropping rows or changing native whole-batch ack.
  Keep builders unbound; scratch Runs require separate owned scratch terminals.
- Terminal send accepted/input_accepted is not turn_started; inspect receipt or
  screen without resending. Permission menus require menu handling, not prompts.

### Metadata and automation

Observed Phase 0 fields (not guessed API names):

| Meaning | worktree show result.worktree | worktree ps result.worktrees[] |
|---|---|---|
| ID | id | worktreeId |
| PR | linkedPR number | linkedPR object with number/state |
| Workflow | workspaceStatus | workspaceStatus |
| Comment | comment | comment |
| Unread | isUnread | unread |
| Links | linkedItems provider/type/number | linkedItems same shape |

ps.status is activity, not workspaceStatus. PR URL was not present; resolve via gh.
Use worktree set --pr/--comment/--workspace-status/--unread for metadata; Pad is
tracker of record. Current health supports version-specific optional fields
conservatively; unknown must remain unknown.

Automations support cron/RRULE and bounded precheck; they always launch/reuse an
agent. A precheck as daemon runner is a hack, not model-free scheduling. Run never
places workers. Search requires human-enabled history index, is single-host and
not a status feed. Orca account list is not Codex/Claude quota. Public unlisted
artifacts/share or omp stream are not private dashboard hosting; publishing is
human-gated, not a fallback to expose private snapshots.

## omp mechanisms

| Area | Mechanisms / constraints |
|---|---|
| Roles | modelRoleStorage project, modelRoles, @role, explicit thinking; .omp/config.yml discovered from project cwd. Fleet mirrors Project Alpha native allowed fallbacks; builder overlay is stricter. |
| Context | compaction.modelThresholds[Sol] = f200000 (fixed trigger, not scaled window), extendedContext false, fresh session/task; optional cache/idle controls do not replace task boundary. |
| Fallback | retry.modelFallback false and pinned-model chain [] for builders, usageReservePolicy fail-closed. Native role fallback may exist for Main/helpers; never infer builder permission from it. |
| Usage/accounts | omp usage/accounts/reset, auth broker usage/history/clients, account policies/pools and shared concurrency limits; cached quota can be stale. Never log provider/account identity or token. Owner approves saved-reset spend. |
| Async | Background bash auto-delivers finite completion; print mode needs wait-async when appropriate. CI run_watch observes Actions only; fleet watch-pr adds broader PR events. |
| Services | Named omp broker services can detach across sessions; reboot persistence not established by research. Production supervisors use systemd user units with install readiness proof. |
| Hooks/extensions | Project .omp/extensions/hooks can expose health; optional RPC steer/follow_up or sendMessage does not prove cross-process inbox wake. Advisors are turn-boundary reviewers, not supervisors, and remain off. |
| Collab/RPC | Collab list can expose local health but relay/control URLs are sensitive. Initial research noted hosted-relay policy tradeoff; fleet does not require relay as status backend. |
| Goal/resume | Goal interactive-only with launch constraints; not an automatic task-spec API. Resume/continue/restoration are not fresh task sessions and do not prove model pin after restart. |
| Stats | omp stats/broker aggregates may support usage metric; preserve comparable periods and exact PR denominators. Never publish raw sessions/account data as dashboard payload. |

## What fleet actually owns

Python + PyYAML supervisor/CLI, validated fleet.yml, health and conservative owner
mapping, PR watcher/merge-group membership, quota admission, guarded terminal
launcher, durable snapshot/delivery receipts, user-service installer, HTTPS push,
read-only central SQLite/HTML app. No model calls in collectors, no merge action,
no automatic Pad dispatch or running-worker pause today. The roadmap names these
gaps explicitly; documentation-only research is not runtime acceptance.

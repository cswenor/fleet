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
| Busy builder steering (TASK-84, source-only) | Native status `send --thread-id`; inbox remains separate | `thread send REF --body TEXT`, `thread check REF [--ack TOKEN]`, and Main-only `thread pending` add configured caller/current-attempt ownership and a pre-send token journal. Normal delivery never uses terminal input; `--priority` refuses before side effects. |
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

## TASK-88 post-restart screen investigation (2026-11-10)

Read-only native captures of the surviving Fleet phase1 terminal
`term_da1a300e-41ee-4026-b823-9c0ec134e8b7` show clipped 80-column tool
borders, duplicate partial working footers and no composer border. The exact
OMP process (PID 3795247, matching `ORCA_TERMINAL_HANDLE`) had `/dev/pts/6`
as fd 0; `TIOCGWINSZ` returned **280 columns × 83 rows**. Increasing
`terminal read --screen --limit 5000` still returned an unrecognized frame.
`tests/fixtures/orca-stale-screen.json` retains the actual 22-row receipt;
the real agent was working, not idle.

The installed `orcad-template/orcad-server.js` exposes these implementation
facts (diagnostic evidence, not a supported Fleet API):

- `getTerminalSize` delegates to the PTY controller's in-memory `Ep` size map.
  `seedHeadlessTerminal` defaults to 80×24 when no size is available.
  **[INFERENCE]** missing restored geometry explains the observed clipping;
  the live map and exact emulator dimensions are not exposed by the CLI.
- `Qot` / `ska` remove blank rows. `returnedLineCount` is not frame height.
  `tailRows.trailingSpaces` preserves whitespace before soft wraps, not
  right-edge padding; it cannot establish screen width.
- Native `terminal read/show`, the 288-command `agent-context` registry and
  `diagnostics memory` expose no frame dimensions/cursor or supported terminal
  resize/refresh command. The internal `terminal.resizeForClient` RPC is not a
  documented CLI recovery surface and was not called.

The same read-only sweep found an unaffected completed Project Alpha pane
`term_24697da9-f443-423f-97c6-64518acc1815`: 75 returned rows, full-width
280-column footer, empty `╰─` composer and exact-pane `done`. The existing
`health.omp_ui` recognized it as idle. Missing footer plus `done`, including
null `programStatus`, must still refuse retirement: an omitted draft is unknown.
No terminal input, resize, signal, restart or shared deployment was performed.

Main's revised scope (`msg_a0b4555866e0`) approves diagnostic-only publication:
no exit override and no inferred geometry. The new refusal identifies proven
terminal-session-before-runtime chronology but grants no input authority.
[The operator runbook](MAIN.md#post-restart-clipped-screen) records Main's
owner-approved manual finished-builder recovery and the UI resize alternative;
the builder exercised neither. Upstream geometry/resize changes require an
owner decision.

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

### Busy builder checkpoints (TASK-84)

Main's configured Run-bound terminal uses
`fleet --config CONFIG thread send REF --body TEXT`; it returns JSON.
The exact live unbound builder terminal/incarnation/worktree and unique
authoritative unsettled opened attempt must match. A unique token is journaled
before native status send in that attempt's thread; a known message ID records
`sent`, while an ambiguous send stays `sending` with no automatic retry.
`fleet --config CONFIG thread pending` returns JSON current-launch lanes with
unacknowledged records, including ambiguous `sending` records and `age_seconds`.

The current unbound builder uses `fleet --config CONFIG thread check REF`:
one JSON object per sent unacknowledged message per line, empty output for none.
Reading retains pending records. Process each message, then use
`fleet --config CONFIG thread check REF --ack TOKEN`; unknown tokens refuse.
Records belong to the current launch/attempt. These commands do not consume
native mail or replace decision `thread wait`/other native inbox handling.
Inspect all native message types at natural checkpoints. Sender IDs are
advisory, not authentication.

All foreground/background inbox/CI wait segments, including wait-tool calls
and `watch-pr --timeout 600`, are at most 600 seconds. Between waits, run the
steering check and act on events/messages before acknowledging and re-waiting.
Long jobs remain async with their own expected-runtime-plus-margin timeout,
which may exceed 600 seconds; bound each wait, not total job duration.
Bounded event waits/checkpoints are not repeated model-turn status polling.

**Priority is refused before side effects:** scratch omp 18.18.1 accepted 109
bytes via native `terminal send --interrupt`, but the priority text and `^C`
entered a foreground `sleep 90` child's PTY output; the child exited 1 after
25.59 seconds. The model read the marker from tool output, not structured
steering. This unsafe arbitrary-child input is not a proven stock enqueue path.
`omp --help` exposes no enqueue CLI; `steeringMode` is one-at-a-time configuration,
and collab exposes a control link, not an enqueue API. Normal steering uses
native status transport only, never terminal input.

The live private scratch Run proof read `msg_d07528856224` after one 60-second
wait, printed its marker, then acknowledged the token; `pending` changed from
one record to no lanes. Send-to-ack was 62.16 seconds including tool/model
overhead. It used synthetic launch/thread state, but real native transport and
unchanged live terminal ownership checks. Both scratch terminals were closed.
Source-only; no shared deployment. Publication requires independent final-head
review, green CI and Main exact-head GO; merge does not authorize deployment or
service/runtime changes. No CLI enqueue is an upstream/owner capability gap.

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
- Terminal handles are runtime-scoped. Re-list after restart/stale-handle; never
  dual-send or assume a missing handle authorizes another agent. The 2026-11-10
  live service restart preserved all 15 handles and Main processes (below).

#### TASK-80 restart runbook: restart, verify, report

TASK-13's live restart on 2026-11-10 at 12:41:58Z took about six seconds.
All 15 terminal handles and every omp process survived: PTYs live outside the
system Orca service. The old conductor nevertheless failed at
`coordinator-fleet`, because its Main-relaunch reconciliation assumptions were
wrong. Private receipts: `~/.local/state/fleet/orca-restart/2026-11-10b/`.
This supersedes the old timer, idle-confirmation, handoff-freshness and Main
relaunch procedure. It does not prove mailbox replay/ack preservation.

Only Main runs a live shared restart, after owner approval and notice to every
affected project Main. Builders may run `--dry-run` only. Merge is not deployment
or restart permission. Keep supervisors running; no Main or builder relaunch,
Run rebind, private config rewrite or supervisor reinstall is part of restart.

**Private plan (version 2)**

Use an owned 0600 plan in a private directory. Every path is absolute; project
names and the plan ID are safe slugs. Unknown/obsolete fields are rejected.
The state directory must be private (0700), or have a private existing parent.
Do not overwrite an earlier restart's journal or reuse its ID for a new restart.

```yaml
version: 2
id: approved-restart-id
state_dir: /private/fleet/restart-evidence
service: orca-serve.service
allow_systemd_sigkill: true # Only with approval for the unit's existing policy.
timeout_seconds: 60 # Optional; integer 1..300.
consumption_timeout_seconds: 120 # Optional; integer 1..600, default two minutes.
projects:
  - name: fleet
    config: /home/dev/.config/fleet/fleet.yml
  - name: project-alpha
    config: /home/dev/.config/fleet/project-alpha.yml
  - name: project-beta
    config: /home/dev/.config/fleet/project-beta.yml
```

No Main commands, session directories, process records, handoffs, confirmations
or execution timestamps belong in the plan. The actual service/runtime identity
is discovered and revalidated immediately before the sole restart attempt.
`allow_systemd_sigkill` records approval of systemd's existing stop policy; Fleet
does not change it or send its own signals. The privileged operation remains
exactly the approved stock `systemctl restart orca-serve.service` through sudo.
No other sudo, lock recovery, second server or generic signal is authorized.

**Dry run and execution**

Run from the reviewed source checkout, with explicit config and Orca executable:

```sh
python3 -m fleet.cli --config /home/dev/.config/fleet/fleet.yml orca-restart \
  --plan /private/fleet/restart-plan.yml --dry-run
```

Dry run prints the BEFORE capture, blockers and exact planned actions. It never
creates a journal, restarts anything, consumes mail or sends notifications.
Inspect service identity/authority, coordinator Run bindings, terminal inventory,
supervisor activity and pending-mail completeness. Process/session diagnostics
remain evidence, not idle gates. No `omp session lease` subprocess is needed.

After approval, Main invokes the same command without `--dry-run` directly from
its configured, Run-bound terminal. The caller survives the restart; no timer,
transient systemd unit, new terminal or Main startup command is needed.
Keep every Main's existing inbox wait job running; do not ask Mains to go quiet
or stop `fleet wait`/main-inbox jobs. Use an async invocation with a deadline
covering restart plus the consumption window so the initiating Main can still
receive mail; keep each inbox/CI wait segment at most 600 seconds and act at
checkpoints before re-arming. `fleet wait` tolerates transport outages for up to 30 seconds;
ambiguous consuming requests are never replayed blindly. `rearm` (exit 4)
means runtime recovered and Main must inspect receipts then re-arm; persistent
outage returns `runtime_unavailable` (exit 2). Any returned native batch/request
metadata remains authoritative for replay/ack decisions.
The conductor captures BEFORE, persists intent, restarts the service, waits
boundedly for a new active service PID/runtime and captures AFTER. It verifies:

- Every BEFORE terminal handle is still listed and connected.
- Every project's configured coordinator retains its Run binding.
- Every project supervisor remains active.
- Per-Run pending-message comparison reports retained, lost and new IDs.
  A lost pending ID or incomplete inventory prevents success.

Capture uses read-only `check --peek`, never consuming check/ack. Fewer than
100 validated pending rows proves completeness; a capped or malformed set
blocks. Inbox history length is informational. Capture is sequential, not
atomic: active Main waits may consume pending messages legitimately during the
capture interval. A missing ID is conservatively flagged as loss requiring
reconciliation, not proof that Orca lost it. Keep waits running; retain native
Delivery receipts separately and reconcile them before ack.

Each project Run receives a native `question` outcome notification from the
initiating caller identity, with a durable message-ID receipt. This wakes
decision-filtered waits; enqueue alone still does not prove wake/read/approval.
Missing or ambiguous notification receipts require reconciliation, never a
false complete result or blind duplicate send.
After enqueuing results, the conductor observes each Run with read-only peek
for up to `consumption_timeout_seconds` (default 120). The report names the
project/Main terminal, Run and result message ID, confirms a complete pending
count of zero or flags undrained/unknown Runs in `attention_required`. It never
sends another success message after that observation (which would refill the
inbox), consumes mail itself or equates an empty pending set with human review.

Post-restart `--screen` may be empty until the terminal redraws. Report it as
unknown; do not auto-send Enter or any input to builders. The owner/Main may
request a redraw only after positively identifying the terminal and safe state.
TASK-81 adds observational Main health fallback, not shell-readiness permission.
If a lane was manually exited/stopped, its launch receipt still requires
`fleet --config CONFIG reconcile --slot PATH --launch EXACT_ID --evidence TEXT`
from Main after positive exit proof. Report unresolved receipts; neither a
service restart nor missing screen/process evidence authorizes relaunch.

Journal and BEFORE/AFTER evidence remain private, with 0600 files and fsynced
receipts. After an interruption, inspect the existing journal and use the same
unchanged plan only for receipt recovery. An unresolved restart intent never
authorizes another restart. Missing terminals, lost mail, inactive services or
unverified caller routing require manual reconciliation; Fleet does not repair
them by creating agents or modifying other projects.

Guarded **builder retirement** (`fleet restart`, not `orca-restart`) still
requires positive empty-composer, launch/session lease and foreground proofs.
After a service restart, absent/null `programStatus` is acceptable only with
agent `state=done|idle` and no interruption. Present running/unknown program
status and working agent state still refuse.

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
open session logs' latest actual model/thinking events, per-Run pending inbox
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
capture. Pending-inbox/terminal caps and missing host coverage are reported incomplete.
`mailboxes[RUN]` contains each plan project's pending IDs/counts in preflight and
BEFORE/AFTER evidence. Read-only `check --terminal <run-show coordinator_handle>
--run RUN --peek` uses Orca's oldest-first unread `current_delivery` query,
including delivered/unacknowledged messages, capped at 100. Only fewer than 100
validated rows with matching Run/count proves completeness; cap, malformed,
truncated or unavailable pending data still blocks. The separate 1000-row history
count is an informational lower bound, never a completeness gate or total.
Main manually compares BEFORE/AFTER pending IDs/counts before normal replay/ack;
there is no automatic subset gate and no read-only native Delivery-ID query.
Retain the last native receipt separately; sequential capture is not atomic.
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

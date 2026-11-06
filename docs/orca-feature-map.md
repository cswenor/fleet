# Orca + omp feature map

Cleaned project-owned edition of the 2026-11-06 TASK-2464 research. Historical
research was documentation-only; live findings below supersede its proposed
architecture. No credentials, reusable terminal handles or session IDs included.
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

Owner decision on TASK-13 (2026-11-06) supersedes isolated-only verification:
**one real shared Orca restart**, only at a Main-coordinated safe point with
the owner's final go immediately beforehand. Builders do not restart Orca.
Orca 1.4.242 documents no isolated profile startup recipe; `serve --data-root`
belongs only to stale-lock reclamation, not runtime isolation.

Discovery: `orca --help` has no quit command; `open --help` documents startup,
and `serve --help` documents foreground operation / Ctrl+C. Current `status`
reports `remoteUpdateSupport.installMode=unsupported-headless-serve`. This is
not permission to kill a process, automate a GUI, reclaim a lock or launch a
second server. **Owner documents and performs the actual host's stop/start.**
Until that exact external operator procedure is recorded, phase B is NO-GO.
Do not assume `open` restarts this headless deployment.

**Before scheduling**

1. Main gives Project Alpha Main at least **60 minutes' notice**. Project Alpha Main
   prevents new build/cohort/CG jobs, finishes or safely stops current work,
   persists Pad handoffs, and explicitly confirms all eight lanes are safe/idle.
   Fleet Main does the same for fleet lanes. No interrupt injection; unknown
   liveness is not idle. Keep both project systemd supervisors running.
2. TASK-22 freshness fix must be deployed with both Mains informed (the shared
   stable fleet checkout affects both projects). Both Mains write durable
   handoff documents with their startup instructions, Run IDs, task/PR/WIP
   evidence and unresolved attempts. Owner starts **fresh** Mains afterward;
   do not restore old task context or automatically relaunch builders.
3. Before final owner go, record explicit `session_dir`/session-ID bindings for
   every model-bearing builder and Main, linked to PID/start ticks and launch
   records. Verify the complete actual model/thinking evidence is readable.
   Legacy sessions without verifiable bindings (including observed PID 2523586)
   block scheduling: each project's Main must coordinate safe retirement or,
   when needed for the drill, a fresh explicitly admitted launch with recorded
   bindings. Never terminate unknown/live work or infer a binding from cwd/title.
   Recheck this evidence immediately before the owner's final go; no restart
   with a known-unverifiable model-bearing session.
4. Fleet Main saves its last observed `fleet wait` receipt as a **metadata-only
   projection**, mode 0600: native `deliveryId`, message IDs and read/ack state.
   Do not save raw message bodies/subjects/payloads. Record explicitly whether
   there was no outstanding Delivery. Keep a known delivered-unacked batch and
   known pending message IDs for the later replay scenario, without discarding
   or prematurely acknowledging real work. The capture never consumes mail.
   Native unacked Delivery identity is **unavailable on Orca 1.4.242's read-only
   API**; inbox `read` is not proof of batch acknowledgement.
5. Identify an external operator shell that survives Orca exit and a live
   systemd user manager. Pin the reviewed fleet checkout throughout the drill.
   Record the owner's stop/start procedure and obtain the immediate final go.
   If any prerequisite fails, retain evidence and defer; never improvise.

**External capture, before and after**

Run from that external shell, using distinct new output directories and unit
names for each capture. Example for this host (substitute `after` only after
the owner-performed restart; no restart command is embedded):

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
Fleet code never escalates privileges. Builders must not run even read-only
`sudo` without Main approval.

**After the owner restarts once**

1. Run the `after` capture externally **before** rebind/relaunch. Compare runtime
   ID/version and handles against `before`, preserving both originals.
2. Owner starts fresh fleet/Project Alpha Mains with their handoff documents. Each
   Main re-lists terminals; matches worktree/incarnation/session/process evidence,
   not title alone; and binds its own replacement terminal using
   `orchestration run-use --id RUN`. Builders never bind the coordinator Run.
   Update private `coordinator.terminal` and lane handles as required; reinstall
   each affected project's supervisor only after its Main reconciles config.
   Never dual-send to old and replacement handles.
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

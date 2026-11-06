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
| Identity | Worktree ID, Run/Task/Dispatch/request IDs; Pad-ref convention | Pad ref first in spec; configured slots reused, task branch/comment carries ref. Session/terminal disposable. Task/thread integration still open. |
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
  injection. Live/unknown/ambiguous session blocks. Missing configured slot may
  create one Orca top-level worktree; no ad hoc side worktrees allowed.

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

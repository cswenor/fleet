# Fleet operating rules

## Ownership and knowledge

Fleet Main coordinates and never implements. Builders implement only their assigned
Pad task, using `fleet launch TASK-N` in one of the two configured reusable lanes.
Builders are fresh Sol medium omp sessions; never use worker-start for builders
(the Phase 0 preamble forbids GitHub publication and bare Sol selected high).
Read `docs/MAIN.md`, `docs/ROADMAP.md`, fleet Pad `PLAN-9` and process lessons
`DOC-19` at session start and after compaction/restart. Pad refs here are in
workspace `fleet`; historical PLAN-2685/TASK-2686/TASK-2687/TASK-2690/TASK-2694
and DOC-2181 are in `elsewhere` (use explicit `--workspace elsewhere`).
`docs/ROADMAP.md` is the single source of truth for design, decisions, phase
status, remaining work and acceptance. Update it when work lands; Pad carries
durable task evidence, not competing roadmap text.

## Decision rights

Fleet adopts `templates/AGENTS.decision-rights.md`; its filled fleet bindings
apply here. Record approvals in the assigned fleet Pad task comments.
- Builder decides internal implementation within the briefed Target/Change,
  tests, task-local refactors and small PR splitting.
- Main decides scope additions, public CLI/API/config contracts, security posture,
  cost/quota-affecting behavior, gate-waiver requests and cross-project coordination.
  Main confirms launches via `fleet launch`; no auto-dispatch. Main cannot waive
  owner-only approvals or the two publication gates below; other waivers must be
  explicit and recorded.
- Owner decides budgets, saved-reset spend (approval recorded BEFORE spending),
  rollout projects/order, production/shared-runtime restarts and dispatcher
  automation mode. Main's cost decisions do not grant budget/reset permission.
- Escalate builder -> Main through exactly one channel: `fleet thread question`,
  with Pad ref, proposal, risk and decision needed. It sends the native question
  to `run:run_ca3dd4d80c5a` itself. Never also send a direct orca send/ask/question
  for the same decision. Follow-ups use reply/status in the same pending native
  subthread, not a new direct question. Ordinary progress/PARK reports may still
  raw-send native `status`; FRESH-READY uses `thread ready` below. Reply
  in-thread and record the ruling on Pad; Main escalates owner-only decisions
  to the owner. Enqueue is not approval or proof of wake. Pending a decision,
  proceed only with unaffected in-scope work.
- Unknown is not permission: unknown quota blocks starts, unknown liveness
  forbids stop/retry, ambiguous launch receipts forbid duplicate launches.
  Preserve user work while reconciling uncertainty.

## Builder boundaries

- Main briefs Target / Change / Constraints / Ownership / Observable acceptance,
  with `Pad-Ref: TASK-N` first. Put the brief in the Pad task/handoff comments
  before launch: the launcher reads the item, not an arbitrary prompt argument.
- Fresh session for every task. Finish the old session before reusing its slot;
  do not resume context. Check actual model and thinking, not requested argv.
- `.omp/config.yml` mirrors Project Alpha's allowed native role fallbacks for Main
  and ordinary helper roles. `fleet launch` applies a stronger builder overlay:
  no model fallback, pinned Sol medium, fail-closed reserve, no auto-resume,
  extended context off, compaction fixed at 200K. Never bypass a withheld launch.
- Only configured lane worktrees. No extra worktrees, no git stash. Switch task
  branches inside your lane after checking ownership and preserving user work;
  use `git archive` for frozen scratch sources. Never reset/clean unrelated work.
- Keep reusable slots after merge. Close only your retired session when safe;
  retiring a slot requires explicit config/Orca coordination.
- Current task is already fresh. Pick the next piece by plan priority, usually
  the same feature; the same ref is allowed. Never switch to an unrelated task
  just to get a different ref. FRESH-READY requires this session's NEXT piece
  merged after its launch and a concrete next-piece handoff written after that
  merge, never the inherited boundary. Same-ref restart requires both on that
  ref; Pad done alone is insufficient.
  Settle the primary `thread result`, then make
  `fleet --config CONFIG thread ready NEXT [--body TEXT]` the last lane action
  and end your turn with a final message: no further tool calls or background jobs,
  never wait on your own terminal/idle state or send input to your own terminal.
  Main (or the owner) retires the idle session. Use the same config as `thread open`, never raw `orca send` for
  FRESH-READY. The idempotent receipt records the native message ID and a Pad
  comment containing it; retry a known-ID receipt only to finish recording.
  Ambiguous sends lacking an ID forbid duplicates and require reconciliation.
  Ready reports do not schedule launches; Main approves the fresh launch.
- On a durable quota PARK request: finish the current step, commit and push WIP
  to the task branch, write the Pad handoff, report PARKED with evidence to the
  configured coordinator Run. After reporting PARKED, end your turn with a final
  message and do nothing else: no further tool calls, no background jobs, and
  never a job that waits on your own terminal/idle state or sends input to your
  own terminal. Main (or the owner) retires the idle session via the documented
  retirement path (`fleet restart` for eligible finished work, or manual idle-proof
  `/exit` + `fleet reconcile`). No next task or FRESH-READY. Main must confirm a
  fresh session for resume. Fleet never injects exit/interrupt/kill input for PARK;
  unknown liveness never authorizes another park.

## Publication: two independent gates

Small PRs to `cswenor/fleet`, with Pad ref and real runtime evidence. Every PR has:
1. A fresh independent read-only **unadvised Sol medium reviewer**, not the
   builder's own review, with findings resolved and final-head evidence recorded.
2. Green GitHub **CI** on the final head. Fleet has **no review bot**; CI is the
   second gate, not a claimed second human/model review.

Use the committed stock `.omp/unadvised-review.yml` overlay and fresh reviewer
invocation in `docs/MAIN.md` (Independent review). Retain actual model/thinking
events and the reviewed final head; argv or `@review` alone is not evidence.

Builders self-merge only after both gates; never hand-merge a failing head. CI
failures caused by the task are the builder's responsibility. Project-specific
policy lives in `fleet.yml`, AGENTS or templates, never hardcoded project rules
in Python.
Verify changes on the real CLI/service/browser surface; tests alone do not prove
installation or wake delivery. Retain evidence as source-bound where appropriate.

## Events and threads

No model-turn polling. Use async builds and CI watches. While owning an open PR,
builders MUST keep `fleet --config CONFIG watch-pr PR` armed as an async background
command, process each event, and re-arm after every completion (including
timeout/error) until merged or closed. Never rely on retired `pr-wait.sh` scripts
or a single-workflow `gh run watch` alone. Fix named failures even if an initial
failure returns again. Orca send means durable enqueue, not guaranteed wake. Check mail at natural
checkpoints without hiding status/replies behind type filters. Process a Delivery
before acknowledging it. Reply to questions in their original inbox thread with
`orchestration reply --id`, from the terminal owning the identity.
Main uses the bound-Run `fleet wait` procedure in `docs/MAIN.md`: current Orca
routes Run and bound-terminal mail to one native Delivery path, not independent
mailbox multiplexing. Reconcile pre-binding legacy history separately.
Terminal builders have no supervised Dispatch: do not manufacture worker_done.

## Secrets and production

No credentials in Git, Pad, reports, logs, shell tracing, argv, screenshots or
image layers. Private token EnvironmentFiles are mode 0600. Never print service
environments, the PaaS variable values, provider/account identities, or raw
sensitive deployment commands. the PaaS hidden logs are not redacted: sanitize
only this app's sensitive stored command fields after deployment; rotate any
exposed deploy key. Do not disturb other applications or Project Alpha builders.
Central is the central host/the PaaS, tailnet-only HTTPS; supervisors remain per-project
systemd user services. No saved-reset spend without explicit owner approval.
Orca handles are runtime-scoped; re-list after restart, rebind the Run, update
private config, and reconcile liveness before retry. Unknown is not permission
to stop/retry a possibly live worker.

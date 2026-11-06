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
- Current task is already fresh. FRESH-READY is only after publishing NEW work
  and handing off a DIFFERENT next task, never the inherited boundary.

## Publication: two independent gates

Small PRs to `cswenor/fleet`, with Pad ref and real runtime evidence. Every PR has:
1. A fresh independent read-only **unadvised Sol medium reviewer**, not the
   builder's own review, with findings resolved and final-head evidence recorded.
2. Green GitHub **CI** on the final head. Fleet has **no review bot**; CI is the
   second gate, not a claimed second human/model review.

Builders self-merge only after both gates; never hand-merge a failing head. CI
failures caused by the task are the builder's responsibility. Scope additions,
public behavior/security/cost changes go to Main; owner alone approves saved
reset spending and rollout choices. Generic project policy lives in `fleet.yml`,
AGENTS or templates, never hardcoded project names/rules in Python.
Verify changes on the real CLI/service/browser surface; tests alone do not prove
installation or wake delivery. Retain evidence as source-bound where appropriate.

## Events and threads

No model-turn polling. Use async builds, CI watches and `fleet watch-pr PR`.
Orca send means durable enqueue, not guaranteed wake. Check mail at natural
checkpoints without hiding status/replies behind type filters. Process a Delivery
before acknowledging it. Reply to questions in their original inbox thread with
`orchestration reply --id`, from the terminal owning the identity.
Main waits on events; today one `check --wait` cannot cover both Run and
terminal mailboxes. Follow the honest interim procedure in `docs/MAIN.md` until
fleet TASK-10 supplies and proves a combined wait; never silently claim coverage.
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

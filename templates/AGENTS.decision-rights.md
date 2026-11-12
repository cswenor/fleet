# Project decision rights

Copy this policy into the project's agent instructions (or reference this file
from them), fill the bindings below, and record owner-approved exceptions there.
This is agent policy, not additional `fleet.yml` schema.

| Binding | Fill for your project | Filled fleet example |
|---|---|---|
| Project / Pad workspace | `<owner/repo>` / `<workspace>` | `cswenor/fleet` / `fleet` |
| Agent instructions | `<AGENTS path>` | `.omp/AGENTS.md` |
| Main destination | `run:<coordinator.run>` | `run:run_ca3dd4d80c5a` |
| Owner approval record | `<durable decision location>` | Assigned fleet Pad task comments |
| Reviewer | `<model and effort>` | Fresh Sol medium |
| CI gate | `<required final-head checks>` | GitHub CI |
| Additional review bot | `<none or project-required bot>` | None |
| Dispatch mode | `<owner-approved mode>` | Main-confirmed `fleet launch`; no auto-dispatch |
| Publication mode | `manual` or `auto_merge`; release `main` (default) / `none`; Review App slug / hold label / merge method | `manual`; exact-head Main GO |

## Authority

- **Builder:** internal implementation within the briefed Target/Change, tests,
  task-local refactors, and splitting work into small PRs. No scope expansion.
- **Main:** scope additions, public CLI/API/config contracts, security posture,
  cost/quota-affecting behavior, gate-waiver requests, cross-project coordination,
  and confirming launches under the owner-approved dispatch mode. A waiver must
  be explicit and recorded; Main cannot waive owner-only approvals or the two
  publication gates below.
- **Owner:** budgets, spending saved resets (approval recorded **before** spend),
  rollout projects and order, production/shared-runtime restarts, and dispatcher
  automation mode. Main's cost decisions do not authorize extra budget or resets.

## Escalation and safety

Builder -> Main: decision requests use **exactly one channel**:
`fleet thread question REF --body '...'`. It sends native type `question` to
`run:<coordinator.run>` itself. Never also send a direct orca send/ask/question for
the same decision. Follow-ups use reply/status in the same pending native
subthread, not a new direct question. Reports use `--type status`. Identify the
Pad task, proposal, risk, and decision needed. Run `fleet thread wait REF` as an
async background command. Reply in the original question thread with `orchestration reply --id`;
the builder wait records the ruling on Pad before returning. Main escalates
owner-only decisions to the owner and records approval before action.
Enqueue is not approval or proof of wake. Proceed only with unaffected in-scope
work while a decision is pending. Never bind a builder terminal to any Run;
scratch Runs belong only to separate owned scratch terminals, closed after use.
After recording a pending decision and finishing unaffected work, ending the turn
is acceptable. With delivery and `coordinator.builder_wake` enabled, Fleet wakes
the proven-idle builder when Main replies. On wake run `thread check REF`, then
`thread wait REF --timeout 600` using the original `--config` to record the reply
on Pad before continuing. No settlement, restart or interrupt is authorized.
Read-only `thread check` uses only the per-task lock. `--ack` additionally uses a
tiny publication lock, never the global launcher lock; contention waits at most
5s then exits 4 with `launch publishing; retry`. Retry later, never loop tightly.
On `launch changed; re-run check`, inspect a fresh check before acknowledging.
Launcher-required paths wait at most 15s; exit 4 / `launcher busy` means retry
later (about 60s), never loop tightly. Immediate-refusal paths remain immediate.

Unknown is not permission: unknown quota blocks starts; unknown liveness does not
authorize stop/retry; ambiguous launch receipts do not authorize duplicate launches.
Preserve user work and resolve uncertainty before acting.

## Publication and project policy

In `publication.mode: manual` (the default), self-merge small task-linked PRs only
after BOTH gates on the final head:
1. Fresh independent read-only unadvised review using the bound reviewer policy;
   resolve findings and retain reviewer/session and exact-head evidence.
2. Green required CI on that same head; fix task-caused failures before merge.

While owning an open PR, builders **MUST** keep `fleet --config CONFIG watch-pr N`
armed as an async background command. Process each event and re-arm after every
completion (including timeout/error) until merged or closed. Never rely on retired
`pr-wait.sh` scripts or a single-workflow `gh run watch` alone; inbox enqueue does
not prove wake. Fix reported failures even if an initial failure returns again.

Run anything that can exceed a few minutes (test suites, evals, profilers,
CI waits) as an async background job with an explicit timeout sized to the
expected runtime plus margin, and process its completion when it wakes you.
Never block your turn in a single foreground wait longer than ~10 minutes.
On a background timeout or no progress, inspect (output size, stack/py-spy,
logs) before re-running; a timeout is evidence, not a retry signal.
Background jobs are exempt from the `stalled` alert, so the explicit timeout
is the hang detector.

For the filled fleet Sol-medium binding, copy `.omp/unadvised-review.yml` with
the workflow skill and use the fresh stock omp invocation in
`docs/MAIN.md` (Independent review). Bind `@review` explicitly in the overlay;
do not invent a reviewer runner or inherit fallback/advisor/auto-spend policy.
Retain actual `model_change` / `thinking_level_change` evidence and exact head.
Other reviewer bindings require Main-approved project policy, not a silent
model substitution.

Do not assume a review bot exists or count CI as a second model review. A project
may require its own bot in addition to these gates. Verify the changed real
surface and retain evidence in the PR and durable task outcome.

In `publication.mode: auto_merge`, both gates remain required for merge, not
necessarily for arming. Builders NEVER merge directly or with admin bypass, and
NEVER modify the configured hold label. `publication.release` accepts `main`
(default) or `none`, and is ignored in manual mode; manual prompts and policy
stay unchanged. Fleet's own manual policy, including its current PR, still
requires exact-head Main GO.

- `main`: send `thread report REF --kind release_ready --pr N --head FULL_SHA
  "evidence"` and wait for Main's exact-head **RELEASE**. Main verifies independent
  review, required CI, the configured `publication.review_app` successful check
  and exact-head APPROVE, and no hold/draft/veto. Unknown, stale-head or wrong-App
  evidence cannot authorize release; aggregate check success is not eligibility.
- `none`: immediately after independent final-head **PASS is posted on the PR**,
  the builder arms native auto-merge before CI/App completion, without Main
  RELEASE. GitHub itself still enforces configured Review App exact-head APPROVE,
  required checks and `merge-gate`; no bypass is authorized.

Arm with `gh pr merge N --auto --match-head-commit FULL_SHA --squash` (use the
configured `publication.merge_method`), recording the actual actor and PR/head
on Pad, plus RELEASE for `main`. Before **any push after arming**, disable
auto-merge/dequeue and verify removal; unknown evidence blocks the push. Then
push, obtain a new independent exact-head PASS, post it on the PR, and rearm.
For `main`, also obtain fresh CI/App gates and a new RELEASE; pushing invalidates
the old ruling. Keep watching until GitHub actually merges or closes the PR;
arming or queue entry is not task completion.

Require the bot's `merge-gate` on live PR/synthetic heads and App-bound reviews
in branch rulesets; Fleet's diagnostic watcher does not enforce hold/veto.
Owner/Main stop publication with hold plus, if armed, disarm/dequeue and verified
removal. Builders never change hold. Asynchronous checks cannot revoke a completed
merge. Configuration neither installs a bot nor authorizes deployment.

Display-only review evidence is not authorization: matching the current head
remains current regardless of App identity, without proving configured App
authority. An older APPROVED head X carried as the first parent of a two-parent
current head, with its other parent proven an ancestor of the base, is only an
**unverified carrier of reviewed head X**, never exact-head approval or release
eligibility. Do not claim a clean merge without remerge-diff proof.

Pick the next piece by plan priority, usually the same feature; the same ref is
allowed. Never switch to an unrelated task just to get a different ref.
FRESH-READY requires this session's NEXT piece published in a merged PR carrying
its ref after its launch, then a concrete next-piece handoff written after that
merge. An inherited publication/handoff is not readiness. Except for a fully
recorded valid park on the current launch, same-ref `restart` requires that new
merge and later handoff on the same ref, with a non-empty next-piece description
after the ref; Pad done alone does not satisfy this guard.
After primary `thread result` settlement, make
`fleet --config CONFIG thread ready NEXT [--body TEXT]` the last lane action and
stop, using the same config as `thread open`. Never raw `orca send` for
FRESH-READY or decisions; decisions use `thread question`. Ordinary progress/PARK
reports may still raw-send native `status`. Ready's idempotent receipt records the
native message ID before its Pad message-ID comment; retry the same known-ID
receipt to finish recording without another send. Ambiguous sends without an ID
refuse duplicates and require reconciliation, not a raw-send workaround.
Ready does not schedule launches; Main approves the fresh launch.
Ready sends native `question` as a launch decision request, waking Main's
decision-filtered wait. Main replies in that native thread with the ruling;
the builder ends its turn with a final message after a successful report, without
waiting for the reply. No further tool calls or background jobs; never a job that
waits on its own terminal/idle state or sends input to its own terminal.
Main (or the owner) retires the idle session via the documented retirement path.
Existing sent `status` receipts remain valid and must never be resent on upgrade.
If complete but no concrete next ref exists, settle with `thread result`, then
make `fleet --config CONFIG thread needs-next REF` the last lane action and end
the turn. It sends one decision request per launch after verifying a new merge
or explicit park completion; never replace this with a status report.
If Main authorizes continuing after `thread result`, record that ruling and run
`fleet --config CONFIG thread reopen REF --authorized-by MSG_ID [--reason TEXT]`
before any new question; never fall back to direct Run questions. This continues
only the latest settled attempt on the same launch-owned unbound terminal/live
incarnation/worktree; it does not authorize a new launch or waive publication gates.

On durable PARK, finish the step, commit/push WIP, write a concrete Pad resume
comment, then make `fleet --config CONFIG thread parked REF --resume-note COMMENT_ID`
the last lane action. Clean tracked files and actual remote HEAD equality are
required. Non-ignored untracked files are preserved regardless of age and
listed (up to 50 relative paths and total count); never stage/move/delete them.
The command settles pushed-WIP completion without a merge; Main can use guarded
different-ref restart to reassign the slot or same-ref restart to confirm a fresh
resume on the parked branch. Then end the turn and do nothing else:
no further tool calls, no background jobs, and never a job that waits on your own
terminal/idle state or sends input to your own terminal. Main (or the owner)
retires the idle session via the documented retirement path (`fleet restart` for
eligible finished work, or manual idle-proof `/exit` + `fleet reconcile`).
No next task or FRESH-READY; resume requires a Main-confirmed fresh session.

Keep project-specific rules in project AGENTS, `fleet.yml` (supported configuration
only), or referenced templates, never project-name branches in fleet code. The
filled fleet bindings illustrate adoption, not defaults for other projects.

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

Unknown is not permission: unknown quota blocks starts; unknown liveness does not
authorize stop/retry; ambiguous launch receipts do not authorize duplicate launches.
Preserve user work and resolve uncertainty before acting.

## Publication and project policy

Self-merge small task-linked PRs only after BOTH gates on the final head:
1. Fresh independent read-only unadvised review using the bound reviewer policy;
   resolve findings and retain reviewer/session and exact-head evidence.
2. Green required CI on that same head; fix task-caused failures before merge.

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

Pick the next piece by plan priority, usually the same feature; the same ref is
allowed. Never switch to an unrelated task just to get a different ref.
FRESH-READY requires this session's NEXT piece published in a merged PR carrying
its ref after its launch, then a concrete next-piece handoff written after that
merge. An inherited publication/handoff is not readiness. Same-ref `restart`
requires that new merge and later handoff on the same ref, with a non-empty
next-piece description after the ref; Pad done alone does not satisfy this guard.

Keep project-specific rules in project AGENTS, `fleet.yml` (supported configuration
only), or referenced templates, never project-name branches in fleet code. The
filled fleet bindings illustrate adoption, not defaults for other projects.

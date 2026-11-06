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

Builder -> Main: send a durable Run question/message identifying the Pad task,
proposed change, risk, and decision needed. Reply in the original question thread;
record the ruling on the Pad task. Main escalates owner-only decisions to the
owner and records approval before action. Enqueue is not approval or proof of wake.
Proceed only with unaffected in-scope work while a decision is pending.

Unknown is not permission: unknown quota blocks starts; unknown liveness does not
authorize stop/retry; ambiguous launch receipts do not authorize duplicate launches.
Preserve user work and resolve uncertainty before acting.

## Publication and project policy

Self-merge small task-linked PRs only after BOTH gates on the final head:
1. Fresh independent read-only unadvised review using the bound reviewer policy;
   resolve findings and retain reviewer/session and exact-head evidence.
2. Green required CI on that same head; fix task-caused failures before merge.

Do not assume a review bot exists or count CI as a second model review. A project
may require its own bot in addition to these gates. Verify the changed real
surface and retain evidence in the PR and durable task outcome.

Keep project-specific rules in project AGENTS, `fleet.yml` (supported configuration
only), or referenced templates, never project-name branches in fleet code. The
filled fleet bindings illustrate adoption, not defaults for other projects.

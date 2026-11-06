# Fleet roadmap

Single source of truth for fleet Main. Updated 2026-11-06. Owner direction:
**fleet dogfoods its workflow; fleet Main coordinates all remaining phases and
never implements. Project Alpha Main remains Project Alpha-only after handover.**

Pad workspace `fleet`: **PLAN-9**; process lessons **DOC-19**. Historical source
is `elsewhere` **PLAN-2685**, including all Phase 0 findings and Main decisions;
TASK-2686 (checks), TASK-2687 (supervisor), TASK-2690 (central), TASK-2694 (handover).
Use `pad --workspace WORKSPACE item show REF` and `item comments REF`; `show`
only includes recent comments. Cross-links are posted on both plans. Fleet task
statuses are `open`, `in-progress`, `done`, `cancelled`, not elsewhere's `todo`.

## Target design: all 11 original items

| # | Original target | Current contract / gap |
|---|---|---|
| 1 | Task = Pad item + Orca worktree + Task/Dispatch; Pad-Ref first; disposable terminal/session IDs | Reuse configured slots, task-specific branches/comments. Terminal launcher has no authoritative supervised Dispatch; TASK-15 closes generic task/thread integration without inventing one. |
| 2 | Fresh supervised omp workers, pinned model, retry-of, effective launch check | **Superseded/NO-GO for builders**. `fleet launch` uses terminal omp with explicit Sol + medium and fresh overlay; actual session events prove resolved model/thinking, not launch.effective alone. Restart still open. |
| 3 | One Run/project; ask/reply/send/check/worker_done; Pad durable | Fleet has its own Run. Terminal builders report status/questions through durable inboxes, not keystrokes or fake worker_done. One-thread-per-task remains TASK-15. |
| 4 | Main wakes on worker_done/escalation/question via background check --wait | TASK-10 implements `fleet wait`: bound Run receives both Run- and bound-terminal-addressed mail; native replay/ack, timeout/error distinction, read-only legacy diagnostics. Project Alpha cutover remains open. |
| 5 | Builders use async builds/CI; supervisor relays PR/review/queue/merge events | Implemented `fleet watch-pr`; send is enqueue only. PR initial DIRTY/failing/merged/closed diagnoses immediately. |
| 6 | Per-project supervisor: health, PR watcher, Pad dispatcher, quota, snapshot | Health/watch/push/install implemented; explicit guarded launch exists. Automatic Pad scheduling is NOT implemented; TASK-12. |
| 7 | Quota reserve, no silent builder fallback, fail-closed | New launches withheld with unknown/stale/reserve/cap/disk failure. Running-lane priority enforcement and saved-reset consent remain TASK-11. |
| 8 | Fresh task context; Sol compaction fixed ~200K; extended context off | `fleet launch` overlay enforces `f200000`, no auto-resume, extendedContext false. Repo config also fixes threshold; native Main/helper fallback policy mirrors Project Alpha, builder overlay overrides it. |
| 9 | Read-only central with 24h PRs/commits/queue/builders/model/quota/disk; outage independent | Implemented the central host/the PaaS central, per-project systemd supervisors. Distinct project tokens; tailnet-only view, no controls. |
| 10 | Decision rights, initially Project Alpha standing policy | Generic template `templates/AGENTS.decision-rights.md` adopted in fleet (TASK-14); task threads remain TASK-15. Project Alpha standing policy stays Project Alpha-owned via TASK-16 coordination. |
| 11 | Project Alpha tier-e CI produces CG + stock1000 + affected-consumer 200; retire local cohorts | Project Alpha repo/lane work under Project Alpha Main, not fleet implementation. TASK-16 tracks acceptance links. |

## Decisions log (2026-11-06)

Phase 0 evidence: Orca 1.4.242, omp 18.13.1, TASK-2686 comment on historical
PLAN-2685. No product code or shared-runtime restart was performed.

| Check | Verdict | Evidence / reason |
|---|---|---|
| 2: supervised omp + model | NO-GO as written | Explicit bare Sol actually selected **high**; @good_worker selected **medium**. launch.effective echoed alias with effort null, not proof of resolution. Restore unverified. |
| 3: preamble/channel | NO-GO until policy reconciled | Injected preamble says “Don't post to Slack, GitHub, or other channels”; conflicts with self-publication. Also emits bare orca on Linux. Two real worker_done completions settled successfully, but no publication proof. |
| 4: event Main | GO for local worker_done only | A real completion woke background check --wait after ~32.5s; FIFO delivery/ack drained both completions. Questions/escalations/restart and combined mailboxes not proven. |
| 5: send wake | NO-GO for assumed push-to-turn | Send is durable enqueue, best-effort nudge only. Late send to settled dispatch rejected dispatch_inactive. Async command completion remains usable. |
| d: restart | UNVERIFIED | No safe independent runtime/profile recipe established; do not kill shared Orca or infer restart behavior from fresh launches. |

- **Launch (Main decision):** terminal launcher, now `fleet launch`, pins
  `omp --model openai-codex/gpt-6.1-sol --thinking medium`; fresh session/task.
  Do not switch builders to worker-start until its publication/thinking contracts
  are demonstrably compatible. Native lifecycle docs are not permission to undo
  this owner/Main decision.
- **Fallback/reserve:** no silent builder provider/model fallback; pinned model
  chain empty, modelFallback false, reserve fail-closed. Missing usage blocks new
  starts. Ordinary native role fallbacks mirror Project Alpha; builder overlay wins.
  Owner approval required before spending saved resets.
- **Context:** fixed Sol `f200000` compaction trigger, extended context off. Large
  standard windows are not a reason to carry task context into the next task.
- **Central placement:** initial devhost user-service central was superseded
  by owner: production **the central host/the PaaS** only; supervisors stay **systemd**.
  Tailnet URL: https://central.example.ts.net:8443/ . Old devhost central and
  HTTPS frontend retired only after production ingestion/browser proof.
- **Slot reuse:** two fleet lanes; a verified ready shell can be reused, active,
  unknown or ambiguous terminals cannot. No extra worktrees; no /exit injection.
  Keep lanes after merge; delete only on explicit slot retirement.
- **Footer:** assigned task is already fresh. FRESH-READY only after publishing
  new work and handing off a DIFFERENT next task, never inherited handoff.
- **the PaaS log hygiene:** hidden stored commands may include encoded deploy-key
  material; hidden is not redacted. Runtime-only token variables, redaction on,
  auto-deploy off on affected releases; sanitize only this app's sensitive command
  fields after manual rollout, rotate any exposed deploy key before sharing logs.
- **Dispatch mode:** Main-confirmed explicit `fleet launch` until owner chooses
  automatic behavior, priority and budget authority. Do not silently auto-dispatch.
- **Review gates:** fresh independent read-only unadvised Sol medium review then
  final-head green CI. No fleet review bot; do not claim one or a second model review.
- **TASK-10 native routing (Main-approved):** live discovery showed a bound
  terminal address redirects into its Run; unbound terminal consuming checks have
  no replayable delivery ID. Use one native Run wait, fail closed unless caller
  owns the configured Run binding, and include read-only terminal history on every
  return. No fabricated terminal Delivery/local ack, no independent-mailbox claim.
  Pre-binding legacy mail needs separate reconciliation; any bound-path bypass
  stops cutover pending upstream capability.

## Phase status and evidence

| Phase | Status | PR / evidence |
|---|---|---|
| 0 live checks | Partial: a–c recorded; d restart open | elsewhere TASK-2686 / PLAN-2685 full findings; GO/NO-GO above. |
| 1 supervisor and Project Alpha adoption | Delivered core; identified gaps below remain | fleet [#1](https://github.com/cswenor/fleet/pull/1), [#3](https://github.com/cswenor/fleet/pull/3), [#4](https://github.com/cswenor/fleet/pull/4), [#5](https://github.com/cswenor/fleet/pull/5), [#6](https://github.com/cswenor/fleet/pull/6). Project Alpha [#2368](https://github.com/example-org/project-alpha/pull/2368) actually merged 2026-11-06; PR CI 42222656293, full native merge-group CI 42223792846 green. Real owner/coordinator event delivery, wrong-model alert, reserve path and fresh model/thinking proved in TASK-2687; no restart proof. |
| 2 central | Delivered, owner-corrected placement | fleet [#7](https://github.com/cswenor/fleet/pull/7), [#8](https://github.com/cswenor/fleet/pull/8), [#9](https://github.com/cswenor/fleet/pull/9), [#10](https://github.com/cswenor/fleet/pull/10), [#11](https://github.com/cswenor/fleet/pull/11). TASK-2690: the central host the PaaS app yktqfo8prpciui09gm2i4763, healthy deployment rrl3jc2y3ngx3elsa8pck3ml at b501e23; live Project Alpha ingestion 8 builders/31 PRs, no collector errors; consistent history migration, 46 rows/integrity ok. |
| Self-host/handover | Delivered and installed 2026-11-06 | [#12](https://github.com/cswenor/fleet/pull/12) setup + fail-closed unknown/disconnected occupancy; [#13](https://github.com/cswenor/fleet/pull/13) complete knowledge. Fresh independent unadvised read-only Sol review PASS, CI [42240339663](https://github.com/cswenor/fleet/actions/runs/42240339663) / [42240526182](https://github.com/cswenor/fleet/actions/runs/42240526182) green. Fleet PLAN-9/DOC-19, two reusable slots and own active supervisor; real CLI status/live-slot refusal and the central host **both projects LIVE** verified. Run run_ca3dd4d80c5a reserved; **Main not started by builder**. Installation receipt in docs/MAIN.md. |
| 3 generic process + Project Alpha process | Partial | TASK-14 generic policy adopted; TASK-15 threads and TASK-16 coordination remain. Project Alpha standing policy/tier-e must land in Project Alpha via its Main/lanes. |
| 4 additional projects | Open, owner chooses | Fleet TASK-17; candidates below, no implied permission to install all. |
| Usage outcome | Baseline recorded; after measurement open | Fleet TASK-18. Do not label the workflow a measured saving yet. |

## Remaining work and concrete acceptance

### TASK-10 — Main wake-up through BOTH address forms

Implemented in [PR15](https://github.com/cswenor/fleet/pull/15) under Main's approved native-routing contract above.
One background wait covers `run:RUN` and its **bound** terminal address because
current Orca routes both into the same Run. It rejects unbound/wrong-Run callers
before any wait/ack, returns all FIFO message types with native delivery IDs,
never implicitly acknowledges, distinguishes timeout (exit 3) from failure
(exit 2), and includes read-only terminal history diagnostics on every result.
It does not consume/ack legacy history or multiplex independent mailboxes.

Real scratch Run evidence (2026-11-06): Run-address status
`msg_513e063d966a` woke background wait / `delivery_7ef7919990b3`;
bound-terminal status `msg_c52376f1f397` woke the same command /
`delivery_c5941d646f44`. Both replayed unchanged before ack. Ack preserved newer
mixed status+question batch `delivery_66cee7d7c68c`; question
`msg_2c010acbb20f` received in-thread reply `msg_f9fdee066674`.
Timeout then later arrival, SIGKILL of the active wait and re-run, replay and ack
all passed; final ack returned timeout without duplicate processing.
Owned unbound/pre-binding shell proof retained old terminal mail unread in
diagnostics after binding while new terminal-address mail routed to the Run.
Unavailable CLI returned error, not timeout. All three owned probe terminals
were closed; no shared runtime restart or production service changes.

**Still open for Main:** coordinate Project Alpha cutover and retire
`/var/tmp/project-alpha-scratch/TASK-2464/queue-report.sh` and
`/var/tmp/project-alpha-scratch/shared/pr-wait.sh` (PR replacement is `fleet watch-pr`)
only after replacement acceptance. Builder does not edit/retire these scripts.
Send remains enqueue, not proof of wake. Shared-runtime restart remains TASK-13.

### TASK-11 — Quota governor enforcement

Warn-only for already-running lanes is insufficient. Agree safe cooperative
pause/checkpoint/resume semantics and priority/ties with owner; withhold/park
lowest-priority work when reserve is reached, preserving in-flight state. Missing
or stale usage fails closed. Demonstrate priority ordering, refusal, recovery,
no silent model fallback, and explicit owner approval recorded **before** spending
any saved reset. Unknown liveness never authorizes destructive stop/retry.

### TASK-12 — Pad ready queue to a free slot

Current behavior: **Main-confirmed**, explicit launch; no automatic dispatcher.
Ask owner whether automatic starts are wanted and which priorities, tie-breaks,
confirmation/scope/cost boundaries apply. Acceptance: recorded decision, ready
and dependency eligibility, cap/reserve/occupancy checks, duplicate-task/slot
protection, and one real queued task launched on a reused slot as fresh Sol medium
with Pad/Orca identity. Automation may not bypass owner budget authority.

### TASK-13 — Orca restart semantics (Phase 0 d)

Use documented isolated runtime or owner-approved maintenance window, never a
surprise shared restart. Capture actual model/thinking before and after, whether
process survived/resumed, replacement handle mapping, coordinator Run rebinding,
and pending delivery replay/ack. Re-list handles; no dual-send. Reconcile
restored_unconfirmed before any retry. A retry re-pins explicit placement/model;
a runtime restart alone does not prove old attempt stopped. Publish evidence and
GO/NO-GO, not an inference from requested launch settings.

### TASK-14 — Generic decision-rights/policy template

Implemented `templates/AGENTS.decision-rights.md`: project-fillable bindings and
filled fleet example, adopted in `.omp/AGENTS.md`, linked from README adoption
instructions and `fleet.example.yml`. Builder owns brief-local implementation;
Main owns scope/public contracts/security/cost decisions and confirms launches;
owner retains budgets, prior saved-reset approval, rollout, restarts and automation
mode. Main cannot waive owner approvals or the two publication gates.

Real fresh read-only Sol medium comprehension session
`01a1b18c-76d1-762e-ba9b-ed8ca0f46e53` (2026-11-06) read the adopted AGENTS:
saved-reset spend -> owner, recorded before spend; public CLI flag -> Main;
brief-local internal refactor -> builder. Template search found no imported
project-specific game/performance policy. No Python, runtime or CI logic changed.
Publication review/CI receipts are retained in the TASK-14 PR and Pad outcome.

### TASK-15 — One durable thread per task

Link Pad ref, task branch/comment, reusable Orca slot and Task/Dispatch where
applicable, across disposable sessions. Terminal launcher currently has no
supervised Dispatch: never fabricate lifecycle completion. Prove a real question,
in-thread reply and result with durable Pad outcome, no lost status under type
filters and no reply on a settled attempt. Fresh session each new task.

### TASK-16 — Phase 3 coordination contract with Project Alpha

Fleet owns generic policy/template and thread capability only. Project Alpha Main
assigns **Project Alpha lanes** for its standing policy doc and `tier-e` CI evidence
job: CG + stock1000 + affected-consumer 200 on dedicated baseline runner, replacing
local cohorts. Fleet Main requests/records Project Alpha-owned task/PR links and actual
green labeled-job evidence, records contract changes here, but never sends input
to Project Alpha builder terminals or implements Project Alpha repo changes itself.

### TASK-17 — Phase 4 install elsewhere

Ask owner **which projects, in what order**. Candidates from Pad workspace list:
Project Beta (`project-beta`), House of Voi (`house-of-voi`), Tolkien (`tolkien`),
Elsewhere (`elsewhere`), coqui (`coqui`), whatsapp-rag (`whatsapp-rag`). For each
approved project: repo-local config/instructions, own Pad/Run/slots, pinned builder
model policy, systemd supervisor, distinct private token registered in the PaaS,
fresh the central host card, actual launch/event/PR-wait proof, clean logs. Record individual
acceptance and owner-declined candidates; candidate list is not rollout approval.

### TASK-18 — Success metric: Codex usage per merged PR

Baseline: seven days ending **2026-11-06**, owner-provided figures.

| Metric | Baseline |
|---|---:|
| Sol calls | 137,049 |
| Cache-read tokens | 22.4 billion |
| Context per call (approx.) | 160K |
| List-equivalent cost (approx.) | $3,600 |
| Project Alpha PRs merged/day (approx.) | 26 |
| Implied 7-day PR count (estimate, not measured denominator) | 182 |

After: collect a comparable full seven-day window, exact merged-PR denominator,
calls/merged PR, cache-read tokens/merged PR and list-cost/merged PR. Preserve
aggregate source provenance and date boundaries, distinguish provider/account
changes and workload differences; report before/after measurable reduction or
lack of one. No secrets/account IDs. Do not infer causal savings from an
incomparable interval or treat ~182 as an exact baseline PR count.

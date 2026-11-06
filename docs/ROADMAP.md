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
| 7 | Quota reserve, no silent builder fallback, fail-closed | New launches withheld with unknown/stale/reserve/cap/disk failure; TASK-11 adds known-reserve cooperative priority parking, recovery reporting and explicit saved-reset consent policy (no spending command). |
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
- **TASK-20 retirement (Main-approved):** `fleet restart NEXT --slot PATH
  --finished-ref OLD` replaces manual `/exit` only with exact launch/session
  binding, positive idle evidence and publication/handoff proof. Never interrupt
  a working/unknown session; journal before one exit, verify same-incarnation
  shell, then normal guarded launch. No forced mode or shared-runtime restart.

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
| Guarded builder retirement | Implemented TASK-20; publication receipts retained on Pad/PR | One exit only after publication + exact idle/session proof; shell verified before normal fresh launch. Real scratch retirement/fresh Sol medium and refusal evidence below. |

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

Implemented in [PR17](https://github.com/cswenor/fleet/pull/17) under owner/Main rulings on TASK-11:
known reserve only; unknown/stale usage withholds starts and alerts, never parks.
Pad priority orders lanes; newest authoritative launch wins a tie, unresolved
timestamps block only ties. One outstanding PARK, timeout escalates once,
exact verified shell exit permits re-evaluation; no terminal input/stop/kill.
Recovery lists parked/outstanding tasks; resume is Main-confirmed fresh context.
Saved resets require recorded owner DECISION before spend; no spending code path.

2026-11-06 isolated CLI drill: known reserve and unknown launch refusals had
distinct reasons. Scratch Run `run_b19810ac37d9`, two explicitly adapted scratch
terminal inventory rows (not additional worktrees), real Pad priorities/delivery.
Unknown produced alert only. Reserve sent exactly one low-priority TASK-21 PARK
`msg_f5940010e069`; repeat cycle did not resend. Fresh session
`01a1b191-42e6-743d-8912-c2d91519ea72` recorded resolved Sol medium/no fallback,
pushed WIP `a203160`, wrote Pad handoff, reported PARKED `msg_21fc41491dac`,
and returned to a verified shell. Next cycle re-evaluated the higher-priority
scratch lane; recovery `msg_3079fd84eb6e` listed TASK-21 parked/TASK-11 outstanding.
Only scratch terminals received PARK; no live builder/production service changed.
Scratch builder/dummy terminals closed and remote `drill/task-21-quota-1793980358`
deleted. 51 tests pass, including pre/post-selection reincarnation suppression,
post-exit durable-copy retry and dedupe across receipt expiry. Reviews prompted
correction of these defects before merge. Final locked-send smoke Run
`run_582aa0f81eed` proved original queued PARK never reaches a replacement launch,
and actual builder send `msg_3b54726d358f` occurs once while failed Run/Pad copies
retry after verified shell exit (`msg_5a4ca093a581`). All smoke terminals closed.
Preselection smoke `run_57fb2effd2f8` proved stale recorded incarnation causes
alert-only/no PARK, then reconciliation permits exactly one actual dummy PARK
`msg_4030331dc6e9`. Its owned scratch terminals were closed.
Long-outage smoke `run_5c4ce0b02cd8` aged pending receipts eight days and proved
builder `msg_67a7ceef943f`/Run `msg_1ff2f5c2039a` sent once while only failed Pad
retried. Pending events protect successful-recipient receipts from pruning.
See the PR and Pad for final-head independent review/CI gate receipts.

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

### TASK-20 — Guarded finished-session retirement

`fleet restart NEXT --slot PATH --finished-ref OLD` requires a merged PR carrying
OLD, plus Pad OLD done or a post-publication handoff on NEXT, with OLD != NEXT.
Canonical slot-keyed `launches.json` binds the exact terminal/incarnation and
private session directory. Single connected writable terminal, exact-pane
idle/done, current empty idle composer, live session lease, local Linux foreground
OMP process binding and final assistant stop are required. Unavailable `/proc`
or unsupported hosts refuse. Only `credential_pin` / `title_change` may follow; unknown or later
work refuses. Stale working screens, background jobs, dialogs, disconnected or
ambiguous targets and missing legacy binding all fail closed.

The launcher lock covers rechecks, journal-before-one-`/exit`, bounded wait for a
same-incarnation POSIX shell and normal admission/fresh launch. Partial receipts
block repeat retirement of that launch ID; no Esc, interrupt or forced mode.
No Orca restart, quota parking, dispatcher or Project Alpha edits are included.

Real CLI scratch proof (2026-11-06), restricted to fleet-phase1/private state:
dialog and async sleep35/wait refused with exit2; finished TASK-14/merged PR16
retired launch `1793980873022128830`, verified its shell and fresh-launched
`1793981033926533680`. New session `01a1b19a-325e-7798-8a45-3f9803897828`
recorded Sol model + medium thinking events. Unpublished TASK-20 and absent
target refused with exit2. Owned scratch terminal closed after shell proof;
no other lane/runtime touched. Disconnected/orphaned, races and partial-failure
no-retry boundaries have unit coverage. Full publication receipts remain on
TASK-20 and its PR.

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

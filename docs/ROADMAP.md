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
| 1 | Task = Pad item + Orca worktree + Task/Dispatch; Pad-Ref first; disposable terminal/session IDs | TASK-15 links canonical Pad task thread, actual branch, reusable slot and launch attempt; unique native question subthreads isolate replies. Terminal builders have no supervised Dispatch; none invented. |
| 2 | Fresh supervised omp workers, pinned model, retry-of, effective launch check | **Superseded/NO-GO for builders**. `fleet launch` uses terminal omp with explicit Sol + medium and fresh overlay; actual session events prove resolved model/thinking, not launch.effective alone. Restart still open. |
| 3 | One Run/project; ask/reply/send/check/worker_done; Pad durable | Fleet's Run receives typed questions/status; thread wait caches terminal history and copies Main decisions to Pad. Decision-first presentation never filters or changes whole-batch ack. |
| 4 | Main wakes on worker_done/escalation/question via background check --wait | TASK-10 implements `fleet wait`: bound Run receives both Run- and bound-terminal-addressed mail; native replay/ack, timeout/error distinction, read-only legacy diagnostics. Project Alpha cutover remains open. |
| 5 | Builders use async builds/CI; supervisor relays PR/review/queue/merge events | Implemented `fleet watch-pr`; send is enqueue only. PR initial DIRTY/failing/merged/closed diagnoses immediately. |
| 6 | Per-project supervisor: health, PR watcher, Pad dispatcher, quota, snapshot | Health/watch/push/install implemented. TASK-12 adds read-only `fleet next` proposals; owner chose Main-confirmed explicit launch, never automatic starts. |
| 7 | Quota reserve, no silent builder fallback, fail-closed | New launches withheld with unknown/stale/reserve/cap/disk failure; TASK-11 adds known-reserve cooperative priority parking, recovery reporting and explicit saved-reset consent policy (no spending command). |
| 8 | Fresh task context; Sol compaction fixed ~200K; extended context off | `fleet launch` overlay enforces `f200000`, no auto-resume, extendedContext false. Repo config also fixes threshold; native Main/helper fallback policy mirrors Project Alpha, builder overlay overrides it. |
| 9 | Read-only central with 24h PRs/commits/queue/builders/model/quota/disk; outage independent | Implemented the central host/the PaaS central, per-project systemd supervisors. Distinct project tokens; tailnet-only view, no controls. |
| 10 | Decision rights, initially Project Alpha standing policy | Generic template adopted (TASK-14); TASK-15 adds typed decision requests and durable task threads. Project Alpha standing policy stays Project Alpha-owned via TASK-16 coordination. |
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
- **Dispatch mode (owner decision on TASK-12):** Main-confirmed only; no automatic
  starts. `fleet next` proposes, Main applies scope/budget constraints and confirms
  each explicit `fleet launch`. Main approved task-list/dependency queries because
  Pad's current `project ready` returns in-progress work, omitting open backlog.
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
- **TASK-32 stock-first audit:** [audit table](stock-omp-gh-audit.md), merged
  [PR26](https://github.com/cswenor/fleet/pull/26), retains whole-PR watcher and
  project policy composition: stock CI watches do not replace PR event semantics.
  Main approved stock unadvised-review overlay instead of improvised setup, plus
  native no-auto-reset pins in reviewer/builder overlays (owner never-auto-spend
  decision). TASK-33 owns installing the same overlay with the project skill.
- **TASK-31 stock-first lifecycle audit:** [live function-level table](orca-feature-map.md#task-31-stock-first-lifecycle-audit-2026-11-06).
  Orca 1.4.242 rejects OMP Sol `--effort medium`; model-only launch actually
  records Sol/high. Native transport/replay/ack/wake works, but standalone
  builders cannot use native `ask` and do not appear in supervised worker-list.
  Main approved audit-only completion (decision `msg_e1e0ad77642b`): keep policy
  wrappers; no equivalent replacement proved. Both scratch terminals closed;
  no shared restart/deployment. TASK-34 separately owns restart race/self-claim.

## Phase status and evidence

| Phase | Status | PR / evidence |
|---|---|---|
| 0 live checks | Partial: a–c recorded; d restart open | elsewhere TASK-2686 / PLAN-2685 full findings; GO/NO-GO above. |
| 1 supervisor and Project Alpha adoption | Delivered core; identified gaps below remain | fleet [#1](https://github.com/cswenor/fleet/pull/1), [#3](https://github.com/cswenor/fleet/pull/3), [#4](https://github.com/cswenor/fleet/pull/4), [#5](https://github.com/cswenor/fleet/pull/5), [#6](https://github.com/cswenor/fleet/pull/6). Project Alpha [#2368](https://github.com/example-org/project-alpha/pull/2368) actually merged 2026-11-06; PR CI 42222656293, full native merge-group CI 42223792846 green. Real owner/coordinator event delivery, wrong-model alert, reserve path and fresh model/thinking proved in TASK-2687; no restart proof. |
| 2 central | Delivered, owner-corrected placement | fleet [#7](https://github.com/cswenor/fleet/pull/7), [#8](https://github.com/cswenor/fleet/pull/8), [#9](https://github.com/cswenor/fleet/pull/9), [#10](https://github.com/cswenor/fleet/pull/10), [#11](https://github.com/cswenor/fleet/pull/11). TASK-2690: the central host the PaaS app yktqfo8prpciui09gm2i4763, healthy deployment rrl3jc2y3ngx3elsa8pck3ml at b501e23; live Project Alpha ingestion 8 builders/31 PRs, no collector errors; consistent history migration, 46 rows/integrity ok. |
| Self-host/handover | Delivered and installed 2026-11-06 | [#12](https://github.com/cswenor/fleet/pull/12) setup + fail-closed unknown/disconnected occupancy; [#13](https://github.com/cswenor/fleet/pull/13) complete knowledge. Fresh independent unadvised read-only Sol review PASS, CI [42240339663](https://github.com/cswenor/fleet/actions/runs/42240339663) / [42240526182](https://github.com/cswenor/fleet/actions/runs/42240526182) green. Fleet PLAN-9/DOC-19, two reusable slots and own active supervisor; real CLI status/live-slot refusal and the central host **both projects LIVE** verified. Run run_ca3dd4d80c5a reserved; **Main not started by builder**. Installation receipt in docs/MAIN.md. |
| 3 generic process + Project Alpha process | Partial | TASK-14 generic policy and TASK-15 threads implemented; publication receipts on assigned Pad tasks. TASK-16 Project Alpha coordination remains. |
| 4 additional projects | Open, owner chooses | Fleet TASK-17; candidates below, no implied permission to install all. |
| Usage outcome | Baseline recorded; after measurement open | Fleet TASK-18. Do not label the workflow a measured saving yet. |
| Guarded builder retirement | Implemented TASK-20; publication receipts retained on Pad/PR | One exit only after publication + exact idle/session proof; shell verified before normal fresh launch. Real scratch retirement/fresh Sol medium and refusal evidence below. |

## Remaining work and concrete acceptance

### TASK-36 — macOS host support

Phase A: [source-bound audit](macos-audit.md) merged in
[PR31](https://github.com/cswenor/fleet/pull/31), independent stock-overlay Sol
medium review PASS (session `01a1b245-769c-76a1-9da3-29194f827ab1`, head
`e448e039e078195329f51b486c319e008b77c492`), green
[CI42277352305](https://github.com/cswenor/fleet/actions/runs/42277352305).
Main ruling `msg_f64e0177b905` approves per-user LaunchAgents, strict private
literal env startup loading for supervisor/central, unchanged Linux units/state,
explicit unsupported macOS automatic retirement and partial capture.

Platform implementation adds stock launchctl bootstrap/bootout/print with
running+PID readiness; optional supervisor token file and central startup loader
require owned regular mode-0600 nonsymlink files without shell execution.
Only PATH and the token file path enter plists, never token values.
Orca discovery honors config/env overrides, then macOS `orca` / Linux
`orca-ide`; existing XDG/default receipt/database paths are retained.
macOS restart never sends input without the unavailable equivalent exact proof;
capture returns explicit partial process/boot evidence rather than a procfs crash.
CI runs the full unit suite and CLI smoke on Ubuntu and macOS.

Linux CLI proof: status observed both live lanes; may-start withheld at builder
cap; launch refused duplicate TASK-36/live slot/unresolved receipt; restart
refused absent exact finished-task binding without input. Real private capture
saved mode-0600 metadata for 15 terminals/20 OMP processes, boot ID present,
exit 1 with explicit `proc_scan_incomplete` (not a completeness claim).
Private-env subprocess proved literal execution and rejected bad permissions/
malformed input without secret output. All 166 local unit tests and CLI help
smokes passed. Publication gate receipts are retained on TASK-36 and its PR.
macOS CI exposed `/var` versus `/private/var` aliases in hand-built test configs;
fixtures now use canonical paths, matching `config.load`, without relaxing
production slot checks. The complete 166-test suite also passes with symlinked
TMPDIR on Linux; capture's fake-secret marker no longer collides with `/private`.
Phase B remains **open**: no owner Mac access is authorized. Actual Mac
LaunchAgent active/reinstall, Orca status/admission/fresh Sol-medium launch,
safe restart refusal/manual reconciliation, partial capture and real wake
delivery still require owner-approved host access. macOS runner CI is not this
runtime evidence. Shared-checkout rollout requires Main's announced window;
merge alone is not deployment or permission to restart services.

### TASK-33 — Project workflow skill and onboarding

Implemented the generic [Fleet skill](../.omp/skills/fleet/SKILL.md), adopted by
Fleet itself through stock project discovery. README onboarding installs a
version-pinned copy plus the unchanged `.omp/unadvised-review.yml`; updates
preserve configured `fleet.yml` and require inspecting local customizations.
No new installer, public registry publication, lifecycle replacement or rollout.
The skill covers Main/builder authority, full onboarding, native Run wait/ack,
Pad briefs/threads, explicit next/launch, review + CI, merge/deployment, guarded
retirement/reconciliation, PARK/resets and owner-operated runtime restart.
It retains the TASK-31/32 audited wrappers and TASK-34 branch-preparation caveat.

2026-11-06 real command evidence: omp 18.17.0 / Orca 1.4.242. Repeated documented
copy/update into a temporary project preserved its configured `fleet.yml`;
skill and review overlay remained byte-identical. `omp skill list --json`
reported `fleet`, `source=native:project`, from the target `.omp/skills/fleet`.
Fresh session `01a1b228-0b1b-72a8-82ee-56366b74142a` actually read `skill://fleet`
without the prompt naming it, then answered the blocking-decision/publication
question correctly: unique task question + async same wait, final-head independent
review + green CI, no builder Run binding or automatic reset spending.
Session events recorded Sol medium, no fallback. No services/other projects
changed; publication gate receipts are retained on TASK-33 and its PR.

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

Implemented `fleet next [--json]`: read-only proposal from configured Pad workspace,
open tasks in active plans, incoming blockers done/cancelled (unknown fails closed),
priority then oldest creation/ref; every task/slot includes exclusion reasons.
Actual Git branches, card comments, active builders and unresolved receipts block
duplicate ownership. Proposals require a verified free shell and current
quota/disk/cap/health admission. Explicit `launch` rechecks task/dependencies,
ownership, admission and slot identity under the launcher lock; no auto-dispatch.
Confirmed in-progress continuation retains fresh-handoff/restart semantics.
Review correction: unresolved receipts now withhold whole slots, not only their
original task. Exact matching shell, matching TASK-20 shell-verification receipt,
or explicit Main-terminal `fleet reconcile` receipt resolves one launch; absence
never proves exit. Main approved the minimal reconciliation CLI on TASK-12.
Automatic slot selection also now applies branch/card exemptions after selection.

2026-11-06 live CLI: TASK-13 first eligible; TASK-25 excluded by in-progress
TASK-24; in-progress/finished tasks and both live lanes excluded. Real duplicate
TASK-12 launch refused with active-builder/receipt and occupied-slot reasons.
Main owns final queued TASK-13 launch into reused lane2 after this builder's
publication/handoff, including resolved Sol medium and Pad/Orca identity receipts.
Until that receipt is recorded, end-to-end queued-launch acceptance remains open.

### TASK-29 — Nested ancestry and workspace task states

Corrects TASK-12's direct-parent/open-only regression. Shared `next` and explicit
launch/restart eligibility walks up to 20 parent links to the nearest PLAN,
requires that plan active, and refuses cycles, missing/unreadable parents or
overflow with explicit reasons. No admission, retirement or dispatch-mode change.
Main approved schema-derived categories: tasks status default = not-started,
terminal_options = terminal, exact `in-progress` option = continuation; all other
states refuse. Complete `pad.statuses` overrides support other vocabularies;
unavailable discovery uses fleet defaults, malformed schemas refuse.

Read-only live proof (2026-11-06): elsewhere schema derives todo/in-progress/done;
TASK-2680 -> TASK-2102 -> active PLAN-1914 and todo TASK-2692 -> active PLAN-2646
are eligible. Unknown status and missing TASK-999999999 parent refuse. Real fleet
`next --json` retains TASK-17/TASK-18 as eligible, all drill fixtures cancelled,
both live lanes excluded; no proposal under cap/unavailable quota. No Project Alpha
mutation/launch or stable-checkout deployment. Main owns the pre-deployment
announcement to Project Alpha Main; publication receipts retained on TASK-29/PR.


### TASK-34 — Restart check-to-send metadata and prepared NEXT branches

Restart re-verifies append-only allowlisted post-turn metadata instead of
requiring the whole log digest to remain equal. Previously checked bytes,
session identity and foreground process remain bound; rewrites, truncation,
new turns, unknown events and non-idle panes still refuse before input.
Pre-exit duplicate admission exempts only the canonical retiring launch's exact
slot/worktree/terminal/incarnation active-builder claim, not competing claims.
Main prepares NEXT's branch in the clean existing lane before restart.

Root cause: health prefers Orca's cached branch to the terminal title, whereas
dispatcher branch claims read actual Git. Once Orca refreshes a prepared NEXT
branch, the still-live OLD session self-claims NEXT; stale OLD cache avoids it.
The old retirement exemption covered receipts only. Separately, late allowed UI
metadata changed the full-log digest between the two idle checks.

2026-11-06 real scratch CLI: Main-approved isolation excludes only this builder
terminal from inventory; all target pane/screen/lease/proc/input/shell/launch
operations are real. On local `task-35-restart-drill`, health labelled the
retiring TASK-28 fixture as TASK-35. Appending `custom/todo_hud_state` during
admission still produced one exit, same-incarnation shell verification and fresh
TASK-35 launch `1793990391811651379` on scratch Run `run_5c543be6255b`.
Unknown metadata refused with no retirement journal; an explicitly adapted
competing other-slot TASK-35 claim also refused (no other live slot mutated).
Clean TASK-34 pushed candidate `8ee6b0d` was restored after the drill.
Replacement session `01a1b228-fef2-72ff-8063-3dfde6e88d3d` recorded actual Sol
medium and an assistant stop acknowledging TASK-35 without changes. Both owned
scratch terminals were closed with `ptyKilled=true`. Its thread-open refused
because the isolated state was not inherited by the terminal; this drill proves
restart/launch, not task-thread delivery. Final suite: 154 tests pass.
Publication/reviewer/final-head CI receipts are retained on TASK-34 and its PR.

### TASK-22 — Quota freshness and transient unknown alerts

Implemented completion-time quota evaluation and one provider-scoped cache
invalidation/re-read for unknown results. Known reserve never retries; still
unknown/erroring usage withholds starts and never parks. Supervisor unknown
alerts require `quota.unknown_alert_cycles` consecutive cycles (default 3),
persisted across restarts; known reserve and unrelated errors remain immediate.
Initial launch admission precedes hooks/create; post-preparation failure retains
a `launch-preparations` safe-state/retry receipt. Post-retirement failure retains
launch-only recovery guidance; no repeated exit.

Root-cause proof (2026-11-06): real cached reports had independent anonymous
ages 43/52/197s. Documented `omp usage invalidate --provider …` followed by usage
refreshed all reports to 1–3s in 2.81s. The same real response was unknown when
evaluated at the command's start and available at completion: Fleet's snapshot
start timestamp rejected reports fetched later in that collection as future-dated.
Independent cache ages are observed; the installed binary's per-account refresh
scheduling/TTL is not established.

Real isolated CLI: with a 10s freshness threshold, report ages 132–134s refreshed
to 1–3s and `may-start` exited 0. Explicit unknown/reserve drill files both
withheld (exit 1); unknown `launch` exited 2 without running its hook. Three
scratch `serve --once --no-deliver` cycles emitted no quota event at streaks
1/2 and exactly one at 3. No messages, additional worktrees, Project Alpha changes or
production service rollout were used. Publication receipts retained on TASK-22.

### TASK-13 — Orca restart semantics (Phase 0 d)

Phase A implements the read-only `python3 -m fleet.orca_capture` tool and
[restart runbook](orca-feature-map.md#task-13-restart-runbook-phase-a-phase-b-not-exercised).
Owner's TASK-13 decision supersedes isolated-only verification: one real shared
restart at a coordinated safe point, **not by the builder**, with final owner
go immediately beforehand and external capture. Orca 1.4.242 has no documented
isolated profile startup recipe or quit command; current host reports
`unsupported-headless-serve`. Owner must document/perform actual stop/start.
Project Alpha requires 60 minutes' notice, explicit all-eight-lane safe confirmation,
TASK-22 deployed, both supervisors kept running, and fresh Main handoffs/startup.

Main approved title hashes + Pad refs, message metadata only, no consuming
capture/ack and an explicit missing-native-Delivery-ID limitation. Main saves
its already-observed Delivery/message receipt privately before quiescing.
Real no-restart `systemd-run --user` BEFORE capture: unit
`fleet-task13-before-dry2-1793985794`, invocation
`241a262c21144a8e8f23059be75f63cb`; 13 terminals, 14 OMP processes,
both supervisors active/running with NRestarts=0, output 0700/0600.
Partial exit 1 reports `proc_scan_incomplete`; missing session evidence stays
unknown. Tool/runbook are phase A preparation, **not Phase 0 d GO**.

Completeness follow-up: all same-user OMP processes (not only configured cwd),
safe argv shape, explicit internal-worker non-session status, per-entry proc
diagnostics, and live deleted-executable link handling. External BEFORE unit
`fleet-task13-complete-before2-1793985794`, invocation
`52acd8c64d8c48e4916805cc976acd66`, exits 0: 17 processes (9 complete agents,
7 daemon brokers, 1 text-predict worker), no process errors. Remaining per-entry
exclusions are foreign owners, known non-OMP OS services and zombie Orca; exact
PIDs/fields/errno recorded in the runbook/private snapshot. Both supervisors
active/running, NRestarts=0. No shared restart; phase B remains unverified.
Review correction: use kernel status UIDs, not proc directory ownership;
unknown credentials remain incomplete. Historical credential-based BEFORE
capture `fleet-task13-statusuid-final-1793985794`, invocation
`08ba668f56624b74b80eb212daff4abc`, exits 0 with 17 processes/no errors.
143 tests pass, including credential mismatch and exit-race boundaries.
Final independent review removed mutable-name-based non-OMP exclusions; earlier
exit-0 classifications are superseded. Strict final external unit
`fleet-task13-strict-final-1793985794` exits 1 with explicit incompleteness:
19 OMP, one agent without an open session log (PID 2523586), plus same-user
executable EACCES at PIDs 1248/1249/3582181. 143 tests pass. Main ruling
`msg_88fdc4be4ae4`: partial alone is not failure, but every model-bearing
builder/Main session must be complete and every incomplete entry listed.
No fleet privilege escalation; no builder sudo without Main approval.
Before final owner go, require launch-recorded session-directory/ID bindings and
complete model/thinking evidence. Unverifiable legacy sessions block scheduling;
project Mains coordinate safe retirement or fresh admitted launches first.

Phase B remains UNVERIFIED/NO-GO pending the coordinated owner-operated restart.
Capture actual model/thinking, process survival/resume, replacement handles,
coordinator Run rebinding and pending replay/ack before any GO verdict.
Re-list handles; no dual-send. Reconcile `restored_unconfirmed` and possible live
attempts before a fresh explicitly pinned retry; restart is not proof of exit.

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

Implemented `fleet thread open/question/wait/result`: canonical task ID links Pad
ref, actual branch, reusable slot and authoritative launch attempt. Main approved
unique native question subthreads per attempt/question (native reply lacks a
reply-to field); old/settled attempts cannot authorize new work. Async builder
wait inspects non-consuming terminal history in one process, caches all types,
and copies only question/decision/outcome to Pad. Unseen terminal replies still
require retaining the terminal until copied; no Dispatch/worker_done invented.

`fleet wait` adds stable decision-first classification/counts, no filtering or
ack change. Consecutive identical GitHub relay content dedupes per stream;
alert content change and recovery/recurrence still notify. Delivery errors log
recipient plus safe Orca code. Health flags known bound slot terminals
`owner_delivery_broken`; launcher creates a new terminal in the same slot rather
than reusing bound/unknown-binding shells, retaining selection reasons.
Sender/body classification is advisory triage only (`--from` is not
authenticated); it never grants authority, suppresses, auto-acks or downgrades.

2026-11-06 real question `msg_abb92cc4c1f6` -> Main in-thread approval
`msg_793046954969` -> background wait returned in 24.35s and copied the decision
to Pad, canonical task thread `fleet-40361fd9-c248-4226-a3cc-52d318103de6`.
Main requires all further relay probes on scratch identities, not live Main.
Scratch Run `run_165b92486eb1`: 20 baseline sends versus one deduped send for 20
identical candidate events; CLI delivery `delivery_fd91ee1570a2` showed the
question first and all 21 relays, replayed unchanged, then whole-batch ack.
Real health flagged a bound scratch ready shell; launcher harness with adapted
inventory/admission and harmless command created a different terminal in the
same slot with reason `run_bound` (no third paid builder/model launched).
Real mismatched scratch delivery logged `recipient_run_mismatch`.
Separate CLI processes with isolated adapted launch receipts for scratch
TASK-26/27 showed old reply `msg_df104931d560` without accepting it (timeout),
then accepted only matching new reply `msg_af22a9adceef` and recorded Pad result.
All three owned scratch terminals closed. Fresh-session/model launch boundary
remains the existing launcher contract; the isolation drill did not launch omp.
Independent final-head review/CI and publication receipts are retained on Pad/PR.
Initial independent review found two P2 defects, both reproduced with failing
regressions then fixed: retained bound ready shells no longer block retirement
of the exact builder; failed question Pad writes retry before the decision.
101 tests passed before current-main integration. Final-source native scratch question
`msg_b2f43c67d5f2` / reply `msg_854cabb99fef` survived an injected initial Pad
outage: original question then decision copied, no resend. Live read-only
retirement selection excluded the retained bound shell without input/exit.
Both additional owned scratch terminals closed; no service/runtime restart.
Follow-up review caught cross-lane contention on a nonblocking project-wide
thread lock. Task-local ledgers/locks now isolate unrelated waits, with a
two-lane simultaneous-I/O regression. Integrated current-main dispatcher guards
and preserved both branches' launcher checks; 113 tests and real cached-reply
CLI replay pass. Own pre-publication TASK-15 ledger migrated, canonical ID kept.

### TASK-20 — Guarded finished-session retirement

`fleet restart NEXT --slot PATH --finished-ref OLD` requires a merged PR carrying
OLD, plus Pad OLD done or a post-publication handoff on NEXT, with OLD != NEXT.
Canonical slot-keyed `launches.json` binds the exact terminal/incarnation and
private session directory. Single connected writable terminal, exact-pane
idle/done, current empty idle composer, live session lease, local Linux foreground
OMP process binding and final assistant stop are required. Unavailable `/proc`
or unsupported hosts refuse. TASK-28 matches real omp 18.17.0 metadata encodings:
top-level `credential_pin` / `title_change` and exactly
`type=custom, customType=todo_hud_state`; unknown or later work refuses.
Stale working screens, background jobs, dialogs, disconnected or
ambiguous targets and missing legacy binding all fail closed.
TASK-28 also checks shared next-task eligibility/dependencies/duplicate assignments
and competing receipts before exit, exempting only the exact proven old receipt.
Foreground argv permits one optional leading configured Orca status-extension
pair; every other argument stays exact. The path defaults only from Fleet's own
`ORCA_OMP_STATUS_EXTENSION`, never from target-process environment.

2026-11-06 TASK-28 CLI drill (private scratch state, Main-approved inventory
adapter restricted to one owned lane2 terminal): real session
`01a1b1e0-f215-700c-9447-ccb857eb5357` used todos, then `/todo expand` wrote
`custom/todo_hud_state` after its final stop. Reserve admission refused with zero
input; normal restart sent exactly one `/exit`, verified the same-incarnation
shell and launched TASK-30 session with resolved Sol medium/no fallback.
Unknown-event and mid-turn log-copy CLI probes refused. Actual plain-argv fleet
builder foreground binding also passed and its native exact-pane projection was
present without the injected extension. Both owned scratch terminals
`term_431b5ad9` / `term_f2606fe3` were closed; scratch Run `run_4dbf1efa9597`
remains as durable drill evidence. No production retirement or runtime restart.

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

Owner approved **Project Beta only** on TASK-17 (2026-11-06). House of Voi,
Tolkien, Elsewhere, coqui and whatsapp-rag were not selected. Project Beta starts
with one reusable lane; its own Main is reserved, not started by the installer.
Use the README onboarding checklist and preserve project policy and bot gates.

| Project | Installation acceptance |
|---|---|
| `example-org/project-beta` | Linux preparation approved after owner migration decision (2026-11-06 18:47Z). One reusable lane, own reserved Run/shell (Main not started), private config/env 0600 and active supervisor; real CLI, skill-discovery and reserved-Run event wake proved below. Email PR merge, real builder launch and the PaaS deployment remain gated on Main's confirmation of Mac wind-down. |

Per-project acceptance remains repo-local config/instructions, own Pad/Run/slots,
pinned builder model policy, systemd supervisor, distinct private the PaaS token,
fresh the central host card, actual launch/event/PR-wait proof and clean logs. Approval of
one project does not authorize changing another project's runtime or policy.

Owner's Linux migration ruling supersedes upstream `3c859e0b`'s owner-Mac
instruction; Main recorded it on TASK-17 and reply `msg_65e059fe3888`. Local
Project Beta checkout/lane fast-forwarded without resets to `77249a60`.
Reserved project Run `run_c018565b177d` belongs to idle coordinator shell
`term_585ef494-543b-4a24-b1c3-b886aa1c7a42`; no Main started. One configured
lane `/home/dev/orca/workspaces/project-beta/task-17-fleet-lane`, no extra lanes.
Owner's other rollout candidates remain unselected; seven Mac tasks/branches
are untouched pending owner-coordinated handoffs.

Real `fleet --config ~/.config/fleet/project-beta.yml status --json` returned no
collector errors, one unbound ready-shell lane and no strays; `may-start --json`
returned true. Project supervisor active/running, `NRestarts=0`; private config
and distinct token EnvironmentFile mode 0600, local snapshot present, service
log token-match count zero. Reserved-shell `fleet wait` woke on transport smoke
`msg_10583047ad63` / `delivery_815be85da53d`, then actual supervisor PR434 event
`msg_6f577362779c` / `delivery_d3a5fa943250`; latter retained unacknowledged.

Fresh project read-only session `01a1b247-f613-74d1-bf5d-531f4952c1b3` actually
read `skill://fleet`, answered durable question/wait and all three project merge
gates correctly, and recorded Sol medium/no fallback. Copied skill/review overlay
remain byte-identical; target formatter excludes only these upstream files.
Project Beta lint/typecheck and four launcher/scratch tests passed. Compose CLI
smoke with dummy tokens proved distinct Email token mapping, loopback exposure
and unchanged snapshots volume. These are preparation receipts, not completed
central ingestion/deployment, PR publication or builder-launch acceptance.

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

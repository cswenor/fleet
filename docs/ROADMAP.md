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
| 4 additional projects | Project Beta accepted; further rollout owner-gated | TASK-17 receipts below: merged project policy, real launch/thread/event/retirement and three-project the central host proof. Other candidates remain unapproved. |
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
retirement/reconciliation, PARK/resets and owner-authorized runtime restart.
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

### TASK-38 — Registry preparation; publication deferred to TASK-43

Owner/Main narrowed TASK-38 to docs-only on 2026-11-06 (ruling
`msg_3024c91a84bd`): Stencil registry access is invite/waitlist-only and the owner
has no account. Registry publication and registry-install acceptance move to
TASK-43, blocked on owner access. No package name/scope/URL is assigned and
nothing has been published; no login, token creation or credential-file access
was performed.

The skill retains approved `metadata.version: 0.1.0`. Stock omp 18.17.0
`skill publish .omp/skills/fleet --dry-run` validates a package containing only
`SKILL.md`; the validator requires version metadata and reports no license
requirement. A dry-run is not publication or registry-authentication proof.
Privacy scanning found no private host/account names, user paths, runtime
handles, credential-shaped strings or URLs in the skill.
README and skill onboarding keep the reviewed project-local copy procedure,
not an invented registry install spec. A fresh temporary-project copy smoke
verified byte-identical skill/overlay content, preservation of configured
`fleet.yml` on repeat adoption and `omp skill list --json` discovery as
`native:project`; no registry install is claimed.

### TASK-37 — Selective wake and urgent supervisor relays

`fleet wait --wake-on question,escalation,urgent` gates native blocking wake,
never delivery contents or acknowledgment. Default `all` retains wake-on-anything.
`urgent` aliases native `question`: Orca has no urgent type, and real ordinary
terminal escalation sends fail `sender_not_assignee` without an active Dispatch.
Supervisor urgent relays therefore use `question` with `[urgent] ` subjects;
urgency and classification remain advisory, never authority.
Main approval `msg_c5ad5f7fa703` requires the distinct `supervisor_urgent`
advisory bucket with `replyExpected: false` / `no reply expected`; builder
decision questions stay first and never share that bucket unless their sender
and markers match the unauthenticated supervisor convention.

Generic `urgent.events` defaults to wrong-model, provider-stalled, idle,
stray-worktree, merge-group failure and PR-stuck conditions; `[]` disables
upgrades. `urgent.pr_stuck_minutes` defaults to 45. The durable PR timer resets
on state/head/merge-state/check/review progress, not comment churn.

2026-11-06 real CLI proof on isolated Run `run_78bc1026ab9a`: ordinary status
timed out without delivery; question woke with whole mixed pending mail
(`delivery_7750277bab91`), which replayed unchanged before ack. Urgent question
woke with the next ordinary status (`delivery_325f3402054d`). Explicit whole-batch
acks drained the Run. Real scratch supervisor emitters also delivered wrong-model
and 45-minute PR-stuck events (`msg_a973e77a60df`, `msg_a24b4220ca2c`) together;
replay and explicit whole-batch ack passed. All 179 local unit tests passed.
Builder terminal remained unbound; no live-Run probes or production changes.
Published in [PR34](https://github.com/cswenor/fleet/pull/34), merge
`38c9a356e8c8895fab51a8225d606c0a9b0bb4d6`, after Main GO
`msg_684d0aa6c2b6`. Final head `1caa1f4780e8464046d21e7fbcf46402246aeea0`
received fresh independent stock-overlay Sol-medium PASS (session
`01a1b270-229d-7522-8f7d-1f28c80725b5`) and green Ubuntu/macOS
[CI42284129502](https://github.com/cswenor/fleet/actions/runs/42284129502).
Main reports deployment to the stable shared checkout at 21:12 UTC and Fleet's
supervisor reinstall; Project Alpha's supervisor reinstall remains pending its Main.
Main coordinates the Project Alpha switch and TASK-10 script retirement.

### TASK-44 — Scope initial watcher events to the selected PR

Initial `watch-pr` events now use the same exact-PR filter as subsequent
snapshots. Unrelated historical merge-group failures cannot terminate a watch
or contaminate its own DIRTY/failing/merged/closed diagnostics. Known primary
and secondary members still receive immediate failures, including known members
of partially resolved groups. Unknown membership (`pr: null`) remains an explicit
coordinator diagnostic, never guessed as membership of the selected PR.
Supervisor initial failure/urgent emission is unchanged.

2026-11-06 evidence: new regression cases failed before the one-line filter fix
(six failures), then all 182 unit tests passed. Real source CLI against live
Project Alpha PR2376 reached its three-second timeout with no unrelated event;
the repository still had failed merge-group run 42261967575 for PR2373.
The smoke used isolated scratch config/state and no inbox delivery or services.
Publication requires independent final-head Sol-medium review, green CI and
Main's explicit shared-behavior merge GO; merge is not deployment approval.

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

The latest TASK-13 owner direction permits **one unattended shared restart**
of the stock system unit `orca-serve.service` through an external conductor,
not an owner-present manual stop/start. Planned time: **2026-11-07 14:00 UTC**;
Main's cross-project notice is due by **13:00 UTC**. This implementation is
**rehearsal only**: no builder shared restart, production scheduling or rollout.
Main released the announcement merge hold at 21:11Z (`msg_96e2c57e8824`);
final-head independent review and CI remain required. Merge is not deployment.

The [single operator runbook](orca-feature-map.md#task-13-restart-runbook-phase-a-phase-b-not-exercised)
owns the schema, dry-run blockers, exact stock-unit sudo exception, external
systemd timer/service recipe, private journal/resume and manual reconciliation.
CLI: `fleet orca-restart --plan PATH [--dry-run]`. Approved private plan location:
`/home/dev/.local/state/fleet/orca-restart/plan.yml`; actual missing inputs
(including Project Alpha Main command/handoff) must come from Main. No launch/env
schema or invented operational commands. Final ruling `msg_dc4d07132417`
approves existing stock systemd SIGKILL policy and bounded recovery; record
`allow_systemd_sigkill: true`, actual stop timeout/duration/SIGKILL outcome and
PTY scopes outside the unit, with no unit edits. Default false remains a gate.
Read-only preflight validates all model-bearing session bindings and positive
idle/shell evidence; incomplete owned OMP always blocks. No name exclusions.
Manually started Mains may use the approved explicit process/session-file
record instead of a launch receipt; builders retain strict launch bindings.
Restored/remapped Main handles always require manual reconciliation, even
when incarnation and Run match; automatic remapping is refused.

The conductor must preserve metadata-only BEFORE/AFTER evidence, prove process
survival before deciding whether a fresh Main is needed, never duplicate a
survivor, and reconcile each project's Run/config/supervisor separately.
Project Beta now has an owner-started Main (2026-11-06 22:05Z); all three
projects use Main mode. Main `msg_1bd7a113d407` relayed Email's T-15 private
handoff path and approved `fleet_idle` attesting both fleet and Email Main/lane
safe-idle, without schema or native/process gate changes. Stop timeout cannot authorize blind
relaunch; bounded recovery requires positive old-process exit and inactive/
failed stock unit MainPID=0. Ambiguous terminal intent requires manual
reconciliation, never a new plan ID/state reset to force retry.

Historical BEFORE-only capture receipts and the superseded exit-0 process-name
classifications remain in the runbook; the final strict capture honestly
reported unreadable same-user entries and one missing agent session. They are
not restart GO. The conductor is implemented and source-bound rehearsed:
external dry-run invocation `ace6add5d06c4ecdb767925bf591ce12` identified the
exact service/runtime and 16 handles, printed every conditional action and
refused execution (exit 2) on real missing prerequisites. The private operator
draft contains the supplied exact Project Alpha command/handoff and manual Main
record. Approved fresh session directories stay under each project's state;
T-30 handoff freshness and dual-Run handle/session-dir announcements are enforced.
Idle confirmations and legacy lane mappings remain operator blockers. Main's
21:11Z receipt supersedes the stale Fleet Main identity; refresh it at T-15.
Main ruling `msg_65510e97c8bc` approves strictly validated canonical discovery
from live argv for FD-less fresh sessions, never resumed/legacy logs. The exact
process/argv/private directory/session is revalidated before every mutation.
The replacement Main's actual argv/native/Run proof refreshed its private record;
private fixture cutover accepted valid evidence and refused expected-argv drift.
New external capture `a936ebd269804bad92f7c3b054ca2fea`
observed 27 OMP processes, including the previously omitted self-updated
Project Alpha Main's deleted backup executable (strict pattern plus argv0 proof).
Actual model/thinking and exact process/session/handle matching were retained;
unreadable same-user diagnostics still prevent any blanket capture GO claim.
Owned scratch sleep service PID 2410269 exited/relaunched as 2410278; resuming
the journal did not repeat the restart. A command/read/close round trip proved
real scratch terminal creation in the existing lane. No shared restart or
Main launch was exercised. Full limitations/receipts are in the runbook;
242 regression tests pass; eighteen independent-review findings are resolved:
duplication, routing/reporting/config gaps, recorded
Main resume, supervisor continuity, expiry, nested helpers, private argv leaks,
stale survivor metadata, unverified failure-report senders, stale recovery idle
authorization, competing survivors, foreign-user census false blockers and
created-Main acceptance amid competing/incomplete or wrong-terminal evidence,
and dropped supervisor EnvironmentFile bindings.
Reinstall now preserves the effective single EnvironmentFile and optional flag;
ambiguous bindings refuse before intent. An owned scratch user service stayed
active but lost its synthetic marker before the fix; a new PID retained it after.
The real stock installer/systemd proof used only a private fixture consumer/config,
not a real supervisor/credential; its unit and throwaways were removed.
Fixed with shared validation, receipt-only recovery,
per-attempt confirmation checks, current session/terminal/sender checks and
digest-only evidence; argv stays in the private plan. Main `msg_e654aa00fe74`
approved model-free complete current census as an additional down-runtime retry
gate; survivors or unknowns require manual reconciliation, not filesystem-only
idle proof. Runtime fault-adapter smoke retained attempts with zero privileged
calls for expired confirmations and real surviving-agent census.
Positively foreign-user processes are outside the owned census; unknown ownership
still blocks. A scratch proc-directory view of real `/proc/1` proved that distinction.
Created-Main acceptance waits boundedly for complete startup metadata and
requires a sole root model plus shared process/native terminal binding before
cutover. Real read-only created-receipt fixtures retained `created` for partial
census, adapted competing rows and a mismatched handle. Actual Email process
environment/cgroup and native reads accepted its matching handle under an
explicit one-row complete-census adapter; no live state changed.
Email Main PID 751081/start 124254263/session
`01a1b2e5-903f-72b8-91ec-7908dcad7687` passed actual canonical argv/model/
thinking/native Run binding checks. Private plan/manual record remain 0600.
Updated external three-Main dry run returned exit 2 with twelve blockers and
zero known private-prompt matches, creating no execution journal or live changes.
Three owned old dry-run artifacts were sanitized; scoped journal inspection
found no exact known private-prompt matches. Real current-census refusal and reserved-shell
Run ownership were exercised without Main creation. Partial census/missing or
unknown old terminal blocks automatic fresh Main; manual reconciliation remains
required. External stock reporting first failed without sender identity, then
explicit own-builder sender/run context enqueued `msg_64a5b634e1b5` from
invocation `bb883caf3cba491db4ef3d367739d1be`. This incorrectly targeted the live
Main Run; future probes must use scratch Runs. Main approved the production
sender convention in `msg_96e2c57e8824`: verified project handle/Run, status-only
automated/non-authority text, no retry on failed/ambiguous enqueue.
Earlier source preserved the existing Main's handle and refused a simulated
disconnection. Final-source survivor/cached reconciliation additionally requires a
complete current census and exactly one same-root model candidate. Real partial
census with two root agents refused; an adapter presenting the two complete real
rows also refused the collision. No new Main, config or native mutation.
PR35 final publication receipts remain on Pad/PR.

**Phase B remains UNVERIFIED/NO-GO per dimension:** actual model/thinking,
process survival versus fresh context, replacement handles/incarnations,
coordinator Run rebinding, outstanding native Delivery replay/whole-batch ack
and supervisor continuity. Preserve pre/post mail metadata privately; native
unacked Delivery identity cannot be reconstructed from read-only inbox status.
Only dated real before/after receipts can change these verdicts.

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
with one reusable lane; the installer reserved its own Main Run/shell but did not start Main.
Use the README onboarding checklist and preserve project policy and bot gates.

| Project | Installation acceptance |
|---|---|
| `example-org/project-beta` | Accepted on Linux: project policy merged through all three gates; own Pad/Run, one reusable lane, private runtime-only token and active supervisor; real pinned launch, durable thread/result, supervisor merge-event wake, clean retirement and fresh the central host card. Fleet Main reports Email Main started at 22:05Z; not started by the installer. |

Per-project acceptance remains repo-local config/instructions, own Pad/Run/slots,
pinned builder model policy, systemd supervisor, distinct private the PaaS token,
fresh the central host card, actual launch/event/PR-wait proof and clean logs. Approval of
one project does not authorize changing another project's runtime or policy.

Owner's Linux migration ruling supersedes upstream `3c859e0b`'s owner-Mac
instruction; Main recorded it on TASK-17 and reply `msg_65e059fe3888`.
Installer reserved project Run `run_c018565b177d` with coordinator shell
`term_585ef494-543b-4a24-b1c3-b886aa1c7a42` but did not start Main. One configured
lane `/home/dev/orca/workspaces/project-beta/task-17-fleet-lane`, no extra lanes.
Owner's other rollout candidates remain unselected. Main confirmed the Mac Main
reached a safe idle point before release; none of its seven product tasks was started.

Real `fleet --config ~/.config/fleet/project-beta.yml status --json` returned no
collector errors, one unbound ready-shell lane and no strays; `may-start --json`
returned true. Project supervisor active/running, `NRestarts=0`; private config
and distinct token EnvironmentFile mode 0600, local snapshot present, service
log token-match count zero. Reserved-shell `fleet wait` woke on transport smoke
`msg_10583047ad63` / `delivery_815be85da53d`, then actual supervisor PR434 event
`msg_6f577362779c` / `delivery_d3a5fa943250`. The installer processed and acknowledged
the inherited deliveries before arming a fresh wait. Actual PR511 merge relay
`msg_efed3a2f1f0a` / `delivery_96fd6bfc30e9` returned the background wait on the
reserved Email Run; durable enqueue alone was not counted as wake evidence.

Fresh project read-only session `01a1b247-f613-74d1-bf5d-531f4952c1b3` actually
read `skill://fleet`, answered durable question/wait and all three project merge
gates correctly, and recorded Sol medium/no fallback. Copied skill/review overlay
remain byte-identical; target formatter excludes only these upstream files.
Project Beta lint/typecheck and four launcher/scratch tests passed. Compose CLI
smoke with dummy tokens proved distinct Email token mapping, loopback exposure
and unchanged snapshots volume.

Completion receipts (2026-11-06): [Project Beta PR511](https://github.com/example-org/project-beta/pull/511)
merged by the project bot at 21:47:07Z as `1a4e23a9f6f7d3856d81052878c6646b274250f6`.
The bot refreshed its head to `f6032e21954f4bbc3d79b1ea6dec4c8a7fde081c`; a new
stock-overlay independent reviewer session `01a1b2d1-3308-77c4-8794-c7f84a46306d`
passed, with actual Sol medium/no-fallback events and read/grep/glob-only tools.
[Full CI42298910753](https://github.com/example-org/project-beta/actions/runs/42298910753)
and bot approval were green on that head. Main removed its temporary hold only
after verifying those receipts; [merge-gate42299433765](https://github.com/example-org/project-beta/actions/runs/42299433765)
then passed. No hand merge or gate waiver.

Main parented the read-only smoke TASK-164 under active Email PLAN-183 after
the launch correctly refused its missing PLAN ancestry. Actual `fleet launch`
receipt `1794001814541243846` reused the unbound configured lane on branch
`cswenor/task-164-fleet-smoke`. Fresh session `01a1b2d7-48e7-7755-83cf-32f79835ffe1`
read `skill://fleet`, opened its durable task thread, recorded its result and
returned `SMOKE-COMPLETE`; actual events prove Sol medium, no fallback or later
routing change. No repository edits or product work. Under Main's explicit
read-only-smoke retirement ruling, the installer journaled final-stop/empty-idle
composer, exact foreground PID/starttime/session/incarnation and no child jobs,
then sent one `/exit`. The old PID disappeared and the same-incarnation bash
became foreground; actual `fleet status` showed `shell_ready: true`, no Run
binding, no collector errors or strays. TASK-164 is done; no next task launched.

Main's exact-head central GO `msg_8c0c0ffc60ac` followed the announced Project Alpha
window. Stock the PaaS REST pinned and deployed `e73838fd3b01abdfff0f305c746353894bb569dc`;
deployment `v78dtlr05uudw98dcr51xvnc` finished at 22:15:32Z. Actual container image
matched that commit, was healthy with read-only root, and retained the original
`yktqfo8prpciui09gm2i4763_snapshots:/data` volume. Auto/preview deployment stayed off;
no supervisor, shared checkout or other application was changed.

Email's distinct token and the three-project allowlist were registered runtime-only
through REST. Actual container inspection proved three nonempty, distinct runtime
tokens; no Fleet token variables were baked into image configuration. Only two
sensitive encoded-key `command` fields among this deployment's 71 stored log rows
were sanitized. Raw/base64/decoded deployment-log token-match count was zero.
The exposed read-only deploy key was rotated through GitHub/the PaaS, actual
replacement-key repository access passed, and the predecessor was revoked.
The temporary team-bound API token was revoked under Main's narrow UI-equivalent
exception (no stock personal-token revoke endpoint); its former authenticated GET
returned HTTP 401. Temporary credential/private-key files were removed.

Real Chromium overview at 22:18Z showed **Project Beta, Project Alpha and fleet LIVE**
(snapshot ages 44s/35s/14s); Email's drill-down worked and browser errors were empty.
Post-deploy Email supervisor logs had zero token matches, tracebacks or failed
pushes; the service remained active and private config/env remained mode 0600.
Central's retained stdout/stderr contained no entries; runtime token presence and
fresh ingestion were independently verified rather than inferred from quiet logs.
Fleet Main accepted the rollout in `msg_fba53cfb806a` and reported that
Email Main had started at 22:05Z; no installer terminal input was sent after that start.

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

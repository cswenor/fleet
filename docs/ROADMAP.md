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
- **Footer (TASK-40):** assigned task is already fresh. Pick the next piece by plan
  priority, usually the same feature; same ref allowed, never unrelated work just
  for a different ref. FRESH-READY requires this session's NEXT piece merged after
  launch and a concrete next-piece handoff after the merge, never inherited work.
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

### TASK-120 — Bound task inbox indexes and shorten wake task locks

Task-ledger writes lazily compact inbox indexes in active and archived attempts.
Only task/subthread-related metadata survives, capped at the newest 64 rows per
attempt; bodies remain only in canonical question/decision records where needed
for durable Pad retry. Fresh native observations are returned intact, not persisted
as unrelated terminal history. Native mail is never consumed or acknowledged.
Before stripping/capping, matching inbox-only unrecorded legacy replies are rescued
into their canonical question records. The pending-question regression
`test_migration_preserves_pending_question_and_inbox_only_unrecorded_reply`
migrates via a separate write, then proves `wait` records the decision with empty
native history. Cap/archived coverage also preserves an oldest reply beyond the
metadata cap. The first candidate lacked this rescue; Project Alpha's safety request
exposed the gap before publication.

Per-task locks default to a bounded 15-second acquisition, with retryable exit 4
and holder PID/operation/UTC-start diagnostics. Supervisor wake explicitly uses
nonblocking task locks for file-only snapshots and final authority revalidation/
journaling. Its slot lease still spans native mailbox/proof/send, excluding
retirement; no native I/O holds the task lock.

Read-only migration of a private copy of real Project Alpha TASK-2843 reduced
4,345,577 bytes to 16,555 bytes. Median JSON load over 30 runs fell from 20.833ms
to 0.058ms; migration/write took 3.261ms. The live source hash was unchanged.
Candidate `thread check TASK-120` against copied Fleet state exited 0 in 571.248ms
with real native ownership reads; no live receipts or services were changed.
All 186 focused thread, lock, builder-wake, steering, supervisor, governor, stop
and parking regressions pass across the scoped runs. Coverage includes real
15-second contention/holder diagnostics, release/acquisition, archived-cache
migration, decision retry, and actual flock availability at every wake native
boundary. Stop timeout fixtures now isolate their fake clock from shared locks.
After the inbox-only reply fix, all 82 thread/steering/parking regressions pass.
Independent final-head review, green CI and Main's exact-head GO remain required.
Merge does not authorize shared installation.

### TASK-117 — Startup health relays and partial restart diagnostics

Project Beta Main corrected the incident account (`msg_697f8a115d9f`, relayed
by Fleet Main in `msg_af0e48d5a73b`): unknown health did not persist for 20 minutes.
The TASK-282 startup relays were enqueued at 16:03:22Z, about eight seconds after
launch receipt `1794412993297190020`; the looping Main wait returned them roughly
1201 seconds later. TASK-274 had the same delayed-delivery symptom. No stale
session cache or persistent unknown snapshot was found.

Main approved a fixed 120-second grace from the exact terminal/incarnation/slot
launch receipt (`msg_6841528bbccd`), only for `model_unknown`/`progress_unknown`
relays. Raw snapshot flags and admission remain fail-closed; all other health
flags are unchanged. Unknown relays carry their snapshot's UTC observation time,
excluded from occurrence dedupe, so delayed delivery cannot look like a fresh
observation. Health rereads the current launch each cycle, including slot reuse.

The TASK-277 partial restart occurred at 12:10:54Z, not approximately 12:20Z:
the branch-preparation receipt records `CommandError` and no builder input.
A repeated restart at 12:11:04Z found no non-shell session; explicit launch
succeeded at 12:11:30Z. Email Main's stdout-only filter omitted stderr, and the
old receipt retained only the exception class. The exact historical Git error
cannot be recovered. Existing branch dry-run preflight remains mandatory;
post-retirement failures now persist an explicit recovery diagnostic, with
bounded/redacted Git command and stderr in preparation/restart receipts.
Ambiguous or already-started replacements forbid blind retry.

Main made a fresh-launch live drill optional (`msg_39f48d448d47`): acceptance
uses focused regressions plus a replay of the real TASK-282 ledger. Read-only
candidate collection resolves both current Project Beta launches as Sol/medium
without health flags. The source-bound TASK-282 startup replay emits no unknown
relays at launch+8s, and emits timestamped unknowns at the 120-second boundary.
No services, live configuration, receipts or delivery were changed by the replay.
All 183 focused command, health/stall, supervisor, retirement and launch
regressions pass. A real read-only Git failure (`rev-parse --verify` on an absent
ref) retained its sanitized command and `fatal: Needed a single revision` stderr
in an isolated preparation receipt and surfaced both in the failure diagnostic.
Publication still requires final-head independent Sol-medium review, green CI
and Main's exact-head GO; merge is not shared deployment permission.

### TASK-118 — Reduce duplicate relay and watcher noise

PR/branch transitions now target only the coordinator Run; builders use
`watch-pr`. Only the supervisor-only `pr_stuck` timer retains an owner copy.
Supervisor checks compare `(head, pending/success/failure)` rather than every
check-run transition. Newest same-name/head attempts with proven App/check-suite
identity supersede old failures and cancellations; independent/unknown producers
keep failures. Existing same-workflow/head history proof is unchanged.
Watchers register newest-wins ownership per repo/PR/caller and
older generations return `superseded` without signals.

Main revised pruning in decisions `msg_4c9da3acbd7d` / `msg_bc065d626aeb`:
`relays clear-stale --terminal T --dry-run` is Main-only, counts-only and never
mutates mail. Native selective mark-read or conditional consume is missing;
peek/consume has a concurrent-question loss race. Automatic and manual mutation
are out of scope until Orca provides that API. Duplicate text alone cannot prove
relay occurrence identity.

Read-only real Project Alpha PR #2546 replay captured seven check snapshots on head
`b109a9f625acb801f3a067dd5e6810994b80a878`, all overall failure. Six adjacent
fine-grained transitions produced twelve old destination copies; aggregate mode
produces zero additional check relays after the baseline failure. Exact Email
message `msg_9416fbe7c825` belongs to Fleet's Run/address, not Project Alpha's Run or
any lane, and was absent from Project Alpha's read-only peek: global listing visibility,
not cross-delivery. No live inbox, service or shared installation was changed.
All 195 focused supervisor/check/watcher/initial/budget/relay/urgency/permission/
launcher regressions pass. Two concurrent real source CLI watches of Fleet
PR #106 returned `superseded` (older, exit 0) and `timeout` (newer, exit 0).
Live read-only lane-2046 assessment found 71 unread: 9 pre-launch stale statuses;
blockers were 9 other-sender statuses, 5 supervisor questions and 48 current
supervisor statuses. No all-stale proof and no mutation support.
First independent review found a P1: same-name checks from independent producers
could hide failures. Matching App/check-suite identity now gates supersession;
collection and summary regressions cover independent/unknown producers. The
53 focused check/parser/watcher regressions pass after this fix. CI also exposed
an obsolete subject-wording parser test with a malformed unnamed check; removed
that incidental test rather than re-pinning wording.
Final-head independent review, green CI and Main exact-head GO remain required.

### TASK-113 — Check steering without waiting for launch/restart

`thread check`/`--ack` reads atomic launches and the task ledger under the per-task
lock, verifying native caller ownership against that snapshot.
Acknowledgment uses a tiny shared launch-publication lock around final identity
revalidation and ledger write; launch uses it only for final record publication.
No native/Orca/Pad/Git I/O or subprocesses run inside it. After at most 5s,
contention exits 4 with `launch publishing; retry` and PID/op/UTC-start metadata.
Changes refuse with `launch changed; re-run check` without acknowledging.
Read-only checks never acquire the global launcher or publication lock.

Launcher lock holders record only PID, operation and UTC start in private
`launcher-holder.json`, removed on release. Contention exits 4 and names the
holder with `retry in ~60s`; unknown external/older holders remain explicit.
`thread pending` waits at most 15s; existing immediate-refusal callers remain
immediate. All launcher waits are capped at 15s. Builders retry later, never
loop tightly. TASK-96's 180s checkpoint contract is superseded.

Initial 299 focused steering/thread/launch/restart/supervisor/wake/parking/
dispatcher/governor/stop regressions passed. After the publication-race fix, all
69 steering/launcher tests pass, including real 15s/5s contention and replacement
while acknowledgment waits for publication, without acknowledging old work.
Main measured installed `fleet launch TASK-114` at 27.7s end-to-end. The reported
Project Alpha 132s launch has no per-stage measurement here; changing lock hold time
is out of scope. Main's live 90s-held-lock drill on code 6cf72fe returned real
TASK-114 `thread check` exit 0 in 0.77s; Main's `thread pending` exited 4 in 15.58s
with exact holder metadata (`msg_506300b3a096`). Independent review found the
read/write publication race; Main approved the shared tiny lock and 5s bound in
`msg_60110a9b2370`, with no further live drill required. Shared deployment remains
Main-owned. Publication requires independent final-head review, green CI and
Main exact-head GO; receipts belong on TASK-113/PR.

Main's later scope addition (`msg_a9f928c81638`, `msg_83450845375a`) removes
native I/O from every supervisor-cycle launcher holder. Builder wake snapshots
launches under a file-only section, then takes a nonblocking per-slot lease before
the task lock across native proof, final identity/question revalidation and send.
Launch/restart share that lease across terminal mutation; nested launch reuses it.
Busy wake slots skip until next cycle. PARK holds its slot lease across native
proof, file revalidation and transport, without launcher. Lane-free transport
also stays outside launcher; reservations use short launcher → needs-next sections,
but known-ID receipts take only needs-next (max5s). Contention journals the known
receipt in the supervisor's `pending_receipts`, reconciled next cycle without
resend. Manual needs-next first preserves its known ID under the task lock.
Every lock acquisition records only PID/op/UTC-start in its holder sidecar.
The PR body lists every launcher holder, remaining I/O and expected (unmeasured)
hold-time class. Manual command holders retain their serialization; launch/restart
hold-time reduction remains a follow-up. Main requires no new live drill for
these supervisor changes. All 114 focused supervisor/wake/governor/urgency tests
pass, including real flock probes at native call boundaries, stale-launch wake
refusal and lane-free deduplication; final-head review/CI/GO must be repeated.

Independent review on 84ead87 found the wake/restart reuse and known-receipt
contention gaps. Main approved per-slot leases and the shared report-lock fallback
in `msg_14e3457fee2a`. Final lock order is launcher → slot → publication → task →
needs-next; ack releases its task ownership-proof lock before publication → task
file-only commit. All 309 focused steering/launch/restart/thread/wake/supervisor/
governor/urgency regressions pass (35.408s), including busy-slot skip, actual
launch/restart exclusion at wake send, known-ID retention under launcher/report
contention and no-resend reconciliation. Repeated final-head gates remain required.
Later review on 6683c58 identified the analogous PARK proof/send reuse gap;
PARK now holds the same slot lease throughout and never acquires launcher.
All 59 supervisor/governor/wake regressions pass (7.526s), including slot exclusion
at every PARK native boundary and busy-slot refusal before native calls.
The macOS CI failure in the Linux-only restart entrypoint proof was a fixture
platform omission, corrected using the existing explicit Linux fixture convention.


### TASK-112 — Declare Main-owned scratch worktrees

Optional `coordinator.expected_worktree_globs` accepts only nonempty absolute
path globs (default `[]`, case-sensitive `fnmatch`). Matching non-lane project
worktrees remain visible in status, marked expected, and relay once per continuous
observation as plain status, never urgent. Nonmatching strays retain urgency;
new declarations discard undelivered old urgent retries. Builder no-extra-worktrees
policy and configured lanes are unchanged.
All 78 focused parser/status/urgency regressions pass. Read-only candidate
evaluation against a private copy of real Project Alpha config with
`['/var/tmp/project-alpha-scratch/TASK-*/wt*']` classified TASK-2875 `wt` and `wt2`
as expected/status; the actual source `status` CLI marked both expected (exit 0).
No other stray was present; unmatched urgency and queued-urgent cutover are
covered by regressions. No live config, state, delivery or service was changed.
Publication requires independent final-head Sol-medium review, green CI and
Main exact-head GO. Shared deployment remains Main-owned; receipts belong on TASK-112/PR.


### TASK-111 — Provider-independent builder identity and quota

Builder health reads latest model/thinking/fallback metadata from the exact
launch's slot/incarnation-bound session JSONL, not the terminal's human label or
provider projection. New launch receipts pin resolved model/thinking and exact
argv. Legacy expected models come from the launch overlay's single compaction
model key; ambiguous expectations remain flagged, never reinterpreted through
current config. Only recorded thinking is compared.

Retirement matches OLD receipt argv while the NEW launch uses current config.
Main's legacy ruling `msg_a5d938a0b15f` permits exact nonce-scoped session-dir and
overlay argv identity with all existing foreground/routing/idle proofs; absent
or ambiguous launch artifacts refuse with verified-idle `/exit` + reconcile +
launch recovery. No other retirement or publication proof is relaxed.

Admission derives the quota provider from `builder.resolved_model`; a legacy
`quota.provider` cannot select the reviewer's pool. Unknown builder usage fails
closed and names that provider. Reviewer publication remains an independent gate.

Read-only candidate evaluation of real project-gamma TASK-33 launch
`1794371506079746465` reports `anthropic/claude-fable-5-2`, `xhigh`, fallback false
and no health flags. The real legacy process matches both launch artifacts.
Real `omp usage` for Anthropic is readable: shared 5h 100%, shared 7d 90.5%,
reserve 2%, known and admission allowed. No service/state/delivery changes or
shared deployment occurred. Publication requires independent final-head
Sol-medium review, green CI and Main exact-head GO; receipts belong on TASK-111.
Shared deployment remains Main-owned.

### TASK-106 — Preflight restart admission and preserve user files

Restart checks tracked cleanliness, task branch checkout feasibility without
switching the live session, rules availability, Pad admission, coordinator
visibility and quota/disk/capacity before retirement. Preflight refusals explicitly
leave the old session untouched. Normal launch checks repeat after retirement;
if a hook or concurrent change withholds that launch, fix admission and use
`fleet launch NEXT --slot PATH`, not another restart.

Launch preserves non-ignored untracked files, recording the first 50 relative
paths and total count in its receipt and a fixed user-ownership warning in the
builder prompt. Tracked/staged changes and conflicting checkouts still refuse.
Same-ref recovery preserves the branch and committed WIP without fetch/switch,
but now refuses uncommitted tracked changes too.

Main's live lane2 drill on candidate `4f2d1cf` passed: TASK-107 → TASK-108 restart
launched `1794369563248871263`, listing `drill-user-file.txt` in receipt and prompt;
the file stayed untracked with unchanged hash. Fresh session `01a1c8c2` recorded
Sol/medium without fallback. A later tracked README change refused before retirement;
OMP PID 564797 stayed alive, idle pane and TASK-108 launch receipt unchanged.
Main's acceptance and safe point are recorded in `msg_d4e3e458b065` on TASK-106.

Independent review found a P2: the initial preflight required tracked rules.
The corrected check resolves target-tree files/symlinks plus preserved untracked,
ignored or absolute rules; it still refuses removed, missing or cyclic rules.
Final-head Sol-medium review, green CI and Main exact-head GO remain required;
shared deployment is Main-owned.

### TASK-104 — Resume PARKED attempts on the same ref

Main-confirmed same-ref `restart REF --slot PATH --finished-ref REF` accepts a
fully recorded valid PARKED receipt for the current canonical launch without a
new merge or handoff. Normal exact ownership, idle/session retirement and launch
admission still apply; the fresh launch preserves the parked branch. Different-ref
park reassignment is unchanged. PARKED never qualifies for FRESH-READY; refusal
points to Main's same-ref restart instead.

Parking requires clean tracked files and HEAD equal to the actual origin branch.
All non-ignored untracked files are preserved regardless of age, never staged,
moved or deleted. Receipts and PARKED reports list up to 50 relative paths and
the total count. Different-ref launch still refuses dirty slots. Main revised the
original mtime contract in `msg_f3f555dfeee8`: user files may arrive after launch.

All 137 focused parking/restart/thread/command regressions pass, including a
real-Git regression for invalid-UTF-8 and carriage-return filenames. Untracked
listing uses binary capture and lossless filesystem decoding; receipt/report
JSON escapes preserve exact names.

Main's real lane2 TASK-105 drill on candidate `36cf900` parked launch
`1794366971325729448` with one user file dropped after launch. Recorded report
`msg_bf22233d2a23` lists that file. Main's same-ref restart reached `launched`
using that park, without a merge, preserving branch and pushed WIP `685e4142`.
Fresh launch `1794367231543468557`, session `01a1c89f`, recorded Sol/medium with
no fallback; the user file hash was unchanged. Private source-bound restart
receipt and detailed Main acceptance `msg_c460c4b72c46` are retained on TASK-104.
The independent review's filename-decoding P2 is covered by the real-Git fix.
Publication requires fresh final-head Sol-medium review, green CI and Main
exact-head GO. Shared deployment remains Main-owned.

### TASK-103 — Debounce collection-unavailable relays and retain safe causes

Snapshot `errors` retains immediate stable collection-failure labels; the adjacent
`error_details` map supplies exception class plus a sanitized first-line reason,
bounded to 200 characters. Environment values, token-like strings, auth headers,
credential-bearing URLs and secret command arguments are redacted before bounding.
Admission still fails immediately. The supervisor persists per-key failure streaks
and emits collection-error relays only after two consecutive failed cycles; an
absent failure resets that key. Existing content dedupe, transport receipts and
the additional unknown-quota threshold remain unchanged.

Scratch source CLI `serve --once --no-deliver` with an isolated state directory
and failing `gh` PATH shim produced zero GitHub relays on cycle one and one on
cycle two, including the sanitized `GhError` reason. `status --json` exposed the
failure immediately. No live state, delivery or installed service was changed.
Publication requires independent final-head Sol-medium review, green CI and
Main exact-head GO; durable receipts belong on TASK-103/PR.
Shared deployment remains Main-owned.

### TASK-101 — Wake idle builders for Main's recorded native reply

`coordinator.builder_wake` (boolean, default true) enables a narrow fixed-line
reminder for a current open primary/carried attempt's pending question. Reply
evidence is native status mail matching its unique question subthread, configured
Main terminal/Run, builder recipient and exact `Re:` subject (stock Orca exposes
no reply-to ID). Main approved that evidence contract in `msg_357fa1ccc6b6`.
The authoritative launch worktree/incarnation, unbound builder terminal and
shared exact-pane idle/empty-composer recognizer must all agree before input.
No draft, working/background, dialog, screenless or unknown pane can be woken.

The supervisor journals before input in `builder_wakes`, preserving one attempt
per question/reply pair for the current launch, including ambiguous sends without
retry. Per-lane cooldown uses `wake_cooldown_seconds`; unsafe proof relays one
ordinary `builder_reply_unwakeable` status to Main after
`unwakeable_after_seconds` from the reply. Delivery off never inputs.
Builders may end a turn with a recorded pending decision; on wake they run
`thread check`, then `thread wait` with the original config to record Main's
decision before continuing. Main wake, restart and settlement are unchanged.

All 44 focused wake and 34 thread regressions pass, plus 17 supervisor and 31
launcher regressions. Coverage includes ambiguous-send dedupe, cooldown, unsafe
panes, replacement races, carried refs, continuations, retired launches and
lock contention. Main's scratch TASK-102 question `msg_c939d70c430b` matched
native reply `msg_67660a2f860d`; the candidate correctly refused its stale screen
(`composer state: unknown`) with zero input attempts. On a fresh terminal,
scratch launch `1794359057405840344` question `msg_8fe9c1dfaf3b` matched Main
reply `msg_dbf10cdd3ff9`; the candidate journaled and sent one fixed wake at
01:07:27Z. The real builder resumed at 01:07:34Z, ran `thread check` and
`thread wait`, recorded the reply on Pad and ended idle. A repeated candidate
cycle retained exactly one attempt. Private before/after/pickup screens and
journals are retained under the task's acceptance receipt; no Main delivery,
shared supervisor restart or deployment occurred. Publication requires independent
final-head Sol-medium review, green CI and Main exact-head GO; durable receipts
belong on TASK-101. Shared deployment remains Main-owned.

Independent review identified a P2 in the initially briefed fixed reminder:
`thread check` alone does not retrieve decisions. Main approved requiring
`thread check`, then `thread wait --timeout 600` before continuing, both with
the shell-quoted loaded supervisor config path (`msg_75aabf976109`,
`msg_9ccb8e6c14ae`). Missing, relative or nonprintable paths refuse input.
Exact-line and quoting regressions cover the corrected template. Main explicitly
accepted the existing runtime proof without another lane wake: that builder
already ran `thread wait` and durably recorded its decision.

### TASK-100 — Superseded cancelled merge groups are status

Supervisor merge-group relays downgrade a cancelled run only when the current
queue is known and its nonempty head SHA matches no entry head. One coordinator
status per run says `superseded, cancelled`, including when group membership is
discovered incrementally. Current-head cancellations, real failures/timeouts,
missing head evidence and unknown/missing queue state retain existing urgency.
Single-PR `watch-pr` semantics are unchanged. PR-stuck timers already use only
current queue entries, so obsolete group conclusions do not enter that path.

All 73 focused urgency/supervisor regressions pass, including queued retry
reclassification and preservation of successful coordinator delivery receipts.
Independent reviews caught the initial retry-path gap and the need to retain
historical failure urgency across same-ID reruns; both are now covered.
Read-only source evaluation
against the real Project Alpha config/API observed six current queue entries and
eleven cancelled runs; all eleven were superseded and classified as status,
including runs 42915555803, 42915551278, 42915549524, 42914925624,
42914921137, 42914920121 and 42914917384. No delivery, live-state write or
shared deployment occurred. Publication requires independent final-head
Sol-medium review, green CI and Main exact-head GO; durable receipts belong
on TASK-100/PR. Shared deployment remains Main-owned.

### TASK-98 — Waiting queue entries inherit progress ahead

Queue entries are ordered by their GitHub `position`. A queued PR with no own
merge-group run includes preceding entries in its stuck-progress signature;
any queued/in-progress run ahead resets its timer on every observation.
Completed run/job transitions ahead also reset the timer. Once the relevant
queue is idle for `urgent.pr_stuck_minutes`, the waiting PR alerts normally.
An entry with its own run retains TASK-95's unchanged-run timeout; entries behind
never mask a stuck PR. Dequeue resumes normal PR rules. Null synthetic heads
mean no own run, not unknown data; failed queue/run/job reads still preserve
the prior timer and alert occurrence and hold undelivered retries.

Read-only evaluation using the real Project Alpha config/API captured #2503 at
position 4, `UNMERGEABLE`, with a null head, behind #2497/run 42909986458
and #2514/run 42910072252 (both in progress). Replaying that unchanged capture
after two hours produced no stuck alert and reset #2503's in-memory timer.
The bounded captured queue/run-status fixture also exercises collection and the
idle boundary. No delivery, live-state write or shared deployment occurred.
Publication requires independent final-head Sol-medium review, green CI and
Main exact-head GO; durable receipts belong on TASK-98/PR.

### TASK-99 — Model usage metadata after final stop

Retirement accepts the exact observed omp 18.18.6 encoding
`type=model_usage` with no `customType`, including background title-call usage
after the final assistant stop. Later user/tool messages, unknown event types
and misencoded custom events still refuse; other retirement proofs are unchanged.

The unmodified fixture retains session `01a1c718-38e9-74cc-8d12-692918ac2b43`'s
final stop `05b76182`, two model usage events, title change and todo HUD snapshot
through `5af9a781` (`2026-11-10T20:19:17.359Z`), excluding the later session exit.
All 82 retirement regressions pass. Read-only source replay accepts the captured
session through that snapshot and still refuses the full capture's session exit.
Main owns shared deployment and the next real finished-builder restart acceptance
(the separate stale-screen refusal remains unchanged). Publication requires
independent final-head Sol-medium review, green CI and Main exact-head GO;
durable receipts belong on TASK-99/PR.

### TASK-97 — Hidden host eval-state metadata after final stop

Retirement accepts the observed omp 18.18.6 encoding
`type=custom_message`, `customType=eval-state-context` only when `display` is
the boolean `false`. Unknown custom types, visible/missing/nonboolean display,
and later user work still refuse. No other retirement proof changes.

The fixture preserves the real event `e764e6c6` from project-gamma session
`01a1c704-b25a-7764-806b-e7b5538af432` at
`2026-11-10T20:11:32.153Z`, redacting only its host reminder body.
A read-only source replay accepts that captured session through this event;
the later `custom/session_exit` in the full capture still refuses.
Main owns the next real lane retirement acceptance after deployment.
Publication requires independent final-head Sol-medium review, green CI and
Main exact-head GO; durable receipts belong on TASK-97/PR.
Shared deployment remains Main-owned.

### TASK-96 — Steering checkpoints wait behind launcher contention (superseded)

The original 180s launcher wait for check/ack/pending is superseded by TASK-113:
check/ack no longer take that lock; pending waits at most 15s and contention exits
4 with holder metadata. Existing immediate-refusal paths stay immediate.

All 26 steering plus 99 retirement/stop regressions pass, covering release,
timeout, stale ownership, ack preservation and unchanged immediate refusal.
Zero-wait callers do not read the clock, preserving existing timing behavior.
A source CLI smoke with
isolated native ownership fixtures and a real scratch lock-holder subprocess
held `launcher.lock` for 20 seconds: `thread check` returned the instruction with
exit 0 after 20.06 seconds. No live launch/state or shared deployment changed.
Publication requires independent final-head Sol-medium review, green CI and
Main exact-head GO; durable receipts belong on TASK-96/PR.

### TASK-95 — Merge-queue CI progress participates in PR stuck timers

Queued entries are matched to merge-group runs by the entry's synthetic
`headCommit.oid`, not the lead PR encoded in a queue branch. At the existing
snapshot cadence, only queued heads receive extra run/job reads; shared heads
reuse one result. Run status/conclusion/update time and job/step transitions
reset the timer. An unchanged running group or an entry with no run still
alerts after `urgent.pr_stuck_minutes`. Dequeue resumes normal PR-head rules.
Unknown queue/run/job data preserves the prior timer and alert occurrence,
suppresses new stuck alerts, and holds undelivered stuck retries.
Existing merge-group failure alerts and single-PR watch behavior are unchanged.

Read-only source evaluation against the real Project Alpha config and queue observed
#2512/#2511 with active runs 42865395363/42865396616 (12 jobs each), plus queued
#2500/run 42867246057. Exact synthetic-head matching succeeded with no unknowns;
an in-memory aged PR-head baseline produced no stuck alert after observing the
queue progress. No live state, delivery or deployment was changed. Publication
requires independent final-head Sol-medium review, green CI and Main exact-head
GO; receipts belong on TASK-95/PR. Shared deployment remains Main-owned.

### TASK-94 — Quiet summaries and ack-time relay holding

Legacy terminal history is opt-in via `--legacy-history`; saved JSON is unchanged.
Builder-class waits hold relay-only ack results until selected mail or timeout,
preserving consumed batches through errors and loop checkpoints. Auto-handled
runtimes combine held and selected deliveries. Main approved the explicit-ack
boundary (`msg_c9e215941402`): return held mail on selected arrival or timeout,
leaving selected mail queued until the next invocation acknowledges printed mail.
Never acknowledge held unprinted rows.

All 59 focused mailbox/wait regressions pass, including CLI save/output/receipt
coverage for both runtime modes. Main's real bound-Run capture confirms installed
Linux Orca 1.4.242 uses explicit ack (`msg_88f6fa87075b`): new delivery
`delivery_ef0008d36c89` has no auto-ack field; only the prior delivery is acked.
Main-owned frozen-head acceptance, independent final-head review, green CI and
exact-head GO remain publication gates. Shared deployment remains Main-owned.

### TASK-93 — One-step Main inbox

`fleet wait --summary` now includes every full message body in delimited blocks,
including relays and read-only legacy history. Recommended Main commands use
`--auto-ack`: a private Run+caller receipt arms only after successful save and
stdout flush, and is consumed before the next native check. Explicit ack wins;
native auto-handled batches are skipped. Ambiguous ack intent is not retried.
An exclusive Run+caller flock spans consumption, saving, printing and receipts;
another CLI consumer refuses immediately with Run/PID evidence.
Main's scope addition `msg_97501daaaa50` requires typed report heads to match
the authoritative local task slot's Git HEAD before any send/record side effect.

Fifteen option, 10 builder-wake, 30 mailbox and 34 thread regressions cover full bodies, prior-only
ack, explicit override, output crash/save failure, private modes, concurrent
refusal and report head mismatch. Independent review found premature receipt
clearing on preflight failure; intent now clears only after validation/binding,
immediately before native check, with a regression.
The next review found terminal-control injection in raw body output; controls
and Unicode format overrides now render as visible escapes, without truncating
printable text/newlines or changing saved mail. A regression covers ANSI/C1/bidi.
Main live acceptance found the inherited PR88 builder+ack path returned its
internal 1ms checkpoint as an early timeout. Empty successful ack checks now
continue peeking for the remaining timeout without repeating the ack. CLI
regressions cover both explicit/automatic ack and two real competing processes.

Delivered in [PR90](https://github.com/cswenor/fleet/pull/90), merged as
`76594aaa8c6d2951ce20496ce0d05cc51e17f621`. Final reviewed head
`6dbcd135c1ffae95a6d646d24bdbb33ca2a45e1a`: independent stock unadvised Sol
medium session `01a1c63d-f1a7-72dd-b410-a77c86f0eba9` PASS; actual model,
thinking and read-only tool events verified. Ubuntu/macOS
[CI42852608761](https://github.com/cswenor/fleet/actions/runs/42852608761) green.
Main live acceptance `msg_e3ced1504538` on that exact frozen head: explicit ack
plus timeout 20 returned a full relay batch after 21 seconds; a competing wait
refused immediately with exit 2 and owning PID while the first remained alive,
auto-acked the prior delivery and completed its timeout 40. Main exact-head GO:
`msg_8d9ac2a9123a`. Shared deployment remains Main-owned.

### TASK-92 — Exclude draft and parked PRs from stuck alerts

The REST collector retains draft status and label names. Draft PRs or PRs
carrying any `github.parked_labels` name (case-insensitive, default `["parked"]`)
have no stuck timer or stuck relay, including pending delivery retries.
The full configured interval starts again at the first ready, unparked
observation. An empty label list disables only label exclusion.
Other PR/check/merge relays and health alerts are unchanged.

All 49 urgency/config regressions pass, covering pause/resume boundaries, custom
and empty label lists, pending-relay cancellation and unchanged ordinary relays.
Read-only source evaluation used the real Project Alpha config and REST-collected
PRs #2477 (draft) and #2510 (ready). At the configured threshold on unchanged
captured state, only #2510 would alert; #2477 had no timer. Ledger and clock
advancement were in memory; no delivery, live-state write or deployment occurred.
Publication requires independent final-head review, green CI and Main exact-head
GO; receipts belong on TASK-92/PR. Shared deployment remains Main-owned.

### TASK-91 — Builder wake selection and typed Main action reports

Main-approved `builder` wake selection matches builder report/decision classes;
recommended Main wait is `question,escalation,urgent,builder`. Supervisor status
relays alone do not wake. Stock native waits cannot express sender/class filters;
Main approved read-only classified peeks inside the single async process, default
3 seconds (`--interval` 1–30), then one whole native consuming check. Native-only
selections stay blocking. Explicit ack keeps native precedence; a capped
100-row peek without a match fails explicitly rather than hiding unseen mail.
No supervisor idle-wake behavior changes.

`thread report REF --kind release_ready|review_request|ci_failed|result --pr N
--head FULL_SHA "text"` sends a typed native question using existing task-question
ownership, ambiguity and reply recording. Payload contains ref/kind/PR/head;
summary rows expose kind/PR/head. Typed result asks do not settle attempts.
Main approvals: `msg_1def2e8d902f`, `msg_46e09e0ff7e5`.
Main live acceptance on frozen `559eb25` woke on builder status
`msg_dd67af095e9c` after 11 seconds, retaining a simultaneous supervisor relay.
Typed review-request native payload round-trip was also confirmed.
Review identified relay-only replay deferral. Main approved bounded deferral
(`msg_3c2b4072356c`, `msg_b6034cd22a36`): selected arrival, explicit ack or the
timeout checkpoint permits one whole-batch consumption; empty peeks do not.
Native read-only APIs expose no active-Delivery metadata. Builder summaries now
show each report, not only counts. On frozen `0e7f87c`, Main verified relay-only
delivery at the 6-second checkpoint and builder probe `msg_faa295078f71` wake
with its summary row (`msg_dd5134347671`). A second review found a stale final
peek; the timeout checkpoint now refreshes pending rows after the final sleep,
with a regression for arrivals during that sleep. All 110 focused tests pass.
Final-head review, CI and exact-head GO remain required; shared deployment
remains Main-owned.

### TASK-90 — Fresh launch prepares the task branch

Main's required same-ref recovery ruling (`msg_2b61fd450783`) preserves the
existing branch, index and dirty/unpushed WIP without fetch/switch whenever the
slot's last launch ref or current task-branch ref matches. Title changes do not
rename resumed work. Occupancy, admission and receipt guards remain unchanged.
Different-ref launch fetches origin and refuses uncommitted work, detached HEAD or unpushed
commits before changing branches. Deleted squash/rebase branches require an exact
merged-PR head match; later local commits still refuse. It creates the title-derived
`task-<n>-<slug>` from `origin/main`, reuses a pushed unmerged exact branch, and
preserves merged names with numeric suffixes for fresh pieces. Remote-only
branches are supported; behind/diverged local task branches require reconciliation.
GitHub merged-PR state handles squash/rebase merges and deleted historical names,
without mistaking an unused prepared branch at main for completed work.
No stash, reset or clean is used.
Restart keeps OLD's branch until verified exit, then uses normal preparation.
The launch receipt and prompt name the actual prepared branch; primary
`thread open` refuses a changed branch before recording it.
Main approved leading-only branch ownership in `msg_41260eb681d4`: title mentions
no longer create additional assignments. A real generated
`task-123-unblock-task-456` branch permits TASK-456 elsewhere while TASK-123
still refuses as a duplicate. Cards, active builders and launch receipts retain
their independent ownership claims.

All 158 focused launcher/thread/retirement/dispatcher regressions pass. A source CLI
subprocess with a real isolated bare origin/checkout proved launch selects a
fresh branch at fetched main and receipt, prompt and thread agree; only native
transport/admission were fixtures, so no real builder was started.
A second source CLI smoke with offline origin, renamed same-ref title and
private commits plus staged/unstaged/untracked work preserved the exact branch,
HEAD, index and file bytes. Native transport/admission remained fixtures.
Main authorized this session's bootstrap correction from inherited TASK-87
branch in `msg_3e3fd59c8753`, retaining its immutable older thread receipt.
Main owns next-real-lane-launch acceptance after merge and shared deployment.
Publication requires independent final-head review, green CI and exact-head GO;
durable receipts belong on TASK-90/PR.

### TASK-87 — Main inbox wrapper absorbed into `fleet wait`

`--loop` rearms internally across timeout/recovered transport checkpoints,
without replaying an initial acknowledgment. Default single-shot behavior and
native whole-batch acknowledgment remain unchanged. Interrupted request metadata
survives in `rearmEvents`; unknown native outcomes still require reconciliation.
`--save PATH` atomically replaces complete JSON with mode 0600; `--summary`
prints delivery IDs/counts and decision/urgent rows, including needs-next and
lane-free questions. Main's documented single command replaces `main-inbox.sh`;
all saved rows, not just the summary, must be processed before acknowledgment.

All 537 tests pass after current-main integration. Actual source CLI subprocess smoke with isolated native
fixtures exercised timeout → interrupted consuming check → delivery, exactly one
initial ack, full report preservation, recovery request retention and private save.
Review found save failures could hide auto-handled mail; destination preflight
and full-JSON stdout fallback now preserve the batch/request IDs on exit 2,
including with summary enabled. CLI subprocess smoke proved both failure paths.
Summary-only calls require a save destination before consumption, so compact
triage cannot silently discard an already-handled batch's full payload.
Main live acceptance on frozen `2cf9096` returned six real messages after ~66s,
with one decision summary row, all six payloads saved and mode 0600. Main then
requested historical status-type `FRESH-READY` summary rows; these are included
without changing classification/wake/ack. Final-head live acceptance remains required.
Live Fleet-Run acceptance is Main-owned from its bound terminal; builders remain
unbound. Publication/live receipts belong on TASK-87/PR; exact-head Main GO is
required before merge, and shared deployment remains Main-owned.


### TASK-88 — Post-restart clipped-screen diagnosis and recovery

Main revised scope in `msg_a0b4555866e0`: retain strict empty-composer proof,
add a chronology-bound diagnostic and document manual recovery; no
`--exit-stale-finished` override or inferred frame geometry. The installed stock
Orca CLI exposes neither authoritative frame dimensions nor terminal resize/
refresh. Upstream API changes remain an owner decision.

Read-only live phase1 capture has clipped 80-column borders and no composer
border while its exact bound OMP PTY is 280×83. A 5000-row read does not repair
it. The real captured regression refuses even with exact-pane done/idle and null
program status. Source `_idle` on the affected live launch confirms that the
terminal session predates Orca and reports the runbook without sending input.
Unavailable chronology retains the ordinary refusal; all retirement gates stay
unchanged.
All 509 tests pass after current-main integration, including the captured refusal
and chronology boundary cases.

[Operator recovery](MAIN.md#post-restart-clipped-screen) separates Main's
owner-approved manual finished-builder exit/reconcile/launch exception from
operator UI resize followed by fresh full idle/composer proof. Neither was
executed by this builder. [Source-bound evidence](orca-feature-map.md#task-88-post-restart-screen-investigation-2026-11-10)
records the suspected 80×24 fallback as inference, not measured emulator geometry.
Publication receipts live on TASK-88/PR; shared deployment and a future live
relaunch remain Main-owned.

### TASK-83 — Finished-lane decisions and explicit park completion

`thread needs-next REF` sends one native `NEEDS-NEXT-REF` question per launch
after canonical settlement and proof of a PR merged after launch or explicit
pushed-WIP park completion. It needs no invented next-ref handoff. Known-ID Pad
retries never resend; ambiguous sends require reconciliation. Ready and needs-next
are mutually exclusive for a launch.

`thread parked REF --resume-note COMMENT_ID` verifies the current primary launch,
live unbound terminal ownership, opened task branch, clean worktree, HEAD equal
to the actual origin branch head and the exact newer Pad resume comment. It
records PARKED and settles the attempt without a fake merge. Different-ref
guarded restart accepts that receipt; same-ref restart and ready retain their
new-merge/later-handoff requirements. Retirement/admission proofs are unchanged.

The supervisor queues one `Lane free: … needs next task` decision request per
launch for canonically settled, positively idle OMP/shell lanes with verified
merge/park completion and no ready/needs-next report. This replaces generic
idle/missing-ready relays for that case, not blocked/stalled/unknown states.
Neither path dispatches automatically.

All 506 tests pass after integrating TASK-85. Review regressions prove deferred
lane-free requests survive continuation, historical ready receipts remain
launch-wide, and both decision channels share an ambiguous-send-safe journal.
Main-authorized scratch TASK-86 / Run `run_12d3b9ad93d0`
proved real `parked` (`msg_8f0d6ec00eb4`) and `needs-next`
(`msg_f8b9996041ea`) delivery, with no second question on command retry.
A separate private no-report state copy exercised the real source supervisor
against the scratch shell: exactly one lane-free question queued with delivery
disabled. No real launch receipts, Main input or shared deployment was used.
Extended live proof on scratch Run `run_6c9b17119508` delivered PARKED
`msg_5c82c3fe9e6d` and supervisor-first lane-free question `msg_c31a98f78e0d`.
Repeated enqueue and a later real CLI `needs-next` returned that same question
ID; native peek contained exactly the park plus one question. All four owned
scratch terminals were closed, the remote scratch branch deleted, and TASK-86
marked done.
Publication requires independent final-head review, green CI and Main exact-head
GO; scratch cleanup and publication receipts belong on TASK-83/PR.

### TASK-85 — Actionable-only Main wake and delayed unwakeable relays

Main wake decisions now consider only native question/escalation messages, matching
`fleet wait`'s decision/urgent classes. Status-only backlogs never wake or relay;
status arrivals change neither the oldest actionable age nor set-based dedupe.
Idle wake keeps its 120-second default and existing identity/composer proofs.
Failed proofs relay only at `coordinator.unwakeable_after_seconds` (positive finite,
default 900), once per actionable set. Cooldown remains 600 seconds.

All 15 focused wake/config regressions pass, including status-only idle/busy,
young question behind old status, exact 900-second relay boundary, status-stable
relay/cooldown and custom threshold. Read-only dry evaluation of real Project Alpha
and Email Runs found valid complete empty unread sets: no wake or relay. No
input, message delivery, acknowledgement or shared deployment was performed.
Publication requires fresh independent final-head review, green CI and Main's
exact-head GO; receipts live on TASK-85/PR.

### TASK-80 — Restart and verify surviving terminals

TASK-13's live 2026-11-10 12:41:58Z `orca-serve` restart took about six seconds;
all 15 handles and omp processes survived. The old conductor then failed at
`coordinator-fleet`; Main relaunch assumptions and screen/idle/handoff gates
are superseded by restart-and-verify.

Version-2 plans contain ID, private state directory, stock service and project
config paths, with optional restart/consumption timeouts and existing-stop-policy approval. The
conductor runs directly from the initiating Main terminal, retains private
BEFORE/AFTER/journal receipts, verifies a new active service PID/runtime,
connected surviving handles, coordinator bindings and active supervisors.
Per-Run pending-ID comparison reports lost/new messages and refuses success
on loss or incomplete evidence. Native question notifications to every project
Run retain message-ID receipts from the real caller. No Main launch, Run rebind,
config rewrite, supervisor reinstall or blind restart/send retry remains.

Addendum 3 keeps all Main wait jobs running. `fleet wait` adds bounded 30-second
transport recovery with explicit `rearm`/`runtime_unavailable` events and no
blind consuming-check/ack replay. Result consumption is observed read-only for
up to 120 seconds by default; zero-pending Runs are confirmed and undrained/
unknown Runs name the Main to nudge. Native Delivery receipts still require
separate replay/ack reconciliation.

Guarded builder retirement separately accepts absent/null `programStatus` only
with exact agent done/idle, no interruption and all existing positive
composer/session/lease/foreground proofs. Present running/unknown status and
working agents still refuse. The regression failed before the fix; all 74
retirement tests passed afterward. Empty-screen health fallback is TASK-81.

All 470 tests pass after integrating TASK-81 and TASK-82. The source CLI three-project
dry-run on 2026-11-10 at 13:38:10Z returned no blockers, discovered the
service/runtime ancestry, listed 17 connected terminals, verified all three
supervisors active and captured complete pending sets of 0 Fleet, 5 Project Alpha
and 0 Email messages. The read-only run created no journal. Private receipt:
`~/.local/state/fleet/orca-restart/task80-final-source-dry-run.json` (0600).
Actual `fleet wait` CLI subprocess smoke with isolated outage fixtures proved
read recovery (exit 0), interrupted consumption/rearm (exit 4, exactly one
check/ack), and persistent outage (exit 2 after 30.07s). Real journal/native-peek
fixture smoke confirmed pending-to-zero, named undrained warning after 2s,
read-only commands only and receipt-preserving resume. No live outage was induced.
Review fixes restrict consumption evidence to scalar allowlisted identities;
message bodies/subjects are never journaled. Cold-cache capability discovery
and the native request share one remaining deadline; successful capabilities
are cached independently of the caller's timeout. Actual CLI smoke with blocked
cold-cache help returned runtime_unavailable/exit 2 after 30.08s, with no native
operation sent. Journal/native-peek subprocess smoke retained request IDs and
confirmed no private content persisted.
Readiness service/status probes share one monotonic deadline and reject late
success. Read-only real service/Orca probes accepted a synthetic old baseline;
the unchanged real runtime refused after 1.000s. A real stalled subprocess
substituted for the service probe timed out at 1.002s without a late status call.
Before restart intent, resume refreshes BEFORE from the final authority inspection;
only an existing restart intent freezes the original baseline. Regression covers
new terminals/mail lost after pre-restart interruption. Real native inspection/
private-journal smoke refreshed a stale empty baseline to 15 handles/three complete
pending Runs, with caller/readiness fixtures and privileged restart suppressed.
After preservation, resume retains the original AFTER and completed pending-ID
comparison; current liveness is captured separately as `resume-observation`.
Regression covers legitimate mail consumption and newly disconnected terminals.
Real read-only observation/private-journal smoke with seeded completed receipts
retained AFTER, observed 17 current handles and avoided false mail-loss failure;
caller/readiness/notification fixtures sent no restart or notification.
Publication receipts belong on TASK-80/PR. A live restart is owner/Main-only
after merge; no builder shared restart or deployment was performed.

### TASK-82 — Wake an idle Main with unread Run mail

The per-project supervisor peeks its own Run and submits the fixed inbox reminder
only with old unread mail and positive bound-Run/exact-pane OMP idle proof using
the shared composer recognizer. Default wake is enabled, age 120 seconds and
per-set cooldown 600 seconds; all controls validate. Unsafe/unknown screens or
identity relay `main_unread_unwakeable` nonurgently to the escalation Run once
per unread set. Attempts are journaled before input, including screen-hash proof;
no-deliver never inputs. Builder terminals and restart behavior are unchanged.
Restored panes with null `programStatus` are accepted only with idle/done agent
state, no interrupt and all remaining real-screen/identity guards. Both journal
lists retain 200 entries. A validated 100-row oldest-first unread prefix still
wakes/escalates; restart capture continues to report incomplete at that cap.
Regressions cover the reported restored-pane shape, capped backlogs, unsafe
surfaces, cooldown, consumption and journal bounds; all 470 tests pass.

Scratch Run `run_897600d0861f`, session
`01a1c597-189b-70f7-b8e6-a5fb126ab27a`: two read-only peeks retained
`msg_79df3d249106`, and history still showed `read=0`. With a one-second scratch
age threshold, the source supervisor submitted its fixed reminder from positive
idle proof; the agent then checked, acknowledged `delivery_f0f7abc0af5b` and
reported the probe body plus `INBOX-ACTED`. A later drained-mail cycle sent no
second wake. Native notices worked in this fresh scratch pane, so the agent was
explicitly instructed to leave the controlled probe unread until Fleet's fixed
line; this proves the Fleet path, not reproduction of Orca's post-restart defect.
No other project's Main received input. Independent final-head review, CI and
Main exact-head GO remain mandatory before merge; no shared deployment performed.

### TASK-81 — Post-restart screenless Main observation

Real busy Fleet and idle Email Main captures already classified as up. The
reported unknowns followed Orca restart: Project Alpha Main returned empty
`source=screen-unavailable` and plain stream tails despite a connected terminal
and an exact-pane OMP agent in `worktree ps`. Main re-scoped TASK-81 in
`msg_218f40699deb`: no stale-history fallback and no readiness changes.

For an unavailable/empty screen only, coordinator health now requires the same
connected incarnation, `agentIdentity=omp`, stable worktree/tab/leaf routing and
exactly one OMP agent on that worktree's exact pane. Truncated/partial inventory,
missing or ambiguous agents and changed identity remain unknown. The result is
observational `up`, reason `screen unavailable; omp agent present`; agent state
does not establish idle. Builder health, shell readiness, launch, retirement,
Main-down timing and unknown-alert debounce remain unchanged.

The source-bound Project Alpha fixture reproduced unknown before the fix and up
afterward; captured busy/idle screens and negative identity/inventory cases are
covered too. Live source CLI status observed Fleet Main up. Project Alpha redrew before
the post-change live smoke, which observed up through its real screen rather than
the fallback; the pre-redraw receipts retain that acceptance evidence. Independent
final-head review, CI and Main exact-head GO are required before merge; no shared
deployment or terminal input was performed.

### TASK-70 — Main liveness in plain status

Plain `fleet status` displays one `Main:` line with the snapshot coordinator's
state and reason (`down` is emphasized as `DOWN`); no configured terminal means
no line. Snapshot collection, JSON, health classification and alerts are unchanged.
No outage duration is invented: the snapshot does not contain one.

Rendering regressions cover up, down, suspect, unknown and unconfigured states.
All 458 tests pass after integrating TASK-79. Source CLI plain/JSON smoke on the real Fleet, Project Alpha and
Project Beta configs returned exit 0 and matching `Main: up (live omp UI)` for
each project. Publication receipts belong on TASK-70/PR; merge requires independent
final-head review, green CI and Main exact-head GO. No shared installation or
service restart was performed.

### TASK-79 — Debounce unknown Main observations

`coordinator.unknown_alert_cycles` is a positive integer, default 3. The persisted
`main_liveness` ledger counts consecutive unknown observations for the same Main
identity; up, suspect, down or an identity change resets the streak. At threshold,
the existing nonurgent occurrence and project/escalation recipients are unchanged.
Main health classification, suspect timing and immediate real-down alerts are unchanged.

Regressions reproduced premature relays before the fix; all 456 tests pass after it.
A real Fleet CLI status snapshot observed Main up; a private scratch supervisor
cycle smoke forced unknown and observed cumulative Main relays 0, 0, 1, 1 at
cycles 1–4. Native delivery, central push and quota actions were disabled for this
probe, and its scratch ledger was removed. Publication requires fresh independent
final-head review, green CI and Main exact-head GO; no shared deployment performed.

### TASK-77 — Pending inbox capture, independent of history size

Capture now uses read-only Run `check --peek` for pending messages, including
delivered/unacknowledged rows. Fewer than 100 validated rows proves completeness;
at the 100-row cap or with unknown/malformed data, capture still blocks. History
size is informational (1000 returned is only a lower bound). Preflight and the
conductor retain `mailboxes[RUN]` for every plan project; Main compares pending
IDs/counts manually across BEFORE/AFTER and retains native Delivery receipts.
No automatic mailbox subset gate or shared-runtime mutation was added.

Main approved the bounded contract and clean cutover in `msg_9075866655fd` /
`msg_f23c853ac55e`. Real scratch Run `run_f91cee517a8c`: two peeks returned
`msg_2036e2489be5`, history remained `read=0`, normal check delivered it in
`delivery_7b7c652d708b`, another peek still included it before ack, then ack
drained the set. The scratch terminal was closed. Live Fleet capture observed
1000 history rows and zero pending, complete, without `inbox_inventory_incomplete`;
unrelated process evidence remained partial. The real three-project source CLI
dry run likewise omitted `capture_inbox_inventory_incomplete`, retaining
execution-window, project-handoff and idle/confirmation blockers. All 448 tests
pass. Publication requires independent final-head review, green CI and Main
exact-head GO; no installation or shared restart was performed.

### TASK-73 — Restart CLI idle diagnostics

Dry-run `projects[NAME]` now projects only `idle_checks` / `main_idle_checks`
status/reason fields from preflight. Preflight-blocked execution returns and
journals the same diagnostics; refusal notifications include the receiving
project's checks. No idle checks or restart authorization behavior changed.
Regressions cover failed/skipped/passed checks, field filtering and blocked
execution without capture/restart. All 445 tests pass. Publication and real
three-project CLI evidence belong on TASK-73/PR; merge requires Main exact-head GO.


### TASK-76 / TASK-78 — Soft-wrapped shell prompts

Shell readiness accepts the full rendered screen receipt. TASK-78 corrects
PR71's inverted interpretation: `tailRows[i].wrapped` means row i continues
row i-1 (xterm `isWrapped`), not that row i continues onto the next row.
Starting at the last non-blank row, walk backward while the current row is
wrapped, join that logical line, then apply the unchanged strict prompt regex.
A final wrapped row is valid; a new unwrapped prompt never absorbs prior output.
All screen consumers preserve metadata. Older text-only receipts retain the
bounded four-row fallback; malformed metadata still refuses readiness. A logical
line starting before the captured rows also refuses, even if its suffix matches
the prompt regex (independent review caught this clipped-line boundary).

Real Orca screen captures from an owned 120-column scratch terminal supply
regressions for wrapped prompts (`false,true`), a clean prompt after a wrapped
command (`false,true,false`), and wrapped non-prompt output ending in `$`.
The original reported 51-column Email prompt and `cd ~` transition are covered
too. The captured positive cases failed before correction; all 452 tests pass
afterward, including clipped-line refusals. Live CLI screen reads accept the
scratch wrapped prompt and reject
Email lane 2's resumed omp UI. The scratch terminal was closed; no input was
sent to Email. Captures are retained in `tests/fixtures/orca-wrapped-screens.json`.
Wrapped reply residue and restart's single-line line-kill verification remain
unchanged. Publication requires independent final-head review, green CI and
Main exact-head GO. Merge does not deploy shared health behavior.

### TASK-73 — Restart CLI idle diagnostics

Dry-run `projects[NAME]` now projects only `idle_checks` / `main_idle_checks`
status/reason fields from preflight. Preflight-blocked execution returns and
journals the same diagnostics; refusal notifications include the receiving
project's checks. No idle checks or restart authorization behavior changed.
Regressions cover failed/skipped/passed checks, field filtering and blocked
execution without capture/restart. All 445 tests pass. Publication and real
three-project CLI evidence belong on TASK-73/PR; merge requires Main exact-head GO.


### TASK-89 — Sanctioned emergency lane stop (source-only)

Owner approved `fleet thread stop REF --reason TEXT`; Main's ruling
`msg_e8abade9e41f` approves exactly one bare Escape, bounded idle wait (default
120 seconds, configurable 1–600), empty composer proof and one fixed STOP line.
Normal steering/PARK remain cooperative. Main-only current-attempt identity,
terminal incarnation, exact pane/process/session lease and Linux foreground
proofs refuse mismatches. The private 0600 steering journal records every input
intent/outcome and exposes the unacknowledged STOP in check/pending. A launch-wide
0600 `stops.json` guard prevents retries across primary/carried refs and reopened
continuations; timeout reports “turn did not stop.”

Source CLI smoke on isolated Run `run_6931dfeff680` / OMP session
`01a1c5df-cd05-70fb-835b-fcb5bf533a34` (18.18.6, actual Sol medium events):
foreground `sleep 120` aborted after exactly one Escape byte; tool output was
only `TASK89_LIVE_CHILD_STARTED` and `[Command aborted]`, with no injected text.
Idle+empty composer proof preceded native STOP `msg_1d603c46d849` and one terminal
submission. The actual recipient ran scratch `thread check TASK-89`, printed
token `f09cf20f-4ef5-4910-86ef-a258b365bb42`, left it unacknowledged and stopped.
Main's real scratch `pending` showed that token. Separate 18.18.1 scratch proof
confirmed idle multiline draft clearing with raw Ctrl-C; Ctrl-U alone did not
reliably clear it. No child Ctrl-C or Orca `--interrupt` was used.

An earlier scratch attempt refused before input because its private config
omitted the auto-injected status extension. Its journal was retained and that
scratch session replaced, not retried. All 559 tests pass, including 22 stop
tests for refusal/ambiguity/composer races, actual custom-config CLI consumption,
single-line path safety and the real captured console footer.
Independent reviews found a ref-local retry bypass and ignored fresh working
state before composer input. Launch-wide guarding and fresh agent/screen checks
before each input fix both, with carried-ref, partial-write and resume-race regressions.
Main ruling `msg_9a318614d8e8` authorizes the reviewer-requested correction to
embed the absolute shell-quoted configuration in the STOP check command.
Final fresh-input live proof on 18.18.6 repeated mid-child Escape, empty composer,
STOP submission and actual recipient check without acknowledgement or resumed work.
Final config reproof used a private path containing spaces and an apostrophe;
the actual recipient executed the exact submitted check command and read the STOP.
All ten owned scratch terminals closed with positive `ptyKilled=true` receipts.
Publication receipts and scratch cleanup belong on TASK-89/PR; independent
final-head review, green CI and Main exact-head GO remain gates. No shared
installation/restart was performed. Launcher inherited-branch behavior remains
a separate Main-owned follow-up, not changed here.

### TASK-84 — Busy builder steering (source-only)

Main uses `fleet --config CONFIG thread send REF --body TEXT` from its configured,
Run-bound terminal. The unique authoritative unsettled opened attempt and exact
live unbound builder terminal/incarnation/worktree must match. A unique token is
journaled before native status delivery in the attempt thread. Known message IDs
record `sent`; ambiguous sends retain `sending` and must not be automatically
retried. `thread pending` returns JSON current-launch lanes and unacknowledged
records, including ambiguous sends and their age in seconds, without consuming
native mail.

The generated prompt supplies an explicit configured `thread check REF` command.
The current unbound builder receives one JSON message object per line for sent,
unacknowledged steering in its current launch/attempt; empty means none. Reading
leaves messages pending. Process each message before `thread check REF --ack TOKEN`;
unknown tokens refuse. Neither reading nor Fleet acknowledgment consumes the
native inbox. This is Fleet-managed steering only: retain `thread wait` for
decision replies and inspect all other native inbox types at natural checkpoints.
Sender IDs are advisory, not authentication.

Every foreground/background inbox or CI wait, including wait-tool calls and
`watch-pr --timeout` segments, is at most 600 seconds. Between waits, run the
steering check, process/ack messages and act on events before re-waiting. Long
jobs remain async and may run beyond 600 seconds with their own runtime-plus-margin
timeout; only each wait is bounded. This supersedes TASK-74's foreground-only
wait wording below. Bounded event waits/checkpoints are not model-turn polling.

`thread send REF --body TEXT --priority` refuses before side effects. In the
scratch omp 18.18.1 probe, native `terminal send --interrupt` accepted 109 bytes,
but text and `^C` appeared inside a foreground `sleep 90` child's PTY output.
The child exited 1 after 25.59 seconds; the model read the desired marker from
tool output. This is unsafe arbitrary-child input, not structured steering.
`omp --help` lacked an enqueue CLI; `steeringMode` is one-at-a-time configuration,
and collab exposes a control link rather than an enqueue API. Normal steering
never uses terminal input; there is no proven safe stock priority path.

All 530 tests pass after integrating TASK-88. The independent review's historical
lane-collision finding is fixed: filter opened unsettled attempts before target
uniqueness, so a settled receipt in another slot cannot block the sole active
attempt. Its regression failed before and passes after; two active attempts
still refuse as ambiguous. An owned scratch omp session in fleet-lane2 completed one
60-second wait, read native steering `msg_d07528856224` through the real source
CLI, printed `TASK84-BOUNDED-STEERING-OBSERVED`, then acknowledged its token.
The send-to-ack interval was 62.16 seconds including CLI/model overhead; Main's
real `pending` showed the message before processing and an empty lane list after.
The first check used unavailable `python`; the scratch agent recovered with
`python3`. The proof used a private scratch Run/config/state and a synthetic
opened-attempt fixture; real terminal identity/incarnation checks and native
delivery were unchanged. No real launch receipts were modified.
Source-only: no shared deployment. Publication requires independent final-head
review, green CI and Main exact-head GO. Merge does not authorize installation
or shared service/runtime changes. The lack of a stock CLI steering enqueue is
an upstream/owner gap, not permission to interrupt builders.

### TASK-74 — Bounded async builder jobs

Builder default and project footers, the unconditional launch prompt, workflow
skill, decision-rights template and README now require jobs that can exceed a
few minutes to run asynchronously with an explicit expected-runtime-plus-margin
timeout. TASK-84 supersedes the original foreground-only limit: every foreground
or background inbox/CI wait segment is at most 600 seconds, including wait-tool
and PR-watcher waits. Builders process completion wakes and inspect output size,
stack/py-spy or logs before
re-running a timed-out/non-progressing job. Background jobs remain exempt from
`stalled`; their explicit timeout is the hang detector. No supervisor heuristic
or CPU/output monitoring changed.

Read-only launcher harness rendered the complete rule with an empty custom
footer (3,686 bytes), default footer (6,059 bytes) and repository footer
(6,521 bytes), with native transport mocked and no builder launch. All 443
tests pass; no wording-pinning tests added. Publication requires fresh final-head
independent review, green CI and Main exact-head GO; merge does not deploy shared
policy or authorize service changes.

### TASK-75 — End turns without self-exit jobs

Builder PARK and successful FRESH-READY instructions now explicitly end with a
final message and no further tools/background jobs, especially no job waiting
on the builder's own terminal/idle state or sending input to that terminal.
Main (or the owner) retires the idle session: guarded `fleet restart` for eligible
finished work; otherwise positively proven idle, one manual `/exit`, verified
same-incarnation foreground shell and `fleet reconcile`. Unfinished PARK does
not bypass restart's publication gate; resume remains fresh and Main-confirmed.
Governor logic, PARK protocol and reconciliation are unchanged. Main approved
the cooperative PARK message wording addition in `msg_77312071802b`.

Read-only launcher harness rendered the complete rule with an empty override
(4,364 bytes), default footer (7,163 bytes) and repository footer (7,605 bytes),
with native transport mocked and no builder launch. Main's pre-merge check caught
the adoption example footer still carrying the obsolete instruction; it is now
updated too, with a 6,882-byte read-only rendered-prompt smoke.
Scratch governor rendering
also produced the corrected PARK message without native sends. All 445 tests pass
after integrating current main (443 before integration);
no permanent wording tests added. Repository-wide search excluding only roadmap
history finds no obsolete self-exit wording. Publication requires fresh final-head
independent review, green CI and
Main exact-head GO. Merge does not deploy shared policy; supervisors must be
reinstalled for the cooperative PARK message change to take effect.

### TASK-74 — Bounded async builder jobs

Builder default and project footers, the unconditional launch prompt, workflow
skill, decision-rights template and README now require jobs that can exceed a
few minutes to run asynchronously with an explicit expected-runtime-plus-margin
timeout. Builders process completion wakes, never spend more than ~10 minutes
in one foreground wait, and inspect output size, stack/py-spy or logs before
re-running a timed-out/non-progressing job. Background jobs remain exempt from
`stalled`; their explicit timeout is the hang detector. No supervisor heuristic
or CPU/output monitoring changed.

Read-only launcher harness rendered the complete rule with an empty custom
footer (3,686 bytes), default footer (6,059 bytes) and repository footer
(6,521 bytes), with native transport mocked and no builder launch. All 443
tests pass; no wording-pinning tests added. Publication requires fresh final-head
independent review, green CI and Main exact-head GO; merge does not deploy shared
policy or authorize service changes.

### TASK-72 — Explain shared-restart idle gates

Main idle recognition now delegates to `health.omp_ui`, sharing its complete
thinking-glyph set. Background jobs, working panes, dialogs and drafts still
block; builder retirement semantics and conductor execution are unchanged.
Per-project `idle_checks` and `main_idle_checks` report bounded reasons for
binding, footer/composer, background job, pane state, final session stop, lease,
foreground process group and each builder lane. The runbook requires each Main
to stop ALL of its own async/background jobs (inbox waits, timers, PR watches,
deploy checks) at T-10, confirm none remain, send its confirmation before going
quiet and end its turn with an empty composer.

All 443 tests pass, including all-glyph idle acceptance, background/working/draft
refusals and distinct pane/lease/foreground diagnostics. Read-only `_main_idle`
against live Fleet Main PID 3634706 refused with background job, working/footer,
pane-state and final-stop reasons; foreground identity passed. Scratch Opus 5.5
session `01a1bbdd-1dc8-7695-a9be-d9fee94ad3bc` recorded actual `high` thinking,
displayed `π > ◒` with an empty composer and passed all eight idle sub-checks,
including real session lease and foreground group. Its log SHA-256 before exit:
`31cf6809019c273b29e834238817bef38e4a08639fe41043f3458dc80664aa25`.
The scratch session was retired; no shared restart, installation or service
change was performed. Publication requires final-head independent review, green
CI and Main exact-head GO.

### TASK-71 — Post-turn automatic compaction

Restart's completed-turn proof accepts only the adjacent omp 18.18.1 pair
`type=compaction` then `type=message`, `message.role=custom`,
`message.customType=eval-state-context` after a final assistant stop.
Unpaired/interrupted pairs, unknown custom types and later user/unfinished
assistant messages still refuse. Revalidation permits an appended complete pair
but never newly appended conversation work. Manual Main preflight shares the
same completed-turn policy; UI and foreground-process requirements are unchanged.

The two acceptance/revalidation regressions failed before correction. Real
Email launch `1794139059772045518`, session
`01a1bb05-7b70-766a-8693-3cb215cab807`, was replayed byte-for-byte through its
post-compaction pre-exit boundary: `_session` accepted 5,820,997 bytes,
SHA-256 `04b4b4d0f4f38248335a5c2b94695cd4d3dc51a49b6ed3e4e79dd8dca8bd73e3`.
Including the subsequent `session_exit` still refused. No live retirement,
terminal input, shared installation or service restart was performed.
Publication requires independent final-head review, green CI and Main exact-head GO.
### TASK-69 — Thinking-level footer recognition

`omp_ui` now accepts the Unicode thinking indicators for off/minimal/low/medium/
high/xhigh/max/auto (`⦸ ○ ◔ ◑ ◒ ◕ ◉ ⟳`) and all half-circle orientations
(`◐`–`◓`). The spinner/π prefix, separator and current closing-border checks
remain unchanged. Indicator mapping follows omp's
[Unicode symbol preset](https://github.com/ketema/omp/blob/main/packages/tui/src/theme/symbols.ts)
and [compact model segment](https://github.com/ketema/omp/blob/main/packages/tui/src/status-line/segments.ts);
custom ASCII/Nerd Font presets remain outside this recognizer's existing contract.

Regressions failed before the fix for 27 working/idle/background combinations.
All 435 tests pass afterward, including malformed/stale-footer boundaries.
Read-only source CLI `python3 -m fleet.cli --config CONFIG status --json`
against the fleet, Project Alpha and Project Beta private configs observed all three
live Mains as `up`, reason `live omp UI`; no shared installation/service changes.
Publication requires independent final-head review, green CI and Main exact-head GO.
### TASK-68 — Coordinator liveness alerts

Source implementation monitors the configured Main independently of builder
inventory: missing/disconnected/orphaned terminal → immediate `main_down`;
supported shell prompt or current omp crash marker → `main_down` after positive
finite `coordinator.liveness_minutes` (default 5). Unknown metadata/read/UI emits
nonurgent `main_unknown`. Live omp UI clears the occurrence, incarnation changes
restart its timer, and persisted signatures prevent repeat sends as minutes grow.
No terminal input, restart, admission change or builder-health change.
Optional `coordinator.escalation_run` adds an independently attended destination.
Cross-Run native sends use the destination Run context, retaining per-recipient
retry receipts.

Real source CLI `serve --once` drill used private config/state and two scratch
Runs/terminals, with real native/GitHub/quota reads and no shared service changes.
It observed live Sol omp → exited bare shell, then a thresholded urgent
`main_down` exactly once in each Run (`msg_0d394aeac0f1`,
`msg_5da8030d6862`), with repeated cycles silent. The drill used a 0.05-minute
threshold; clock-boundary regressions cover the default five minutes.
The extra Run initially failed because inherited delivery pinned the project Run;
the repaired retry delivered only that missing copy. Closing the already-exited
scratch terminal then proved immediate missing-terminal alert delivery even with
the sender terminal gone (`msg_565d43e90eed` at the escalation Run).
Independent review found a reconnect edge: disconnected → shell on the same
incarnation could clear dedupe before the shell threshold and send a second
urgent without a live-UI recovery. Its regression failed before correction;
reported outages now remain active across reconnect-to-shell transitions.
A second independent review found that an omitted remote host could masquerade
as a missing Main. The false-urgent regression failed before correction;
partial-host inventories now report `main_unknown`, following capture's existing
completeness contract.
A third independent review found stale crash markers above unrecognized
applications could become false urgents. Three retained-marker regressions
failed before correction; crash proof now requires a current recognizable
crash/recovery suffix or a positively recognized shell.
Publication awaits independent final-head review, green CI and Main exact-head GO;
merge does not deploy to the installed shared supervisor.

### TASK-67 — Leading-card assignment claims

Card ownership now uses only the leading ref, allowing non-word decoration
(punctuation, symbols, whitespace, emoji/keycaps) then documented status words.
Later refs in prose and cards without a leading
ref claim nothing. Duplicate diagnostics say `card (leading ref)`; selected-slot
card claims remain allowed. Branch refs, carried/unresolved receipts, active
builders and `health.REF` are unchanged.

The real-card and nonleading-prose regressions failed before the fix. Regression
coverage also retains genuine cross-slot blocking, selected-slot allowance,
presentation prefixes and all branch refs. Two independent reviews caught
incomplete emoji recognition; hourglass/star/keycap/tagged-flag regressions failed
before correction. Main approved non-word decoration rather than a Unicode-data
dependency (`msg_38c02738976d`), preserving the arbitrary-prose-word boundary;
double-exclamation/horizontal-arrow and `note:` regressions cover that ruling.
A read-only source `check_launch` against live
Project Alpha inventory returned no TASK-2753 duplicate claim for lane
2187; it correctly retained that slot's unrelated unresolved TASK-2750 receipt
`1794096515943963418`. Inventory collection had no errors. No launch, live state
write, service restart or shared deployment was performed. Publication receipts
belong on TASK-67/PR; merge is held for Main's exact-head GO.


### TASK-65 — Conditional GitHub reads and budget deferral

REST reads now use per-page ETags through stock `gh`; 304 responses reuse cached
bodies without authenticated primary-rate charges. Watches keep process-local
state; snapshot/supervisor cycles persist private credential-fingerprinted state,
bounded to 256 entries / 16 MiB. GraphQL remains uncached and separately metered.
Warm unchanged nonqueued watches omit group history unless own/unresolved group
evidence needs tracking; cold discovery and existing supersession rules remain.
History URLs use hourly buckets with exact local rolling-window filtering.

REST remaining below 10% or actual rate-limit 403/429 defers reads until reset
(one-hour bound, `Retry-After` for secondary limits). Watch sleep respects timeout;
supervisor local health continues, emitting one nonurgent budget relay per window.
Permission-denied optional workflow/group/queue reads become explicit unknown,
retaining failing checks and known group failures; required reads fail loudly
with sanitized path/error JSON. Main ruling `msg_89b9269b0bfc` defers the real
installation-token repro to Project Alpha Main/lane2187 after deployment; no cross-project
credential is provided. Synthetic 403 fixtures cover degradation, not live app proof.

All 416 tests pass after integration with TASK-66. Real source CLI measurements used the live Project Alpha config,
private scratch state, delivery disabled, push URL cleared and hooks disabled.
Only configuration isolation and request-counting wrappers were injected; native,
quota and GitHub reads remained real. Baseline frozen `origin/main` supervisor:
49 cold / 38 warm `gh api` invocations, zero 304s, 33.95 / 27.00s. Updated supervisor:
53 cold / 41 warm calls, 0 / 39 HTTP 304s, 34.47 / 25.80s; warm calls include one
GraphQL and one charged REST response. Explicit pagination accounts for additional
request counts. Baseline PR2403 cold poll: 19 calls; unchanged warm polls: 7 calls,
zero 304s, 5.49–5.92s. Updated cold poll: 20 calls; warm polls: 8 calls, all eight
304s, 5.77–5.82s. PR2403 retains group evidence, so its two history pages remain
conditional reads; regressions separately prove nonqueued history omission.
At those observed unchanged rates, three supervisors plus eight watches would
consume approximately 180 primary REST requests/hour, excluding cold starts,
actual changes, other clients and separate GraphQL usage; not a load-test guarantee.
Measurements used stock `gh` auth, not a builder App pool. No shared installation
or restart was performed. `--debug` exposes request/304 counts per poll/cycle.
Independent review caught supervisor history loss during optional Actions denial.
The regression failed before correction: unknown history now retains the previous
supervisor group baseline across repeated denials, without recovery/re-notification.
Source CLI fault smoke primed real Project Alpha history, then injected only an Actions
group-history permission 403: all 100 saved group runs remained, with explicit
unknown status. Other GitHub/native/quota reads stayed real; delivery/push/hooks
were disabled and state private. This is fault injection, not a live App-token denial.
A second independent review caught Enterprise pagination authority mismatch.
The regression failed before correction; Link validation now uses stock `GH_HOST`
while still refusing unrelated hosts. Live GitHub pagination over five open
Project Alpha PRs used three pages twice: six requests, three 304s. Enterprise-host
acceptance is fixture-based, not a live Enterprise-server claim.
A third independent review caught unresolved pending groups being skipped before
they could fail. The regression failed before correction; unresolved pending
groups now keep conditional history refresh active until their outcome is known.
A source `watch-pr` CLI smoke using synthetic gh HTTP responses observed pending
then failure and returned exactly `Merge group 1 failure` after two group reads.
Publication receipts belong on TASK-65/PR; merge requires Main exact-head GO.

### TASK-66 — Obsolete cooperative PARK resolution

Active parks become `superseded` only with a newer same-slot launch and exact
verified retirement evidence for the old launch/terminal/incarnation. Unknown
identity still blocks. Settled current canonical task-thread attempts are skipped
when selecting PARK candidates; unsettled continuations remain eligible.
Each new reserve episode blocked by an older park emits one ordinary status relay.

Main-approved `park-reconcile --launch ID --evidence TEXT` requires the configured
Main identity, exact active park launch and nonempty evidence. Its private
append-only receipt takes effect next cycle, avoiding races with the service's
in-memory supervisor ledger. `governor.reconciliations[receipt_id]` records
`consumed`/`ignored` atomically with the supervisor transition; stale receipts
report once and never clear later parks. Resolved requests remain in `parked`
with status `parked` (verified shell exit), `superseded` (guarded replacement), or
`reconciled` (Main evidence). Launch admission reconciliation is unchanged.
Contract rulings: `msg_ec5d082e8116`, `msg_0a67577d7613`.

Three regressions failed before correction; all 378 tests pass. Main approved a
seeded replay, not a fresh guarded restart: actual TASK-64→TASK-65 launch/
retirement evidence replayed through source CLI `serve --once` with private
state/config and an inventory fixture restricted to owned scratch identities.
Real GitHub/quota/disk collection and native delivery stayed intact; quota used
the existing drill fixture. Run `run_458169af88d0` received supersession
`msg_05aca6561017`, exactly one blocked-episode relay `msg_db25f137c717`, and
reconciliation `msg_b78c0d89214c`. Settled selection skipped; unknown incarnation
remained active. Real CLI receipt `3872dddd-6993-4570-b4fd-4f6735c9bcd0` was mode
0600 and consumed next cycle. Candidate PARK selection ran without delivery;
no scratch PARK was sent to a live builder or Pad. All FIFO batches processed
and acknowledged; both owned scratch terminals closed with `ptyKilled: true`.
No live supervisor changes, fresh model launch or shared deployment claimed.
Publication receipts belong on TASK-66/PR; merge is held for Main's exact-head GO.

### TASK-64 — Superseded workflow checks and watcher receipts

Head check collection omits cancelled/completed non-success workflow checks only
with linked same-workflow/head successor proof. Enrichment is bounded to three
100-run pages, only on heads with cancelled/completed non-success checks.
Main ruling `msg_ebb4be6eec04` requires this healthy/pending fast path and process-local
memoization: unchanged head/check-suite/conclusion evidence reuses supersession
proof (bounded to 512 head entries); status/linkage changes invalidate it.
Unknown/ambiguous linkage and unavailable history retain checks.
Legacy statuses and merge-group policy remain unchanged. `watch-pr` records
initial-event keys per terminal/repo/PR in private state, including diagnoses
returned through live transitions. Re-arm waits on unchanged keys; new head,
PR update or failed-check occurrence re-emits. Native identity wins over the
`--terminal` fallback; anonymous calls retain prior behavior. `--include-seen`
forces re-emission.

Receipt regressions failed before implementation. The consolidated false-wake
module covers other-PR groups (PR38), superseded same-PR groups (PR55), cancelled
workflow replacement and re-arm receipts. All 372 tests pass.
Real source `fleet.cli:main watch-pr 2403 --timeout 20` against Project Alpha returned
timeout, not the stale cancelled-run diagnosis. After quota/review corrections:
32.06s wall time, 15 `gh` calls, one head-workflow read (second collection memoized).
Only config loading/private state and a counting wrapper
were injected; GitHub reads remained real. Real merged fleet PR58 returned its
initial event, re-arm timed out (2s timeout, 3.87s wall), and `--include-seen`
re-emitted it, using 2/4/2 `gh` calls. Identified calls used explicit fallback
terminal identity; anonymous calls separately retained repeated initial delivery.
Scratch state was removed. No shared installation/restart was performed.
Quota-amended source supervisor cycles used the real Project Alpha config with private
state, delivery disabled, push URL cleared and hooks disabled. Cold cycle: three
open PRs, 28 GitHub calls including three extra workflow reads, 23.64s; warm cycle:
25 GitHub calls, zero extra workflow reads, 19.79s. Both had no collection errors.
Only config isolation/counting was injected; native/quota/GitHub collection was real.
Independent review found transient history failures permanently memoized and
URL-path-only linkage able to hide unrelated checks. Both regressions failed
before correction: failed/truncated enrichment is now retried, URL linkage requires
HTTPS GitHub authority, and conflicting run/suite evidence retains the failure.
Publication receipts belong on TASK-64/PR; merge requires Main exact-head GO.

### TASK-63 — FRESH-READY wakes Main for a launch ruling

`thread ready` now sends native `question`, not `status`, so Main's
`wait --wake-on question,escalation,urgent` wakes and classifies it as
`builder_decision_request`. Main replies in its native thread with the launch
ruling; the builder still stops immediately after sending. Subject/body,
guards, Pad receipt and idempotency are unchanged. Existing sent status
receipts remain valid and are never resent on upgrade. Wake filters and
supervisor alert policy are unchanged.

The ready regression failed before the type change; repeat/classification and
legacy-status-receipt regressions pass. Main-approved scratch drill
`msg_61a46389d101` used Run `run_54ae024b0e1c`, TASK-52/PR53 source publication,
TASK-58's later handoff and a seeded pre-PR53 private launch, not a model launch.
The source CLI decision-filtered background wait woke in 7.82s with delivery
`delivery_9b957e3326f3`: exactly one question `msg_bc3c3415125e`, classified
`builder_decision_request`. Repeated ready returned the same ID, with its real
Pad receipt verified. Scratch Main replied in-thread (`msg_51b987dc9b8a`) and
acknowledged the delivery. Both owned terminals closed with `ptyKilled: true`;
private drill state was removed, TASK-58 remains cancelled. All 352 tests pass.
Publication receipts belong on TASK-63/PR; shared deployment is Main-owned,
and merge is held for Main's exact-head GO.

### TASK-62 — Authorized same-session continuation

`fleet thread reopen REF --authorized-by MSG_ID [--reason TEXT]` continues the
latest settled attempt on its same authoritative launch-owned unbound terminal,
live incarnation and worktree. The supplied Main authorization ID is audit
evidence, not authenticated proof. The current snapshot stays under
`attempts[launch_id]`; prior attempts are archived, and continuations have distinct
attempt IDs/native primary subthreads. Existing ledgers lacking an attempt ID
read as their original launch attempt. Native status and Pad record the link,
authorization and reason. Same-ID retries reuse the continuation; known-ID Pad
retries never resend; ambiguous sends block further work pending reconciliation.
Question/wait/result use the continuation. Ready retains the original launch's
newer-merge/later-handoff guard. Main approved minimal completion/ready cache
invalidation per launch plus attempt in `msg_b45f2dd5f037`, superseding the
launch-only memo lifetime below; alert policy is otherwise unchanged.

The missing continuation API regression failed before implementation. Real
source CLI drill on scratch Run `run_83a69b89849c` settled a seeded scratch launch,
reopened with authorization `msg_4c8ef0d04068`, and repeated without another
thread-open send (`msg_379b13726514`). A different authorization while unsettled
refused exit 2. New question `msg_7f484219e0f0` received native in-thread reply
`msg_8fa527e2d6ea`; the continuation settled with result `msg_8c6cbb25160d`.
Real Pad writes and native transport were used; launch state was seeded, not a
fresh model launch. Both owned scratch terminals closed with `ptyKilled: true`.
No shared deployment or restart was performed. Publication receipts belong on
TASK-62/PR; merge remains held for Main's exact-head GO.

### TASK-59 — Terminal-scoped health alert recovery

Health occurrences now key worktree, terminal handle, terminal incarnation and
flag. A persisted absence counter requires `builder.alert_recovery_cycles`
consecutive absent cycles (positive integer, default 2) before recovery; changed
content and replacement incarnations still alert immediately. Old worktree/flag
signatures transfer only to matching current content, then retire. Non-health
alert recovery, health classification and urgency policy remain unchanged.

Regressions reproduced alternating-terminal resends, flicker, missed replacement
incarnations and old-ledger migration failures before correction. Coverage also
checks consecutive absence, configured recovery, recurrence and changed evidence.
Four real source `fleet.cli:main serve --once` cycles used private config/state
and scratch Run `run_b976fb2fbb1c`. Inventory allowlisted this live Sol builder
and one owned sleep-based scratch terminal in the same worktree. A deliberately
mismatching expected model and absent scratch launch ledger exercised real
classification: one wrong-model, one model-unknown and two terminal-distinct
progress-unknown relays, each delivered exactly once across all four cycles.
All cycles exited 0 without collection errors, pending deliveries or delivery
failures. Real GitHub/quota/disk collection and native transport were unchanged.
Both FIFO batches (including initial GitHub/stray status) were processed and
acknowledged; both owned scratch terminals closed with `ptyKilled: true`.
This proves source supervisor delivery, not shared installation or a fresh
guarded model launch. Publication receipts belong on TASK-59/PR; shared behavior
is held for Main's exact-head merge GO and separate deployment coordination.

### TASK-61 — Stalled builder turns

Health now binds session progress to the current slot launch, terminal and
incarnation, then reads the single slot-bound OMP JSONL's newest event timestamp.
Working UI without an event for `builder.stall_minutes` (default 60) raises
urgent-by-default `stalled`, even with fresh spinner output. An owned open PR's
failed/cancelled checks also raise it after `builder.stall_after_ci_minutes`
(default 20), with no later event, in working/idle/draft UI. Main ruling
`msg_871468e9c542` supersedes the original background threshold: live async-job
background UI is progress and exempt from both rules regardless of event age.
Both thresholds are per-project config. Unknown launch/log
evidence raises nonurgent `progress_unknown`. Alert context includes task, PRs,
failed checks, event age and UI; changing age alone does not repeat an occurrence.
No terminal input/nudge command, watch-pr change or shared deployment is included.

Regressions reproduced missing spinner/stale-session, failed-PR and unknown
progress detection. New stale-90-minute-background and background-failed-PR
regressions reproduced the amended contract's violation before its correction.
Repeated real scratch Sol-medium sessions ran foreground `sleep 40` and async
`sleep 45`: source collection showed working/event age 25s/`stalled`, versus
background/event age 24s/no flags, both at a 6s threshold. Drills used private
state and seeded launch bindings to actual scratch terminal incarnations/session
files; inventory filtering excluded all other builders. They do not claim
guarded fleet launch or deployed-service proof. Earlier original-contract
evidence is retained on Pad but superseded by this Main ruling.
All 337 tests pass. Actual model/thinking events for both repeated drills were
Sol/medium; both owned terminals closed with `ptyKilled: true`, overlay removed.
Independent review found snapshot-clock false unknowns during concurrent log
writes and suppressed same-name CI rerun failures. Both regressions failed
before correction. Read-completion wall clock now validates event timestamps,
snapshot-relative age clamps to zero, and stable PR-head/check-ID/completion
evidence distinguishes new failures without age-driven repeats.
The real source `fleet.cli:main status --json` entrypoint returned no collection
errors, bound this launch to TASK-61/PR56, and showed background UI, event age 0s,
and no flags; the other idle lane retained its existing idle flag.
Publication receipts belong on TASK-61/PR; Main exact-head merge GO is required.


### TASK-57 — Durable end-of-task ready reports

`fleet thread ready NEXT [--body TEXT]` reports FRESH-READY after the current
primary attempt is settled. The unbound caller must own its authoritative launch.
Readiness requires a PR carrying the current ref merged after that launch and
a concrete later handoff on NEXT, including same-ref continuations. Main approved
this across-ref boundary in `msg_c496aff9f1eb`. TASK-63 supersedes the original
native status with a question to the configured Run; its message ID is saved before the Pad receipt comment. Repeats
return that ID, Pad failures retry only recording, and ambiguous sends refuse
duplicates. Ready never schedules or launches work. Builder guidance separates
this final launch decision request from in-task `thread question` and progress/PARK status.

The supervisor closes the completed-idle silence gap with one nonurgent
“settled, no ready report” status per verified launch/handoff, after
`builder.ready_report_minutes` (positive finite, default 15). New attempts time
from settlement; legacy attempts start a persisted timer at first verified
handoff observation. A ready message ID or replacement launch invalidates a
queued fallback. Other idle suppression and urgency policy remain unchanged.

Main-approved real source-CLI drill used scratch Run `run_0e0412bd2023`,
source-bound TASK-52/PR53 evidence and a seeded pre-merge scratch launch timestamp,
not a real model launch. After settlement, two ready calls returned
`msg_1e6810ccb770`; native delivery `delivery_c2da8862eb2d` contained exactly one
FRESH-READY status and Pad contained its receipt. Separate private state with an
aged settlement and no ready receipt produced one nonurgent fallback
`msg_28024839246d` across two source supervisor cycles. The inventory filter
included only the owned sleep-based scratch terminal; real GitHub/quota/disk
collection and native transport remained in use. Native baseline PR events were
processed across both FIFO batches before acknowledgment. Both owned terminals
closed with `ptyKilled: true`, TASK-58 was cancelled, and private drill state was
removed. Publication receipts belong on TASK-57/PR; shared deployment remains
Main-owned and held for exact-head GO.

Independent review found a missing live-incarnation check before ready delivery.
The new ownership regression failed before the correction; ready now verifies
live incarnation, worktree routing and connected/writable/non-orphaned state
immediately before sending. All 322 tests pass. A second isolated real CLI drill,
Run `run_f228070a4ebc`, refused a deliberately mismatched scratch incarnation
(exit 2), then accepted the matching live identity. Repeating ready returned
`msg_59f3364e9434`; delivery `delivery_39d4effca84a` contained exactly one ready
status. Both owned terminals closed with `ptyKilled: true` and private state was
removed; the same source-bound/seeded launch limitation applies.

### TASK-52 — Completed-boundary idle alerts

Idle emission now requires checking the authoritative slot launch and terminal
incarnation, then its canonical settled thread result. Pad done suppresses idle;
otherwise next refs from the result/finished-item comments are checked using the
existing retirement publication/handoff proof on the different next item.
Unknown/malformed/unreadable evidence retains the alert. Main approved this
boundary and positive-only process-memory memoisation per exact launch in
`msg_045c30a72de5`; negative results retry normally. Raw health and all other
urgent kinds remain unchanged; no new status event or supervisor Pad write.

The settled+done regression failed before the fix. All 299 local tests pass.
Real source supervisor cycles used private config/state, scratch Run
`run_f3d5b6a90660`, one owned sleep-based idle terminal, and an inventory-only
filter excluding all real builders. Seeded settled TASK-54/PR51 evidence was
verified against real Pad done: no idle alert. An unsettled replacement emitted
one native question, `msg_367bfe9e596d`, in delivery `delivery_0b61d2ef7c76`;
both cycles had zero pending deliveries/failures and no collection errors.
Real GitHub/quota/disk collection and native transport were unchanged. This is
supervisor emission proof, not a fresh model launch or deployment. Publication
receipts belong on TASK-52/PR; shared rollout requires Main's exact-head GO.

### TASK-60 — Superseded initial merge-group failures

`watch-pr` no longer replays a failed group superseded by a newer completely
resolved group for the selected PR, including a pending successor. Proven
second-parent PR-head mismatch also suppresses an obsolete initial failure;
synthetic queue head SHA inequality alone is never lineage proof. Unknown
ordering, membership and lineage retain failures. Live failure transitions and
supervisor event semantics are unchanged.

Main approved per-PR-only pending membership resolution (decision
`msg_56814084c026`), bounded newest-first after the oldest relevant failure and
stopping at a complete successor. Complete immutable membership retains the
existing cache; unsuccessful lookups are memoized only within one collection.
Regression scenarios failed before the fix and pass afterward; all 327 tests pass.
Independent review found that a later workflow on the same synthetic group could
hide a sibling failure. New cases reproduced that bug; supersession now requires
a distinct known synthetic group SHA, in both initial filtering and bounded reads.

Real source CLI entrypoint against Project Alpha PR2395 observed old failed group
42339647000 and newer in-progress group 42341249696 without a stale exit:
`watch-pr 2395 --timeout 5 --interval 5` returned timeout after collection/wait,
not the old failure. Read-only GitHub instrumentation counted 17 calls, including
3 compares across both collections; private scratch config/state isolated this
from live supervisors and Runs. Publication receipts belong on TASK-60/PR;
exact-head merge GO and shared deployment remain Main-owned.

### TASK-56 — Shared structural health UI detection

Health and retirement now share `health.omp_ui`: the current composer/status band
and bottom permission/Ask widget determine UI state, never transcript keywords.
Real dialogs set `waiting_dialog`; a gear-marked running job reports `background`,
not a dialog or stale-output `idle` alert. Ordinary stale-output alerts remain
unchanged when no current job marker exists. Unknown/partial widgets and drafts
still cannot establish positive retirement idle evidence. Model/provider,
disconnection, ownership and urgent relay policy are unchanged.

The omp 18.17 fixture regressions reproduced false dialog flags for approval prose
and background waits, plus a missed real Ask dialog. Coverage includes the exact
“Waiting for live drill completion” phrase, idle-π/job repaint, quoted widgets,
stale footer/gear rows and partial widgets. Independent review identified a
concurrent permission/job precedence bug; a failing-before/passing-after composite
of captured screens now proves a dialog wins over its job footer and retains
the stale-output alert. All 290 tests pass.

Real source health inventory reads of owned scratch terminal
`term_28cf1cbd-48ab-4221-9e48-d10e73062da9`, session
`01a1b3e5-2dc2-7023-a465-3d0f95d153ee` (omp 18.17.0), observed permission and Ask
as `waiting_dialog`, approval prose as active/no flags, and a `sleep 180` async
job with “Waiting for live drill completion” plus `⚙ 1` as background/no flags.
Ask hides the footer, so its existing `model_unknown` flag retains precedence
over `waiting_dialog` in the display state. No model-classification change.
The drill used a private minimal config and real read-only Orca inventory,
without adapters, Run binding, live supervisor writes or deployment.
Publication receipts belong on TASK-56/PR; shared rollout remains Main-owned.

### TASK-54 — Structural retirement UI detection

Retirement no longer searches final assistant prose for approval/permission/deny
words. Real omp 18.17.0 approval dialogs and Ask widgets replace the idle
composer; background work uses a spinner and a `⚙ 1` status-band segment.
The existing exact two-row idle/empty-composer shape rejects those surfaces and
unknown layouts. The job marker is checked only inside the composer status band,
including a repaint with an idle π glyph. Exact-pane, final-stop, session lease,
foreground process and single-exit proofs remain unchanged.

Captured real idle, permission, Ask and background screens are regression
fixtures. Approval-prose regression failed before the fix; all 284 local tests
pass afterward. Main-approved private CLI drill (`msg_7fb06ec48feb`) used OLD
TASK-34/PR30 and NEXT fixture TASK-55, scratch Run `run_42470da0a918`, and an
inventory-only adapter limited to owned scratch terminals in the existing lane.
An open real permission dialog refused exit 2 before any retirement journal/input.
After approval, OLD session `01a1b3dc-ca93-73c7-b0a1-f6a3e17acd8e` ended with
“Main approved merge; permission and deny are prose.” Restart then sent one exit,
verified the same-incarnation shell and launched TASK-55 attempt
`1794019057868778452`. An initial missing scratch extension binding also refused
before input; explicit known extension configuration corrected the fixture.
Replacement session `01a1b3de-65fa-76a4-9ee6-def77db85c63` recorded actual
Sol/medium/no fallback and stopped without implementation. Both owned scratch
terminals closed with `ptyKilled: true`; TASK-55 cancelled and adapter removed.
Publication receipts belong on TASK-54/PR; shared rollout remains Main-owned.

### TASK-53 — Keep builders awake for their PR events

Owning an open PR requires an armed async `fleet --config CONFIG watch-pr N`.
Process every event and re-arm after every completion (including timeout/error)
until merged or closed. Retired `pr-wait.sh` scripts and single-workflow
`gh run watch` alone are not substitutes: terminal inbox enqueue cannot start
a builder turn. Default/generated/project footers, adoption policy, workflow
skill and README/Main now carry the same requirement.

Check-change subjects name failing checks concisely; shared event identity,
exact-PR filtering, initial-state diagnosis and native transport are unchanged.
Regressions exercise unchanged pending snapshots followed by both a failed
check-run shape and a codex-review-like failed commit status. All 282 tests pass.

Real source-CLI drill on scratch draft [PR49](https://github.com/cswenor/fleet/pull/49):
a commit-status check stayed pending for at least 180 seconds, failed at
02:34:06Z and ended the armed watcher at 02:34:14Z. After re-arm, a
`codex-review-TASK-53` status failed at 02:35:17Z and woke it at 02:35:22Z,
with the failing name in its concise subject. An earlier normal CI completion
also woke the watcher and was re-armed. PR49 was closed, never merged; a final
watch reported closed. No missed failure transition was reproduced: the repair
is the mandatory persistent watch lifecycle and actionable failure diagnostics,
not a native transport change.

Shared deployment remains Main-owned, after exact-head GO and publication gates.

### TASK-51 — Complete restart publication search

Completion lookup uses quoted title/body ref search and a merge-date window from
launch start minus one day. GraphQL cursor pagination must finish consistently;
API errors, missing/duplicate/changing pages and the 1000-result search cap
withhold retirement explicitly. Exact word-boundary title/body matching remains
the authority. Different-ref lookup falls back to earlier merge dates only when
the recent window has no exact match; same-ref still requires a merge strictly
after launch and a later concrete handoff. Main approved this boundary in
`msg_793affbccfda`.

Read-only source CLI proof against `example-org/project-beta`: `_completion` for
NEXT TASK-162 / finished TASK-160, with supplied launch boundary `1794010000`,
returned [PR516](https://github.com/example-org/project-beta/pull/516) and actual Pad
status `done`. No restart, retirement input, config/state writes or deployment.
Restart regressions cover >100 unrelated candidates, body-only ref, historical
completion, unchanged same-ref ordering and fail-closed incomplete/API results.
Publication/review/CI receipts belong on TASK-51; shared deployment stays Main-gated.

### TASK-49 — One builder decision channel

Builder decisions use exactly one channel: `fleet thread question` sends the
native question to the configured Run itself. Never also send a direct Orca
send/ask/question for the same decision; follow-ups use reply/status in the same
pending native subthread. Launcher/default/project footers, local/adoption
policy, workflow skill and README/Main guidance now state this explicitly.
CLI success adds `sent: true` and a no-separate-send notice alongside native
message/subthread IDs; pending refusal exposes those IDs (unknown message ID for
ambiguous sends). Transport and whole-batch delivery/ack remain unchanged.
Main approved omitting optional duplicate heuristics in `msg_d33d1496fa45`.

Real source CLI drill (2026-11-07): Main-created fixture TASK-50, private
config/state, seeded scratch receipt, separate owned scratch terminals, no model
start. Run `run_bdd983068006` delivered exactly one native question
`msg_2b12986a78f0` in `delivery_5113f8eabd07`. Success printed the sent notice and
IDs; a second invocation refused exit 2 with the same pending message/subthread,
without another row. Reply `msg_f4d40cd176fe` was copied to Pad; whole-batch ack
then timed out (exit 3, no rows). Both scratch terminals closed with
`ptyKilled: true`; TASK-50 cancelled. All 276 local tests passed; the CLI receipt
regression failed before the change and passed after. Review/CI and exact-head
Main GO are required before publication; shared deployment stays Main-owned.

### TASK-41 — Explicit prerequisite carry approvals

Main approved `fleet carry --slot PATH --launch EXACT_ID --ref TASK-N --evidence TEXT`:
only the configured Main terminal may record an approval bound to the exact
current launch/slot. Additional live terminal/incarnation and unsettled-primary
guards approved in `msg_93623840d21c`. Unknown/settled launch, primary ref,
competing ownership and empty evidence refuse; Pad ancestry grants no authority.
Carried refs get separate canonical threads sharing only the launch attempt.
Questions and settlement remain ref-local. Next/launch/restart keep carried
claims while the launch is unresolved; verified resolution releases them.
Replacement launches need new approval; primary-ref retirement is unchanged.

Real source CLI drill: isolated Run `run_f53a0916e07f`, private config/state,
Main-created fixtures TASK-47/48, seeded exact launch receipt and sleep-based
active scratch terminal health fixture (not a model-launch proof). `carry` and
both thread opens succeeded. Question `msg_e7547a8e6a4b` received scratch Main
reply `msg_17818ba89ddd`; `thread wait` copied it to actual Pad. Carried result
`msg_bb59deaf37ad` settled only TASK-48; primary remained unsettled. Real `next`
excluded TASK-48 with `carried launch receipt` (global admission separately
withheld for GitHub collection/cap). Both fixture tasks cancelled and both
owned scratch terminals closed with `ptyKilled=true`; no live-Run probes,
extra worktrees, production changes or shared deployment. Review found a missing
primary-thread fail-open: approvals now require the exact primary attempt with
`settled == False`, including carried-thread authorization. A second isolated
CLI smoke (`run_306bc8bb32f6`, same cancelled fixture refs) refused carry before
primary open (exit 2), then accepted carry and carried open after primary open.
All 263 local tests passed. Publication receipts belong on TASK-41/PR; shared
rollout requires Main.


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
Phase B **complete** (2026-11-06): owner-authorized isolated Mac project/Run/lane,
not a macOS CI substitute. Initial frozen source was
`a607ed207e304b65758a566b24579ba1409d0f14`; runtime defects produced
[PR39](https://github.com/cswenor/fleet/pull/39) and separate transport
[PR40](https://github.com/cswenor/fleet/pull/40). Final frozen user-local install:
`618735df11deb49ce97d8044cfeb74347f8e5c9c`, with all 247 tests passing on the
actual Mac and Linux. Versions: macOS 26.2 arm64, Python 3.12.15, PyYAML 6.0.3,
omp 18.12.5, gh 2.87.3, pad 0.7.0, Orca 1.4.251. Both effective automatic reset
settings were `no`; no omp upgrade or saved reset was used.

| Acceptance surface | Observed real runtime evidence |
|---|---|
| LaunchAgent/private env | Initial PID 86327; owned literal dummy env and plist mode 0600, PATH-only plist environment, dummy value absent. Immediate reinstall exposed asynchronous bootout/bootstrap exit 5. Bounded exact-label unload wait fixed it: PID 86491 → 86762, running. |
| Status/admission | Owner signed into GitHub himself after initial unavailable/403 boundaries. Read-only collection from the owned GUI-session coordinator succeeded against cswenor/fleet; status and may-start exited 0, `may_start: true`, no reasons. Builder changed no credential. |
| Fresh launch | One purpose-specific TASK-45 launch `1794005236091197000`; session `01a1b30b-7da6-72ec-8956-c7a946049efd` recorded actual Sol, no fallback, medium (events `3998daf8` / `6fc4879b`), not merely requested argv. omp 18.12.5 accepted the overlay. |
| Refusal/manual retirement | CLI restart refused unsupported exact Linux proof at entry, before discovery/input. Positively observed final stop + empty idle composer + exact unbound incarnation; one manual `/exit`; same-incarnation supported shell. Scratch coordinator reconciled that exact launch, exit 0. |
| Capture | Exit 1, private mode-0600 partial snapshot, null boot ID, zero process proofs, errors `boot_identity_unavailable`, `local_linux_proc_unavailable`, `terminal_inventory_incomplete`; no procfs crash or completeness/exit inference. |
| Native wake | Real scratch `fleet wait` received `msg_640a76005df7` as `delivery_2aea63f04ea6`, exit 0, `nativeAutoAck: true`, native request `d6c300de-3366-423e-ba48-cb391beaa7a2`, explicitly already acknowledged natively. |
| Cleanup | Only owned label booted out and verified absent; plist removed. Both scratch terminals directly closed with `ptyKilled: true`; lane and project setup removed; runtime/config/state/venv/dummy files removed. Scratch root contains only private evidence. TASK-45 cancelled. Native historical Run records retained with Main approval `msg_7f09e5cd01b3`; no global reset. |

PR39 gate receipts: reviewed head `8ba57c4d6a5a90f4ea45e8f3e452d6ffa5a830e7`,
fresh stock unadvised Sol-medium PASS session
`01a1b306-3db7-767a-9706-65c3772f260c`, green Ubuntu/macOS
[CI42306583947](https://github.com/cswenor/fleet/actions/runs/42306583947).

PR40 stock per-verb help discovery chooses `--full` only where advertised,
cached in memory. Linux Orca 1.4.242 rejects that flag: real isolated Run wait
retained the legacy batch schema, replayed `delivery_12bc80650748` before explicit
ack, then timed out after ack; its scratch terminal was closed. Mac 1.4.251
full records restore binding/history/delivery evidence but consuming checks
auto-handle mail. Native `request-show` reported completed and exact
`--retry-request` replayed `delivery_959f2d92ff92` after native acknowledgment.
No local replay store: losing the native request ID can lose recovery access.
Legacy `acknowledged` is only a native echo, not proof Fleet performed an ack.

Independent review caught P1 in the initially requested help-error fallback:
concise auto-handled check could consume mail before schema rejection. Main
corrected the rule (`msg_08dfdcc24b24`): failed/timed-out/ambiguous help refuses
before consuming checks or mutations. Real Mac failure smoke exited 2
`orca_capabilities_unavailable`, preserving all three pending messages including
`msg_7c00c3e6fa46`, with no consuming check issued. Normal wait then delivered
that same mail as `delivery_77b64399589b`, already acknowledged natively. This
smoke used production code identical to final 618735d (the final change added
send/reply safety regressions only).

PR40 final head `618735df11deb49ce97d8044cfeb74347f8e5c9c` received fresh stock
unadvised Sol-medium PASS session `01a1b312-185d-70d7-b14f-60d3806c6c60`, actual
no-fallback model/thinking events and read/grep/glob only; green Ubuntu/macOS
[CI42308131878](https://github.com/cswenor/fleet/actions/runs/42308131878).
Merged after Main exact-head GO `msg_7f09e5cd01b3`. No existing Mac product
session or production Linux Run was used; no shared checkout or Linux service
was changed by this builder. Shared rollout remains Main-coordinated; merge is
not deployment.

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

### TASK-39 — Deleted OMP updater executable identity

Restart, private capture and shared-runtime preflight reuse one executable-link
validator. The updater exception accepts only the full ASCII-digit basename
`omp.<digits>.<digits>.<digits>.bak (deleted)` with argv0 basename `omp`.
Non-deleted backups, near-misses, other executable names and wrong argv0 remain
refused. PID/start ticks, exact session/argv, foreground group, cwd, routing and
terminal incarnation checks are unchanged; mutable process names prove nothing.

2026-11-06 regression failed before the fix and passed afterward; all 256 local
tests passed. An owned compiled waiting-process fixture exercised real Linux
`/proc` and a real foreground PTY: normal executable accepted, renamed non-deleted
backup refused, then unlinked backup accepted with identical PID/start/tty/pgrp/
boot proof. The fixture exited and was reaped. This is faithful kernel-link proof,
not an actual pre-update OMP session or a live builder retirement; no live Runs,
services or other projects were probed or changed.
Publication requires independent final-head Sol-medium review, green CI and
Main's exact-head merge GO; merge is not shared-checkout rollout approval.

### TASK-42 — Guarded post-exit terminal-reply cleanup

Main ruling `msg_cc2c63592b1d` permits exactly one journaled Ctrl-U, no Enter,
only after strict bounded reply recognition, same-boot exact OMP PID/starttime
exit, unchanged terminal incarnation and sole foreground bash/zsh/sh on the
recorded pts with matching routing. Fresh identity/screen/process revalidation
precedes input; the exact clean prompt is required before normal guarded launch.
Any mismatch/failure withholds launch at `shell-verified-with-residue`, with
manual-clear guidance and no cleanup/restart retry. Health and normal launch
remain prompt-only; arbitrary/oversized input is never cleared.

Real owned scratch shell reproduction injected literal `16;8t`, making the
existing prompt-only check return false. The source CLI restart drill then
retired initial session `01a1b309-8b93-75ab-b624-14656f68e1d9`, injected the same
residue after actual exit, proved foreground bash PID 1654786, recorded one
line-kill and clean-prompt verification, and reused the terminal for TASK-46
launch `1794005223019746420`, session `01a1b30b-4b3f-730a-9b44-0a8cd1021b8d`.
Both fresh sessions recorded actual Sol medium. Main-approved throwaway inventory
isolation excluded live lane terminals; all process/session/completion/admission,
terminal input and replacement-launch checks remained real. Scratch Run
`run_cf297299ca3b` and private config/state never targeted live Runs or services.
Both owned scratch terminals closed with `ptyKilled=true`; TASK-46 cancelled and
the throwaway adapter removed. Replacement thread-open refused isolated-state
inheritance mismatch: this proves restart/launch, not task-thread delivery.
All 253 local unit tests passed, including live-PID, non-shell/background shell,
extra-pts-process, changed-boot, failed/ambiguous-cleanup, changed-screen and
arbitrary/oversized negatives.
Publication requires final-head independent Sol-medium review, green CI and
Main's explicit exact-head merge GO; merge is not deployment.

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

The live shared restart occurred 2026-11-10 at 12:41:58Z. All 15 terminal
handles and omp processes survived; the separate PTY daemon remained live.
The old conductor failed at Main reconciliation afterward. TASK-80 supersedes
its relaunch, timer, idle-confirmation, session-record and handoff requirements.
The [current operator runbook](orca-feature-map.md#task-80-restart-runbook-restart-verify-report)
defines restart-and-verify and the minimal private version-2 plan.

The following are **historical pre-live-restart implementation/rehearsal
receipts**, not current operational requirements or restart approval.

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

Historical Phase B verdict before the 2026-11-10 run was UNVERIFIED/NO-GO.
The live run now proves terminal/omp process survival for this host/runtime;
mailbox replay/whole-batch ack remains a separate acceptance dimension. Preserve
native Delivery receipts privately: read-only inbox capture cannot reconstruct
their identity. TASK-80 adds automatic pending-ID comparison, not an ack claim.

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
OLD, plus Pad OLD done or a post-publication handoff on NEXT for OLD != NEXT.
TASK-40 permits OLD == NEXT only with a PR carrying that ref merged strictly
after the retiring launch and a concrete next-piece handoff on that ref strictly
after the merge; Pad done alone is insufficient. Timestamp ordering is timezone-aware.
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
**Baseline source: owner-provided, unverified**; exact UTC boundaries and source
class remain unknown.

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

Preparation (2026-11-07): [reproducible procedure and exercised dry run](metrics.md).
Main approved docs-only incomplete-source scope in `msg_560fc2bfe0d0`; no custom
ingestion. The stock omp 18.17.0 local stats index omits Fleet's dedicated
`--session-dir` logs. The short-window result is explicitly **LOCAL INDEX /
INCOMPLETE / NOT COMPARABLE**, not a savings measurement. Exact GitHub merge
counts are collected separately for Project Alpha, Fleet and Project Beta.

TASK-18 stays **open**. Before the Nov 13 measurement checkpoint, Main must
resolve the intended exact after-window dates, baseline provenance (owner
question: provider dashboard or auth-broker history?), and complete matching
source coverage. Follow-up decision: stock extra-session-root indexing if
available, otherwise a separate small ingestion task. No per-PR baseline rate
or measured change is valid until those gates pass; ~182 remains an estimate.

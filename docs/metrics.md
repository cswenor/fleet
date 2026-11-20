# TASK-18: local usage measurement

**No measured savings.** The historical 137,049-call / 22.4B-cache-read /
approximately $3,600 figures have unknown provenance. The owner did not recognise
them; Main's 2026-11-07 ruling supersedes their designation as a baseline.
Never divide them by the estimated 182 PRs.

## Approved windows

UTC half-open intervals include START and exclude END. Main approved adoption-day
midnight cutoffs in `msg_451dea46f55b`; first retained launch artifacts establish
adoption dates, not proof that every delivery used Fleet.

| Project | Before START | Before END (exclusive) | Adoption evidence / coverage limitation |
|---|---|---|---|
| Project Alpha | 2026-10-30T00:00:00Z | 2026-11-06T00:00:00Z | Fleet adoption on Nov 6; substantial local pre-adoption sessions exist; other clients/hosts unverified |
| Project Beta | 2026-10-30T00:00:00Z | 2026-11-06T00:00:00Z | Nov 6 migration/adoption; pre-adoption Mac sessions unavailable on this host |
| Project Gamma | 2026-11-03T00:00:00Z | 2026-11-10T00:00:00Z | Local checkout/Main/builders began Nov 10; pre-adoption source unavailable |
| Fleet | 2026-10-30T00:00:00Z | 2026-11-06T00:00:00Z | Project began Nov 6; no established pre-Fleet delivery baseline |

For **all four projects**, AFTER is exactly
`[2026-11-13T00:00:00Z, 2026-11-20T00:00:00Z)`.
The after collection ran on Nov 20 after the window closed. The six-day day-zero
checkpoint is not an after measurement.

## Stock capability and source inventory

Checked omp **18.18.6**: `omp stats --help` exposes only JSON/summary and dashboard
host/port flags. `omp config list` exposes no extra stats session-root setting.
`--session-dir` selects a launch's storage, not additional stats input. Do not
change profiles, move live logs, insert symlinks or mutate the stock database to
simulate complete ingestion.

The observed stock `~/.omp/stats.db` contained 608,771 messages from 6,930 source
files, all under the default session tree, through Nov 6 23:58:07Z. Dedicated
Fleet builder, Main and reviewer logs were absent. Database extrema are not
completeness evidence. The new read-only `python -m fleet.metrics` walks original
JSONL sources directly; an optional `--index` adds every source file referenced
by the stock database, **not** its aggregate rows.

Inventory these source classes before every collection:

- Active profile default session tree, including nested subagent transcripts.
- Every project's Fleet state tree: `sessions-*`, `main-sessions-*`, recovery
  sessions and their nested subagents. Include renamed-state compatibility paths
  only once (resolved file paths deduplicate aliases).
- Custom Main/reviewer/evaluation roots, including temporary directories. On the
  measurement host these include both `/tmp` and `/var/tmp`. Files can disappear
  on cleanup/reboot; absence is not zero usage.
- Other hosts, clients, bot containers and custom profile roots. These are **not
  automatically discovered** by this tool. Record missing sources explicitly.
  No provider/account-wide or billed-spend coverage is claimed.

Use header `cwd`, not a directory name or message text, to attribute sessions.
Provide every stable checkout, lane and scratch prefix; the longest matching
path-component prefix wins. Repository renames need both old/new local aliases.
An unmapped isolated helper inherits attribution through its header's
`parentSession` path only when that ancestor is in the declared file inventory;
follow nested ancestry, but an explicit matching cwd always wins. Cycles refuse.
Uninventoried ancestors are never silently read. Still-unmapped calls are counted
separately, never guessed from directory names or prompts.
Keep host paths, real project bindings and raw source evidence private.

Fleet's public repository was recreated on Nov 12. Main identified its retained
private predecessor: old PR numbers and `mergedAt` timestamps are intact there
(`msg_b1deb784cbd0`). Historical windows query that archive, not the new repo.
The read-only archive query returned **0 before / 103 checkpoint merges**.
Use the current public repository for the Nov 13–20 after window. Private
repository bindings stay in the manifest/Pad, not public documentation.


## Tally contract

- Read-only UTF-8 `.jsonl` and `.jsonl.gz`; optional leading stock `title` records
  precede the `session` header. Valid non-session JSON files are counted and
  skipped; malformed/empty headers or invalid records inside a session refuse
  the measurement. No partial aggregate is emitted on a parse/GitHub error.
- Calls are recorded assistant `message` events and internal `model_usage` events
  (for example title generation), reported separately by event type, provider and
  model. Sum both event types for total recorded model calls. Recorded error
  calls count; internal HTTP retries with no usage event remain unobservable.
- Union copied logs by `(session id, event id)`; divergent copies refuse. Forks
  can receive a new session ID while retaining original entries. Exact inherited
  event ID/timestamp/model/provider/usage fingerprints count once globally;
  cross-project attribution conflicts refuse rather than guessing an owner.
- Input, output, cache-read and cache-write are the four recorded token counters.
  Missing usage has explicit global/per-model counters; the call and error still
  count, but unavailable tokens are not invented and cost counts as unpriced.
  List-equivalent USD sums recorded `usage.cost.total` only:
  no repricing, subscription-spend or invoice claim. Historical omp/catalog and
  service-tier differences can change this estimate.
- GitHub GraphQL paginates **repository** merged PRs, then filters exact
  `mergedAt` endpoints. There is no search-result cap. Count every merge,
  including docs/reverts, once by PR number. Stop only when there are no more
  pages or descending `updatedAt` is older than START (`mergedAt <= updatedAt`).
  Concurrent updates can move pagination boundaries: retain collection time and
  investigate differing counts on a later rerun.
- Per-PR rates default to **null/N/A**. `--complete-project NAME` is an explicit
  operator assertion of independently verified matching **declared source scope**;
  never pass it merely because the tally succeeded. A local-only scope must be
  labelled partial and explicitly exclude remote bot/client usage. Missing usage
  suppresses rates; unpriced calls suppress cost/PR. Missing/zero PR denominator
  gives N/A. The partial rates below divide the displayed observed totals by
  GitHub merges; they are not account-wide rates.
- Calculate `100 * (after_per_pr / before_per_pr - 1)` only for like declared
  scopes and with a nonzero, known before rate. Observed local rates can describe
  the retained evidence; they do not establish a change in account-wide usage
  or savings. Leave change N/A when the source scopes do not match.

## Reproduce

Python >=3.11; read-only authenticated `gh` access for PR counts. No provider
requests, reset spending, uploads or service changes. The published collector
writes per-repository GitHub request-attribution telemetry and locks under
`${XDG_STATE_HOME:-$HOME/.local/state}/fleet/`. Set `XDG_STATE_HOME` to a fresh
private directory for this invocation, as the after collection did, to avoid
writing live project state. Run from the source checkout (no shared installation
required):

```sh
metrics_state=$(mktemp -d) && \
XDG_STATE_HOME="$metrics_state" python3 -m fleet.metrics \
  --root "$DEFAULT_SESSION_ROOT" \
  --root "$FLEET_STATE_ROOT" \
  --root "$CUSTOM_REVIEW_SESSION_ROOT" \
  --root "$OTHER_CUSTOM_SESSION_ROOT" \
  --index "$STOCK_STATS_DB" \
  --project "project-alpha=$ALPHA_CHECKOUT" \
  --project "project-alpha=$ALPHA_LANES" \
  --project "project-beta=$BETA_CHECKOUT" \
  --project "project-beta=$BETA_LANES" \
  --project "project-gamma=$GAMMA_CHECKOUT" \
  --project "project-gamma=$GAMMA_LANES" \
  --project "fleet=$FLEET_CHECKOUT" \
  --project "fleet=$FLEET_LANES" \
  --repo "project-alpha=$ALPHA_REPO" \
  --repo "project-beta=$BETA_REPO" \
  --repo "project-gamma=$GAMMA_REPO" \
  --repo fleet=cswenor/fleet \
  --start 2026-11-13T00:00:00Z --end 2026-11-20T00:00:00Z
```

Fill private bindings from the source inventory; repeat `--project` for scratch
and historical aliases, and `--root` for other approved source trees. Do not
print environment values or publish a raw configuration. Run the same procedure
with each BEFORE interval above. A completed increasing interval is mandatory;
future endpoints refuse. A source root or indexed source file missing also
refuses, rather than silently shrinking the measurement.

For Fleet windows before the public cutover, set `repos.fleet` in a separate
private before/checkpoint manifest to the retained predecessor repository.
The after manifest uses `cswenor/fleet`. This is repository identity continuity,
not a change in the counting procedure.


For a durable private inventory, `--manifest /private/task18-sources.json`
replaces the repeated bindings. Its JSON shape is:

```json
{
  "roots": ["/private/default-sessions", "/private/custom-session-dir"],
  "index": "/private/stats.db",
  "projects": {"project-alpha": ["/private/checkout", "/private/lanes"]},
  "repos": {"project-alpha": "example-org/project-alpha"}
}
```

Run with the same state isolation:

```sh
metrics_state=$(mktemp -d) && \
XDG_STATE_HOME="$metrics_state" python3 -m fleet.metrics \
  --manifest /private/task18-sources.json \
  --start 2026-11-13T00:00:00Z --end 2026-11-20T00:00:00Z
```

Inventory actual custom session directories, not arbitrary scratch trees
containing deliberately invalid test fixtures. Standard runtime filenames have
a timestamp and UUID; include their sibling nested-subagent directory as well.
Refresh the private inventory for new launches/reviewers before the after run;
retain the original inventory to explain coverage changes. Never publish it.


Retain numeric JSON, tool/head versions, exact UTC endpoints, source-root classes,
private binding manifest, collection time, coverage exceptions and GitHub counts
on TASK-18. Only identity-free aggregates belong in Git/Pad; no prompts, content,
credentials, raw errors or provider/account identities (even shortened).

## Interpretation

Model/provider mix, PR size, scope, docs-only/revert share, reviewer/bot usage,
idle days, migration, custom-profile retention and ephemeral reviewer directories
confound comparisons. A project with no pre-adoption sources has **no measured
baseline**, even if GitHub reports merges. Repo-attributed local usage must never
be divided by a different project's merges or account-wide merges. The tool's
runtime-version list records observed in-window events, not version coverage of
all clients. Arithmetic alone never establishes causality.

## Before result — observed local sources, not a complete baseline

Collected 2026-11-12T23:35:41Z for Oct 30–Nov 6; Gamma's Nov 3–10 run
collected at 23:38:11Z. Retained inventory: 11,267/11,269 files respectively,
11,261/11,263 distinct session IDs; two non-session files skipped. No divergent
copies or missing usage found. Zero duplicate events in these windows.
New live session files appeared between inventories, outside the completed windows.
Recorded runtime versions: 18.13.1 for Oct 30–Nov 6;
18.13.1/18.17.0/18.18.1 for the wider Gamma diagnostic.

These results supersede the earlier cwd-only snapshot: explicit inventoried
ancestry restored 24,171 Sol and 3,064 Sonnet isolated-helper calls to Alpha.
Three temporary Beta reviewer roots disappeared after the initial collection,
before AFTER opened. The original manifests and private omission diagnosis are
retained; this recollection explicitly uses remaining sources, not a silently
shrunk replay of the original inventory. No raw logs were moved or copied.

| Project / approved before interval | Exact merged PRs | Observed usage / per-PR coverage |
|---|---:|---|
| Alpha / Oct 30–Nov 6 | 336 | Local-host partial rates below; excludes remote review-bot/off-host usage |
| Beta / Oct 30–Nov 6 | 157 | No local events; per-PR N/A: Mac usage unavailable |
| Gamma / Nov 3–10 | 0 | No local events; per-PR N/A: no pre-adoption source |
| Fleet / Oct 30–Nov 6 | 0 | No local events; per-PR N/A: project began Nov 6; archive queried |

**No local events never means zero actual usage.** A before rate is unavailable
for Beta, Gamma and Fleet.
The Oct 30–Nov 6 run observed one still-unattributed call; it is excluded,
not guessed into Alpha. This is observed partial usage, not full-scope coverage.

Alpha's complete *observed* model breakdown below sums assistant and internal
events. All recorded cost rows were priced; 197 Sol error calls are included.

| Provider / model | Calls | Input | Output | Cache-read | Cache-write | Recorded list USD |
|---|---:|---:|---:|---:|---:|---:|
| anthropic / claude-opus-5-5 | 9,985 | 26,472 | 4,791,283 | 4,489,974,949 | 22,470,799 | 1,173.6929298 |
| anthropic / claude-sonnet-5-5 | 32,109 | 5,373,274 | 21,232,928 | 2,826,199,643 | 108,050,163 | 937.8964443 |
| openai-codex / gpt-6.1-sol | 144,291 | 308,531,606 | 86,273,573 | 23,773,405,568 | 0 | 3,857.1394988 |

Main attests that Alpha's pre-adoption Main and lanes ran on this host
(`msg_d23185b95551`). This inventory includes their default sessions and nested
helpers; it excludes the remote review-bot's model calls and any other off-host
clients. The resulting **partial local-host Sol usage per merged PR** is:

| Observed Sol scope / window | Calls/PR | Cache-read tokens/PR | Recorded list USD/PR |
|---|---:|---:|---:|
| Alpha local Main/builders/helpers/reviewers, excluding remote review-bot and off-host clients / Oct 30–Nov 6 | 429.4375 | 70,754,183.238095 | 11.479582 |
| Alpha, same observed local scope / six-day Nov 6–12 checkpoint (not AFTER) | 706.191257 | 73,347,465.092896 | 13.366844 |
| Fleet / same observed local scope / six-day Nov 6–12 checkpoint, archive denominator 103 | 100.951456 | 9,202,625.864078 | 1.677386 |


## Day-zero checkpoint — six completed days, NOT AFTER

Main corrected the cutoff in `msg_b95c9f064f00`: use the completed
`[2026-11-06T00:00:00Z, 2026-11-12T00:00:00Z)` checkpoint, not a future midnight.
Collected **2026-11-12T23:41:14Z**, before the after window opened.
Retained inventory: 11,270 files, 11,264 session IDs; 33 copied/inherited usage
events deduplicated, two non-session files skipped, 33 still-unattributed calls,
zero missing-usage events and zero unpriced recorded calls. Inventoried ancestry
restored 27 Alpha and 70 Beta Sol calls from the earlier unattributed count.
Runtime events reported 18.13.1, 18.17.0, 18.18.1 and 18.18.6.

| Project | Exact merged PRs in checkpoint | Per-PR rates |
|---|---:|---|
| Alpha | 183 | Partial local-only rates above; excludes remote review-bot/off-host usage |
| Beta | 198 | N/A: full client/bot coverage unproven |
| Gamma | 66 | N/A: full client/bot coverage unproven |
| Fleet | 103 | Partial local-only rates above; excludes remote bot/off-host usage |

Observed provider/model totals (assistant + internal events), list USD rounded
to six decimals for display; the numeric JSON retains recorded precision:

| Project / provider / model | Calls | Input | Output | Cache-read | Cache-write | Recorded list USD |
|---|---:|---:|---:|---:|---:|---:|
| Fleet / anthropic / claude-opus-5-5 | 4,389 | 11,550 | 1,834,637 | 1,968,743,127 | 4,714,939 | 468.207077 |
| Fleet / anthropic / claude-sonnet-5-5 | 1,659 | 1,229,757 | 115,833 | 1,208,863 | 90,328 | 4.100042 |
| Fleet / openai-codex / gpt-6.1-sol | 10,398 | 24,361,092 | 2,926,154 | 947,870,464 | 0 | 172.770770 |
| Alpha / anthropic / claude-fable-5-2 | 1,780 | 5,179 | 1,681,925 | 759,217,749 | 4,663,779 | 549.255647 |
| Alpha / anthropic / claude-opus-5-5 | 9,331 | 22,040 | 4,462,494 | 3,730,948,305 | 30,821,299 | 1,082.098093 |
| Alpha / anthropic / claude-sonnet-5-5 | 22,506 | 9,555,789 | 8,371,422 | 829,477,747 | 44,212,607 | 362.624001 |
| Alpha / openai-codex / gpt-5.6-luna | 47 | 73,214 | 9,963 | 64,896 | 0 | 0.027896 |
| Alpha / openai-codex / gpt-6.1-sol | 129,233 | 250,101,751 | 60,367,034 | 13,422,586,112 | 0 | 2,446.132453 |
| Beta / anthropic / claude-fable-5-2 | 97 | 198 | 263,654 | 26,346,682 | 808,809 | 28.758024 |
| Beta / anthropic / claude-opus-5-5 | 9,271 | 24,666 | 5,684,875 | 3,142,531,999 | 14,459,833 | 857.981228 |
| Beta / anthropic / claude-sonnet-5-5 | 6,306 | 4,051,812 | 951,368 | 19,806,819 | 1,481,025 | 25.522086 |
| Beta / openai-codex / gpt-6.1-sol | 30,846 | 52,758,345 | 9,008,360 | 2,988,021,376 | 0 | 494.402428 |
| Gamma / anthropic / claude-fable-5-2 | 6,314 | 454,303 | 5,371,121 | 745,307,800 | 20,447,650 | 694.703224 |
| Gamma / anthropic / claude-opus-5-5 | 2,185 | 291,603 | 1,114,460 | 688,741,961 | 3,426,472 | 188.615780 |
| Gamma / openai-codex / gpt-6.1-sol | 7,526 | 19,080,882 | 2,035,129 | 589,882,112 | 0 | 117.501265 |

This six-day checkpoint is deliberately not compared to a seven-day window.

## After result — Nov 13–20 UTC

The private TASK-18 receipt binds the refreshed manifest and numeric output to
the published collector. Use the same command in [Reproduce](#reproduce) with
that manifest and the fixed half-open interval. Do not pass `--complete-project`:
these are retained local observations, not complete project usage.

The refresh kept the default session tree (including nested helpers), Fleet
state sessions, and indexed session files. It verified live custom roots and
added 426 actual runtime session directories found under `/tmp` and `/var/tmp`
by matching session headers to runtime filenames. Neither broad temporary tree
was a collector input. A Gamma scratch checkout was bound by its runtime header
and repository content; sessions in the separate review-bot repository were not
assigned to Fleet. Parentless sessions with only a generic temporary cwd were
left unattributed.

146 previously inventoried temporary roots were gone (in addition to the three
Beta reviewer roots already known missing before AFTER). They were each recorded
privately. The roots were known before the window; whether any continued to
record in-window is unknown. The refresh could not inspect 30 temporary
directories, including isolated service/CI paths and one Beta scratch path.
All indexed files were present. No claim is made about deleted new roots,
unreachable clients, or local/remote review-bot coverage.

The merge denominators are from `mergedAt` in each project's repository. Fleet
uses only the current repository: this window is entirely after the Nov 12
cutover. Beta's query uses its current repository after the transfer.

Collected at `2026-11-20T00:08:26.948225Z` using published source `11e720a`.
The tally read 13,861 files covering 13,804 distinct session IDs, deduplicated
21 events, and left 41 calls unattributed. It skipped 55 non-session files
(tool-change journals and inventory notes). Attributed rows had 831 recorded
error calls, zero missing-usage events and zero unpriced calls; the tally
does not assess usage completeness or pricing for unattributed calls.
The collector reported no source conflict. Observed runtime versions: 18.13.1,
18.17.0, 18.18.1, 18.18.6 and 18.22.2.

Observed model totals sum assistant and internal usage events. List-equivalent
USD is recorded by the runtime, not billed spend.

| Project / provider / model | Calls | Input | Output | Cache-read | Cache-write | Recorded list USD |
|---|---:|---:|---:|---:|---:|---:|
| Fleet / anthropic / claude-opus-5-5 | 795 | 2,248 | 212,289 | 523,262,909 | 1,144,041 | 118.059682 |
| Fleet / anthropic / claude-sonnet-5-5 | 461 | 289,163 | 27,102 | 143,346 | 3,243 | 0.876653 |
| Fleet / openai-codex / gpt-6.1-sol | 1,024 | 2,714,049 | 311,698 | 108,763,264 | 0 | 19.421404 |
| Alpha / anthropic / claude-fable-5-2 | 794 | 1,618 | 633,378 | 371,071,017 | 4,031,770 | 164.070587 |
| Alpha / anthropic / claude-opus-5-5 | 7,917 | 22,502 | 2,742,364 | 3,864,041,731 | 8,075,439 | 892.349146 |
| Alpha / anthropic / claude-sonnet-5-5 | 27,837 | 11,437,117 | 11,787,985 | 1,432,307,800 | 60,509,773 | 524.902629 |
| Alpha / openai-codex / gpt-6.1-sol | 109,637 | 254,795,666 | 58,508,327 | 11,244,316,544 | 0 | 2,158.073384 |
| Alpha / openai-codex / gpt-6.2-sol | 3,047 | 8,116,745 | 715,359 | 344,738,560 | 0 | 57.860936 |
| Beta / anthropic / claude-opus-5-5 | 3,233 | 8,616 | 1,563,703 | 1,512,639,970 | 5,784,691 | 380.114046 |
| Beta / anthropic / claude-sonnet-5-5 | 3,402 | 2,742,170 | 476,139 | 3,732,664 | 33,125 | 10.751496 |
| Beta / openai-codex / gpt-6.1-sol | 10,776 | 19,277,405 | 3,512,715 | 1,134,119,296 | 0 | 187.093890 |
| Beta / openai-codex / gpt-6.2-sol | 1,501 | 4,217,101 | 354,128 | 152,991,488 | 0 | 27.274631 |
| Gamma / anthropic / claude-fable-5-2 | 15,896 | 952,077 | 12,204,923 | 1,946,141,190 | 48,315,942 | 1,658.096846 |
| Gamma / anthropic / claude-opus-5-5 | 23,481 | 2,790,656 | 13,955,195 | 5,832,932,672 | 60,037,273 | 1,937.151242 |
| Gamma / openai-codex / gpt-6.1-sol | 28,596 | 65,551,807 | 10,114,592 | 2,838,385,536 | 0 | 516.088088 |
| Gamma / openai-codex / gpt-6.2-sol | 9,006 | 29,164,764 | 2,030,071 | 738,214,016 | 0 | 152.451640 |

Fleet switched its Sol pin from `gpt-6.1-sol` to `gpt-6.2-sol` on Nov 18
(`11e720a`). Model rows reflect recorded calls, not configuration. The retained
Fleet rows contain no 6.2 calls in this interval; that does not prove none were
made. Other projects recorded both versions.

### Observed local Sol usage per merged PR

Rates divide the retained local Sol numerator by **all** in-window GitHub
merges. Alpha and Fleet are partial local-host observations, excluding remote
review-bot/off-host usage and any missing or unattributed calls. Replaying
Alpha's before interval with the refreshed inventory reproduced exactly the six
Alpha event-type rows in the private receipt (three model totals are shown
above). The comparison uses the same declared retained local scope and the Sol
family, not a fixed model or workload.

| Project / window | Merged PRs | Sol calls | Cache-read tokens | Recorded list USD | Calls/PR | Cache-read/PR | List USD/PR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Alpha before (partial) | 336 | 144,291 | 23,773,405,568 | 3,857.139499 | 429.44 | 70,754,183 | 11.479582 |
| Alpha after (partial) | 107 | 112,684 | 11,589,055,104 | 2,215.934320 | 1,053.12 | 108,308,926 | 20.709667 |
| Beta after (local totals only) | 61 | 12,277 | 1,287,110,784 | 214.368520 | N/A | N/A | N/A |
| Gamma after (local totals only) | 196 | 37,602 | 3,576,599,552 | 668.539727 | N/A | N/A | N/A |
| Fleet after (partial) | 9 | 1,024 | 108,763,264 | 19.421404 | 113.78 | 12,084,807 | 2.157934 |

Alpha's observed local changes: calls/PR **+145.2%**, cache-read/PR **+53.1%**,
recorded list USD/PR **+80.4%**. Absolute Sol usage fell, but merges fell
proportionately more. Beta/Gamma after rates are N/A because coverage of their
client and reviewer sources is unproven. Before-to-after change is N/A for
Beta/Gamma (no comparable before source) and Fleet (no pre-Fleet baseline).

Project Mains reported workload shifts that make these windows hard to compare:

- **Alpha (336 → 107 merges):** On Nov 10, the owner paused new content, which
  had produced many small PRs, and shifted lanes to large, stacked engine
  changes (split ceiling approximately 15,000 lines or 100 files). PRs also
  received an independent pre-publication review and multi-round bot review.
  Larger PRs and more review can raise calls/PR without indicating lower
  efficiency. Alpha also changed build admission to two slots of eight jobs.
- **Beta:** The Sol pin switched from 6.1 to 6.2 on Nov 18. Several infrastructure
  PRs, including gateway moves, took multiple iterations.
- **Gamma:** Main ran four to six local review rounds on several high-risk PRs.
  Builder lanes parked late in the window to preserve Codex review quota,
  limiting merge throughput.

Fleet changed GitHub read budgeting and snapshot reuse, another potential
throughput confounder. These ratios do not control for PR size, idle time, or
the mix of work, and they exclude partly remote review-bot usage. Model mix
and runtime/catalog pricing also make recorded list USD unsuitable as a
savings estimate.

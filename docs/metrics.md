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
Collect only on/after Nov 20 00:00Z. A day-zero checkpoint is not this after
measurement. TASK-18 stays open until the after window closes and comparable
coverage is established, or limitations are explicitly reported.

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
Unmapped calls are counted separately, never silently assigned to a project.
Keep host paths, real project bindings and raw source evidence private.

Fleet's current GitHub repository was created on 2026-11-12T11:55:59Z after
repository recreation. It returns zero merges for earlier windows; this is
**missing historical denominator coverage**, not proof of zero delivery.
Imported Git history does not reconstruct GitHub `mergedAt` events. No per-PR
baseline for Fleet is claimed.


## Tally contract

- Read-only UTF-8 `.jsonl` and `.jsonl.gz`; optional leading stock `title` records
  precede the `session` header. Non-session files in mixed scratch trees are
  counted and skipped. Invalid records inside a session refuse the measurement;
  no partial aggregate is emitted on a parse/GitHub error.
- Calls are recorded assistant `message` events and internal `model_usage` events
  (for example title generation), reported separately by event type, provider and
  model. Sum both event types for total recorded model calls. Recorded error
  calls count; internal HTTP retries with no usage event remain unobservable.
- Union copied logs by `(session id, event id)`; divergent copies refuse. Forks
  can receive a new session ID while retaining original entries. Exact inherited
  event ID/timestamp/model/provider/usage fingerprints count once globally;
  cross-project attribution conflicts refuse rather than guessing an owner.
- Input, output, cache-read and cache-write are the four recorded token counters.
  Missing usage has an explicit counter, not fabricated zeros. Missing cost is
  counted as unpriced. List-equivalent USD sums recorded `usage.cost.total` only:
  no repricing, subscription-spend or invoice claim. Historical omp/catalog and
  service-tier differences can change this estimate.
- GitHub GraphQL paginates **repository** merged PRs, then filters exact
  `mergedAt` endpoints. There is no search-result cap. Count every merge,
  including docs/reverts, once by PR number. Stop only when there are no more
  pages or descending `updatedAt` is older than START (`mergedAt <= updatedAt`).
  Concurrent updates can move pagination boundaries: retain collection time and
  investigate differing counts on a later rerun.
- Per-PR rates default to **null/N/A**. `--complete-project NAME` is an explicit
  operator assertion of independently verified matching source coverage; never
  pass it merely because the tally succeeded. Missing usage suppresses rates;
  unpriced calls suppress cost/PR. Missing/zero PR denominator gives N/A.
- Once BOTH windows have matching complete scope, calculate
  `100 * (after_per_pr / before_per_pr - 1)`; unknown/zero before rate gives N/A.
  Recorded totals from an incomplete source remain useful lower bounds, not a
  comparable baseline or proof of savings.

## Reproduce

Python >=3.11; read-only authenticated `gh` access for PR counts. No provider
requests, reset spending, uploads, service changes or other-project writes.
Run from the source checkout (no shared installation required):

```sh
python3 -m fleet.metrics \
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

Then run `python3 -m fleet.metrics --manifest /private/task18-sources.json
--start 2026-11-13T00:00:00Z --end 2026-11-20T00:00:00Z`.
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

Collected 2026-11-12T23:17:10Z for Oct 30–Nov 6; Gamma's Nov 3–10 run
collected at 23:19:24Z. Source inventory: 11,262/11,263 files respectively,
11,256/11,257 distinct session IDs; two non-session files skipped. No divergent
copies or missing usage found. Zero duplicate events in these windows.
The one additional inventory file appeared during collection, outside the
completed windows. Recorded runtime versions: 18.13.1 for Oct 30–Nov 6;
18.13.1/18.17.0/18.18.1 for the wider Gamma diagnostic.

| Project / approved before interval | Current-repo merged PRs | Observed usage / per-PR coverage |
|---|---:|---|
| Alpha / Oct 30–Nov 6 | 336 | Model totals below; per-PR N/A: complete client coverage unproven |
| Beta / Oct 30–Nov 6 | 157 | No local events; per-PR N/A: Mac usage unavailable |
| Gamma / Nov 3–10 | 0 | No local events; per-PR N/A: no pre-adoption source |
| Fleet / Oct 30–Nov 6 | 0 | No local events; per-PR N/A: no baseline, historical merge events unavailable |

**No local events never means zero actual usage.** No BEFORE per-PR rate or
percentage change is accepted.
The Oct 30–Nov 6 run also observed 27,236 unattributed calls outside the supplied
project prefixes; they are excluded, not silently assigned to Alpha.

Alpha's complete *observed* model breakdown below sums assistant and internal
events. All recorded cost rows were priced; 109 Sol error calls are included.

| Provider / model | Calls | Input | Output | Cache-read | Cache-write | Recorded list USD |
|---|---:|---:|---:|---:|---:|---:|
| anthropic / claude-opus-5-5 | 9,985 | 26,472 | 4,791,283 | 4,489,974,949 | 22,470,799 | 1,173.6929298 |
| anthropic / claude-sonnet-5-5 | 29,045 | 5,366,832 | 19,002,054 | 2,480,010,360 | 96,559,917 | 834.994908 |
| openai-codex / gpt-6.1-sol | 120,120 | 269,128,224 | 81,172,844 | 22,552,713,344 | 0 | 3,605.2562224 |

## Day-zero checkpoint — six completed days, NOT AFTER

Main corrected the cutoff in `msg_b95c9f064f00`: use the completed
`[2026-11-06T00:00:00Z, 2026-11-12T00:00:00Z)` checkpoint, not a future midnight.
Collected **2026-11-12T23:19:34Z**, before the after window opened.
Source inventory: 11,263 files, 11,257 session IDs; 33 copied/inherited usage
events deduplicated, two non-session files skipped, 130 unattributed calls,
zero missing-usage events and zero unpriced recorded calls. Runtime events
reported 18.13.1, 18.17.0, 18.18.1 and 18.18.6.

| Project | Exact current-repo merged PRs in checkpoint | Per-PR rates |
|---|---:|---|
| Alpha | 183 | N/A: full client/bot coverage unproven |
| Beta | 198 | N/A: full client/bot coverage unproven |
| Gamma | 66 | N/A: full client/bot coverage unproven |
| Fleet | 0 | N/A: pre-recreation GitHub merge history absent |

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
| Alpha / openai-codex / gpt-6.1-sol | 129,206 | 250,052,750 | 60,359,707 | 13,421,694,208 | 0 | 2,445.871991 |
| Beta / anthropic / claude-fable-5-2 | 97 | 198 | 263,654 | 26,346,682 | 808,809 | 28.758024 |
| Beta / anthropic / claude-opus-5-5 | 9,271 | 24,666 | 5,684,875 | 3,142,531,999 | 14,459,833 | 857.981228 |
| Beta / anthropic / claude-sonnet-5-5 | 6,306 | 4,051,812 | 951,368 | 19,806,819 | 1,481,025 | 25.522086 |
| Beta / openai-codex / gpt-6.1-sol | 30,776 | 52,623,648 | 8,994,010 | 2,986,253,312 | 0 | 493.812727 |
| Gamma / anthropic / claude-fable-5-2 | 6,314 | 454,303 | 5,371,121 | 745,307,800 | 20,447,650 | 694.703224 |
| Gamma / anthropic / claude-opus-5-5 | 2,185 | 291,603 | 1,114,460 | 688,741,961 | 3,426,472 | 188.615780 |
| Gamma / openai-codex / gpt-6.1-sol | 7,526 | 19,080,882 | 2,035,129 | 589,882,112 | 0 | 117.501265 |

This six-day checkpoint is deliberately not compared to a seven-day before
window. **After totals, after per-PR rates and measured change are not available
until the Nov 13–20 interval has completed; no savings claim.**

## Nov 20 handoff

Run on/after **2026-11-20T00:00:00Z**, from the published source checkout:

```sh
python3 -m fleet.metrics --manifest /private/task18-sources.json \
  --start 2026-11-13T00:00:00Z --end 2026-11-20T00:00:00Z
```

TASK-18's private Pad handoff binds the actual mode-0600 manifest path. Refresh
custom session roots and cwd aliases first; retain old roots and investigate any
that disappeared rather than dropping them silently. Include bot-container and
other-host sources only through an approved read-only inventory. Missing
historical sources cannot be repaired by inventing totals or reconstructing
`mergedAt` from Git commit times. Keep rates/change N/A until coverage is proven.

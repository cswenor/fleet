# TASK-18: usage measurement procedure

Status: preparation only. **No measured savings.** TASK-18 remains open.
Main approved this docs-only, incomplete-source preparation in `msg_560fc2bfe0d0`;
custom-session ingestion is not authorized in this PR.

## Sources and coverage gate

Baseline source: **owner-provided, unverified**. The [roadmap baseline](ROADMAP.md#task-18--success-metric-codex-usage-per-merged-pr)
reports seven days ending 2026-11-06, but its exact UTC boundaries, source class,
host/client coverage, error counting and price basis are unknown. Its ~182 PRs
are an estimate, not an exact denominator. Do not derive a baseline per-PR rate
or percentage change from that estimate.

Observed stock tools: omp 18.17.0 and gh 2.97.0, checked 2026-11-07:

- `omp stats --json`: syncs local session files into `~/.omp/stats.db`, then
  returns aggregate statistics. The observed default output spans approximately
  the latest day, not an arbitrary exact seven-day interval. Use the indexed
  `messages` table below for exact millisecond boundaries, not hourly rollups.
- `omp stats --help`: only JSON/summary and dashboard host/port flags; no
  session-directory or date-range option. `omp config list` exposes
  `session.worktreeScope` but no stats sessions-root setting. Do not change
  configuration or relocate live sessions to make this measurement work.
- **Known hole:** the local index does not include Fleet `--session-dir`
  launch logs under `~/.local/state/fleet/*/sessions-*/`. After sync, zero
  `messages.session_file` rows referenced that Fleet state root; the latest
  indexed Fleet-folder message was 2026-11-06T19:58:49Z despite later launches.
  Dedicated Main/reviewer dirs and other hosts/clients also require inventory.
  Local-index totals are not account-wide provider usage.
- `omp usage --help`: live limits, hourly limit history (`--history --days N`)
  and client burn (`clients --days N`) are different surfaces, with no exact UTC
  range flag shown. Limits are not call/cache-read/list-cost totals. Do not run
  `accounts`, print raw usage JSON, or assume `--redact` removes identifiers:
  help describes shortened unique prefixes, not identity-free output.
- Candidate account-wide sources: provider usage dashboard exports and
  auth-broker history. **Owner question:** which source produced the baseline,
  and can it export these exact interval/model fields without identities?
  Neither candidate has been established as a comparable aggregate source here.

**Before a comparable result:** Main must resolve either a stock option to index
extra session dirs or a separate small ingestion task, and obtain baseline
source provenance from the owner. Ingestion alone cannot make a local index
comparable to an unknown account-wide baseline. Inventory every host/client and
session root; establish coverage, duplicates and omitted internal retries.
Changing provider/account mix must be reported without publishing identities.
Missing data is unknown, never zero. Database minimum/maximum timestamps do not
prove completeness.

## Exact interval and field contract

Use UTC half-open intervals `[START, END)`: include START, exclude END. Require
explicit `Z` and use seven complete days (604800 seconds) for the final comparison.
The baseline's phrase “ending 2026-11-06” does not establish either endpoint.
The Nov 13 checkpoint must resolve the intended after interval with Main; do not
quietly choose whether Nov 13 is the start or collection date. The example below
is a **candidate diagnostic** ending Nov 13, not an approved comparable window.
If the after window starts Nov 13, use Nov 13–20 and collect only after Nov 20.
Never include a running partial day or convert local calendar days across DST.

- Sol calls = indexed assistant-message rows with `model = gpt-6.1-sol`, grouped
  by provider. Includes recorded error rows; not necessarily every HTTP attempt
  or broker retry. The index deduplicates `(session_file, entry_id)`, not copies
  under different paths. Do not combine copied databases by summing them.
- Cache-read = sum of `cache_read_tokens`; uncached input = `input_tokens`;
  output = `output_tokens`; cache-write = `cache_write_tokens`.
- Context/call proxy = `(input + cache_read + cache_write) / calls`. Report
  cache-read/call separately; neither is the configured context-window size.
- List-equivalent USD = sum of recorded `cost_total`, with components and
  `cost_unpriced` count retained. It is an estimate recorded by omp, not billed
  subscription spend. Unpriced rows make the cost incomplete. Preserve model,
  provider, tool version and price basis; baseline prices are unverified.
- Exact merged PRs = unique GitHub PR numbers whose `mergedAt` falls in the same
  interval, separately for `example-org/project-alpha`, `cswenor/fleet` and
  `example-org/project-beta`. Count every merge, including docs/reverts, unless both
  windows use a separately documented identical exclusion policy.
- Per-PR rates require **matching coverage**: calls / merged PRs, cache-read /
  merged PRs, list-cost / merged PRs. Zero denominator yields N/A, not zero or
  infinity. Do not divide all-client usage by Project Alpha-only merges. Establish
  repo attribution before reporting repo-specific usage rates.
- Once both windows pass coverage/provenance gates, measured change is
  `100 * (after_rate / before_rate - 1)`; a zero/unknown baseline yields N/A.
  Report workload, PR size/scope, rollout timing, review overhead and provider
  mix differences. Even comparable arithmetic does not establish causality.

## Reproduce the local-index diagnostic

Prerequisites: Python >=3.11, authenticated read-only `gh` access to all three
repos, the same local omp profile/home, and a completed interval. No resets,
provider requests, credential exports or service changes. `omp stats` writes its
own derived index during synchronization; the measurement opens SQLite read-only.

Run `omp stats --help`, `omp usage --help` and inspect only relevant keys from
`omp config list` when upgrading. Run this single shell block from the checkout;
replace only START/END. It prints allowlisted aggregates, never session paths,
prompts, errors, tokens used for authentication, or provider/account identities.
Do not publish raw `omp stats --json`, database dumps or subprocess stderr.

```sh
START=2026-11-06T22:00:00Z END=2026-11-06T23:00:00Z python3 - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"{args[0]} failed; inspect privately, no partial result")
    return result.stdout


def utc(text):
    if not text.endswith("Z"):
        raise SystemExit("UTC Z endpoints required")
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


start, end = os.environ["START"], os.environ["END"]
a, b = utc(start), utc(end)
if not a < b <= dt.datetime.now(dt.timezone.utc):
    raise SystemExit("require a completed, increasing interval")
versions = {name: run([name, "--version"]).splitlines()[0]
            for name in ("omp", "gh")}
run(["omp", "stats", "--json"])  # sync; do not publish raw folder aggregates
with sqlite3.connect((Path.home() / ".omp/stats.db").as_uri() + "?mode=ro",
                     uri=True) as db:
    db.row_factory = sqlite3.Row
    usage = [dict(row) for row in db.execute("""
        SELECT provider, model, count(*) AS calls,
               sum(CASE WHEN stop_reason='error' THEN 1 ELSE 0 END) AS errors,
               sum(input_tokens) AS input, sum(output_tokens) AS output,
               sum(cache_read_tokens) AS cache_read,
               sum(cache_write_tokens) AS cache_write,
               sum(cost_total) AS list_usd, sum(cost_unpriced) AS unpriced,
               sum(cost_input) AS input_usd, sum(cost_output) AS output_usd,
               sum(cost_cache_read) AS cache_read_usd,
               sum(cost_cache_write) AS cache_write_usd,
               min(timestamp) AS first_ms, max(timestamp) AS last_ms
        FROM messages WHERE timestamp >= ? AND timestamp < ? AND model = ?
        GROUP BY provider, model ORDER BY provider, model
        """, (int(a.timestamp()*1000), int(b.timestamp()*1000), "gpt-6.1-sol"))]
for row in usage:
    row["context_per_call_proxy"] = (
        row["input"] + row["cache_read"] + row["cache_write"]) / row["calls"]
    row["cache_read_per_call"] = row["cache_read"] / row["calls"]

query = """query($owner:String!,$name:String!,$endCursor:String) {
  repository(owner:$owner,name:$name) {
    pullRequests(first:100,after:$endCursor,states:MERGED,
                 orderBy:{field:UPDATED_AT,direction:DESC}) {
      nodes { number mergedAt updatedAt }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""
merges = {}
for repo in ("example-org/project-alpha", "cswenor/fleet", "example-org/project-beta"):
    owner, name = repo.split("/")
    cursor, numbers = None, set()
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={query}",
                "-f", f"owner={owner}", "-f", f"name={name}"]
        if cursor:
            args += ["-f", f"endCursor={cursor}"]
        payload = json.loads(run(args))
        if payload.get("errors") or not payload.get("data", {}).get("repository"):
            raise SystemExit("GitHub incomplete/error; no partial result")
        page = payload["data"]["repository"]["pullRequests"]
        nodes = page["nodes"]
        numbers.update(n["number"] for n in nodes if a <= utc(n["mergedAt"]) < b)
        # mergedAt <= updatedAt: older updates cannot contain an in-window merge.
        if not page["pageInfo"]["hasNextPage"] or any(
                utc(n["updatedAt"]) < a for n in nodes):
            break
        cursor = page["pageInfo"]["endCursor"]
    merges[repo] = len(numbers)

print(json.dumps({"status": "LOCAL INDEX / INCOMPLETE / NOT COMPARABLE",
                  "start": start, "end_exclusive": end, "versions": versions,
                  "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "usage": usage, "merged_prs": merges}, indent=2))
PY
```

For the Nov 13 **diagnostic checkpoint**, rerun that block with
`START=2026-11-06T00:00:00Z END=2026-11-13T00:00:00Z` only after END; this does
not bypass the unresolved after-window/source decisions. The GraphQL query
paginates repository merges rather than GitHub search (no 1,000-result cap or
search-index lag). Concurrent repository updates can move pagination boundaries;
repeat after the window closes and investigate differing counts before acceptance.

Retain the aggregate JSON with collection time, exact UTC endpoints, source class,
versions, model/provider grouping, included repo set, coverage exceptions and
price basis on TASK-18. Keep any raw source evidence private; only aggregates
belong in Git/Pad. Never include account IDs, even shortened ones.

## Exercised dry run — LOCAL INDEX / INCOMPLETE / NOT COMPARABLE

Window: **2026-11-06T22:00:00Z <= timestamp < 2026-11-06T23:00:00Z**.
Local default-session index, Sol / openai-codex only; collection 2026-11-07.
No repo attribution or complete-account-burn claim. Dedicated launch sessions
are absent. These numbers are procedure proof, not an after measurement.

| Indexed usage metric | Observed |
|---|---:|
| Calls (including 1 recorded error) | 165 |
| Uncached input tokens | 320,873 |
| Cache-read tokens | 21,598,976 |
| Output tokens | 99,409 |
| Cache-write tokens | 0 |
| Context/call proxy | 132,847.57 |
| Cache-read/call | 130,902.88 |
| Recorded list-equivalent USD | 3.7957336 |
| Unpriced rows | 0 |

| Repository | Exact merged PRs |
|---|---:|
| example-org/project-alpha | 3 |
| cswenor/fleet | 7 |
| example-org/project-beta | 0 |

GitHub GraphQL returned one page per repo; bounded search independently returned
the same counts. Per-PR usage and baseline change: **N/A**, because source
coverage and baseline provenance are unresolved. No reduction/increase claimed.

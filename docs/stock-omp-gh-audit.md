# Stock-first omp / gh audit (TASK-32)

Observed 2026-11-06 in the assigned fleet lane: omp **18.17.0**, gh **2.97.0**;
Orca installation context **1.4.242**. No shared deployment, runtime restart,
reset spending, extra worktree or Project Alpha mutation. Verdicts concern equivalent
behavior, not whether a command has a similar name. Orca lifecycle is TASK-31;
project skill/overlay installation is TASK-33.

## Function decisions

| Fleet function | Stock candidate | Real evidence | Verdict | Gap / replacement risk |
|---|---|---|---|---|
| `watcher.watch`: whole-PR event wait | `gh pr checks --watch`, `gh run watch`, omp `github.run_watch` | A/B below: merged PR19 produces passing checks without a merge event; one completed Actions run returns success. | **Keep** whole-PR watcher; use stock watches for CI-only waits. | Checks are not reviews, comments, head changes, conflicts, closure or associated merge-group failures. Queue membership alone is not an existing watcher wake condition. Deleting the wrapper would narrow the accepted wake contract. |
| `github.collect`, `events`: project snapshot and semantic event identities | `gh api`, `gh pr view`, `gh run list` | A/B retrieve actual checks, run and merge state separately; implementation already delegates network reads to `gh`. | **Keep** composition/diffing, reuse stock transport. | One stock watch cannot populate the 24h project snapshot or deduplicate semantic changes and associated merge-group failures. |
| Independent fresh read-only unadvised Sol-medium review | stock `--config`, `--model @review --thinking medium`, `--tools`, fresh print/session directory | C: role is Sol-medium, advisors off, but ordinary project retry allows Opus fallback. Project Beta's existing stock overlay only disables advisor. | **Replace** improvised reviewer setup with committed stock overlay and one documented invocation, in the next PR. | Must disable fallback/auto-resume/prewalk, restrict tools and retain actual model/thinking plus final-head evidence. A role name or requested argv alone is not proof. No new reviewer runner/bot. |
| `quota.collect` / `evaluate`: pre-launch pooled admission and freshness | `omp usage --json --provider …`; stock retry reserve policy | D: installed JSON returns three reports; stock policy keys exist. Fleet already consumes this data and configures the native per-builder reserve. | **Keep** pre-launch policy; retain native usage and request-time reserve. | Per-account request routing is not pooled launch admission, stale-data handling, disk/cap/occupancy checks or known-vs-unknown PARK authority. |
| `governor.advance`: priority PARK / WIP / Pad handoff | native fail-closed reserve, broker account selection | D: native policy description addresses accounts within reserve; broker command inventory manages credentials. | **Keep** cooperative coordinator policy. | Neither candidate supplies Pad priority, one outstanding PARK, durable recipient retries, exact incarnation/exit reconciliation or fresh-context resume. No forced stop substituted. |
| Saved-reset consent | `codexResets.autoRedeem`, `claudeResets.autoRedeem` | D: effective values were `yes` and `unset`, respectively; no reset command invoked. Main subsequently disabled both globally. | **Keep** owner-only consent; pin no-spend in reviewer and builder overlays in the next PR. | Native auto-redemption can conflict with recorded-before-spend policy. Main explicitly expanded scope in `msg_97b0dd465d6a`; no other global changes authorized. |
| `supervisor.cycle`, relays and central push | omp async bash/services, `omp ps`, GitHub watches | Installed `omp --help` lists process supervision; B shows CI watch completion. Source composes project observations, owner resolution, durable delivery and HTTPS push. | **Keep** policy/relay composition; stock async waits remain the execution primitive. | Process completion is not branch-owner routing, deduped retryable Orca/Pad delivery or independent always-on project observation. Send is enqueue, not wake proof. |
| `central.Store` / `Handler`, `container.main` | `omp auth-broker`, `auth-gateway`, collab | D/E command inventory: broker manages credentials; gateway forwards model requests. No project-snapshot dashboard command surfaced. | **Keep** sanitized multi-project status store/server and container entrypoint. | No equivalent demonstrated. Broker usage/credential services are not the identity-free PR/queue/builders/disk history dashboard; do not expose credentials to approximate it. This is bounded inventory evidence, not proof no extension could exist. |
| `dispatcher.propose`, `check_launch` | `pad project ready`, `project next`, `item list` / `deps` | F: both readiness commands returned TASK-10/13/16, all explicitly described as in-progress. | **Keep** eligibility/ownership/admission composition; stock Pad queries already supply data. | Current ready/next omit the open backlog. They do not replace nearest-active-PLAN ancestry, workspace status schema, terminal blockers, exact branch/receipt ownership and locked launch recheck. |
| `service._install`: per-project supervisor | plain systemd user unit | E: installed fleet unit is active/running, `Restart=on-failure`, `UMask=0077`, `NoNewPrivileges=yes`; `systemd-analyze --user verify` exited 0. | **Keep** small config-to-stock-unit installer. | Already uses systemd, not a competing process manager. Static copy/paste loses configured paths, safe escaping and private EnvironmentFile checks. Verification does not prove logout/reboot persistence or authorize restarts. |

## Reproducible observations

### A — checks are narrower than PR events

```sh
gh pr checks 19 --repo cswenor/fleet --watch
gh pr view 19 --repo cswenor/fleet --json state,mergedAt,mergeStateStatus,reviews
```

Checks printed `test pass 14s` (Actions run
[42254070242](https://github.com/cswenor/fleet/actions/runs/42254070242)), exit 0.
The separate view returned `state=MERGED`, `mergedAt=2026-11-06T16:38:32Z`,
`mergeStateStatus=UNKNOWN`, `reviews=[]`. Passing checks alone did not report merge.
This demonstrates a contract gap, not a live transition/review/queue drill.
`gh pr checks --help` exposes check fields and pending exit 8; no PR event fields.

### B — single-run watch

```sh
gh run watch 42240339663 --repo cswenor/fleet --exit-status
```

Returned `Run CI (42240339663) has already completed with 'success'`, exit 0
([run](https://github.com/cswenor/fleet/actions/runs/42240339663)).
Useful for CI-only waits, not an end-to-end PR event watcher.
The installed omp `github.enabled` setting was false; a fresh read-only probe
reported `xd://github` unavailable. Bundled `omp://tools/github.md` documents
`run_watch` as Actions-only, with explicit run or commit selection. A second
fresh probe with `github.enabled=true` and read/write tools still reported no
mounted GitHub device; no native `run_watch` execution is claimed. The installed
`gh run watch` was exercised directly instead. omp async shell completion already
supports these finite commands without model-turn polling.

### C — reviewer configuration

```sh
omp config get modelRoles --json
omp config get advisor.enabled --json
omp config get task.agentAdvisor --json
omp config get retry.modelFallback --json
omp config get retry.fallbackChains --json
omp config get autoResume --json
```

Observed `review=openai-codex/gpt-6.1-sol:medium`, advisor false,
reviewer/security-reviewer advisor overrides off, autoResume false, but
modelFallback true and Sol's fallback chain contains Opus-medium.
`--config`, `--tools`, `--session-dir`, `--no-prewalk`, `--print` are advertised
by installed `omp --help`. Stock overlays and role resolution suffice; a custom
review launcher is unnecessary. Main approved this next-PR replacement in
TASK-32 thread reply `msg_47fa480d3624`; actual review receipts belong on Pad/PR.

### D — stock quota and reset policy

```sh
omp usage --help
omp usage --provider openai-codex --json
omp config list --json
omp auth-broker --help
```

Usage was captured in memory and only structural/anonymous aggregates printed:
root keys `generatedAt,reports,accountsWithoutUsage,disabledCredentials,capacity`;
three reports, each with one limit; fetchedAt is epoch milliseconds.
At inspection their ages were 283/164/193 seconds. No account/provider metadata,
identities or credentials retained here. Config schema exposes
`retry.usageReservePct`, `retry.usageReservePolicy`, both reset autoRedeem keys.
Effective ordinary-session reserve was 0/auto, Codex resets yes, Claude unset.
These are inspection facts, not permission to change global policy or spend.
Never run `omp usage reset TARGET` without recorded owner approval.

### E — existing stock service manager and command inventory

```sh
omp --help
omp auth-broker --help
systemctl --user show fleet-cswenor-fleet.service \
  -p ActiveState,SubState,Restart,NoNewPrivileges,UMask
systemd-analyze --user verify ~/.config/systemd/user/fleet-cswenor-fleet.service
```

Observed active/running, on-failure, yes, 0077; verify exit 0 with no diagnostics.
No service environment/credentials printed and no service mutated. Command
inventory exposes usage, broker/gateway, collab, stats and process management,
not a Fleet project dashboard. Central equivalence remains unproven; retain it.

### F — stock Pad readiness mismatch

```sh
pad --workspace fleet --format json project ready
pad --workspace fleet --format json project next
pad --workspace fleet item --help
```

Both returned exactly TASK-10, TASK-13, TASK-16 with reasons beginning
`In-progress task in active plan`. Item inventory exposes list/search/deps,
not a separate query command. Fleet already uses these native data operations;
replacing its readiness policy with ready/next would change eligible work.

## Cutover boundary

Audit first; then only approved equivalent replacements in small separately
reviewed, green-CI PRs. No watcher/governor/central/dispatcher/service deletion is
justified by this evidence. TASK-33 should reuse the committed stock review
overlay and documented workflow rather than adding a runner. Main announces any
shared-checkout behavioral deployment before fast-forwarding; this audit does
not deploy changes or grant rollout/reset budget authority.

# Fleet

Per-project Orca + omp fleet supervision. Generic project configuration; no model
calls in collection or event diffing. Requires Python 3.11+, PyYAML 6, `gh`, `omp`,
and a running Orca runtime with its CLI.

## Install and inspect

```sh
pipx install .
cp fleet.example.yml /path/to/project/fleet.yml
# Fill in the checkout, Orca repo id, coordinator run/terminal, and lane slots.
cd /path/to/project
fleet status
fleet status --json
fleet may-start --json
```

Without pipx, use a virtual environment and `pip install .`. On Debian install
`python3-venv` first. To run from source with system PyYAML:
`python3 -m fleet.cli --config /absolute/path/fleet.yml status`.

`fleet.yml` version 1 is illustrated in `fleet.example.yml`. `builder.model` is
the launch selector, including `@role` if desired; `resolved_model` is the exact
provider/model expected at runtime. `thinking` is explicit and defaults to medium.
Slots are absolute checkout paths. The main checkout is not a stray lane.
Only unique normalized PR branch → Orca worktree → connected omp terminal mappings
have owners. Ambiguous mappings never select the first terminal.

Status contains last-24h PR timestamps/time-to-merge, default-branch commits,
merge queue/recent merge-group failures, builder models/state, quota, and disk.
Unknown collectors are explicit errors, not zero activity. Model detection uses
Orca provider projection when present, otherwise the latest OMP footer; it is not
proof of thinking level or model pinning after a restart.

Quota aggregates selected-provider account capacity by shared window, excluding
tier-specific limits. Each window must remain above the reserve percentage.
Missing/stale usage fails closed. Account identities, provider metadata and
credentials are never persisted in the snapshot. `may-start` exits 1 when quota,
disk, builder cap, or incomplete collection prevents a launch.

## Development

```sh
python3 -m unittest discover -s tests -v
```

TASK-2687: initial read-only collectors, parsers, semantic diff, status CLI and
quota admission. This command set does not mutate Orca or GitHub.

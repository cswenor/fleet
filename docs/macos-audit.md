# TASK-36: macOS portability audit

Phase A scope: audit, portable service installation/discovery, explicit safe
refusals/partial capture, Linux regression proof and Linux/macOS CI. Phase B
requires owner-approved access to an actual Mac. No Mac access, installation,
shared runtime restart or production deployment is authorized by this audit.
Line numbers below refer to the pre-implementation TASK-36 source.

This historical audit is complete; subsequent owner-authorized phase B runtime
acceptance and cleanup receipts are in the
[roadmap](ROADMAP.md#task-36--macos-host-support). Its original boundaries below
do not grant rollout authority.

| Source | Current assumption | Stock macOS equivalent / limitation | Plan |
|---|---|---|---|
| `fleet/service.py:12-16,40-91` | systemd user units, daemon-reload/enable/restart/is-active | User LaunchAgent under `~/Library/LaunchAgents`; `launchctl bootstrap gui/UID`, `bootout`, `print` | Dispatch only service installation by platform; retain Linux unit behavior; require observed running state/PID, not plist existence. |
| `fleet/service.py:19-37,63-68` | systemd reads a private EnvironmentFile; central HTTP readiness | launchd has EnvironmentVariables but no EnvironmentFile directive | Never copy secrets into plist/argv/logs; read a private file at process startup through a dedicated entry point; retain HTTP readiness. Document supported file syntax explicitly. |
| `fleet/service.py:44-58` | CLI from PATH or source shim in `~/.local/bin`; inherited PATH | POSIX executable shim and launchd EnvironmentVariables PATH | Keep shim; explicitly preserve executable search PATH, including Homebrew when present in caller PATH; do not inherit caller tokens. |
| `fleet/restart.py:19-57` | `/proc` exact argv/exe/cwd/environment key plus foreground process group and stable start ticks | `ps -p PID -o ...` and `lsof -a -p PID -d cwd` expose partial process evidence; flattened argv/environment do not provide the same unambiguous exact binding | Preserve Linux proof. Explicitly refuse automatic retirement on macOS until equivalent exact routing/argv/incarnation evidence is proven; never infer ownership from comm, cwd or idle alone. |
| `fleet/orca_capture.py:14,26-30,56-138` | Linux procfs UID/status/exe/fd/session/start identity | Stock ps/lsof can inventory partial metadata, not the present exact proof | Return explicit partial capture, no process-completeness or exit claim on macOS; do not substitute weaker evidence silently. |
| `fleet/orca_capture.py:179-186,191-192,217-219` | systemctl service state, procfs boot ID, Linux unit names | launchctl print per-user label; `sysctl kern.boottime` is not a Linux boot UUID | Collect allowlisted launchd service state; mark unavailable boot/process identity explicitly and return nonzero partial status rather than crash. Keep existing CLI unit identifiers if mapping to labels is unambiguous. |
| `fleet/config.py:97-99` | config/env override then only `orca-ide` on PATH | Installed macOS Orca CLI or explicit ORCA_CLI_COMMAND/config path | Explicit config and env take precedence; discover macOS `orca` only on Darwin; never select bare Linux `orca` (screen reader). Do not guess an app bundle layout. |
| `fleet/config.py:101-102`; `fleet/central.py:98-100` | XDG_STATE_HOME or `~/.local/state` | macOS accepts these POSIX paths; native Library paths are optional conventions | Retain existing state locations and overrides: changing them would lose receipt/database discovery. Document explicit paths rather than migrate state. |
| `fleet/health.py:28-32,81-124` | conservative shell prompt regex and Orca metadata | Default macOS zsh prompt differs; unknown prompts remain occupied | Document a supported explicit prompt for reusable macOS lanes; do not broaden arbitrary prompt recognition into ownership proof. Orca terminal show on this Linux host exposes incarnation/handle, not PID. Recheck Mac metadata in phase B. |
| `fleet/launcher.py:39-197` | POSIX shell quoting/cd, private files, omp on terminal PATH | POSIX zsh supports these operations | Reuse; require omp/gh/pad in both terminal and service PATH; no new lifecycle wrapper. |
| `fleet/state.py:3,10-15,39-48`; `fleet/snapshot.py:25` | fcntl flock, UID permissions, disk_usage | Available on Darwin/POSIX | No platform layer needed; run existing behavior tests on macOS. |
| `pyproject.toml:9-10`; `.github/workflows/ci.yml:9-18` | Python >=3.11; CI only Ubuntu/Python 3.12 | GitHub macos-latest with setup-python | Add OS matrix, retain Python version and full suite plus actual CLI help smoke. Linux-specific proc fixtures must explicitly select the Linux branch; add native unsupported-path coverage. |
| `README.md`; `.omp/skills/fleet/SKILL.md` | install/recovery examples use systemd and Linux host paths | launchd LaunchAgents and explicit project-local paths | Add macOS onboarding/service controls, private-env contract, retirement/capture limits, and phase-B runtime checklist. Keep the central host central deployment and owner approvals unchanged. |

## Evidence and safety boundary

On the assigned Linux host, `fleet --help` exposes install-service, launch,
restart and capture-related workflow commands; `fleet`, `omp`, `gh`, `pad`,
`ps` and `lsof` resolve. `launchctl` is absent. Read-only `orca terminal show`
returns terminal incarnation/worktree/routing metadata but no foreground PID.
This is source-bound Linux discovery, **not macOS runtime proof**.

Stock reference: [launchd.plist manual](https://www.manpagez.com/man/5/launchd.plist/)
for ProgramArguments, WorkingDirectory, EnvironmentVariables, KeepAlive and
Umask. Installed macOS `man launchctl` and `man launchd.plist` must be checked
in phase B: command failure or missing GUI domain must withhold installation,
not trigger a system-wide daemon or privilege escalation.

Phase B acceptance remains: owner-approved Mac access; real launchd install and
running state, status/may-start and fresh launch with actual Sol-medium session
events, explicit safe restart refusal (or separately reviewed equivalent proof),
private partial capture, and service removal/reinstall recovery. CI is not proof
of an installed Mac service or Orca wake delivery.

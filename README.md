# mac-crash-observability

My 2019 16-inch MacBook Pro has kernel-panicked once or twice a week for about two years, through factory
resets and clean macOS installs. Apple writes a detailed report for every crash, but reading them one at a time
never showed a pattern. So I built a pipeline that collects every report the Mac writes, normalizes them into one
event stream, and charts them in **Grafana** backed by **Loki**.

**What it surfaced in the first week of data:**

- 2 kernel panics 19 minutes apart, in **unrelated** kernel subsystems (GPU acceleration, then the filesystem layer).
  The second came 11 minutes after a fresh boot.
- 60 **identical** APFS hash mismatches on the sealed system volume: the same 16 KB block read back the same
  wrong bytes every time.
- Crashes in five different system processes that faulted **inside that exact block**.
- 26 kills of Apple's own processes for an invalid code signature on a page of code.
- Apple Diagnostics, run the same afternoon: "No issues found."

That combination (intermittent faults across unrelated parts of the system, while on-demand tests pass) is
exactly what a single report or a one-off hardware test misses, and why the history matters.

## Who it's for

| Who | What they get |
|---|---|
| Anyone whose Mac crashes, freezes or restarts on its own | A timeline that separates "one misbehaving app" from "the system itself is failing", plus evidence to bring to a repair |
| Me, ongoing | A before/after record: does a repair, reinstall or macOS update actually stop the crashes? |
| People learning observability | A small but complete pipeline on real data (collect → normalize → scrub → ship → store → visualize), documented line by line in [`observability/README.md`](observability/README.md) |

It isn't much use on a healthy Mac, which writes very few reports worth charting.

## How it works

```
 ┌──────────────────────────── on the MacBook ────────────────────────────┐   ┌──── Mac mini / Raspberry Pi ────┐
 launchd (hourly) ─► update.sh ─► data/raw/ ─► build_index.py ─► index.ndjson ─► push_to_loki.py ──HTTP──► Loki ◄── Grafana
```

1. **launchd** (macOS's built-in scheduler) runs `update.sh` every hour and at login.
2. `update.sh` copies new reports from macOS's DiagnosticReports folders and scans the kernel log since its last run.
3. `build_index.py` turns 8 different report formats into one schema, converts times to UTC, and replaces every
   machine identifier with a placeholder.
4. `push_to_loki.py` sends events Loki hasn't seen yet.
5. **Loki** stores them; **Grafana** queries Loki and draws the dashboard.

## The stack

| Layer | Component | What it does here | Why this choice |
|---|---|---|---|
| Schedule | launchd LaunchAgent | Runs `update.sh --scheduled` hourly and at login | Built into macOS, survives reboots, runs a missed job when the Mac wakes. Nothing extra to keep alive on a machine that crashes |
| Collect | zsh + macOS tools (`cp`, `/usr/bin/log`, `pmset`, `ioreg`) | Copies reports, scans the kernel log | Nothing to install; read-only on the system |
| Normalize | `build_index.py` (Python standard library) | One schema, UTC timestamps, identifier scrubbing | Runs on the `python3` that ships with Xcode Command Line Tools; no dependencies |
| Format | NDJSON (one JSON event per line) | The hand-off between collection and shipping | What log pipelines and Loki's push API consume; readable diffs in git; works with `jq` |
| Ship | `push_to_loki.py` | POSTs new events to Loki, remembers what it sent | A one-shot script fits a Mac that may crash mid-run. Grafana Alloy, the standard collection agent, is included as an optional alternative |
| Store | Loki 3.7 | Stores events, indexed by 4 labels | Indexes labels rather than full text, so its footprint suits a Raspberry Pi; queried with LogQL |
| Visualize + alert | Grafana 12.4 | Crash overview dashboard; alerting | Data source and dashboard are provisioned from files in this repo |
| Run | Docker Compose | Starts Loki + Grafana with pinned versions | One file for a Mac mini (Docker Desktop, OrbStack or Colima) or a 64-bit Raspberry Pi |
| Guard | git pre-commit hook | Blocks commits that contain any identifier from `.env` | Public repo, private data |

**Why not Splunk, Datadog or Elasticsearch?** Splunk Enterprise doesn't run on ARM or macOS (only its forwarder
does), so it can't be hosted on a Pi or an Apple-silicon Mac mini, and its free license has no alerting or
logins. Datadog's free plan doesn't include logs. Elasticsearch/OpenSearch work but are far heavier than a few
hundred events need. Grafana Cloud's free tier keeps logs for 14 days, and crash patterns play out over months.
The full comparison, plus what Loki, Grafana and Alloy are, is in
[`observability/README.md`](observability/README.md#5-why-this-stack-and-not-something-else).

## Quick start (on the Mac you want to monitor)

Requirements: macOS, `git`, and the `python3` that ships with Xcode Command Line Tools (`xcode-select --install`).
No pip packages are needed.

```bash
git clone https://github.com/salamshakur/mac-crash-observability.git
cd mac-crash-observability
scripts/setup_env.sh
scripts/install_hooks.sh
scripts/update.sh
scripts/install_schedule.sh
```

In order: create `.env` with this Mac's identifiers; block commits that would contain them; do a first collection
(asks for your password once); collect automatically from now on. Avoid cloning into Documents, Desktop or
Downloads, because macOS privacy protection blocks background jobs there.

Then, on an always-on machine, start Loki + Grafana by following
[`observability/README.md`](observability/README.md#6-run-it-on-a-mac-mini-or-raspberry-pi), and set `LOKI_URL`
in `.env`. Scheduled runs start pushing as soon as it's set.

## Scripts

All scripts live in `scripts/` and run from the repo root. Apart from `install_hooks.sh` (writes `.git/hooks/`)
and `install_schedule.sh` (writes `~/Library/LaunchAgents/`), nothing touches the Mac outside this folder.

| Script | When to run | Reads | Writes | Notes |
|---|---|---|---|---|
| `setup_env.sh` | Once per Mac; `--force` after renaming the Mac | `ioreg`, `scutil`, `hostname`, `sysctl`, `/etc/localtime` | `.env` (mode 600) | Read-only on the system. Masks values in its output |
| `install_hooks.sh` | Once per clone | – | `.git/hooks/pre-commit` | Makes every `git commit` run `check_public.sh --staged` |
| `install_schedule.sh` | Once per Mac | – | `~/Library/LaunchAgents/io.github.mac-crash-observability.update.plist` | Runs `update.sh --scheduled` hourly and at login. `--status`, `--uninstall`; `INTERVAL=1800` for every 30 min. Log: `data/logs/update.log` |
| `update.sh` | Automatically (scheduled), or by hand after a crash | DiagnosticReports folders, `.contents.panic`, kernel log since the last run | new files in `data/raw/` and `data/system-info/kernel-log/`, then runs `build_index.py` | Never overwrites, so re-running is safe. By hand it asks for your password for the T2 copies; `--scheduled` never prompts and skips them. `--push` sends to Loki |
| `collect_system_info.sh` | While investigating (takes minutes) | unified log, `pmset`, `system_profiler`, `diskutil`, `csrutil` | `data/system-info/<timestamp>/`, then runs `build_index.py` | Deep dive: long kernel-log windows, full shutdown-cause history, SSD/GPU/RAM details. Optional arg: log window, e.g. `30d` |
| `build_index.py` | Called by the scripts above | `data/raw/`, `data/system-info/`, `.env` | `data/index.ndjson` | Parses every report type, converts times to UTC, scrubs identifiers. Prints counts per category |
| `push_to_loki.py` | Via `update.sh`, or by hand | `data/index.ndjson`, `LOKI_URL` in `.env` | `data/.loki_pushed` (IDs already sent) | `--dry-run` shows the payload; `--all` resends everything (use on an empty Loki) |
| `check_public.sh` | Automatically before each commit; by hand before pushing | `.env`, files git would publish | – | Exits non-zero and lists files if any identifier is found |

## Repo layout

| Path | What it is | In git? |
|---|---|---|
| `.env.example` | Template of settings and machine identifiers | yes |
| `.env` | Your values, filled by `setup_env.sh` | **no** |
| `scripts/` | Collection, indexing, shipping, scheduling and safety scripts | yes |
| `data/raw/{system,user,bridge}/` | Untouched copies of Apple's reports | **no** |
| `data/system-info/` | Kernel-log scans and `collect_system_info.sh` output | **no** |
| `data/logs/` | Output of scheduled runs | **no** |
| `data/index.ndjson` | Normalized events, identifiers replaced by placeholders | yes |
| `observability/` | Docker Compose stack: Loki, Grafana, optional Alloy, dashboard, study guide | yes |
| `AGENTS.md`, `CLAUDE.md` | Rules for AI coding agents working in this repo | yes |

## Privacy model

Apple's reports embed your **serial number, hardware UUID, macOS username and computer/host names**.
None of these are needed to analyze crashes, and a serial number in a public repo is an invitation for
warranty fraud or Activation Lock social engineering. So:

1. **Nothing identifying is hard-coded.** `setup_env.sh` reads the values on each Mac:

   | `.env` key | Command | Example |
   |---|---|---|
   | `MAC_SERIAL` | `ioreg -rd1 -c IOPlatformExpertDevice` → `IOPlatformSerialNumber` | `C02XXXXXXXXX` |
   | `MAC_HW_UUID` | same → `IOPlatformUUID` | `XXXXXXXX-XXXX-…` |
   | `MAC_USERNAME` | `id -un` | `jane` |
   | `MAC_COMPUTER_NAME` | `scutil --get ComputerName` | `Jane's MacBook Pro` |
   | `MAC_LOCAL_HOSTNAME` | `scutil --get LocalHostName` | `Janes-MacBook-Pro` |
   | `MAC_HOSTNAME` | `hostname` | `janes-mbp.lan` |
   | `MAC_MODEL` | `sysctl -n hw.model` | `MacBookPro16,1` |
   | `MAC_TIMEZONE` | `readlink /etc/localtime` | `America/New_York` |

2. **`build_index.py` scrubs** each of those values (and variants such as `Janes-MacBook-Pro` derived from
   `Jane's MacBook Pro`) out of every field, replacing them with `<SERIAL>`, `<HW_UUID>`, `<USER>`,
   `<COMPUTER_NAME>`, `<HOSTNAME>`. Loki/Grafana see only `HOST_LABEL` (default: the model, e.g. `macbookpro16-1`).
3. **Raw data never leaves your Mac.** `data/raw/` and `data/system-info/` are gitignored.
4. **A pre-commit hook double-checks.** `scripts/check_public.sh --staged` greps what you're about to commit
   for every identifier in `.env` and refuses the commit if it finds one.

## The event index

`data/index.ndjson` has one JSON object per line ([NDJSON](https://github.com/ndjson/ndjson-spec)), sorted by time.
NDJSON rather than one big JSON array because log shippers and Loki's push API work line by line, new
events are just appended lines, and `grep`/`jq` work on it directly.

Every event has `ts` (ISO-8601 UTC), `category`, `source`, `process`, `host`, `file`, `event_id`. Then, per category:

| `category` | Comes from | Key fields |
|---|---|---|
| `kernel_panic` | `.contents.panic` (saved as `*_kernel.panic`) | `panic_subsystem` (first kext in backtrace), `panic_reason`, `exception`, `process`, `uptime_s`, `hibernation_exit_count` |
| `t2_panic_copy` | `ProxiedDevice-Bridge/panic-full-*.ips` | `caused_by`, `bridge_os`. The T2 chip's copy of a macOS panic, so **don't add these to panic counts** |
| `app_crash` | `*.ips` (bug_type 309) | `exception_type`, `signal`, `subtype`, `top_frame`, `faulting_image`, `fault_region`, `fs_pagein_error`, `codesign_invalid`, `responsible` |
| `resource_report` | `*.diag` | `resource` (`cpu usage`, `disk writes`), `detail`, `action_taken` |
| `jetsam` | `JetsamEvent-*.ips` | `largest_process` (killed for memory pressure) |
| `shutdown_stall` | `*.shutdownStall` | binary spindump; open with `spindump -i <file>` |
| `apfs_hash_mismatch` | kernel log | `volume`, `inode`, `offset`, `bytes`, `expected_hash`, `got_hash` |
| `shutdown_cause` | kernel log + `pmset -g log` | `shutdown_cause` code; `shutdown_cause_text` only when macOS printed one |

Handy queries without any dashboard:

```bash
jq -r 'select(.category=="kernel_panic") | [.ts, .panic_subsystem, .panic_reason] | @tsv' data/index.ndjson
jq -r 'select(.category=="app_crash") | .process' data/index.ndjson | sort | uniq -c | sort -rn | head
jq -s 'group_by(.category) | map({(.[0].category): length}) | add' data/index.ndjson
```

## Roadmap

- [x] Hourly collection via launchd
- [ ] Grafana alert rule for kernel panics, provisioned from the repo
- [ ] Battery and thermal history from `pmset -g log`
- [ ] Decode shutdown-stall spindumps into readable stacks

## Using it on your own Mac

Fork it and run the quick start; your identifiers stay in your own `.env`. Report formats change between
macOS versions. If `build_index.py` emits `category: parse_error` events, open an issue with the
(scrubbed) error field.

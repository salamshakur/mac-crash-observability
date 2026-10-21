# mac-crash-observability

Collect every crash and kernel panic report a Mac writes, normalize them into one event stream,
and chart them in **Grafana** backed by **Loki**. The goal is to see patterns (which subsystem panics, which
processes die and how, whether storage reads are failing) instead of reading reports one at a time.

Built while diagnosing a 2019 16" MacBook Pro (MacBookPro16,1) that kept kernel-panicking across clean macOS
reinstalls. That investigation is in [`docs/investigation-2026-10-21.md`](docs/investigation-2026-10-21.md).

```
 MacBook (the crashing Mac)                                  Mac mini / Raspberry Pi (always on)
 ┌───────────────────────────────────────────────┐          ┌──────────────────────────────────┐
 │ /Library/Logs/DiagnosticReports   ─┐          │          │  Loki  :3100  (stores events)    │
 │ ~/Library/Logs/DiagnosticReports  ─┼─ update.sh ─► data/index.ndjson ── push_to_loki.py ─►  │
 │ kernel log (log show)             ─┘          │   HTTP   │  Grafana :3000 (dashboards)      │
 └───────────────────────────────────────────────┘          └──────────────────────────────────┘
```

## Quick start (on the Mac you want to monitor)

Requirements: macOS, `git`, and the `python3` that ships with Xcode Command Line Tools (`xcode-select --install`).
No pip packages are needed.

```bash
git clone https://github.com/salamshakur/mac-crash-observability.git
cd mac-crash-observability

scripts/setup_env.sh            # creates .env and fills in this Mac's serial, UUID, names, timezone
scripts/install_hooks.sh        # blocks commits that would contain those identifiers
scripts/update.sh               # copy reports into data/raw/ and build data/index.ndjson
scripts/collect_system_info.sh  # optional, slow: kernel log, shutdown causes, SSD/GPU info
```

After **every crash or restart**, run:

```bash
scripts/update.sh          # or: scripts/update.sh --push   to also send new events to Loki
```

To get the dashboards, start the Grafana + Loki stack on another machine. See
[`observability/README.md`](observability/README.md), a step-by-step guide that also explains
what Loki and Grafana are and how every line of the compose file works.

## Repo layout

| Path | What it is | In git? |
|---|---|---|
| `.env.example` | Template of settings and machine identifiers | yes |
| `.env` | Your values, filled by `setup_env.sh` | **no** |
| `scripts/setup_env.sh` | Detects serial, hardware UUID, username, hostnames, model, timezone | yes |
| `scripts/update.sh` | Copies new reports, saves the latest panic, rebuilds the index, optional `--push` | yes |
| `scripts/collect_system_info.sh` | Saves kernel-log excerpts, shutdown causes, `system_profiler` output | yes |
| `scripts/build_index.py` | Parses all reports into `data/index.ndjson` + `data/index.csv`, scrubbing identifiers | yes |
| `scripts/push_to_loki.py` | Sends new events to Loki's push API | yes |
| `scripts/check_public.sh` | Fails if any `.env` identifier appears in tracked/staged files | yes |
| `scripts/install_hooks.sh` | Installs `check_public.sh` as a git pre-commit hook | yes |
| `data/raw/{system,user,bridge}/` | Untouched copies of Apple's reports | **no** |
| `data/system-info/<timestamp>/` | Output of `collect_system_info.sh` | **no** |
| `data/index.ndjson`, `data/index.csv` | Normalized events, identifiers replaced by placeholders | yes |
| `observability/` | Docker Compose stack: Loki, Grafana, optional Alloy, dashboard | yes |
| `docs/` | Write-ups of investigations | yes |

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

## Grafana + Loki in one paragraph

**Loki** is a log database from Grafana Labs. Unlike Elasticsearch it does **not** index the text of each
log line. It indexes only a few **labels** (here: `job`, `host`, `category`, `source`) and stores the lines as
compressed chunks, then filters and parses them at query time with **LogQL** (`{category="app_crash"} | json | signal="SIGBUS"`).
That makes it cheap to run on a Raspberry Pi. **Grafana** is the UI that queries Loki and draws the dashboards.
**Grafana Alloy** is Grafana's collection agent; its predecessor Promtail reached end of life on March 2, 2026.
Loki is common in teams that already run Prometheus/Grafana. ELK/OpenSearch, Splunk and Datadog are other widely
used choices. The full explanation and setup are in [`observability/README.md`](observability/README.md).

## Contributing / using it on your own Mac

Fork it, run the quick start, and your identifiers stay in your own `.env`. Report formats change between
macOS versions. If `build_index.py` emits `category: parse_error` events, open an issue with the
(scrubbed!) error field.

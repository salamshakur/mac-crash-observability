# The observability stack: Grafana + Loki (+ Alloy)

A study guide and a runbook in one. It covers what each piece is, why it's here, how the compose file works line
by line, how to run it on a Mac mini or Raspberry Pi, and how to query your crash data.

- [1. The big picture](#1-the-big-picture)
- [2. What is Loki?](#2-what-is-loki)
- [3. What is Grafana?](#3-what-is-grafana)
- [4. What is Alloy (and what happened to Promtail)?](#4-what-is-alloy-and-what-happened-to-promtail)
- [5. Why this stack and not something else?](#5-why-this-stack-and-not-something-else)
- [6. Run it on a Mac mini or Raspberry Pi](#6-run-it-on-a-mac-mini-or-raspberry-pi)
- [7. Send events from the crashing Mac](#7-send-events-from-the-crashing-mac)
- [8. The compose file, line by line](#8-the-compose-file-line-by-line)
- [9. The Loki config, explained](#9-the-loki-config-explained)
- [10. Grafana provisioning, explained](#10-grafana-provisioning-explained)
- [11. LogQL by example](#11-logql-by-example)
- [12. The dashboard panels](#12-the-dashboard-panels)
- [13. Operating it: upgrades, backups, security](#13-operating-it-upgrades-backups-security)
- [14. Troubleshooting](#14-troubleshooting)
- [15. Further reading](#15-further-reading)

---

## 1. The big picture

**Observability** is being able to answer "what is this system doing and why?" from the data it emits. That data
is usually split into three kinds:

| Signal | Shape | Typical tool (Grafana stack) |
|---|---|---|
| **Metrics** | numbers over time (`cpu=73%`) | Prometheus / Mimir |
| **Logs** | timestamped text or JSON events | **Loki** |
| **Traces** | one request's path through services | Tempo |

Crash reports are **events**: something happened at time T with these details. So they are logs, and Loki is the
store. The pipeline in this repo:

```
 Crashing Mac                         Server (Mac mini / Pi)
 ────────────                         ──────────────────────
 Apple reports ─► build_index.py ─► index.ndjson ─► push_to_loki.py ──HTTP POST──► Loki ◄──LogQL── Grafana ◄── you (browser)
   (collect)        (parse+scrub)      (events)       (ship)                     (store)            (visualize)
```

Every observability pipeline has those same four stages: **collect → process → store → visualize**.

## 2. What is Loki?

[Loki](https://grafana.com/docs/loki/latest/) is an open-source log aggregation system from Grafana Labs, often
described as "Prometheus, but for logs." Its defining design choice:

> Loki indexes **labels**, not the content of each log line. Log entries are grouped into **streams**, and each
> stream is identified by its set of labels.

Concretely, an entry pushed by this repo looks like:

```
labels: {job="mac-crash", host="macbookpro16-1", category="app_crash", source="user"}   ← indexed
line:   {"process":"corespotlightd","signal":"SIGBUS","subtype":"FS pagein error: 94 Bad message",…}  ← compressed, not indexed
time:   2026-10-21T19:55:42Z
```

- **Stream** = all entries sharing one exact label set. Each distinct combination of label values is a new stream.
- **Chunks** = a stream's lines, compressed and written to storage (here: the container's filesystem).
- **Query** = pick streams by label (fast, uses the index), then scan and filter their lines (`|= "SIGBUS"`,
  `| json | signal="SIGBUS"`).

**Cardinality** is the number of distinct label combinations. Loki's docs warn that high cardinality "causes Loki
to build a huge index and to flush thousands of tiny chunks", and recommend few labels with bounded values.
That's why `process`, `signal`, `event_id` are **not** labels here. They stay inside the JSON and are
extracted at query time with `| json`. Only `job`, `host`, `category`, `source` are labels.

**Loki vs. Elasticsearch/OpenSearch:** Elasticsearch builds a full-text index of every word, so arbitrary
search is very fast but storage and RAM are heavy. Loki's small index makes it cheap to run (fine on a Pi) at the
cost of scanning lines at query time. For a few thousand crash events, scanning is instant.

## 3. What is Grafana?

[Grafana](https://grafana.com/docs/grafana/latest/) is the visualization layer. It doesn't store your data. It
connects to **data sources** (Loki, Prometheus, PostgreSQL, CloudWatch, …), runs queries, and renders
**dashboards** made of **panels** (time series, stats, tables, logs). It also does alerting, for example
"notify me if there are 2+ kernel panics in 24h".

## 4. What is Alloy (and what happened to Promtail)?

Something has to get logs *into* Loki. That's a **collector/agent**: it tails files or reads journald, parses
lines, attaches labels and pushes to Loki.

- **Promtail** was Loki's original agent. Grafana's docs state Promtail is **end of life as of March 2, 2026**;
  all future development happens in **Grafana Alloy**, and a migration tool converts Promtail configs.
- **[Grafana Alloy](https://grafana.com/docs/alloy/latest/)** is Grafana's OpenTelemetry-compatible collector
  for logs, metrics, traces and profiles. Its config is a pipeline of **components** wired output → input
  (see `alloy/config.alloy`).

**This repo uses a push script by default instead of an agent.** The machine being monitored is the one that
crashes, and a one-shot `scripts/push_to_loki.py` after each reboot is simpler than keeping an agent alive on it.
Alloy is included as an optional, commented example (`--profile collector`) for learning. There's a caveat:
`build_index.py` rewrites `index.ndjson` each run, and a file tailer may re-read a rewritten file and send
lines twice. For production log files that are only ever appended to, Alloy is the right tool.

## 5. Why this stack and not something else?

| Option | Good at | Why not the default here |
|---|---|---|
| **Grafana + Loki** | Cheap, label-based log store with a great UI and alerting; the same stack many teams run in production | (chosen) |
| ELK / OpenSearch + Kibana/Dashboards | Full-text search over everything | JVM services that need a lot of memory; heavy for a Pi |
| Datadog / Splunk / Grafana Cloud | No servers to run | Ships your data to a vendor; paid beyond free tiers |
| DuckDB / SQLite + a notebook | Ad-hoc SQL over `index.ndjson` | No live dashboards or alerting |

Loki isn't *the* standard. It's one of several common ones, and it's especially common wherever Prometheus and Grafana
already run. Learning it teaches transferable ideas: labels vs. content, cardinality, structured logs, pull
vs. push collection, dashboards-as-code.

## 6. Run it on a Mac mini or Raspberry Pi

Run the stack on a machine that stays on and isn't the one crashing.

### 6a. Install Docker

| Machine | Install |
|---|---|
| **Mac mini** (Apple silicon or Intel) | [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) or [OrbStack](https://orbstack.dev) or `brew install colima docker docker-compose && colima start` |
| **Raspberry Pi 4/5** | Needs a **64-bit** OS (Raspberry Pi OS 64-bit or Ubuntu Server arm64). Install with Docker's convenience script: `curl -fsSL https://get.docker.com \| sh`, then `sudo usermod -aG docker $USER` and log out/in |
| Any Linux server | [Docker Engine](https://docs.docker.com/engine/install/) |

Check: `docker version` and `docker compose version` both print versions.

### 6b. Start the stack

```bash
git clone https://github.com/salamshakur/mac-crash-observability.git
cd mac-crash-observability/observability
cp .env.example .env          # then edit .env: set GRAFANA_ADMIN_PASSWORD
docker compose up -d          # pulls images on first run (a few hundred MB)
docker compose ps             # both services should be "running"
curl http://localhost:3100/ready   # prints "ready" once Loki finishes starting (~15-30 s)
```

Open **http://\<server\>:3000** (e.g. `http://raspberrypi.local:3000` or `http://mac-mini.local:3000`), log in with
the admin user/password from `.env`, then go to **Dashboards → Mac crashes → Mac crash overview**. It will be
empty until you send events (next section).

### 6c. Everyday commands

```bash
docker compose logs -f loki      # follow Loki's logs (Ctrl+C to stop following)
docker compose restart grafana   # restart one service
docker compose down              # stop and remove containers; volumes (your data) are kept
docker compose down -v           # ...and delete the volumes too: ALL STORED EVENTS ARE LOST
```

## 7. Send events from the crashing Mac

On the MacBook, in the repo:

```bash
# in .env
LOKI_URL=http://raspberrypi.local:3100      # or the server's IP, e.g. http://192.168.1.50:3100

scripts/update.sh --push                    # collect + index + push only events not sent before
python3 scripts/push_to_loki.py --dry-run   # see exactly what would be sent
```

`push_to_loki.py` POSTs to Loki's push API, `POST /loki/api/v1/push`, with this body:

```json
{"streams": [{
  "stream": {"job": "mac-crash", "host": "macbookpro16-1", "category": "kernel_panic", "source": "system"},
  "values": [["1792612518000000000", "{\"panic_subsystem\": \"com.apple.filesystems.lifs\", …}"]]
}]}
```

Each value is `[timestamp in nanoseconds as a string, the log line]`. The script remembers sent `event_id`s in
`data/.loki_pushed`, so re-running only sends new events.

## 8. The compose file, line by line

```yaml
name: mac-crash-observability
```
The **project name**. It prefixes container, network and volume names (`mac-crash-observability-loki-1`).

```yaml
services:
  loki:
    image: grafana/loki:3.7.5
```
A **service** is one container definition. The **image tag is pinned** to an exact version. `latest` would
silently upgrade on the next `docker compose pull`, possibly with breaking config changes. Upgrade deliberately
(section 13).

```yaml
    command: -config.file=/etc/loki/loki-config.yaml
```
Arguments passed to the image's entrypoint (the `loki` binary): use our config file.

```yaml
    ports:
      - "3100:3100"
```
`HOST:CONTAINER`. Publishes Loki's HTTP port on the server so the MacBook can push to it. Grafana doesn't need
this. It reaches Loki over the internal Docker network.

```yaml
    volumes:
      - ./loki/loki-config.yaml:/etc/loki/loki-config.yaml:ro
      - loki-data:/loki
```
Two kinds of volume:
- **Bind mount** (`./path:/container/path`) puts a file from this repo inside the container. `:ro` = read-only.
  Edit the file in git and restart, and the container sees the change.
- **Named volume** (`loki-data:/loki`) is storage managed by Docker that survives `docker compose down`
  and image upgrades. This is where Loki keeps chunks and index. Deleting it (`down -v`) deletes your data.

```yaml
    restart: unless-stopped
```
Restart after crashes and after the host reboots, unless you stopped it yourself.

```yaml
  grafana:
    image: grafana/grafana:12.4.6
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD in observability/.env}
```
Grafana reads any setting from `GF_<SECTION>_<KEY>` environment variables. `${VAR}` is **interpolated by
Compose** from `observability/.env`:
- `${VAR:-default}` uses `default` if `VAR` is unset or empty.
- `${VAR:?message}` makes `docker compose up` **fail with that message** if `VAR` is missing. That's a guard
  against starting Grafana with no password.

```yaml
      GF_USERS_ALLOW_SIGN_UP: "false"
      GF_ANALYTICS_REPORTING_ENABLED: "false"
```
No self-registration; no usage stats sent to Grafana Labs.

```yaml
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/etc/grafana/dashboards:ro
      - grafana-data:/var/lib/grafana
```
**Provisioning** = configuration as code. On startup Grafana reads `provisioning/` and creates the Loki data
source and loads every dashboard JSON from `dashboards/`, so a fresh install comes up fully configured
(section 10). `grafana-data` keeps users, preferences and UI-made changes.

```yaml
    depends_on:
      - loki
```
Start Loki first. This only orders **startup**. It doesn't wait for Loki to be *ready*. That's fine:
Grafana doesn't need Loki to boot, and it only queries Loki when you open a dashboard.

```yaml
  alloy:
    image: grafana/alloy:v1.17.1
    profiles: ["collector"]
```
A **profile** makes a service opt-in: plain `docker compose up` skips it, and
`docker compose --profile collector up -d` includes it. Only useful if the stack runs on the same Mac that
produces `data/index.ndjson`.

```yaml
    command: run --server.http.listen-addr=0.0.0.0:12345 --storage.path=/var/lib/alloy/data /etc/alloy/config.alloy
    volumes:
      - ../data:/data:ro
      - alloy-data:/var/lib/alloy/data
```
Alloy serves its own debug UI on `:12345` (a live graph of the pipeline). `../data` mounts the repo's data
folder read-only. `alloy-data` stores read positions so restarts don't re-send.

```yaml
volumes:
  loki-data:
  grafana-data:
  alloy-data:
```
Declares the named volumes. `docker volume ls` lists them, and `docker volume inspect <name>` shows where they
live on disk.

## 9. The Loki config, explained

`loki/loki-config.yaml` runs Loki in **monolithic mode**: every component (distributor, ingester, querier,
compactor…) in one process. Large deployments split these into separately scaled services, but one process is
right for a home server.

| Setting | Why |
|---|---|
| `auth_enabled: false` | Single-tenant mode, so no `X-Scope-OrgID` header is needed. **Don't expose port 3100 to the internet** in this mode |
| `common.storage.filesystem` | Chunks and rules on local disk (the `loki-data` volume). Production setups use S3/GCS/Azure |
| `replication_factor: 1`, `ring.kvstore: inmemory` | One node, no clustering |
| `schema_config` → `store: tsdb`, `schema: v13`, `period: 24h` | The current recommended index format; one index table per day |
| `limits_config.reject_old_samples: false` | By default Loki rejects samples older than **1 week** (`reject_old_samples: true`, `reject_old_samples_max_age: 1w`). Crash history is often older, so it's off |
| `retention_period: 0s`, `compactor.retention_enabled: false` | Keep everything; this dataset is tiny |
| `analytics.reporting_enabled: false` | No usage reporting |

**Out-of-order writes.** Within one stream, Loki accepts entries up to `max_chunk_age / 2` (1 hour with the default
2 h) older than the newest entry already in that stream. `push_to_loki.py` sorts each stream by time before
sending, so a first backfill works. If you later push an event that's more than an hour older than that stream's
newest entry, Loki rejects it as too far behind (section 14).

## 10. Grafana provisioning, explained

- `grafana/provisioning/datasources/loki.yaml` creates a data source named **Loki** with a **fixed `uid: loki`**,
  pointing at `http://loki:3100`. `loki` resolves because Compose puts both containers on one network with DNS by
  service name. `access: proxy` means Grafana's backend makes the requests, not your browser.
- `grafana/provisioning/dashboards/dashboards.yaml` is a **dashboard provider**: "load every JSON file in
  `/etc/grafana/dashboards` into folder *Mac crashes*".
- `grafana/dashboards/mac-crash-overview.json` is the dashboard itself. Panels reference the data source by
  `uid`, which is why the uid is fixed.

To change the dashboard: edit it in the UI, then **Share → Export → Save to file** (or the JSON model under
dashboard settings), and replace the file in git. UI edits alone live only in the `grafana-data` volume.

## 11. LogQL by example

Try these in **Explore** (compass icon) with the Loki data source. A LogQL query starts with a **stream selector**
in `{}` and then a **pipeline** of `|` stages.

```logql
{job="mac-crash"}                                         # every event
{job="mac-crash", category="kernel_panic"}                # only panics (label match = uses the index)
{job="mac-crash", category="app_crash"} |= "SIGBUS"       # line contains text
{job="mac-crash", category="app_crash"} | json | signal="SIGBUS"            # parse JSON, filter on a field
{job="mac-crash", category="app_crash"} | json | fs_pagein_error="true"     # storage-read failures
{job="mac-crash", category="kernel_panic"} | json | line_format "{{.panic_subsystem}}: {{.panic_reason}}"
```

**Metric queries** turn lines into numbers. `count_over_time(<logs>[window])` counts lines per window:

```logql
sum(count_over_time({job="mac-crash", category="kernel_panic"}[24h]))                         # panics per day
sum by (category) (count_over_time({job="mac-crash"}[1h]))                                    # events/hour by type
topk(10, sum by (process) (count_over_time({job="mac-crash", category="app_crash"} | json [7d])))   # worst processes
sum by (panic_subsystem) (count_over_time({job="mac-crash", category="kernel_panic"} | json [30d])) # panics by kext
```

`| json` turns JSON keys into temporary labels you can filter on and `sum by` over, without making them
indexed labels. This is the cardinality rule from section 2 in practice.

## 12. The dashboard panels

| Panel | Query idea | What it tells you |
|---|---|---|
| Kernel panics / App crashes / APFS hash mismatches | `count_over_time(...[$__range])` | Totals for the selected time range |
| Storage-read crashes | `fs_pagein_error="true" or codesign_invalid="true"` | Crashes where macOS couldn't read a page of a file intact |
| Events over time by category | `sum by (category)(count_over_time(...[$__interval]))` | Bursts and when they happen |
| Top crashing processes | `topk(15, sum by (process) ...)` | Which apps die most |
| Crash signals | `sum by (signal) ...` | SIGSEGV vs SIGBUS vs code-sign kills |
| Kernel panics (log) | `line_format` with subsystem + reason | Whether panics hit the **same** subsystem (points to software or one component) or **different** ones |
| Faulting code | `sum by (process, signal, top_frame)` | Repeat offenders at the code level |
| All events | everything, formatted | The raw timeline |

`$__range` and `$__interval` are Grafana variables: the dashboard's time range and the auto-chosen bucket size.
The **Mac** dropdown (`$host`) filters by `HOST_LABEL` if you send data from several Macs.

**Exercise:** add an alert rule (Alerting → Alert rules → New) on
`sum(count_over_time({job="mac-crash", category="kernel_panic"}[24h])) > 1`.

## 13. Operating it: upgrades, backups, security

- **Upgrade:** read the release notes, change the tag in `docker-compose.yml`, then
  `docker compose pull && docker compose up -d`. Commit the change.
- **Back up** the volumes (stop first for a consistent copy):
  ```bash
  docker compose stop
  docker run --rm -v mac-crash-observability_loki-data:/v -v "$PWD":/b alpine tar czf /b/loki-data.tgz -C /v .
  docker compose start
  ```
  Your source of truth is still `data/raw/` on the Mac. Loki can always be rebuilt with
  `push_to_loki.py --all` into an empty Loki.
- **Security:** keep ports 3000/3100 on your LAN. For remote access use a VPN such as Tailscale or WireGuard,
  rather than router port-forwarding. Change the Grafana admin password from the default.

## 14. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Can't reach Loki at …` from `push_to_loki.py` | Stack not running, wrong `LOKI_URL`, or firewall. Test: `curl http://<server>:3100/ready` |
| Loki: `entry too far behind` | Pushed an event > 1 h older than that stream's newest entry (section 9). Fine to ignore for a stray event; for a full re-import, empty Loki first (`docker compose down -v`, `up -d`, then `push_to_loki.py --all`) |
| Loki: `greater_than_max_sample_age` | `reject_old_samples` is on. Check the config was mounted (`docker compose exec loki cat /etc/loki/loki-config.yaml`) |
| Grafana panels say "No data" | Time picker range doesn't include the events (default last 30 days). Widen it, or check Explore with `{job="mac-crash"}` |
| `GRAFANA_ADMIN_PASSWORD` error on `up` | Create `observability/.env` from `.env.example` |
| Raspberry Pi: `exec format error` / `no matching manifest` | 32-bit OS. Reinstall a 64-bit OS (`uname -m` should say `aarch64`) |

## 15. Further reading

- Loki docs: https://grafana.com/docs/loki/latest/ (start with *Get started → Labels*)
- LogQL: https://grafana.com/docs/loki/latest/query/
- Out-of-order and old logs in Loki: https://grafana.com/blog/2024/01/04/the-concise-guide-to-loki-how-to-work-with-out-of-order-and-older-logs/
- Promtail EOL notice: https://grafana.com/docs/loki/latest/send-data/promtail/
- Grafana Alloy: https://grafana.com/docs/alloy/latest/
- Grafana provisioning: https://grafana.com/docs/grafana/latest/administration/provisioning/
- Docker Compose file reference: https://docs.docker.com/reference/compose-file/

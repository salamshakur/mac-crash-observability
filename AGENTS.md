# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, etc.) working in this repo.

## What this repo is
Collects macOS crash/panic reports, normalizes them into `data/index.ndjson`, and ships them to Loki for Grafana
dashboards. See `README.md` (usage, event schema) and `observability/README.md` (stack).

## Hard rules
- **Never commit or print machine identifiers.** `.env`, `data/raw/`, `data/system-info/` contain the owner's
  serial number, hardware UUID, username and hostnames. They are gitignored; keep it that way.
  Never copy values from `.env` into code, docs, commit messages or examples. Use placeholders
  (`<SERIAL>`, `C02XXXXXXXXX`).
- Never hard-code identifiers. New identifiers go in `.env.example` (blank), get detected in
  `scripts/setup_env.sh`, and get scrubbed via `SCRUB_KEYS` in `scripts/build_index.py`.
- Before committing, run `scripts/check_public.sh` (the pre-commit hook does this when installed via
  `scripts/install_hooks.sh`).
- Raw reports in `data/raw/` are evidence: read them, never modify or delete them.
- `update.sh --scheduled` runs unattended under launchd: it must never prompt (use `sudo -n`), and must stay fast.

## Conventions
- Python: standard library only, compatible with Python 3.9 (the Xcode CLT python3). No pip deps.
- Shell: `scripts/*.sh` run on macOS. `setup_env.sh`/`check_public.sh` use bash 3.2-compatible syntax;
  `update.sh`/`collect_system_info.sh` use zsh. Call `/usr/bin/log` by full path (zsh has a `log` builtin).
- Timestamps in the index are ISO-8601 UTC with a `Z` suffix. Local times from file names use `MAC_TIMEZONE`.
- Loki labels stay low-cardinality: `job`, `host`, `category`, `source` only. Everything else stays in the
  JSON line and is parsed with `| json` at query time.
- Pin container images to exact versions in `observability/docker-compose.yml`.
- Don't label unknown values. E.g. SMC shutdown cause text comes only from what macOS printed in `pmset -g log`.

## Useful commands
```bash
scripts/install_schedule.sh --status      # is the hourly LaunchAgent loaded? last exit code
tail -f data/logs/update.log              # output of scheduled runs
python3 scripts/build_index.py              # rebuild data/index.ndjson
python3 scripts/push_to_loki.py --dry-run   # inspect Loki payloads
scripts/check_public.sh                     # identifier scan of tracked files
jq -s 'group_by(.category) | map({(.[0].category): length}) | add' data/index.ndjson
cd observability && docker compose config   # validate compose file
```

## Adding a new report type
1. Add a parser function in `scripts/build_index.py` returning a dict with at least `category`, `source`, `ts`, `process`.
2. Route it in `collect()`.
3. Document the category in the README's event table, and add a panel/query in
   `observability/grafana/dashboards/mac-crash-overview.json` if useful.

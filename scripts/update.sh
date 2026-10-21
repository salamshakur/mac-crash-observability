#!/bin/zsh
# update.sh: run after every crash or restart.
#   1. copies any new reports from macOS into data/raw/ (never overwrites)
#   2. saves the latest kernel panic under a dated name (macOS overwrites it on every panic)
#   3. rebuilds data/index.ndjson + data/index.csv (identifiers scrubbed via .env)
#   4. with --push: sends new events to Loki (LOKI_URL in .env)
#
#   scripts/update.sh            collect + index
#   scripts/update.sh --push     collect + index + ship to Loki
set -u
setopt NO_NOMATCH          # a glob that matches nothing stays literal instead of aborting
ROOT="${0:A:h:h}"            # zsh: absolute path of this script's folder, then its parent
RAW="$ROOT/data/raw"
mkdir -p "$RAW/system" "$RAW/user" "$RAW/bridge"

[ -f "$ROOT/.env" ] || { echo "No .env yet. Run scripts/setup_env.sh first."; exit 1; }

# -n = no-clobber: a file already in data/raw is skipped, so re-running never duplicates.
# Report names include a timestamp, so a new report always has a new name.
cp -np /Library/Logs/DiagnosticReports/*.* "$RAW/system/" 2>/dev/null
cp -np ~/Library/Logs/DiagnosticReports/*.* "$RAW/user/" 2>/dev/null

# T2 (bridgeOS) panic copies are readable by admins only, hence sudo.
if ls /Library/Logs/DiagnosticReports/ProxiedDevice-Bridge/*.ips >/dev/null 2>&1; then
  sudo cp -np /Library/Logs/DiagnosticReports/ProxiedDevice-Bridge/*.ips "$RAW/bridge/" 2>/dev/null
  sudo chown "$USER" "$RAW"/bridge/* 2>/dev/null
fi

# The full text of the most recent kernel panic lives in a hidden file that is replaced on each panic.
P=/Library/Logs/DiagnosticReports/.contents.panic
[ -f "$P" ] && cp -np "$P" "$RAW/system/$(date -r "$P" +%Y-%m-%d-%H%M%S)_kernel.panic" 2>/dev/null

python3 "$ROOT/scripts/build_index.py" || exit 1

if [ "${1:-}" = "--push" ]; then
  python3 "$ROOT/scripts/push_to_loki.py"
fi

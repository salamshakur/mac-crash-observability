#!/bin/zsh
# update.sh: collect new macOS diagnostic reports and rebuild the event index.
#
#   scripts/update.sh              collect + index (asks for your password for the T2 copies)
#   scripts/update.sh --push       ...then send new events to Loki (LOKI_URL in .env)
#   scripts/update.sh --scheduled  unattended mode used by the LaunchAgent (scripts/install_schedule.sh):
#                                  never prompts for a password, and pushes to Loki if LOKI_URL is set
#
# Every run:
#   1. copies new reports from macOS into data/raw/ (never overwrites; crash-safe, see copy_new)
#   2. saves the latest kernel panic under a dated name (macOS overwrites it on every panic)
#   3. scans the kernel log since the previous run for APFS hash mismatches and shutdown causes
#   4. rebuilds data/index.ndjson (identifiers scrubbed via .env)
set -u
setopt NO_NOMATCH                # a glob that matches nothing stays literal instead of aborting
ROOT="${0:A:h:h}"                # zsh: absolute path of this script's folder, then its parent
DATA="$ROOT/data"
RAW="$DATA/raw"

PUSH=0; SCHEDULED=0
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    --scheduled) SCHEDULED=1 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

[ -f "$ROOT/.env" ] || { echo "No .env yet. Run scripts/setup_env.sh first."; exit 1; }
mkdir -p "$RAW/system" "$RAW/user" "$RAW/bridge"
[ $SCHEDULED -eq 1 ] && echo "=== $(date '+%Y-%m-%d %H:%M:%S') scheduled run"

# Crash-safe copying. This Mac may panic in the middle of a run. A plain `cp` interrupted by a panic would
# leave a truncated report under its real name, and since we never overwrite, it would never be fixed.
# So each file is copied to a hidden ".partial" name first and renamed only once complete. A rename within
# one folder is atomic: after a crash there is either the whole report or a leftover .partial, never half
# a report. Leftovers are deleted at the start of the next run, and the copy is retried.
rm -f "$RAW"/*/.*.partial

# copy_new SRC DEST_DIR [NAME] [sudo]: copy SRC unless DEST_DIR/NAME already exists (NAME defaults to SRC's name)
copy_new() {
  local src="$1" dir="$2" name="${3:-${1:t}}" as="${4:-}"   # ${1:t} = zsh basename
  [ -f "$src" ] || return 0
  [ -e "$dir/$name" ] && return 0                           # already have it: re-running is safe
  local tmp="$dir/.$name.partial"
  if [ "$as" = sudo ]; then
    sudo cp -p "$src" "$tmp" && sudo chown "$USER" "$tmp" || return 1
  else
    cp -p "$src" "$tmp" 2>/dev/null || return 1
  fi
  mv "$tmp" "$dir/$name"
}

# 1. Reports. File names include a timestamp, so a new report always has a new name.
for f in /Library/Logs/DiagnosticReports/*.*;  do copy_new "$f" "$RAW/system"; done
for f in ~/Library/Logs/DiagnosticReports/*.*; do copy_new "$f" "$RAW/user";   done

# T2 (bridgeOS) panic copies are readable by admins only. Interactive runs ask for the password;
# scheduled runs use `sudo -n` (never prompts) and skip them. They duplicate panics captured in step 2.
BRIDGE=/Library/Logs/DiagnosticReports/ProxiedDevice-Bridge
if ls "$BRIDGE"/*.ips >/dev/null 2>&1; then
  if [ $SCHEDULED -eq 0 ] || sudo -n true 2>/dev/null; then
    for f in "$BRIDGE"/*.ips; do copy_new "$f" "$RAW/bridge" "" sudo; done
  else
    echo "skipped T2 panic copies (need an admin password; panics are still captured from .contents.panic)"
  fi
fi

# 2. The full text of the latest kernel panic lives in a hidden file that is replaced on each panic.
P=/Library/Logs/DiagnosticReports/.contents.panic
[ -f "$P" ] && copy_new "$P" "$RAW/system" "$(date -r "$P" +%Y-%m-%d-%H%M%S)_kernel.panic"

# 3. Kernel log since the last scan (first run: last 24 h). zsh has its own `log` builtin, so use the full path.
STATE="$DATA/.last_log_scan"
START="$(cat "$STATE" 2>/dev/null || date -v-24H '+%Y-%m-%d %H:%M:%S')"
NOW="$(date '+%Y-%m-%d %H:%M:%S')"
KLOG="$DATA/system-info/kernel-log"
mkdir -p "$KLOG"
OUT="$KLOG/$(date '+%Y%m%d-%H%M%S').txt"
/usr/bin/log show --start "$START" --end "$NOW" --style syslog \
  --predicate 'process == "kernel" AND (eventMessage CONTAINS "apfs_announce_hash_mismatch" OR eventMessage CONTAINS "Previous shutdown cause")' \
  > "$OUT" 2>/dev/null
if grep -q 'kernel\[' "$OUT"; then
  echo "kernel log: $(grep -c 'kernel\[' "$OUT") matching lines since $START"
else
  rm -f "$OUT"                   # nothing found; don't keep an empty file
fi
echo "$NOW" > "$STATE"

# 4. Index
python3 "$ROOT/scripts/build_index.py" || exit 1

# 5. Ship to Loki
LOKI_URL="$(awk -F= '$1=="LOKI_URL" { sub(/^[^=]*=/, ""); print }' "$ROOT/.env")"
if [ $PUSH -eq 1 ] || { [ $SCHEDULED -eq 1 ] && [ -n "$LOKI_URL" ]; }; then
  python3 "$ROOT/scripts/push_to_loki.py"
fi

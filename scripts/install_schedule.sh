#!/bin/bash
# install_schedule.sh: run `update.sh --scheduled` every hour and at login, using a macOS LaunchAgent.
#
#   scripts/install_schedule.sh              install (or reinstall) and run once now
#   scripts/install_schedule.sh --status     show launchd's view of the job
#   scripts/install_schedule.sh --uninstall  stop and remove it
#   INTERVAL=1800 scripts/install_schedule.sh   run every 30 minutes instead
#
# launchd is macOS's built-in service manager (like systemd on Linux). A LaunchAgent runs as you, only
# while you're logged in, and survives reboots. If the Mac is asleep when a run is due, it runs on wake.
# Output goes to data/logs/update.log.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="io.github.mac-crash-observability.update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LOG="$ROOT/data/logs/update.log"
INTERVAL="${INTERVAL:-3600}"

case "${1:-}" in
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL"
    exit 0 ;;
  --status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E "state =|last exit code|run interval|runs =" || echo "Not installed"
    echo "Log: $LOG"
    exit 0 ;;
esac

[ "$(uname)" = "Darwin" ] || { echo "macOS only"; exit 1; }
[ -f "$ROOT/.env" ] || { echo "No .env yet. Run scripts/setup_env.sh first."; exit 1; }

# macOS privacy protection (TCC) blocks background jobs from reading Documents, Desktop and Downloads.
case "$ROOT" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
    echo "warning: the repo is inside a privacy-protected folder; scheduled runs may fail with"
    echo "         'Operation not permitted'. Move it (e.g. to ~/Code) or give /bin/zsh Full Disk Access." ;;
esac

mkdir -p "$ROOT/data/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$ROOT/scripts/update.sh</string>
    <string>--scheduled</string>
  </array>
  <key>StartInterval</key>     <integer>$INTERVAL</integer>
  <key>RunAtLoad</key>         <true/>
  <key>ProcessType</key>       <string>Background</string>
  <key>LowPriorityIO</key>     <true/>
  <key>StandardOutPath</key>   <string>$LOG</string>
  <key>StandardErrorPath</key> <string>$LOG</string>
</dict>
</plist>
PLISTEOF
plutil -lint "$PLIST" >/dev/null

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true   # reinstall cleanly if already loaded
launchctl bootstrap "$DOMAIN" "$PLIST"                     # RunAtLoad: first run starts now
echo "Installed $LABEL: runs every $((INTERVAL / 60)) min and at login."
echo "Watch it:   tail -f \"$LOG\""
echo "Status:     scripts/install_schedule.sh --status"

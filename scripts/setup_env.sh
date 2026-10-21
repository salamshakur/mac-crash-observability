#!/bin/bash
# setup_env.sh: create .env from .env.example and fill in this Mac's values.
#
#   scripts/setup_env.sh           fill only the blank keys (keeps anything you edited)
#   scripts/setup_env.sh --force   re-detect every value, overwriting what's there
#
# Nothing here needs sudo. Every command is read-only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

if [ "$(uname)" != "Darwin" ]; then
  echo "setup_env.sh must run on the Mac whose crashes you're collecting (uname is $(uname))." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"          # readable by you only
  echo "Created .env from .env.example"
fi

# ioreg prints the IOPlatformExpertDevice node, which holds the serial number and hardware UUID:
#   "IOPlatformSerialNumber" = "C02XXXXXXX"
#   "IOPlatformUUID" = "XXXXXXXX-XXXX-..."
platform_value() {
  ioreg -rd1 -c IOPlatformExpertDevice | awk -F'"' -v key="$1" '$2 == key { print $4; exit }'
}

detect() {
  case "$1" in
    MAC_SERIAL)         platform_value IOPlatformSerialNumber ;;
    MAC_HW_UUID)        platform_value IOPlatformUUID ;;
    MAC_USERNAME)       id -un ;;
    MAC_COMPUTER_NAME)  scutil --get ComputerName 2>/dev/null || true ;;   # "Jane's MacBook Pro"
    MAC_LOCAL_HOSTNAME) scutil --get LocalHostName 2>/dev/null || true ;;  # "Janes-MacBook-Pro" (Bonjour .local name)
    MAC_HOSTNAME)       hostname ;;                                         # network hostname, may come from DHCP
    MAC_MODEL)          sysctl -n hw.model ;;                               # e.g. MacBookPro16,1
    MAC_TIMEZONE)       readlink /etc/localtime | sed 's#.*/zoneinfo/##' ;;
  esac
}

# Replace KEY=... in .env without sed -i (whose syntax differs between macOS and Linux).
set_kv() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" 'BEGIN{done=0}
       $0 ~ "^"k"=" { print k"="v; done=1; next } { print }
       END { if (!done) print k"="v }' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
}

current() { awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"; }

for key in MAC_SERIAL MAC_HW_UUID MAC_USERNAME MAC_COMPUTER_NAME MAC_LOCAL_HOSTNAME MAC_HOSTNAME MAC_MODEL MAC_TIMEZONE; do
  if [ $FORCE -eq 1 ] || [ -z "$(current "$key")" ]; then
    val="$(detect "$key")"
    set_kv "$key" "$val"
    # mask identifiers in the terminal output so they don't end up in screenshots
    case "$key" in
      MAC_MODEL|MAC_TIMEZONE) shown="$val" ;;
      *) shown="${val:0:3}***" ;;
    esac
    printf "  %-20s %s\n" "$key" "$shown"
  fi
done

if [ "$(current HOST_LABEL)" = "my-mac" ] || [ -z "$(current HOST_LABEL)" ]; then
  set_kv HOST_LABEL "$(sysctl -n hw.model | tr 'A-Z,' 'a-z-')"
fi

echo "Done. .env is ready (gitignored). Review it with: open -e \"$ENV_FILE\""
echo "Next: set LOKI_URL in .env to your Mac mini / Raspberry Pi, then run scripts/update.sh"

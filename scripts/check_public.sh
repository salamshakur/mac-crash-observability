#!/bin/bash
# check_public.sh: fail if any identifier from .env appears in files git would publish.
#
#   scripts/check_public.sh            check every tracked file
#   scripts/check_public.sh --staged   check only what's staged (used by the pre-commit hook)
#
# Install as a pre-commit hook with: scripts/install_hooks.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "check_public: no .env, nothing to check against"; exit 0; }

MODE="--cached"                  # git grep --cached = search the index (staged content)
[ "${1:-}" = "--staged" ] || MODE="" # no flag = search tracked files in the working tree

fail=0
while IFS='=' read -r key value; do
  case "$key" in MAC_SERIAL|MAC_HW_UUID|MAC_COMPUTER_NAME|MAC_LOCAL_HOSTNAME|MAC_HOSTNAME) ;; *) continue ;; esac
  value="${value%\"}"; value="${value#\"}"
  [ ${#value} -ge 4 ] || continue
  # -I skip binaries, -i case-insensitive, -F fixed string, -l list files only
  if hits=$(git grep $MODE -I -i -l -F -e "$value" -- . ':!.env' 2>/dev/null); then
    echo "✗ $key value found in:"; echo "$hits" | sed 's/^/    /'; fail=1
  fi
done < .env

# the username is checked only as a /Users/<name> path, since short names match ordinary words
user=$(awk -F= '$1=="MAC_USERNAME"{print $2}' .env)
if [ -n "$user" ] && hits=$(git grep $MODE -I -l -F -e "/Users/$user" -- . ':!.env' 2>/dev/null); then
  echo "✗ /Users/$user found in:"; echo "$hits" | sed 's/^/    /'; fail=1
fi

if [ $fail -eq 1 ]; then
  echo "Refusing: identifiers would be published. Re-run scripts/build_index.py or unstage those files."
  exit 1
fi
echo "✓ no identifiers from .env in ${MODE:+staged }files"

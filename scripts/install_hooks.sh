#!/bin/bash
# install_hooks.sh: make git run check_public.sh before every commit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"
[ -d "$ROOT/.git" ] || { echo "Not a git repo: $ROOT"; exit 1; }
cat > "$HOOK" <<'EOF'
#!/bin/bash
exec "$(git rev-parse --show-toplevel)/scripts/check_public.sh" --staged
EOF
chmod +x "$HOOK"
echo "Installed pre-commit hook → $HOOK"

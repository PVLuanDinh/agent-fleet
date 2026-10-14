#!/usr/bin/env bash
set -euo pipefail

# agent-fleet -- install as a Claude Code skill and run the self-check
# Usage: ./setup.sh [--force]

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HOME}/.claude/skills/agent-fleet"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Error: Python 3.10+ is required (python3 or python on PATH)."; exit 1; }

echo "=== agent-fleet setup ==="

DEST_REAL=""
[ -d "$DEST" ] && DEST_REAL="$(cd "$DEST" && pwd)"

if [ "$DEST_REAL" = "$SRC" ]; then
  echo "Already running from $DEST; skipping copy."
else
  if [ -e "$DEST" ]; then
    if diff -rq -x .git -x __pycache__ "$SRC" "$DEST" >/dev/null 2>&1; then
      echo "Existing copy at $DEST is identical; nothing to copy."
    elif [ "$FORCE" -eq 1 ]; then
      echo "Overwriting $DEST (--force)"
      rm -rf "$DEST"
    else
      echo "Error: $DEST exists and differs from this folder. Re-run with --force to overwrite."
      exit 1
    fi
  fi
  if [ ! -e "$DEST" ]; then
    mkdir -p "$DEST"
    (cd "$SRC" && tar --exclude=.git --exclude=__pycache__ -cf - .) | (cd "$DEST" && tar -xf -)
    echo "Copied to $DEST"
  fi
fi

echo "Running self-check..."
"$PY" "$DEST/scripts/selfcheck.py"

echo ""
echo "=== Setup complete! ==="
echo "Scripts: $DEST/scripts"
echo "Next: $PY $DEST/scripts/board.py --project myproject where"
echo "Using Claude Code? SKILL.md is loaded from $DEST; CLAUDE.md has repo context."

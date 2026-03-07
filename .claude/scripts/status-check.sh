#!/bin/bash
# STATUS.md session-end check
# Stop hook — fires when Claude is about to end the session.
# Exit 2 = block + feed message back to Claude. Exit 0 = pass through.

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

STATUS_FILE="$REPO_ROOT/STATUS.md"

# No STATUS.md at all — prompt to create it
if [ ! -f "$STATUS_FILE" ]; then
  echo "STATUS.md is missing from this repo."
  echo ""
  echo "Before finishing, create STATUS.md using the format in ~/ij/CLAUDE.md."
  echo "Required fields: status, priority, updated, current_focus, next_up"
  exit 2
fi

# Check if STATUS.md was touched this session:
# 1. Uncommitted changes
if ! git -C "$REPO_ROOT" diff --quiet -- STATUS.md 2>/dev/null || \
   ! git -C "$REPO_ROOT" diff --cached --quiet -- STATUS.md 2>/dev/null; then
  exit 0
fi

# 2. Committed in the last 8 hours
if git -C "$REPO_ROOT" log --since="8 hours ago" --oneline -- STATUS.md 2>/dev/null | grep -q .; then
  exit 0
fi

# STATUS.md exists but wasn't updated this session
CURRENT_FOCUS=$(grep -A5 "## Current focus" "$STATUS_FILE" 2>/dev/null | grep "^- " | head -1 | sed 's/^- //')
LAST_UPDATED=$(grep "^updated:" "$STATUS_FILE" 2>/dev/null | head -1 | sed 's/updated:[[:space:]]*//')

echo "STATUS.md was not updated this session."
echo ""
echo "  Current focus : ${CURRENT_FOCUS:-'(not set)'}"
echo "  Last updated  : ${LAST_UPDATED:-'(unknown)'}"
echo ""
echo "Update STATUS.md before finishing:"
echo "  - updated: $(date +%Y-%m-%d)"
echo "  - current_focus: what you worked on this session"
echo "  - next_up: what comes next"
exit 2

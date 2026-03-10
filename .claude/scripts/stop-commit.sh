#!/bin/bash
# Stop hook — commits any uncommitted changes at the end of each Claude turn.
# Fires once per response, after Claude has had a chance to write intentional commits.
# This is a safety net: if Claude already committed, there's nothing to do.

# Find repo root from script location — works when deployed to any repo
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT=$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)

if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

cd "$REPO_ROOT" || exit 0

# Nothing to commit
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  exit 0
fi

git add -A

# Get all changed files after staging
CHANGED=$(git diff --cached --name-only 2>/dev/null)
if [ -z "$CHANGED" ]; then
  exit 0
fi

# Build file list (basenames, up to 3, then "+N more")
FILE_COUNT=$(echo "$CHANGED" | wc -l | tr -d ' ')
if [ "$FILE_COUNT" -le 3 ]; then
  FILE_LIST=$(echo "$CHANGED" | while read -r f; do basename "$f"; done | tr '\n' ',' | sed 's/,$//' | sed 's/,/, /g')
else
  FIRST3=$(echo "$CHANGED" | head -3 | while read -r f; do basename "$f"; done | tr '\n' ',' | sed 's/,$//' | sed 's/,/, /g')
  REMAINING=$((FILE_COUNT - 3))
  FILE_LIST="${FIRST3} +${REMAINING} more"
fi

# Diff stats
NUMSTAT=$(git diff --cached --numstat 2>/dev/null)
ADDED=$(echo "$NUMSTAT" | awk '{sum += $1} END {print sum+0}')
DELETED=$(echo "$NUMSTAT" | awk '{sum += $2} END {print sum+0}')

STAT=""
if [ "$ADDED" -gt 0 ] && [ "$DELETED" -gt 0 ]; then
  STAT=" (+${ADDED} -${DELETED})"
elif [ "$ADDED" -gt 0 ]; then
  STAT=" (+${ADDED})"
elif [ "$DELETED" -gt 0 ]; then
  STAT=" (-${DELETED})"
fi

git commit -q -m "auto: ${FILE_LIST}${STAT}" --no-verify 2>/dev/null

exit 0

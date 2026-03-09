#!/bin/bash
# Auto-commit hook for Claude Code
# Receives JSON on stdin from PostToolUse event

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Find the git root for the changed file
REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null)

if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

cd "$REPO_ROOT"

# Only commit if there are changes
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  exit 0
fi

# Get relative path for commit message
REL_PATH=$(python3 -c "import os; print(os.path.relpath('$FILE_PATH', '$REPO_ROOT'))" 2>/dev/null || basename "$FILE_PATH")

git add -A
git commit -q -m "auto: update ${REL_PATH}" --no-verify 2>/dev/null

exit 0

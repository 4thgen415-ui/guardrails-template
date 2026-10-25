#!/usr/bin/env bash
# Claude Code PreToolUse hook for Edit|Write|MultiEdit.
# Reads the hook JSON on stdin. Exit 0 = allow, exit 2 = block (stderr is shown
# to the agent so it can correct course).
#
# Blocks:
#   1. Any edit to a test file while the test lock is active
#      (lock active when .ai/tests-locked exists or AI_TESTS_LOCKED=1;
#       create it with scripts/ai/lock-tests.sh).
#   2. New content containing placeholder text (outside docs and scripts/ai/).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ai/test-patterns.sh
source "$script_dir/test-patterns.sh"

if ! command -v jq >/dev/null 2>&1; then
  echo "pre-edit-guard: jq is required but not installed; blocking edit to stay safe." >&2
  exit 2
fi

input="$(cat)"
project_dir="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

tool="$(jq -r '.tool_name // empty' <<<"$input")"
file_path="$(jq -r '.tool_input.file_path // empty' <<<"$input")"

case "$tool" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

# Repo-relative path for pattern matching.
rel_path="$file_path"
if [[ "$rel_path" == "$project_dir"/* ]]; then
  rel_path="${rel_path#"$project_dir"/}"
fi

# 1. Test lock.
if [[ -f "$project_dir/.ai/tests-locked" || "${AI_TESTS_LOCKED:-0}" == "1" ]]; then
  if is_test_file "$rel_path"; then
    echo "BLOCKED: $rel_path is a test file and tests are locked (read-only during implementation)." >&2
    echo "Implement the smallest change in non-test code that makes the tests pass. Only the owner unlocks tests (scripts/ai/unlock-tests.sh)." >&2
    exit 2
  fi
fi

# 2. Placeholder text in new content. Docs and the guardrail scripts are exempt
# because they legitimately describe these patterns.
case "$rel_path" in
  *.md|*.mdc|*.txt|docs/*|scripts/ai/*|.githooks/*) exit 0 ;;
esac

new_content="$(jq -r '
  [ .tool_input.content?, .tool_input.new_string?, (.tool_input.edits[]?.new_string) ]
  | map(select(. != null)) | join("\n")' <<<"$input")"

placeholder_regex='lorem ipsum|TODO:? *implement|implement (this|me) later|your code here|rest of (the )?(code|implementation|file)|\.\.\. ?(existing|remaining) code|placeholder (text|implementation|value|content)|not implemented yet|INSERT_[A-Z_]+_HERE'

if match="$(grep -inE "$placeholder_regex" <<<"$new_content" | head -n 3)"; then
  if [[ -n "$match" ]]; then
    echo "BLOCKED: placeholder text in edit to $rel_path:" >&2
    echo "$match" >&2
    echo "Write the real implementation, or stop and say what information is missing." >&2
    exit 2
  fi
fi

exit 0

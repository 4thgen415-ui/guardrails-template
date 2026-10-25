#!/usr/bin/env bash
# Shared definition of "what is a test file". Sourced by the guard scripts so
# every check agrees. Extend TEST_FILE_REGEX if your project uses other layouts.
#   TypeScript/Cloudflare examples that match:
#     src/user-service.test.ts   test/worker.spec.ts   __tests__/d1-repo.ts
# shellcheck disable=SC2034  # used by the scripts that source this file
TEST_FILE_REGEX='(^|/)(tests?|__tests__|spec|e2e)/|\.(test|spec)\.[cm]?[jt]sx?$|_test\.(go|py)$|(^|/)test_[^/]*\.py$|^\.ai/oracles/'

# Directories/files that hold the guardrails themselves. They are never treated
# as test files even though some contain "test" in their names.
GUARDRAIL_PATH_REGEX='^(scripts/ai/|\.githooks/|\.github/|\.claude/|\.cursor/)'

is_test_file() {
  local path="$1"
  if [[ "$path" =~ $GUARDRAIL_PATH_REGEX ]]; then
    return 1
  fi
  [[ "$path" =~ $TEST_FILE_REGEX ]]
}

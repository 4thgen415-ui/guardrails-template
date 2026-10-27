#!/usr/bin/env bash
# ship.sh - the SHIP gate (AGENTS.md "Work modes: SANDBOX (default) and SHIP").
# Run only when the owner says "push this", "ship it" or "deploy". SANDBOX, the
# default, never runs this; it uses one quick check of under ~1 minute instead.
# Exits non-zero when anything fails; nothing merges to main until it passes.
# Usage: scripts/ai/ship.sh [BASE]   full suite + every guardrail check vs BASE (default origin/main)
#        scripts/ai/ship.sh --suite  full suite only (the AGENTS.md test, lint, typecheck, build commands)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2
export AI_MODE=ship
suite() {
  if [[ ! -f package.json ]]; then
    echo "NOT CONFIGURED: no package.json, so the npm test/lint/typecheck/build commands were not run."
    return 0
  fi
  npm test && npm run lint && npm run typecheck && npm run build
}
if [[ "${1:-}" == "--suite" ]]; then
  suite
  exit $?
fi
base="${1:-origin/main}"
failed=()
if suite; then echo "--- PASS full suite"; else echo "--- FAIL full suite"; failed+=("full suite"); fi
if scripts/ai/run-all-checks.sh "$base"; then echo "--- PASS run-all-checks"; else echo "--- FAIL run-all-checks"; failed+=("run-all-checks"); fi
echo
if [[ ${#failed[@]} -gt 0 ]]; then
  echo "SHIP BLOCKED: ${failed[*]}"
  exit 1
fi
echo "SHIP CHECKS PASSED (base $base)"

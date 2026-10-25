#!/usr/bin/env bash
# Fails if a change range touches test files AND non-test files together.
#
# Mechanism (no bypass label, by design):
#   - Tests change only in a test-only PR/commit (e.g. the owner's "red" commit).
#   - Implementation PRs must not touch tests at all.
#   A PR that mixes both always fails; split it instead.
#
# Usage:
#   scripts/ai/check-tests-untouched.sh BASE_REF HEAD_REF     # range BASE...HEAD
#   scripts/ai/check-tests-untouched.sh --staged              # pre-commit use
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ai/test-patterns.sh
source "$script_dir/test-patterns.sh"

if [[ "${1:-}" == "--staged" ]]; then
  mapfile -t changed < <(git diff --cached --name-only --diff-filter=ACDMRT)
  label="staged changes"
else
  base="${1:?usage: check-tests-untouched.sh BASE_REF HEAD_REF | --staged}"
  head="${2:-HEAD}"
  mapfile -t changed < <(git diff --name-only --diff-filter=ACDMRT "$base...$head")
  label="$base...$head"
fi

tests=()
others=()
for f in "${changed[@]}"; do
  [[ -z "$f" ]] && continue
  if is_test_file "$f"; then tests+=("$f"); else others+=("$f"); fi
done

echo "check-tests-untouched ($label): ${#tests[@]} test file(s), ${#others[@]} non-test file(s) changed"
if [[ ${#tests[@]} -gt 0 && ${#others[@]} -gt 0 ]]; then
  echo "FAIL: test files changed together with implementation files." >&2
  printf '  test:     %s\n' "${tests[@]}" >&2
  printf '  non-test: %s\n' "${others[@]}" >&2
  echo "Tests are read-only during implementation. Put test changes in their own test-only PR." >&2
  exit 1
fi
echo "OK"

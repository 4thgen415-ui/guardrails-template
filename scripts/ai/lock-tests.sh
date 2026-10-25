#!/usr/bin/env bash
# Make every tracked test file read-only and activate the edit guard.
# Run after committing the failing tests (the "red" step).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ai/test-patterns.sh
source "$script_dir/test-patterns.sh"
root="$(git rev-parse --show-toplevel)"
cd "$root"
count=0
while IFS= read -r f; do
  if [[ -f "$f" ]] && is_test_file "$f"; then
    chmod a-w "$f"
    count=$((count + 1))
  fi
done < <(git ls-files)
mkdir -p .ai
touch .ai/tests-locked
echo "Locked $count test file(s) (chmod a-w) and created .ai/tests-locked"

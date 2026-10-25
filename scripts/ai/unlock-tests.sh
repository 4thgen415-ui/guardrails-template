#!/usr/bin/env bash
# Owner-only: restore write permission on test files and deactivate the guard.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ai/test-patterns.sh
source "$script_dir/test-patterns.sh"
root="$(git rev-parse --show-toplevel)"
cd "$root"
count=0
while IFS= read -r f; do
  if [[ -f "$f" ]] && is_test_file "$f"; then
    chmod u+w "$f"
    count=$((count + 1))
  fi
done < <(git ls-files)
rm -f .ai/tests-locked
echo "Unlocked $count test file(s) and removed .ai/tests-locked"

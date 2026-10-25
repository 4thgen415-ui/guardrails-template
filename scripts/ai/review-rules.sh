#!/usr/bin/env bash
# review-rules.sh: turn recurring review feedback into deterministic checks.
#
# Each rule is one line in the RULES table below:
#   id | scope | extended-regex | message
# scope:
#   src   = source code files (see SOURCE_EXT), excluding docs and guardrail files
#   tests = test files only (see scripts/ai/test-patterns.sh)
#   all   = every tracked text file except this script
# Per-line opt-out (use sparingly, reviewers see it):  review-rules: allow
#
# Usage: scripts/ai/review-rules.sh [repo-root]    (exit 1 if any rule matches)
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/ai/test-patterns.sh
source "$script_dir/test-patterns.sh"

root="${1:-$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$root"

SOURCE_EXT='\.(ts|tsx|js|jsx|mjs|cjs|py|go|rs|vue|svelte|astro|sql)$'
# Docs, agent instructions, and the guardrail implementation are not "source":
# they describe these patterns on purpose (e.g. the FILL markers in AGENTS.md).
EXCLUDE_REGEX='(^|/)(docs/|node_modules/|dist/|build/|\.wrangler/|coverage/)|^(AGENTS\.md|CLAUDE\.md|README\.md)$|^(scripts/ai/|\.githooks/|\.cursor/|\.claude/|\.github/)|\.(md|mdc)$'

RULES=$(cat <<'RULES_EOF'
placeholder-text|src|lorem ipsum|TODO:? *implement|implement (this|me) later|your code here|rest of (the )?(code|implementation)|\.\.\. ?(existing|remaining) code|placeholder (text|implementation|value|content)|not implemented yet|INSERT_[A-Z_]+_HERE|Placeholder text in source. Write the real implementation.
console-log|src|console\.log\(|Leftover console.log. Use the project logger or remove it.
debugger|src|^[[:space:]]*debugger;?[[:space:]]*$|Leftover debugger statement.
focused-test|tests|\b(describe|it|test|context)\.only\(|Focused test (.only) skips the rest of the suite.
hardcoded-key|all|(sk|rk)_live_[0-9A-Za-z]{8,}|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{36}|xox[abprs]-[0-9A-Za-z-]{10,}|-----BEGIN ([A-Z]+ )?PRIVATE KEY-----|Hardcoded secret. Move it to a Worker secret (wrangler secret put) or the CI secret store.
RULES_EOF
)

list_files() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git ls-files -co --exclude-standard
  else
    find . -type f -not -path './.git/*' | sed 's#^\./##'
  fi
}

mapfile -t files < <(list_files | sort -u)
violations=0

while IFS= read -r rule; do
  [[ -z "$rule" ]] && continue
  id="${rule%%|*}"; rest="${rule#*|}"
  scope="${rest%%|*}"; rest="${rest#*|}"
  message="${rest##*|}"
  regex="${rest%|*}"
  for f in "${files[@]}"; do
    [[ -f "$f" ]] || continue
    [[ "$f" == "scripts/ai/review-rules.sh" ]] && continue
    case "$scope" in
      src)
        [[ "$f" =~ $SOURCE_EXT ]] || continue
        [[ "$f" =~ $EXCLUDE_REGEX ]] && continue
        # console.log/debugger rules ignore tests; placeholder rule covers them too.
        if [[ "$id" != "placeholder-text" ]] && is_test_file "$f"; then continue; fi
        ;;
      tests)
        is_test_file "$f" || continue
        ;;
      all)
        grep -Iq . "$f" 2>/dev/null || continue   # skip binary/empty files
        ;;
    esac
    while IFS= read -r hit; do
      [[ -z "$hit" ]] && continue
      [[ "$hit" == *"review-rules: allow"* ]] && continue
      echo "$f:${hit%%:*}: [$id] $message"
      violations=$((violations + 1))
    done < <(grep -nE -i -- "$regex" "$f" 2>/dev/null | cut -c1-200 || true)
  done
done <<<"$RULES"

if [[ "$violations" -gt 0 ]]; then
  echo "review-rules: $violations violation(s)"
  exit 1
fi
echo "review-rules: clean (${#files[@]} files checked)"

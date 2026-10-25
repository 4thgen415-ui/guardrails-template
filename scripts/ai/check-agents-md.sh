#!/usr/bin/env bash
# Structural check for AGENTS.md and CLAUDE.md. Runs locally and in CI.
#  - every required "## " heading exists
#  - Commands has Test/Lint/Typecheck/Build subsections, each with a fenced block
#  - required never-do rules are present
#  - CLAUDE.md is exactly the one-line import "@AGENTS.md"
#  - no "<!-- FILL:" slots remain, unless .guardrails-template exists (template repo)
set -euo pipefail
root="${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$root"
f="AGENTS.md"
errors=0
err() { echo "FAIL: $*"; errors=$((errors + 1)); }

[[ -f "$f" ]] || { echo "FAIL: $f missing"; exit 1; }

required_headings=(
  "## Stack"
  "## Commands"
  "## Naming conventions"
  "## Modules"
  "## Never-dos"
  "## Branch workflow"
  "## Definition of done"
  "## Verification"
  "## Trust but verify"
  "## Security tools"
)
for h in "${required_headings[@]}"; do
  grep -qxF "$h" "$f" || err "missing heading '$h'"
done

# Each command subsection needs a fenced block before the next heading.
for sub in Test Lint Typecheck Build; do
  if ! awk -v h="### $sub" '
      $0 == h { in_sec = 1; next }
      in_sec && /^#/ { exit }
      in_sec && /^```/ { fences++ }
      END { exit !(fences >= 2) }' "$f"; then
    err "Commands: '### $sub' needs a fenced command block"
  fi
done

never_do_patterns=(
  "Never edit tests during implementation"
  "Never write placeholder text"
  "Never remove features, text, or files without owner approval"
  "Never deploy by hand"
  "Never commit secrets"
  "without checking it with Socket"
  "Never commit to \`main\`"
  "Never create branches"
)
for p in "${never_do_patterns[@]}"; do
  grep -qF "$p" "$f" || err "Never-dos: missing rule '$p'"
done

grep -qF "snyk-agent-scan" "$f" || err "Security tools: Snyk Agent Scan command missing"
grep -qF "CodeQL" "$f" || err "Security tools: CodeQL missing"

if [[ ! -f CLAUDE.md ]]; then
  err "CLAUDE.md missing"
elif [[ "$(cat CLAUDE.md)" != "@AGENTS.md" ]]; then
  err "CLAUDE.md must contain exactly the line '@AGENTS.md'"
fi

fill_count="$(grep -c '<!-- FILL:' "$f" || true)"
if [[ -f .guardrails-template ]]; then
  echo "template repo: $fill_count FILL slot(s) allowed"
elif [[ "$fill_count" -gt 0 ]]; then
  grep -n '<!-- FILL:' "$f" | sed 's/^/  /'
  err "$fill_count unfilled FILL slot(s) in $f (fill them, see README first steps)"
fi

if [[ "$errors" -gt 0 ]]; then
  echo "check-agents-md: $errors problem(s)"
  exit 1
fi
echo "check-agents-md: OK (${#required_headings[@]} headings, 4 command slots, ${#never_do_patterns[@]} never-dos)"

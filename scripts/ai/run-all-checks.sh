#!/usr/bin/env bash
# Local check-fix loop: everything CI's guardrail jobs run, plus YAML/JSON/MDC
# validation. Usage: scripts/ai/run-all-checks.sh [BASE_REF]
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
cd "$root" || exit 1
base="${1:-}"
failed=()
step() {
  local name="$1"; shift
  echo "=== $name"
  if "$@"; then echo "--- PASS $name"; else echo "--- FAIL $name"; failed+=("$name"); fi
}
validate_yaml() {
  python3 - "$@" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("PyYAML not installed; skipping YAML parse (actionlint still parses workflows)")
    sys.exit(0)
for p in sys.argv[1:]:
    yaml.safe_load(open(p))
    print("yaml ok", p)
PY
}
validate_mdc() {
  python3 - .cursor/rules/*.mdc <<'PY'
import re, sys
ok = True
for p in sys.argv[1:]:
    text = open(p).read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        print("no frontmatter", p); ok = False; continue
    fm = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
    fm = {k.strip(): v.strip() for k, v in fm.items()}
    keys_ok = set(fm) == {"description", "globs", "alwaysApply"}
    if not (keys_ok and fm["alwaysApply"] == "true" and fm["description"]):
        print("bad frontmatter", p, fm); ok = False
    else:
        print("mdc ok", p, fm)
sys.exit(0 if ok else 1)
PY
}
step "actionlint" actionlint
step "shellcheck" shellcheck -x scripts/ai/*.sh .githooks/pre-commit
step "json (jq)" jq -e . .claude/settings.json
step "yaml" validate_yaml .github/workflows/*.yml .github/dependabot.yml
step "simple-yaml (.ai)" python3 -c "import sys; sys.path.insert(0,'scripts/ai'); from ai_config import parse_simple_yaml as p; [print(f, p(open(f).read())) for f in ['.ai/config.yml','.ai/change-scope.yml']]"
step "mdc frontmatter" validate_mdc
step "py_compile" python3 -m py_compile scripts/ai/*.py scripts/ai/backstops/*.py
step "python unit tests" python3 -m unittest discover -s scripts/ai -p 'test_*.py'
step "backstops unit tests" python3 -m unittest discover -s scripts/ai/backstops -p 'test_*.py'
step "pre-edit hook tests" scripts/ai/test-pre-edit-guard.sh
step "review-rules self-test" scripts/ai/test-review-rules.sh
step "pr-loop tests" scripts/ai/test-pr-loop.sh
step "self-heal / auto-revert tests" scripts/ai/test-self-heal.sh
step "calibration (temp dir)" scripts/ai/calibrate.sh
if command -v node >/dev/null && node -e 'process.exit(+process.versions.node.split(".")[0] >= 23 ? 0 : 1)'; then
  step "oracles" python3 scripts/ai/oracle.py
else
  echo "=== oracles: SKIPPED locally (needs Node 23+ for .ts; CI app-checks runs them on Node 24)"
fi
step "review-rules on repo" scripts/ai/review-rules.sh
step "check-agents-md" scripts/ai/check-agents-md.sh
step "module_lint (report)" python3 scripts/ai/module_lint.py
if [[ -n "$base" ]]; then
  step "check-tests-untouched" scripts/ai/check-tests-untouched.sh "$base" HEAD
  step "editlint" python3 scripts/ai/editlint.py --base "$base"
  step "module_lint --base" python3 scripts/ai/module_lint.py --base "$base"
  step "backstops diff" python3 scripts/ai/backstops/backstops.py diff --base "$base"
  step "package_check" python3 scripts/ai/package_check.py --base "$base"
fi
echo
if [[ ${#failed[@]} -gt 0 ]]; then echo "FAILED: ${failed[*]}"; exit 1; fi
echo "ALL CHECKS PASSED"

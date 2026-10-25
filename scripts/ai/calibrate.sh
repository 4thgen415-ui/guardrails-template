#!/usr/bin/env bash
# Calibration: prove every guard still catches what it is meant to catch.
# Copies this repo into a temp dir (never a branch here), creates main and
# staging there, seeds one known-bad change per guard on staging and expects a
# failure, then seeds one known-good change and expects every guard to pass.
# A guard that stops catching its seeded fault fails this script.
set -euo pipefail
src="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
w="$tmp/repo"
mkdir -p "$w"
(cd "$src" && git ls-files -z -co --exclude-standard | xargs -0 -I{} cp --parents {} "$w/")
cd "$w"
git init -q -b main
git config user.email calib@example.invalid
git config user.name calibrate
git add -A && git commit -qm "calibration base"
git branch staging

caught=0; missed=0
expect() { # expect fail|pass "label" command...
  local want="$1" label="$2"; shift 2
  if "$@" >/dev/null 2>&1; then got=pass; else got=fail; fi
  if [[ "$got" == "$want" ]]; then caught=$((caught+1)); echo "ok   $label ($want as expected)"
  else missed=$((missed+1)); echo "MISS $label (expected $want, got $got)"; fi
}
fresh() { git switch -q staging; git reset -q --hard main; git clean -qfd; }
src_file=examples/honest-empty-result/src/find-users.ts
test_file=examples/honest-empty-result/test/find-users.test.ts

fresh; printf '\nexport const note = "TODO: implement later";\n' >> "$src_file"; git commit -qam seed
expect fail "review-rules: placeholder text" scripts/ai/review-rules.sh .

fresh; echo "// tweak" >> "$test_file"; echo "// tweak" >> "$src_file"; git commit -qam seed
expect fail "tests-untouched: test + code in one change" scripts/ai/check-tests-untouched.sh main HEAD

fresh; sed -i 's/if (rows.length === 0) {/if (rows.length < 1) {/' "$src_file"; git commit -qam seed
expect fail "editlint: unscoped change to findUsersByOrg" python3 scripts/ai/editlint.py --base main

fresh; printf '\nexport const DOCS_URL = "https://docs.example.com/path/to/api";\n' >> "$src_file"; git commit -qam seed
expect fail "backstops: fabricated URL" python3 scripts/ai/backstops/backstops.py diff --base main

fresh; mkdir -p src; for i in $(seq 1 12); do printf 'export function f%s(): number {\n  return %s;\n}\n' "$i" "$i"; done > src/big.ts; git add -A; git commit -qm seed
expect fail "module-lint: new file over 10 symbols" python3 scripts/ai/module_lint.py --base main

fresh; printf '{"cmd": "echo invented", "expect_stdout": "real\\n"}\n' > "$tmp/oracle.json"; mkdir -p "$tmp/oracles"; mv "$tmp/oracle.json" "$tmp/oracles/"
expect fail "oracle: wrong output" python3 scripts/ai/oracle.py --root "$tmp" --dir oracles

fresh; printf '{"dependencies": {"cloudflare-d1-orm-helper-zz9": "^1.0.0"}}\n' > package.json; git add -A; git commit -qm seed
if python3 scripts/ai/package_check.py npm:left-pad >/dev/null 2>&1; then
  expect fail "packages: hallucinated npm package" python3 scripts/ai/package_check.py --base main
else
  echo "skip packages: registry unreachable from here"
fi

fresh; git switch -q main
echo "x" > stray.txt; git add stray.txt
expect fail "pre-commit: agent commit on main" env AI_AGENT=1 .githooks/pre-commit
git reset -q; rm -f stray.txt

fresh; printf '\nexport const PAGE_SIZE = 50;\n' >> "$src_file"
cat > .ai/change-scope.yml <<'SCOPE'
mode: feature
symbols: [PAGE_SIZE]
paths: [examples/honest-empty-result/src/find-users.ts, .ai/change-scope.yml]
delete_files: []
max_changed_lines: 300
max_files: 15
SCOPE
git commit -qam "known-good change"
expect pass "known-good: review-rules" scripts/ai/review-rules.sh .
expect pass "known-good: tests-untouched" scripts/ai/check-tests-untouched.sh main HEAD
expect pass "known-good: editlint" python3 scripts/ai/editlint.py --base main
expect pass "known-good: backstops" python3 scripts/ai/backstops/backstops.py diff --base main
expect pass "known-good: module-lint" python3 scripts/ai/module_lint.py --base main
expect pass "known-good: pre-commit on staging" env AI_AGENT=1 .githooks/pre-commit

echo "calibration: $caught as expected, $missed missed"
[[ "$missed" -eq 0 ]]

#!/usr/bin/env bash
# Tests pr-loop.sh with a fake `gh` on PATH (no network).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'FAKE'
#!/usr/bin/env bash
echo "gh $*" >>"$FAKE_GH_LOG"
args="$*"
case "$args" in
  "api repos/o/r/issues/7/comments"*) cat "$FAKE_GH_COMMENTS" 2>/dev/null || true ;;
  "api repos/o/r/commits/"*"/check-runs"*) echo "${FAKE_FAILING:-0}" ;;
  "api repos/o/r/commits/staging"*) echo "Add getUserById" ;;
  "api repos/o/r/git/ref/heads/main"*) echo "${FAKE_MAIN_SHA:-1111111aaaa}" ;;
  "api repos/o/r/git/ref/heads/staging"*) echo "${FAKE_STAGING_SHA:-2222222bbbb}" ;;
  "api repos/o/r/compare/main...staging --jq .ahead_by") echo "${FAKE_AHEAD:-1}" ;;
  "api repos/o/r/compare/main...staging --jq .status")
    if [[ -s "${FAKE_STATUS_FILE:-/nonexistent}" ]]; then head -1 "$FAKE_STATUS_FILE"; sed -i 1d "$FAKE_STATUS_FILE"; else echo "${FAKE_STATUS:-behind}"; fi ;;
  "api -X PATCH repos/o/r/git/refs/heads/staging"*) [[ -z "${FAKE_PATCH_FAIL:-}" ]] || exit 1 ;;
  "api repos/o/r/actions/runs/"*"/jobs --jq .total_count") echo "${FAKE_JOB_COUNT:-3}" ;;
  "api repos/o/r/actions/runs/"*"/jobs --jq [.jobs[]"*) echo "${FAKE_STARTED:-3}" ;;
  "api repos/o/r/actions/runs/"*"/jobs --jq .jobs[].check_run_url") echo "https://api.github.com/repos/o/r/check-runs/9" ;;
  "api https://api.github.com/repos/o/r/check-runs/9/annotations"*) echo "${FAKE_ANNOTATION:-}" ;;
  "pr list"*) echo "${FAKE_OPEN_PR:-}" ;;
  "issue list"*) echo "${FAKE_LOG_ISSUE:-42}" ;;
  "pr comment"*|"issue comment"*) cat >/dev/null ;;
  "run view"*) echo "Error: expected 3, got 2" ;;
esac
exit 0
FAKE
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" FAKE_GH_LOG="$tmp/log" FAKE_GH_COMMENTS="$tmp/comments"
export GH_REPO=o/r RUN_NAME="AI Guardrails" HEAD_BRANCH=staging PR_NUMBER=7
pass=0; fail=0
check() { if grep -qF -- "$2" "$tmp/log"; then pass=$((pass+1)); echo "ok   $1"; else fail=$((fail+1)); echo "FAIL $1 (missing: $2)"; cat "$tmp/log"; fi; }
check_not() { if ! grep -qF -- "$2" "$tmp/log"; then pass=$((pass+1)); echo "ok   $1"; else fail=$((fail+1)); echo "FAIL $1 (unexpected: $2)"; fi; }
run() { : >"$tmp/log"; "$script_dir/pr-loop.sh" "$@" >/dev/null; }

: >"$tmp/comments"
RUN_ID=101 CONCLUSION=failure HEAD_SHA=aaaa111 run result
check "failure adds ai-failing" "pr edit 7 --add-label ai-failing"
check "failure comments with output" "pr comment 7 --body-file -"
check "failure is logged as a strike" "issue comment 42"
check_not "1 attempt does not close" "pr close"

printf '%s\n' "<!-- ai-pr-loop:failure sha=aaaa111 run=101 -->" "<!-- ai-pr-loop:failure sha=bbbb222 run=102 -->" >"$tmp/comments"
RUN_ID=103 CONCLUSION=failure HEAD_SHA=cccc333 run result
check "cap adds ai-retry-cap-reached" "pr edit 7 --add-label ai-retry-cap-reached"
check "cap closes PR" "pr close 7 --comment"
check_not "cap never deletes a branch" "--delete-branch"
check "cap archives staging as a tag" "git/refs -f ref=refs/tags/archive/staging-"
check "cap resets staging to main" "git/refs/heads/staging -f sha=1111111aaaa -F force=true"

printf '%s\n' "<!-- ai-pr-loop:failure sha=aaaa111 run=101 -->" >"$tmp/comments"
RUN_ID=101 CONCLUSION=failure HEAD_SHA=aaaa111 run result
check_not "no duplicate comment for same run" "pr comment"

RUN_ID=104 CONCLUSION=success HEAD_SHA=dddd444 FAKE_FAILING=0 run result
check "success removes ai-failing" "pr edit 7 --remove-label ai-failing"

# Runs that never executed are unverifiable: no strike, no failure label.
: >"$tmp/comments"
RUN_ID=201 CONCLUSION=failure HEAD_SHA=f001 FAKE_JOB_COUNT=0 run result
check_not "0-job run is not a strike" "--add-label ai-failing"
check_not "0-job run logs nothing" "issue comment 42"
RUN_ID=202 CONCLUSION=startup_failure HEAD_SHA=f002 run result
check_not "startup_failure is not a strike" "--add-label ai-failing"
RUN_ID=203 CONCLUSION=failure HEAD_SHA=f003 FAKE_STARTED=0 \
  FAKE_ANNOTATION="The job was not started because recent account payments have failed or your spending limit needs to be increased." run result
check "billing refusal is labelled unverifiable" "--add-label ai-unverifiable"
check "billing refusal is logged as unverifiable" "issue comment 42"
check_not "billing refusal is not a strike" "--add-label ai-failing"
out="$(GITHUB_OUTPUT="$tmp/out" RUN_ID=204 CONCLUSION=failure HEAD_SHA=f004 FAKE_JOB_COUNT=0 "$script_dir/pr-loop.sh" result; cat "$tmp/out")"
if grep -qx "verdict=unverifiable" <<<"$out"; then pass=$((pass+1)); echo "ok   verdict output blocks self-heal"; else fail=$((fail+1)); echo "FAIL verdict: $out"; fi
: >"$tmp/out"
GITHUB_OUTPUT="$tmp/out" RUN_ID=205 CONCLUSION=failure HEAD_SHA=f005 "$script_dir/pr-loop.sh" result >/dev/null
if grep -qx "verdict=failing" "$tmp/out"; then pass=$((pass+1)); echo "ok   real failure verdict is failing"; else fail=$((fail+1)); echo "FAIL real verdict: $(cat "$tmp/out")"; fi
for case in "0:3:" "3:0:spending limit" "3:0:" ; do
  IFS=: read -r jc st an <<<"$case"
  got="$(FAKE_JOB_COUNT=$jc FAKE_STARTED=$st FAKE_ANNOTATION=$an "$script_dir/pr-loop.sh" classify 9 failure)"
  want=no-jobs; [[ "$jc" != 0 ]] && { [[ -n "$an" ]] && want=billing || want=not-started; }
  if [[ "$got" == "$want" ]]; then pass=$((pass+1)); echo "ok   classify $case -> $got"; else fail=$((fail+1)); echo "FAIL classify $case: $got != $want"; fi
done
: >"$tmp/comments"

HEAD_BRANCH=feature/x RUN_ID=105 CONCLUSION=failure HEAD_SHA=eeee555 run result
check_not "non-staging branch untouched" "pr edit"

FAKE_AHEAD=2 FAKE_OPEN_PR="" run open-pr
check "open-pr creates staging->main PR" "pr create --base main --head staging"
FAKE_AHEAD=0 run open-pr
check_not "no PR when staging not ahead" "pr create"
FAKE_AHEAD=2 FAKE_OPEN_PR=9 run open-pr
check_not "no duplicate PR" "pr create"

run auto-merge 7
check "auto-merge uses merge commits" "pr merge 7 --auto --merge"
check_not "auto-merge keeps staging" "--delete-branch"

FAKE_STATUS=behind run sync-staging
check "sync fast-forwards staging" "git/refs/heads/staging -f sha=1111111aaaa -F force=false"
FAKE_STATUS=ahead run sync-staging
check_not "sync leaves unmerged work" "PATCH"
printf 'behind\nidentical\n' >"$tmp/status"
if FAKE_STATUS_FILE="$tmp/status" FAKE_PATCH_FAIL=1 run sync-staging; then pass=$((pass+1)); echo "ok   sync tolerates losing the race"; else fail=$((fail+1)); echo "FAIL sync race"; fi
printf 'behind\ndiverged\n' >"$tmp/status"
if FAKE_STATUS_FILE="$tmp/status" FAKE_PATCH_FAIL=1 run sync-staging 2>/dev/null; then fail=$((fail+1)); echo "FAIL sync real failure passed"; else pass=$((pass+1)); echo "ok   sync fails when staging really is stuck"; fi

FAKE_MAIN_SHA=same FAKE_STAGING_SHA=same run reset-staging test
check_not "reset no-op when equal" "PATCH"

echo "pr-loop: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]

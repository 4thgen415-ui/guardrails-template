#!/usr/bin/env bash
# Staging -> main PR loop, called by .github/workflows/agent-pr-loop.yml,
# reset-staging.yml and auto-revert. Topology: exactly two branches, main and staging.
#
#   pr-loop.sh open-pr                 open the staging->main PR if staging is ahead and none is open
#   pr-loop.sh auto-merge PR           enable auto-merge (merge commit, staging is never deleted)
#   pr-loop.sh result                  handle a finished workflow_run (env below)
#   pr-loop.sh sync-staging            after a push to main: fast-forward staging to main
#   pr-loop.sh reset-staging REASON    archive staging as a tag, then reset it to main
#   pr-loop.sh auto-revert SHA         main broke after SHA: put a revert on staging and open a PR
#   pr-loop.sh log EVENT [DETAILS]     append an event to the activity log issue
#
# result env: RUN_ID, RUN_NAME, CONCLUSION, HEAD_SHA, HEAD_BRANCH, PR_NUMBER (may be empty), GH_REPO
set -euo pipefail

MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
FAIL_MARKER="<!-- ai-pr-loop:failure"
LOG_LABEL="ai-activity-log"
: "${GH_REPO:=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)}"

ensure_label() {
  gh label create "$1" --color "$2" --description "$3" --force >/dev/null 2>&1 || true
}

ref_sha() {
  gh api "repos/$GH_REPO/git/ref/heads/$1" --jq .object.sha
}

log_event() {
  local event="$1" details="${2:-}"
  ensure_label "$LOG_LABEL" 0e8a16 "Append-only log of automated agent actions"
  local issue
  issue="$(gh issue list --label "$LOG_LABEL" --state all --limit 1 --json number --jq '.[0].number // empty')"
  if [[ -z "$issue" ]]; then
    issue="$(gh issue create --title "AI activity log" --label "$LOG_LABEL" \
      --body "Append-only log of automated actions (merges, strikes, resets, reverts). Read by scripts/ai/autonomy.py." \
      | grep -oE '[0-9]+$')"
  fi
  local line
  line="$(jq -cn --arg event "$event" --arg details "$details" --arg run "${GITHUB_RUN_ID:-local}" \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{ts: $ts, event: $event, details: $details, run: $run}')"
  gh issue comment "$issue" --body "<!-- ai-activity --> $line" >/dev/null
  echo "activity: $line"
}

open_pr() {
  local ahead
  ahead="$(gh api "repos/$GH_REPO/compare/main...staging" --jq .ahead_by)"
  if [[ "$ahead" == "0" ]]; then
    echo "staging is not ahead of main; no PR needed."
    return 0
  fi
  local existing
  existing="$(gh pr list --head staging --base main --state open --json number --jq '.[0].number // empty')"
  if [[ -n "$existing" ]]; then
    echo "PR #$existing already open for staging -> main"
    return 0
  fi
  local subject
  subject="$(gh api "repos/$GH_REPO/commits/staging" --jq '.commit.message | split("\n")[0]')"
  gh pr create --base main --head staging --title "staging -> main: $subject" \
    --body "Automated staging -> main PR ($ahead commit(s)). CI is the only gate; it auto-merges when every required check is green."
}

auto_merge() {
  local pr="$1"
  if gh pr merge "$pr" --auto --merge; then
    echo "Auto-merge (merge commit) enabled for #$pr; staging is kept."
  else
    echo "::warning::Could not enable auto-merge for #$pr. One-time setup: allow_auto_merge=true and main protection with required checks (bootstrap.yml)."
  fi
}

archive_tag() {
  local sha="$1" tag
  tag="archive/staging-$(date -u +%Y%m%d%H%M%S)-${sha:0:7}"
  gh api -X POST "repos/$GH_REPO/git/refs" -f ref="refs/tags/$tag" -f sha="$sha" >/dev/null
  echo "$tag"
}

reset_staging() {
  local reason="${1:-manual}"
  local main_sha staging_sha tag
  main_sha="$(ref_sha main)"
  staging_sha="$(ref_sha staging)"
  if [[ "$main_sha" == "$staging_sha" ]]; then
    echo "staging already equals main ($main_sha)"
    return 0
  fi
  tag="$(archive_tag "$staging_sha")"
  gh api -X PATCH "repos/$GH_REPO/git/refs/heads/staging" -f sha="$main_sha" -F force=true >/dev/null
  echo "staging reset to main ${main_sha:0:7}; previous staging ${staging_sha:0:7} archived as $tag"
  log_event staging_reset "$reason; archived $tag"
}

sync_staging() {
  local status
  status="$(gh api "repos/$GH_REPO/compare/main...staging" --jq .status)"
  case "$status" in
    identical) echo "staging == main" ;;
    behind)
      gh api -X PATCH "repos/$GH_REPO/git/refs/heads/staging" -f sha="$(ref_sha main)" -F force=false >/dev/null
      echo "staging fast-forwarded to main" ;;
    ahead) echo "staging has unmerged work ahead of main; left as is" ;;
    diverged) echo "staging has new work and main has merge commits it lacks; the next staging -> main merge reconciles them" ;;
  esac
}

handle_result() {
  : "${RUN_ID:?}" "${CONCLUSION:?}" "${HEAD_SHA:?}" "${HEAD_BRANCH:?}"
  if [[ "$HEAD_BRANCH" != "staging" ]]; then
    echo "Not staging ($HEAD_BRANCH); nothing to do."
    return 0
  fi
  local pr="${PR_NUMBER:-}"
  if [[ -z "$pr" ]]; then
    pr="$(gh pr list --head staging --base main --state open --json number --jq '.[0].number // empty')"
  fi
  if [[ -z "$pr" ]]; then
    echo "No open staging -> main PR; nothing to do."
    return 0
  fi

  if [[ "$CONCLUSION" != "failure" && "$CONCLUSION" != "timed_out" ]]; then
    local failing
    failing="$(gh api "repos/$GH_REPO/commits/$HEAD_SHA/check-runs" --paginate \
      --jq '[.check_runs[] | select(.conclusion == "failure" or .conclusion == "timed_out")] | length' | awk '{s+=$1} END {print s+0}')"
    if [[ "$failing" == "0" ]]; then
      gh pr edit "$pr" --remove-label ai-failing >/dev/null 2>&1 || true
      echo "No failing checks on $HEAD_SHA; removed ai-failing from #$pr if present."
    fi
    return 0
  fi

  ensure_label ai-failing d73a4a "A required check failed on the latest staging push"
  ensure_label ai-retry-cap-reached 5319e7 "Staging reset to main after $MAX_ATTEMPTS failed attempts"
  gh pr edit "$pr" --add-label ai-failing >/dev/null

  local this_marker="$FAIL_MARKER sha=$HEAD_SHA run=$RUN_ID -->"
  local bodies
  bodies="$(gh api "repos/$GH_REPO/issues/$pr/comments" --paginate --jq '.[].body')"
  if ! grep -qF "run=$RUN_ID -->" <<<"$bodies"; then
    local log
    log="$(gh run view "$RUN_ID" --log-failed 2>/dev/null | tail -n 150 | cut -c1-300 || true)"
    [[ -z "$log" ]] && log="(failed-step log unavailable; see the run page)"
    gh pr comment "$pr" --body-file - <<COMMENT
$this_marker
### ${RUN_NAME:-Workflow} failed on \`${HEAD_SHA:0:7}\`

Run: ${GITHUB_SERVER_URL:-https://github.com}/$GH_REPO/actions/runs/$RUN_ID

<details><summary>Failing output (last 150 lines)</summary>

\`\`\`text
$log
\`\`\`

</details>

Fix the cause in non-test code and push again to staging.
COMMENT
    bodies="$bodies"$'\n'"$this_marker"
    log_event strike "PR #$pr ${RUN_NAME:-workflow} run $RUN_ID sha ${HEAD_SHA:0:7}"
  fi

  # Attempts = distinct staging commits that had at least one failing workflow.
  local attempts
  attempts="$(grep -oE "ai-pr-loop:failure sha=[0-9a-f]+" <<<"$bodies" | sort -u | wc -l | tr -d ' ')"
  echo "staging PR #$pr: $attempts failed attempt(s) of $MAX_ATTEMPTS"
  if (( attempts >= MAX_ATTEMPTS )); then
    gh pr edit "$pr" --add-label ai-retry-cap-reached >/dev/null
    local why="Closed automatically: $attempts pushes to staging failed required checks (cap $MAX_ATTEMPTS). Last failure: ${RUN_NAME:-workflow} run $RUN_ID on ${HEAD_SHA:0:7}. Staging is archived as a tag and reset to main; start again with a narrower scope."
    gh pr close "$pr" --comment "$why"
    reset_staging "retry cap reached on PR #$pr"
    log_event retry_cap "PR #$pr closed after $attempts attempts"
    echo "$why"
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then echo "$why" >>"$GITHUB_STEP_SUMMARY"; fi
  fi
}

auto_revert() {
  # Needs a checkout with push access to staging (GUARDRAILS_ADMIN_TOKEN so CI runs on the push).
  local bad="${1:?sha}"
  git fetch -q origin main staging
  if ! git log -1 --format=%s "$bad" | grep -qE '^Merge pull request #[0-9]+ from [^ ]+/staging$'; then
    echo "auto-revert: ${bad:0:7} is not a staging merge; not reverting (main is changed only through staging PRs)"
    return 0
  fi
  if [[ "$(git rev-parse origin/main)" != "$(git rev-parse "$bad")" ]]; then
    echo "auto-revert: main moved past ${bad:0:7}; not reverting a stale failure"
    return 0
  fi
  if [[ "$(git rev-parse origin/staging)" != "$(git rev-parse origin/main)" ]] &&
     ! git merge-base --is-ancestor origin/staging origin/main; then
    reset_staging "auto_revert of ${bad:0:7}: clearing unmerged staging work first"
    git fetch -q origin staging
  fi
  git switch -q -C staging origin/main
  if [[ "$(git rev-list --parents -n 1 "$bad" | wc -w)" -gt 2 ]]; then
    git revert --no-edit -m 1 "$bad"
  else
    git revert --no-edit "$bad"
  fi
  git push origin staging
  log_event auto_revert "main broke after ${bad:0:7}; revert pushed to staging"
  open_pr
}

case "${1:-}" in
  open-pr) open_pr ;;
  auto-merge) auto_merge "${2:?pr number}" ;;
  result) handle_result ;;
  sync-staging) sync_staging ;;
  reset-staging) reset_staging "${2:-manual}" ;;
  auto-revert) auto_revert "${2:?sha}" ;;
  log) log_event "${2:?event}" "${3:-}" ;;
  *) echo "usage: pr-loop.sh open-pr | auto-merge PR | result | sync-staging | reset-staging REASON | auto-revert SHA | log EVENT [DETAILS]" >&2; exit 2 ;;
esac

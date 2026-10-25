#!/usr/bin/env bash
# Self-healing on staging: find the first commit in main..staging that breaks
# CHECK_CMD (walks the commits oldest first) and push a revert of it to staging (fast-forward,
# never a force push). Runs from agent-pr-loop.yml when autonomy allows it.
#
# Usage: self-heal.sh [--no-push]      env: CHECK_CMD (default scripts/ai/run-all-checks.sh)
set -euo pipefail
push=1
[[ "${1:-}" == "--no-push" ]] && push=0
check_cmd="${CHECK_CMD:-scripts/ai/run-all-checks.sh}"
base="${BASE_REF:-origin/main}"
head="${HEAD_REF:-origin/staging}"

git fetch -q origin main staging 2>/dev/null || true
count="$(git rev-list --count "$base..$head")"
if [[ "$count" == "0" ]]; then
  echo "self-heal: staging has no commits ahead of main"
  exit 0
fi
if bash -c "$check_cmd" >/dev/null 2>&1 && [[ "$(git rev-parse HEAD)" == "$(git rev-parse "$head")" ]]; then
  echo "self-heal: checks pass on staging head; nothing to heal"
  exit 0
fi
if ! git checkout -q "$base" || ! bash -c "$check_cmd" >/dev/null 2>&1; then
  echo "self-heal: checks already fail on main; not searching staging" >&2
  git checkout -q - || true
  exit 1
fi

# First failing commit, oldest first. A plain walk (not `git bisect run`) keeps
# the result identical across git versions; staging batches are short.
bad=""
while read -r sha; do
  git checkout -q "$sha"
  if ! bash -c "$check_cmd" >/dev/null 2>&1; then bad="$sha"; break; fi
done < <(git rev-list --reverse "$base..$head")
if [[ -z "$bad" ]]; then
  echo "self-heal: no single commit fails on its own; nothing to revert" >&2
  git checkout -q "$head"
  exit 1
fi
echo "self-heal: first bad commit $(git log -1 --format='%h %s' "$bad")"

git switch -q -C staging "$head"
git revert --no-edit "$bad"
if ! bash -c "$check_cmd" >/dev/null 2>&1; then
  echo "self-heal: checks still fail after reverting ${bad:0:7}; leaving staging unchanged" >&2
  git reset -q --hard "$head"
  exit 1
fi
echo "self-heal: checks pass after reverting ${bad:0:7}"
if [[ "$push" == "1" ]]; then
  git push origin staging
  if [[ -x scripts/ai/pr-loop.sh ]]; then scripts/ai/pr-loop.sh log self_heal "reverted ${bad:0:7}" || true; fi
fi
echo "SELF_HEAL_REVERTED=$bad"

#!/usr/bin/env bash
# Tests self-heal.sh in a temp repo with a fake origin (no network).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git init -q --bare "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/w" 2>/dev/null
cd "$tmp/w"
git config user.email t@t; git config user.name t
git switch -q -c main
printf 'ok\n' > status.txt
git add -A; git commit -qm base; git push -q origin main
git switch -q -c staging
echo "a" > a.txt; git add -A; git commit -qm "good 1"
printf 'broken\n' > status.txt; git commit -qam "bad: breaks status"
echo "b" > b.txt; git add -A; git commit -qm "good 2"
git push -q origin staging
bad_sha="$(git rev-parse HEAD~1)"
out="$(CHECK_CMD='grep -qx ok status.txt' "$script_dir/self-heal.sh" 2>&1)"
pass=0; fail=0
if grep -q "SELF_HEAL_REVERTED=$bad_sha" <<<"$out"; then pass=$((pass+1)); echo "ok   bisect finds the bad commit"; else fail=$((fail+1)); echo "FAIL bisect: $out"; fi
git fetch -q origin
if [[ "$(git show origin/staging:status.txt)" == "ok" && -f b.txt ]]; then pass=$((pass+1)); echo "ok   revert pushed, good commits kept"; else fail=$((fail+1)); echo "FAIL staging content"; fi
if git merge-base --is-ancestor "$bad_sha" origin/staging; then pass=$((pass+1)); echo "ok   history kept (no force push)"; else fail=$((fail+1)); echo "FAIL history rewritten"; fi
out2="$(CHECK_CMD='grep -qx ok status.txt' "$script_dir/self-heal.sh" 2>&1)"
if grep -q "nothing to heal" <<<"$out2"; then pass=$((pass+1)); echo "ok   healthy staging is left alone"; else fail=$((fail+1)); echo "FAIL second run: $out2"; fi

# auto-revert: a staging merge that broke main is reverted through staging.
mkdir -p "$tmp/bin"
printf '#!/usr/bin/env bash\necho "gh $*" >> "%s/gh.log"\ncase "$*" in *compare*ahead_by*) echo 1 ;; *"pr list"*) echo "" ;; *"issue list"*) echo 1 ;; esac\nexit 0\n' "$tmp" > "$tmp/bin/gh"
chmod +x "$tmp/bin/gh"
git switch -q main
git merge -q --no-ff origin/staging -m "Merge pull request #5 from o/staging"
git push -q origin main
merge_sha="$(git rev-parse HEAD)"
PATH="$tmp/bin:$PATH" GH_REPO=o/r "$script_dir/pr-loop.sh" auto-revert "$merge_sha" >/dev/null 2>&1 || true
git fetch -q origin
if git log -1 --format=%s origin/staging | grep -q '^Revert "Merge pull request #5'; then pass=$((pass+1)); echo "ok   auto-revert puts a revert on staging"; else fail=$((fail+1)); echo "FAIL auto-revert: $(git log -1 --format=%s origin/staging)"; fi
if [[ "$(git rev-parse origin/main)" == "$merge_sha" ]]; then pass=$((pass+1)); echo "ok   auto-revert never pushes main"; else fail=$((fail+1)); echo "FAIL main changed"; fi
if grep -q "pr create --base main --head staging" "$tmp/gh.log"; then pass=$((pass+1)); echo "ok   auto-revert opens the staging PR"; else fail=$((fail+1)); echo "FAIL no PR"; cat "$tmp/gh.log"; fi
echo plain > p.txt; git add -A; git commit -qm "direct commit"; git push -q origin main
out3="$(PATH="$tmp/bin:$PATH" GH_REPO=o/r "$script_dir/pr-loop.sh" auto-revert "$(git rev-parse HEAD)" 2>&1)"
if grep -q "not a staging merge" <<<"$out3"; then pass=$((pass+1)); echo "ok   non-staging commits are not auto-reverted"; else fail=$((fail+1)); echo "FAIL: $out3"; fi

echo "self-heal: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]

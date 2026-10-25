#!/usr/bin/env bash
# Self-test for review-rules.sh: each bad sample must be caught, clean code and
# docs that mention the patterns must pass.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/scripts/ai" "$tmp/src" "$tmp/docs"
cp "$script_dir/review-rules.sh" "$script_dir/test-patterns.sh" "$tmp/scripts/ai/"
git -C "$tmp" init -q

pass=0; fail=0
expect() {
  local name="$1" expected="$2" file="$3" content="$4"
  rm -rf "$tmp/src" "$tmp/docs"; mkdir -p "$tmp/src" "$tmp/docs" "$(dirname "$tmp/$file")"
  printf '%s\n' "$content" >"$tmp/$file"
  local actual=0
  bash "$tmp/scripts/ai/review-rules.sh" "$tmp" >/dev/null 2>&1 || actual=$?
  if [[ "$actual" == "$expected" ]]; then pass=$((pass + 1)); echo "ok   $name"
  else fail=$((fail + 1)); echo "FAIL $name (expected $expected, got $actual)"; fi
}

key_prefix="sk_live_"
expect "clean source passes" 0 src/user-service.ts 'export function getUserById(id: string) { return id; }'
expect "placeholder text caught" 1 src/user-service.ts '// TODO: implement getUserById'
expect "console.log caught" 1 src/worker.ts 'console.log("debug", env);'
expect "debugger caught" 1 src/worker.ts '  debugger;'
expect ".only in test caught" 1 src/user-service.test.ts 'it.only("finds user", () => {});'
expect ".only outside tests ignored" 0 src/lib.ts 'const only = list.only;'
expect "live secret key caught" 1 src/stripe-client.ts "const key = \"${key_prefix}abcdefghijklmnop\";"
expect "secret in docs still caught" 1 docs/setup.md "export STRIPE_KEY=${key_prefix}abcdefghijklmnop"
expect "docs mentioning patterns pass" 0 docs/rules.md 'Never write lorem ipsum or console.log( in code. <!-- FILL: stack -->'
expect "allow marker respected" 0 src/cli.ts 'console.log(result); // review-rules: allow'

echo "review-rules self-test: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]

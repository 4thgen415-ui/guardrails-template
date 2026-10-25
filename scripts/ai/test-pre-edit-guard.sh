#!/usr/bin/env bash
# Feeds sample Claude Code hook JSON to pre-edit-guard.sh and checks exit codes.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="$script_dir/pre-edit-guard.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/.ai"

pass=0
fail=0

run_case() {
  local name="$1" expected="$2" json="$3" locked="$4"
  if [[ "$locked" == "locked" ]]; then
    touch "$tmp/.ai/tests-locked"
  else
    rm -f "$tmp/.ai/tests-locked"
  fi
  local actual=0
  local env_lock=0
  [[ "$locked" == "unlocked_env" ]] && env_lock=1
  AI_TESTS_LOCKED="$env_lock" CLAUDE_PROJECT_DIR="$tmp" "$guard" <<<"$json" >/dev/null 2>&1 || actual=$?
  if [[ "$actual" == "$expected" ]]; then
    pass=$((pass + 1))
    echo "ok   $name (exit $actual)"
  else
    fail=$((fail + 1))
    echo "FAIL $name: expected exit $expected, got $actual"
  fi
}

p="$tmp"
run_case "edit test file while locked is blocked" 2 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$p/src/user-service.test.ts\",\"old_string\":\"a\",\"new_string\":\"b\"}}" locked
run_case "write spec in test/ dir while locked is blocked" 2 \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$p/test/worker.ts\",\"content\":\"x\"}}" locked
run_case "multiedit __tests__ while locked is blocked" 2 \
  "{\"tool_name\":\"MultiEdit\",\"tool_input\":{\"file_path\":\"$p/src/__tests__/d1.ts\",\"edits\":[{\"old_string\":\"a\",\"new_string\":\"b\"}]}}" locked
run_case "edit test file while unlocked is allowed" 0 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$p/src/user-service.test.ts\",\"old_string\":\"a\",\"new_string\":\"b\"}}" unlocked
run_case "edit source while locked is allowed" 0 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$p/src/user-service.ts\",\"old_string\":\"a\",\"new_string\":\"export function getUserById(id: string) { return db.get(id); }\"}}" locked
run_case "placeholder in Write content is blocked" 2 \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$p/src/user-service.ts\",\"content\":\"export function getUserById() {\\n  // TODO: implement\\n}\"}}" unlocked
run_case "placeholder in Edit new_string is blocked" 2 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$p/src/a.ts\",\"old_string\":\"x\",\"new_string\":\"// ... existing code\"}}" unlocked
run_case "placeholder in MultiEdit is blocked" 2 \
  "{\"tool_name\":\"MultiEdit\",\"tool_input\":{\"file_path\":\"$p/src/a.ts\",\"edits\":[{\"old_string\":\"x\",\"new_string\":\"ok\"},{\"old_string\":\"y\",\"new_string\":\"Lorem ipsum dolor\"}]}}" unlocked
run_case "placeholder words in markdown are allowed" 0 \
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$p/docs/notes.md\",\"content\":\"never write placeholder text such as lorem ipsum\"}}" unlocked
run_case "relative path test file while locked is blocked" 2 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"src/user-service.spec.ts\",\"old_string\":\"a\",\"new_string\":\"b\"}}" locked
run_case "non-edit tool is ignored" 0 \
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls\"}}" locked
run_case "env lock blocks test edit" 2 \
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$p/src/x.test.ts\",\"old_string\":\"a\",\"new_string\":\"b\"}}" unlocked_env

echo "pre-edit-guard: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]

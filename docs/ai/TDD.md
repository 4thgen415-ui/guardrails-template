# TDD workflow for AI agents

Red, green, refactor, with the tests owned by the owner and read-only for the agent.

## Workflow

1. **Red (owner).** The owner writes failing tests that describe the behaviour,
   runs them to see them fail, and commits them on their own (a test-only PR or
   commit). CI allows test-only changes.
2. **Lock.** `scripts/ai/lock-tests.sh` makes every test file read-only
   (`chmod a-w`) and creates `.ai/tests-locked`, which turns on the Claude Code
   pre-edit guard. Cursor reads `.cursor/rules/tdd.mdc`.
3. **Green (agent).** On `staging` (the only branch agents use), the agent
   implements the smallest change in non-test code that makes the tests pass.
   Tests and `.ai/oracles/` are read-only. `scripts/ai/check-tests-untouched.sh`
   fails the staging -> main PR if it changes tests together with code, and the
   pre-commit hook rejects such commits on `staging`.
4. **Refactor (agent).** Only with every test green. Run the tests again after
   each refactor. Refactors never change behaviour or tests.
5. **Unlock (owner).** `scripts/ai/unlock-tests.sh` when the owner writes the
   next set of tests.

## Prompt template (copy and paste)

```text
Task: make the failing tests in <TEST FILE PATHS> pass.

Context:
- The tests were committed in <COMMIT SHA>. They are the spec.
- Test files are READ-ONLY. Do not create, edit, rename, or delete any test file.
- Change only these functions: <FUNCTION NAMES>. Declare them in .ai/change-scope.yml.
- Leave every other line byte-for-byte unchanged.

Steps:
1. RED: run `<TEST COMMAND>` and paste the failing output.
2. GREEN: implement the smallest change in non-test code that makes them pass.
   No placeholder text, no invented IDs or data.
3. Run `<TEST COMMAND>` again and paste the passing output.
4. REFACTOR (optional): only with all tests green. Do not change behaviour.
   Run the tests again and paste the output.
5. Run every command in AGENTS.md "## Verification" and paste the output.

If a test seems wrong or the task needs a test change, stop and explain why
instead of editing the test.
```

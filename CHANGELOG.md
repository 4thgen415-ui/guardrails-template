# Changelog

## 2026-10-27

- AGENTS.md: new sections "Work modes: SANDBOX (default) and SHIP" (fast edits
  and no full test runs while the owner requests changes; full suite, split
  ships under 400 lines, merge and safe deploy only on "push this" / "ship it" /
  "deploy") and "Maximum automation (owner rules)" (owner's own wording).
  check-agents-md.sh requires both headings.
- docs/ai/TDD.md, .cursor/rules/tdd.mdc, new .cursor/rules/work-modes.mdc:
  test-first for behaviour/logic changes; not for cosmetic or copy tweaks in
  SANDBOX; full red-green-refactor and the full suite at SHIP.
- New scripts/ai/ship.sh (the SHIP gate, `--suite` for the full suite only).
  The pre-commit hook runs the full suite only when AI_MODE=ship. Claude hooks
  and the pre-edit guard are unchanged (fast checks, always on).

## 2026-10-25

- Initial template: AGENTS.md, TDD docs, Cursor rules, Claude hooks, CodeQL,
  the main + staging PR loop, bootstrap and sentinel workflows.
- AI Guardrails, CodeQL and Dual-model review accept workflow_dispatch, so the
  required checks can be re-created on staging's head if a push event is lost:
  `gh workflow run ai-guardrails.yml --ref staging`.
- Self-heal finds the first failing commit by walking `main..staging` oldest
  first instead of `git bisect run`, which behaved differently on the runner's git.
- editlint: new `edit_comments` scope key lets a change rewrite comments outside
  symbols in the listed files (for example a stale file header).
- Unit tests tolerate temp-dir cleanup races (background git gc on the runner).
- sync-staging tolerates losing the race with reset-staging.yml after a merge.
- PR loop: runs with no jobs, a startup_failure, or jobs refused for billing are
  "unverifiable" (label `ai-unverifiable` for billing), never a strike, never a
  self-heal or auto-revert trigger. `.ai/change-scope.yml` reset to an empty scope.

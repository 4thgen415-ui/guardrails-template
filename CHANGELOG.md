# Changelog

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

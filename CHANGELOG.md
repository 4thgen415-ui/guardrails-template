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

# Changelog

## 2026-10-25

- Initial template: AGENTS.md, TDD docs, Cursor rules, Claude hooks, CodeQL,
  the main + staging PR loop, bootstrap and sentinel workflows.
- AI Guardrails, CodeQL and Dual-model review accept workflow_dispatch, so the
  required checks can be re-created on staging's head if a push event is lost:
  `gh workflow run ai-guardrails.yml --ref staging`.

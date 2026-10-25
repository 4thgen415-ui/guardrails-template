# guardrails-template

The standard set of AI-coding guardrails to start every new project from:
agent instructions (`AGENTS.md`), Cursor rules, Claude Code hooks, read-only
tests, blast-radius and module lints, anti-hallucination backstops, a
dual-model review, oracles, a package registry check, a context guard, a
staging -> main PR loop with self-healing and auto-revert, progressive
autonomy, a bootstrap for new repos, an hourly sentinel, and CodeQL. It works with any stack. The
examples use TypeScript on Cloudflare Workers (D1, R2, Workers AI) with Clerk.
You can rename this repo; nothing depends on its name.

## Create a new repo from this template

Click **Use this template** on GitHub, or:

```sh
gh repo create NAME --template OWNER/guardrails-template --public --clone
```

## Branch topology

Exactly two branches: `main` and `staging`. Agents push only to `staging`; CI
opens the `staging` -> `main` PR and merges it when every required check is
green. `main` accepts changes only through that PR (`source-is-staging`), and
`staging` is never deleted (`delete_branch_on_merge=false`). Other branches are
archived as `archive/...` tags and deleted by the sentinel.

## First steps after creating a repo

1. Fill every `<!-- FILL: ... -->` slot in `AGENTS.md` (`grep -n "FILL:" AGENTS.md`),
   then delete `.guardrails-template`. From then on CI fails if a slot is left.
2. Run `scripts/ai/install-hooks.sh` once per clone (sets `core.hooksPath` to `.githooks`).
3. Install the free [Socket GitHub App](https://github.com/apps/socket-security) on the repo.
4. One-time: add the repo secret `GUARDRAILS_ADMIN_TOKEN`, a fine-grained PAT
   with Administration, Contents, Pull requests, Issues and Workflows read/write.
   Then re-run the **Bootstrap** workflow. It creates `staging`, sets
   `allow_auto_merge=true` and `delete_branch_on_merge=false`, protects `main`
   (PR + required checks + `source-is-staging`, 0 reviews, no force pushes) and
   adds the staging-deletion and branch-creation rulesets. Without the secret,
   Bootstrap fails on purpose and the repo stays unprotected. The same token lets
   bot-made pushes, PRs and merges start CI (the default `GITHUB_TOKEN` cannot).
5. Optional: add `GEMINI_API_KEY` (or `OPENAI_API_KEY`) as a repo secret to turn
   on the dual-model review, then set repo variable `DUAL_REVIEW_REQUIRED=true`.

CodeQL is free on public repos. Private repos need GitHub Advanced Security
(GitHub Code Security). Branch protection on private repos needs a paid plan.

## What's in it

| Path | What it does |
| --- | --- |
| `AGENTS.md`, `CLAUDE.md` | Agent instructions. Cursor, Codex, and Copilot read `AGENTS.md`; `CLAUDE.md` imports it. |
| `.cursor/rules/*.mdc` | Always-on Cursor rules: scoped edits, read-only tests. |
| `.claude/settings.json` | Claude Code hooks: pre-edit guard, post-edit and stop backstops. |
| `docs/ai/TDD.md` | Red-green-refactor workflow and a prompt template. |
| `.ai/change-scope.yml`, `.ai/config.yml` | The PR's declared scope and budget, and the module budget. |
| `scripts/ai/` | Every check (bash and stdlib Python, no dependencies). `run-all-checks.sh` runs them all. |
| `.githooks/pre-commit` | Blocks agent commits anywhere but `staging`, and commits that mix tests with code on `staging`. |
| `.github/workflows/` | `guardrails` (AI Guardrails), `codeql`, `dual-model-review`, `agent-pr-loop`, `reset-staging`, `bootstrap`, `sentinel`. |
| `.ai/oracles/` | Owner-written expected outputs (`scripts/ai/oracle.py`); read-only like tests. |
| `examples/` | A TS helper and test: an empty DB result returns a fixed honest message, not invented IDs. |

## Controls

| Control | Enforced by | Automation |
| --- | --- | --- |
| Agent instructions | `AGENTS.md`, `.cursor/rules` | Advice only; the checks below enforce it |
| Read-only tests | pre-edit hook, `lock-tests.sh`, pre-commit, CI `tests-untouched` | 100% automated after setup (no bypass label) |
| Blast radius | CI `editlint` | 100% automated |
| Modules | CI `module-lint` | 100% automated |
| Review rules | CI `guardrails` (`review-rules.sh`) | 100% automated |
| Backstops | Claude hooks, CI `backstops` | 100% automated |
| Dual-model review | CI `dual-model-review` | Automated once a key secret is set (skips neutrally without one) |
| Branch topology | `source-is-staging`, `branch-policy`, pre-commit, rulesets, `sentinel.yml` | Automated after `GUARDRAILS_ADMIN_TOKEN` |
| PR loop, auto-merge, retry cap | `agent-pr-loop.yml`, `reset-staging.yml` | Automated after `GUARDRAILS_ADMIN_TOKEN` |
| Self-heal and auto-revert | `self-heal.sh`, `pr-loop.sh auto-revert` | Automated; gated by the autonomy level |
| Oracles | CI `app-checks` (`oracle.py`) | 100% automated |
| Package registry | CI `packages` | 100% automated |
| Context guard | Claude PreToolUse hook (`context_guard.py`) | 100% automated (Claude Code only) |
| Progressive autonomy | `autonomy.py`, activity log issue | 100% automated |
| Calibration | CI `guardrails` (`calibrate.sh`, temp dir) | 100% automated |
| CodeQL | `codeql.yml` | 100% automated (free on public repos) |

## Adding a review rule

When a review comment comes up twice, turn it into a rule. Add one line to the
`RULES` table in `scripts/ai/review-rules.sh`:

```text
id|scope|extended-regex|message
no-wrangler-deploy|src|wrangler deploy|Deploys run from CI, never from code.
```

`scope` is `src` (source files, not docs or guardrail files), `tests`, or `all`.
Add a case to `scripts/ai/test-review-rules.sh` that the rule must catch, then
run both scripts. A line containing `review-rules: allow` is skipped. Reviewers
can see it, so use it rarely.

## One-time setup commands (admin)

The Bootstrap workflow does all of this. To apply it by hand instead (same
code, idempotent; a dry run first shows the plan):

```sh
python3 scripts/ai/sentinel.py --repo OWNER/REPO            # dry run (read-only)
python3 scripts/ai/sentinel.py --repo OWNER/REPO --enforce  # apply
```

The required checks come from `required_checks` in `.ai/config.yml`.

## Sentinel

`sentinel.yml` runs hourly in the `guardrails-template` repo only and sweeps
every repository the owner owns, including ones created outside the template.
It is a dry run until the repo variable `SENTINEL_ENFORCE=true` is set. It never
deletes a repo, never renames a default branch, skips forks and archived repos,
leaves branches with open PRs alone, and archives every stray branch as a tag
before deleting it (restore with `git push origin archive/<tag>:refs/heads/<branch>`).

# AGENTS.md

Instructions for every AI coding agent working in this repo (Cursor, Codex,
Copilot, Claude Code via `CLAUDE.md`). Fill each slot marked
`<!-- FILL: ... -->` (find them with `grep -n "FILL:" AGENTS.md`), then delete
the `.guardrails-template` file so CI enforces that no slots are left.

## Stack

<!-- FILL: languages, runtime, frameworks, data stores, auth, hosting. Example:
TypeScript 5 (strict) on Cloudflare Workers; D1 (SQLite) for relational data;
R2 for files; Workers AI for inference; Clerk for auth; Wrangler for deploys. -->

## Commands

Run these exact commands. Never guess a substitute.

### Test

<!-- FILL: exact test command. Example: npm test (vitest + @cloudflare/vitest-pool-workers) -->
```sh
npm test
```

### Lint

<!-- FILL: exact lint command. Example: npm run lint (eslint .) -->
```sh
npm run lint
```

### Typecheck

<!-- FILL: exact typecheck command. Example: npm run typecheck (tsc --noEmit) -->
```sh
npm run typecheck
```

### Build

<!-- FILL: exact build command. Example: npm run build (wrangler deploy --dry-run --outdir dist) -->
```sh
npm run build
```

## Naming conventions

Use concrete examples, not adjectives like "clean" or "consistent".

<!-- FILL: replace or confirm each example below. -->
- Functions: camelCase verb + noun: `getUserById`, not `getUser_by_id` or `userGet`.
- Types and classes: PascalCase nouns: `UserRecord`, `BillingClient`.
- Constants: UPPER_SNAKE_CASE: `MAX_UPLOAD_BYTES`.
- Files: kebab-case: `user-service.ts`, `stripe-client.ts`; tests next to them: `user-service.test.ts`.
- D1 tables and columns: snake_case plural tables: `users`, `created_at`.
- Env bindings (wrangler.toml): UPPER_SNAKE_CASE: `DB`, `BUCKET`, `CLERK_SECRET_KEY`.
- Branches: exactly two, `main` and `staging`. Agents work only on `staging` and
  never create branches (no `feature/...`, `fix/...`, `agent/...`).

## Modules

Build everything as small, self-contained modules with one responsibility each.

- One module = one folder with an explicit public interface: `index.ts`
  (or `__init__.py`) exports the public API; everything else is internal.
- Import other modules only through their index (`src/billing`), never their
  internals (`src/billing/stripe-client`).
- Keep coupling minimal: pass dependencies in (D1 binding, Clerk client) instead
  of reaching for globals.
- No monoliths or god-objects.

GOOD (Cloudflare Worker, TypeScript):

```text
src/billing/index.ts          export { createCheckout } from "./checkout";
src/billing/checkout.ts       createCheckout(env, userId, priceId)
src/billing/stripe-client.ts  internal: fetch wrapper for the Stripe API
src/billing/prices.ts         internal: price IDs and amounts
src/routes/checkout-route.ts  import { createCheckout } from "../billing";
```

BAD:

```text
src/worker.ts                 one file doing routing + Stripe calls + D1 SQL + Clerk auth
src/routes/checkout-route.ts  import { stripeFetch } from "../billing/stripe-client"; // deep import
```

Splitting rule: when a file you are already editing goes over the budget in
`.ai/config.yml` (default 300 lines or 10 top-level symbols) or mixes concerns,
split it first as a separate behaviour-preserving refactor commit on `staging`:
tests unchanged and green before and after, identical public interface,
declared as `mode: refactor-split` in `.ai/change-scope.yml` so
`scripts/ai/editlint.py` checks every moved symbol reappears byte-identical.
`scripts/ai/module_lint.py` fails new over-budget files and deep imports.

## Never-dos

- Never edit tests during implementation. Test files are read-only until the
  owner unlocks them; a PR that changes tests and code together fails CI.
- Never write placeholder text in code (`TODO: implement`, `lorem ipsum`,
  `// ... existing code`, fake IDs such as `user_123`). Write the real thing or
  stop and say what is missing.
- Never remove features, text, or files without owner approval.
- Never deploy by hand (`wrangler deploy` from a laptop). Deploys run from CI.
- Never commit secrets. Use `wrangler secret put NAME` and CI secrets.
- Never add a dependency without checking it with Socket first
  (`socket package score npm <name>` or install via `sfw npm install <name>`).
- Never commit to `main`. Never create branches. Only `main` and `staging`
  exist; agents work only on `staging`.
- Never edit a file you have not read in this session, and never replace a whole
  file to change part of it.
- Never claim something works without running it and showing the output.

## Work modes: SANDBOX (default) and SHIP

Tiny edits (moving a box 2 mm, a copy tweak) must take seconds, not the 10-20
minutes a full test run costs. Pick the mode from the owner's words:

**SANDBOX** (default, whenever the owner is asking for changes)
- Make the edit fast. No full test runs. Test-first is required for behaviour
  or logic changes only, not for cosmetic or copy tweaks (`docs/ai/TDD.md`).
- At most one quick check that takes under ~1 minute, such as a typecheck of
  the touched package or one targeted test. Hooks and pre-commit run only fast
  checks in this mode.
- Every reply shows a screenshot of the preview for visual changes, plus a
  plain-English list of every file and every behaviour changed, so the owner
  can spot hallucinations.
- Never merge to `main` and never deploy. Work stays on `staging`.

**SHIP** (only when the owner explicitly says "push this", "ship it" or "deploy")
1. Run `scripts/ai/ship.sh` (sets `AI_MODE=ship`): the full test suite and
   every check in Verification. Behaviour changes get the full
   red-green-refactor cycle.
   Definition of done and Verification describe SHIP: a SANDBOX edit is a
   preview, never "done"; it becomes done only after this full run,
   protected tests included.
2. Fix failures with the check-fix loop; after 3 failed attempts stop and
   report the output.
3. Diff budget: the 400-line limit per push (in this template `max_changed_lines`
   in `.ai/change-scope.yml`, default 300, which is stricter; never above 400) is deliberately small so each piece gets checked. Never
   raise it and never ask the owner about it. When a ship is bigger than 400
   lines, automatically split it into ordered pieces under 400 lines (each a
   coherent, self-contained change with its tests), run the full checks on each
   piece, and promote each to `main` only after it passes. If a piece fails,
   stop, fix it with the check-fix loop, and continue; never bundle pieces to
   get around the limit.
4. Merge `staging` -> `main` only when `scripts/ai/ship.sh` and CI are green,
   then reset `staging` to `main`.
5. Deploy only through the app's safe-deploy path (smoke test + automatic
   rollback). Never deploy by hand.

Unchanged in both modes: locked and protected tests are never edited or
weakened; no feature, button, screen or text is removed without the owner's OK;
repo visibility never changes (private repos stay private); modules; exactly
two branches; the editlint diff budget. The pre-edit guard (locked tests,
protected paths) is always on, and CI on PRs into `main` stays the full gate.

## Maximum automation (owner rules)

> "wherever I don't have to be involved, you shouldn't involve me stop and ask
> yourself. Is this something I can do and if the answer is yes, then you don't
> bother me. You do bother me if you don't know the answer to a question you
> never make up something or hallucinate or fabricate. That is the number one
> rule that is total blasphemy. If you don't know the answer, you come to me….
> Big decisions that need to be made on the projects you come to me, but if
> they're small and your context engine can sense what I want then just do it
> yourself."
>
> "if it's at all possible, you always want to do deterministic code over LLM
> call cause the LLM call is what cost money so if it could be done without that
> that's preferred without sacrificing anything this has to do something it has
> to be on par. It has to provide value"
>
> (owner, 2026-10-26)

1. Automate everything you can do yourself. Before asking the owner anything,
   ask "Can I do this myself?" If yes, do it and do not ask.
2. Small decisions where the owner's intent is clear from context: decide,
   proceed, and mention the choice in your summary.
3. Ask the owner only for big project decisions or for facts you do not know.
4. Number one rule: never fabricate, guess or hallucinate a fact (names,
   numbers, prices, URLs, IDs, API behaviour, results). If you do not know,
   say so and ask.
5. Prefer deterministic code over LLM calls (LLM calls cost money) whenever
   quality and value stay on par; use an LLM only where code would be worse.
6. Every safety rule in this file still holds. Automation never bypasses tests,
   guards, budgets or the SANDBOX/SHIP rules.

## Branch workflow

Exactly two branches exist: `main` (protected, changes only through a PR from
`staging`) and `staging` (the only branch agents use; it is never deleted).

1. `git switch staging && git pull --ff-only`. Never create another branch.
2. Declare the scope in `.ai/change-scope.yml` (symbols you will change, budget).
3. Commit and push to `staging`. CI opens one `staging` -> `main` PR (or reuses
   the open one) and enables auto-merge. CI is the only gate: when every required
   check is green, including `source-is-staging` and `branch-policy`, the PR
   merges with a merge commit and `staging` is fast-forwarded to `main`.
4. On failure the PR is labeled `ai-failing` with the failing output in a
   comment. When autonomy allows, CI finds the first commit in `main..staging`
   that broke the checks, reverts it, and pushes the revert to `staging` (never
   a force push). After 3 failed pushes the PR is labeled `ai-retry-cap-reached` and
   closed, and `staging` is archived as a tag `archive/staging-<time>-<sha>` and
   reset to `main`. A run that never executed (no jobs, `startup_failure`, or
   jobs refused for billing) is unverifiable: it is not a strike and triggers
   neither self-heal nor auto-revert; billing refusals get `ai-unverifiable`.
5. If `main` fails after a merge, CI reverts that merge through `staging`
   (auto-revert) and records it in the activity log issue.
6. Stray branches are archived as tags and deleted by the sentinel; a ruleset
   blocks creating them where the plan allows rulesets.

## Definition of done

- All four commands (test, lint, typecheck, build) pass locally, and their
  output is shown in your final message.
- `scripts/ai/review-rules.sh` is clean.
- `python3 scripts/ai/editlint.py --base origin/main` and
  `python3 scripts/ai/module_lint.py --base origin/main` pass.
- `python3 scripts/ai/oracle.py` passes (owner-written expected outputs in
  `.ai/oracles/`, read-only like tests) and
  `python3 scripts/ai/package_check.py --base origin/main` passes.
- CI on the PR is green. Nothing else counts as done.

## Verification

Run all of these before you finish, and paste the output:

```sh
npm test
npm run lint
npm run typecheck
npm run build
scripts/ai/review-rules.sh
scripts/ai/check-agents-md.sh
scripts/ai/check-tests-untouched.sh origin/main HEAD
python3 scripts/ai/editlint.py --base origin/main
python3 scripts/ai/module_lint.py --base origin/main
python3 scripts/ai/backstops/backstops.py diff --base origin/main
python3 scripts/ai/oracle.py
python3 scripts/ai/package_check.py --base origin/main
```

## Trust but verify

- Prompts guide; deterministic checks enforce. These instructions are advice to
  the agent. The scripts and CI are the rules.
- Verification is automated. CI, the dual-model review, and the backstops are the
  authority, not a human reading the diff. The owner reads summaries after the
  fact.
- Never claim something works without running it and showing the evidence (the
  command and its real output, or a CI run URL). The Stop hook and the CI
  backstops flag "tests pass" claims that have no evidence.
- Oracles (`.ai/oracles/*.json`) are owner-written expected outputs that the agent
  cannot edit; the only way to pass them is real behaviour.
- Autonomy is earned: `scripts/ai/autonomy.py` reads the activity log. A recent
  auto-revert or retry cap puts the agent on probation (half the change budget,
  dual-model review mandatory, no self-heal); 10 clean merges in a row double
  the budget.

## Security tools

- Snyk Agent Scan (free): scans local agent configs, MCP servers, and skills for
  prompt injection, tool poisoning, and toxic flows. Run before adding any MCP
  server or skill: `uvx snyk-agent-scan@latest` (whole machine) or
  `uvx snyk-agent-scan@latest .cursor/mcp.json`. (The old `uvx mcp-scan@latest`
  is now a redirect to this package.)
- Socket (free): catches typosquatted, malicious, and hallucinated packages.
  Before adding a dependency: `npx socket package score npm <name>`, or install
  through Socket Firewall: `npx sfw npm install <name>`. The Socket GitHub App
  comments on PRs that add risky dependencies.
- Package registry check (in CI, `scripts/ai/package_check.py`): every added npm
  or PyPI dependency must exist on the registry, have the pinned version, and be
  at least 30 days old. Hallucinated package names fail the PR.
- CodeQL (in CI, `.github/workflows/codeql.yml`): static analysis on every PR,
  push to `main` or `staging`, and weekly.

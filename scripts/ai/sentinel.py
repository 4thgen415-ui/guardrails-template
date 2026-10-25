#!/usr/bin/env python3
"""Sentinel and bootstrap: keep repos on the main + staging topology.

Used by .github/workflows/bootstrap.yml (one repo, in repos generated from the
template) and .github/workflows/sentinel.yml (every repo the owner owns, hourly,
for repos created outside the template). Idempotent: running it twice is a no-op.

Per repo it plans (pure function `plan_repo`, unit-tested) and then, only with
--enforce, applies:
  - create `staging` from `main` if missing
  - archive every stray branch as tag archive/<branch>-<date>-<sha7>, verify the
    tag, then delete the branch (branches with an open PR are reported, not touched)
  - repo settings: allow_auto_merge=true, delete_branch_on_merge=false
  - main protection: PR required, required checks (from the repo's .ai/config.yml),
    0 reviews, no force pushes, no deletion (only when guardrails are installed,
    otherwise required checks could never pass)
  - rulesets: staging cannot be deleted; no branch other than main/staging
    (and allowed bot/Pages patterns) can be created
It never deletes a repo, never renames a default branch, never touches forks or
archived repos, and never force-pushes.

Usage:
  sentinel.py --repo OWNER/NAME [--enforce]
  sentinel.py --owner OWNER [--enforce] [--exclude a,b]
Without --enforce it is a dry run: GET requests only, prints the plan.
Requires `gh` authenticated (GH_TOKEN in Actions = GUARDRAILS_ADMIN_TOKEN).
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from ai_config import parse_simple_yaml  # noqa: E402

DEFAULT_REQUIRED_CHECKS = [
    "guardrails", "tests-untouched", "editlint", "module-lint", "backstops", "packages",
    "app-checks", "source-is-staging", "branch-policy", "dual-model-review",
    "analyze (javascript-typescript)",
]
DEFAULT_ALLOWED = ["dependabot/*", "gh-pages"]
STAGING_RULESET = "guardrails: staging cannot be deleted"
CREATION_RULESET = "guardrails: only main and staging"
GUARDRAILS_MARKER = "scripts/ai/check-agents-md.sh"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def gh_api(method: str, path: str, body: dict | None = None, paginate: bool = False):
    cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json", "-i"]
    if paginate:
        cmd = ["gh", "api", "-X", method, path, "--paginate", "--slurp", "-H", "Accept: application/vnd.github+json"]
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
    if paginate:
        if proc.returncode != 0:
            m = re.search(r"\(HTTP (\d{3})\)", proc.stderr)
            raise ApiError(int(m.group(1)) if m else 0, proc.stderr.strip().removeprefix("gh: ")[:300])
        pages = json.loads(proc.stdout or "[]")
        return [item for page in pages for item in (page if isinstance(page, list) else [page])]
    head, _, payload = proc.stdout.partition("\r\n\r\n")
    if not payload and "\n\n" in proc.stdout:
        head, _, payload = proc.stdout.partition("\n\n")
    status = 0
    first = head.splitlines()[0] if head else ""
    parts = first.split()
    if len(parts) >= 2 and parts[1].isdigit():
        status = int(parts[1])
    if status >= 400 or (status == 0 and proc.returncode != 0):
        msg = payload.strip() or proc.stderr.strip()
        try:
            msg = json.loads(msg).get("message", msg)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ApiError(status, str(msg)[:300])
    payload = payload.strip()
    return json.loads(payload) if payload else None


# ---------- read (dry-run safe: GET only) ----------

def gather(repo: str, api=gh_api) -> dict:
    meta = api("GET", f"repos/{repo}")
    info = {
        "repo": repo,
        "archived": meta.get("archived", False),
        "fork": meta.get("fork", False),
        "private": meta.get("private", False),
        "is_template": meta.get("is_template", False),
        "default_branch": meta.get("default_branch"),
        "allow_auto_merge": meta.get("allow_auto_merge"),
        "delete_branch_on_merge": meta.get("delete_branch_on_merge"),
        "branches": {},
        "open_pr_heads": {},
        "main_protection": None,
        "rulesets": [],
        "guardrails": False,
        "config": {},
        "actions_can_open_prs": None,
        "errors": [],
    }
    if info["archived"] or info["fork"]:
        return info
    try:
        for b in api("GET", f"repos/{repo}/branches?per_page=100", paginate=True):
            info["branches"][b["name"]] = b["commit"]["sha"]
    except ApiError as exc:
        info["errors"].append(f"list branches: {exc}")
    try:
        for pr in api("GET", f"repos/{repo}/pulls?state=open&per_page=100", paginate=True):
            if pr["head"]["repo"] and pr["head"]["repo"]["full_name"] == repo:
                info["open_pr_heads"].setdefault(pr["head"]["ref"], []).append(pr["number"])
    except ApiError as exc:
        info["errors"].append(f"list PRs: {exc}")
    if "main" in info["branches"]:
        try:
            prot = api("GET", f"repos/{repo}/branches/main/protection")
            info["main_protection"] = {
                "contexts": sorted((prot.get("required_status_checks") or {}).get("contexts") or []),
                "pr_required": "required_pull_request_reviews" in prot,
                "force_pushes": (prot.get("allow_force_pushes") or {}).get("enabled", False),
                "deletions": (prot.get("allow_deletions") or {}).get("enabled", False),
            }
        except ApiError as exc:
            if exc.status == 404:
                info["main_protection"] = "none"
            elif exc.status == 403:
                info["main_protection"] = "unavailable"
            else:
                info["errors"].append(f"main protection: {exc}")
    try:
        info["rulesets"] = [r["name"] for r in api("GET", f"repos/{repo}/rulesets?per_page=100", paginate=True)]
    except ApiError as exc:
        if exc.status == 403:
            info["rulesets"] = None  # rulesets unavailable on this plan
        else:
            info["errors"].append(f"rulesets: {exc}")
    try:
        wf = api("GET", f"repos/{repo}/actions/permissions/workflow")
        info["actions_can_open_prs"] = bool(wf.get("can_approve_pull_request_reviews"))
    except ApiError as exc:
        info["errors"].append(f"actions workflow permissions: {exc}")
    ref = info["default_branch"] or "main"
    try:
        api("GET", f"repos/{repo}/contents/{GUARDRAILS_MARKER}?ref={ref}")
        info["guardrails"] = True
    except ApiError as exc:
        if exc.status != 404:
            info["errors"].append(f"guardrails marker: {exc}")
    try:
        cfg = api("GET", f"repos/{repo}/contents/.ai/config.yml?ref={ref}")
        info["config"] = parse_simple_yaml(base64.b64decode(cfg["content"]).decode("utf-8"))
    except (ApiError, ValueError, KeyError):
        pass
    return info


# ---------- decide (pure) ----------

def archive_tag_name(branch: str, sha: str, today: str) -> str:
    return f"archive/{branch.replace('/', '-')}-{today}-{sha[:7]}"


def is_allowed(branch: str, patterns: list[str]) -> bool:
    return branch in ("main", "staging") or any(fnmatch.fnmatchcase(branch, p) for p in patterns)


def staging_ruleset() -> dict:
    return {"name": STAGING_RULESET, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/staging"], "exclude": []}},
            "rules": [{"type": "deletion"}]}


def creation_ruleset(patterns: list[str]) -> dict:
    exclude = ["refs/heads/main", "refs/heads/staging"] + [f"refs/heads/{p.replace('*', '**/*')}" for p in patterns]
    return {"name": CREATION_RULESET, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~ALL"], "exclude": exclude}},
            "rules": [{"type": "creation"}]}


def main_protection(checks: list[str]) -> dict:
    # strict=False: main only ever receives merges of staging, so staging differs
    # from main only by merge commits; requiring "up to date" would deadlock.
    return {"required_status_checks": {"strict": False, "contexts": checks},
            "enforce_admins": True,
            "required_pull_request_reviews": {"required_approving_review_count": 0,
                                              "dismiss_stale_reviews": False,
                                              "require_code_owner_reviews": False},
            "restrictions": None, "allow_force_pushes": False, "allow_deletions": False,
            "required_linear_history": False, "required_conversation_resolution": False}


def plan_repo(info: dict, today: str, exclude: set[str] | None = None) -> list[dict]:
    repo = info["repo"]
    name = repo.split("/")[-1]
    if exclude and (repo in exclude or name in exclude):
        return [{"type": "skip", "reason": "excluded"}]
    if info.get("archived"):
        return [{"type": "skip", "reason": "archived"}]
    if info.get("fork"):
        return [{"type": "skip", "reason": "fork"}]
    branches = info.get("branches") or {}
    if not branches:
        return [{"type": "skip", "reason": "empty repository"}]
    if info.get("default_branch") != "main" or "main" not in branches:
        return [{"type": "report", "message": f"default branch is '{info.get('default_branch')}', not main; not changing it (rename it to main to enroll)"}]

    cfg = info.get("config") or {}
    patterns = list(cfg.get("allowed_branch_patterns") or DEFAULT_ALLOWED)
    checks = list(cfg.get("required_checks") or DEFAULT_REQUIRED_CHECKS)
    actions: list[dict] = []
    if "staging" not in branches:
        actions.append({"type": "create_staging", "sha": branches["main"]})
    for branch in sorted(branches):
        if is_allowed(branch, patterns):
            continue
        prs = (info.get("open_pr_heads") or {}).get(branch)
        if prs:
            actions.append({"type": "report", "message": f"stray branch '{branch}' has open PR(s) {prs}; left alone until closed"})
            continue
        sha = branches[branch]
        actions.append({"type": "archive_branch", "branch": branch, "sha": sha, "tag": archive_tag_name(branch, sha, today)})
        actions.append({"type": "delete_branch", "branch": branch, "tag": archive_tag_name(branch, sha, today)})
    if info.get("allow_auto_merge") is not True or info.get("delete_branch_on_merge") is not False:
        actions.append({"type": "set_repo_settings", "settings": {"allow_auto_merge": True, "delete_branch_on_merge": False}})
    if info.get("guardrails") and info.get("actions_can_open_prs") is False:
        actions.append({"type": "allow_actions_prs"})

    prot = info.get("main_protection")
    if not info.get("guardrails"):
        actions.append({"type": "report", "message": "guardrails not installed (no scripts/ai/check-agents-md.sh); main protection with required checks skipped until they are"})
    elif prot == "unavailable":
        actions.append({"type": "report", "message": "branch protection unavailable (private repo on a free plan); make it public or upgrade"})
    else:
        want = sorted(checks)
        ok = isinstance(prot, dict) and prot["contexts"] == want and prot["pr_required"] and not prot["force_pushes"] and not prot["deletions"]
        if not ok:
            actions.append({"type": "protect_main", "body": main_protection(checks)})
    if info.get("rulesets") is None:
        actions.append({"type": "report", "message": "rulesets unavailable (private repo on a free plan): staging deletion and branch-creation rules cannot be enforced; sentinel cleanup is the only control"})
    rulesets = set(info.get("rulesets") or [])
    if prot != "unavailable" and info.get("rulesets") is not None:
        if STAGING_RULESET not in rulesets:
            actions.append({"type": "upsert_ruleset", "body": staging_ruleset()})
        if CREATION_RULESET not in rulesets:
            actions.append({"type": "upsert_ruleset", "body": creation_ruleset(patterns)})
    for err in info.get("errors") or []:
        actions.append({"type": "report", "message": f"read error: {err}"})
    return actions or [{"type": "ok"}]


# ---------- apply (only with --enforce) ----------

def apply(repo: str, actions: list[dict], api=gh_api) -> list[str]:
    results = []
    archived_ok: set[str] = set()
    for a in actions:
        t = a["type"]
        try:
            if t == "create_staging":
                api("POST", f"repos/{repo}/git/refs", {"ref": "refs/heads/staging", "sha": a["sha"]})
                results.append("created staging")
            elif t == "archive_branch":
                try:
                    api("POST", f"repos/{repo}/git/refs", {"ref": f"refs/tags/{a['tag']}", "sha": a["sha"]})
                except ApiError as exc:
                    if exc.status != 422:
                        raise
                tag = api("GET", f"repos/{repo}/git/ref/tags/{a['tag']}")
                if tag["object"]["sha"] != a["sha"]:
                    raise ApiError(409, f"tag {a['tag']} exists at a different commit")
                archived_ok.add(a["branch"])
                results.append(f"archived {a['branch']} as {a['tag']}")
            elif t == "delete_branch":
                if a["branch"] not in archived_ok:
                    results.append(f"NOT deleting {a['branch']}: archive tag not verified")
                    continue
                api("DELETE", f"repos/{repo}/git/refs/heads/{a['branch']}")
                results.append(f"deleted {a['branch']} (restore: git push origin {a['tag']}:refs/heads/{a['branch']})")
            elif t == "set_repo_settings":
                api("PATCH", f"repos/{repo}", a["settings"])
                results.append(f"settings {a['settings']}")
            elif t == "allow_actions_prs":
                api("PUT", f"repos/{repo}/actions/permissions/workflow",
                    {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": True})
                results.append("allowed GitHub Actions to open pull requests")
            elif t == "protect_main":
                api("PUT", f"repos/{repo}/branches/main/protection", a["body"])
                results.append("protected main")
            elif t == "upsert_ruleset":
                existing = {r["name"]: r["id"] for r in api("GET", f"repos/{repo}/rulesets?per_page=100", paginate=True)}
                if a["body"]["name"] in existing:
                    api("PUT", f"repos/{repo}/rulesets/{existing[a['body']['name']]}", a["body"])
                else:
                    api("POST", f"repos/{repo}/rulesets", a["body"])
                results.append(f"ruleset '{a['body']['name']}'")
        except ApiError as exc:
            results.append(f"ERROR {t}: {exc}")
    return results


def describe(a: dict) -> str:
    t = a["type"]
    if t == "skip":
        return f"skip ({a['reason']})"
    if t == "report":
        return f"report: {a['message']}"
    if t == "create_staging":
        return f"create staging from main {a['sha'][:7]}"
    if t == "archive_branch":
        return f"archive branch '{a['branch']}' {a['sha'][:7]} as tag {a['tag']}"
    if t == "delete_branch":
        return f"delete branch '{a['branch']}' (after archive tag is verified)"
    if t == "set_repo_settings":
        return f"set {a['settings']}"
    if t == "allow_actions_prs":
        return "allow GitHub Actions to create pull requests (staging -> main PR loop)"
    if t == "protect_main":
        return f"protect main: PR required, 0 reviews, no force push/deletion, checks {a['body']['required_status_checks']['contexts']}"
    if t == "upsert_ruleset":
        return f"ruleset '{a['body']['name']}'"
    return "ok (nothing to do)"


def list_owner_repos(owner: str, api=gh_api) -> list[str]:
    try:
        me = api("GET", "user")["login"]
    except ApiError:
        me = None
    path = "user/repos?affiliation=owner&per_page=100" if me == owner else f"users/{owner}/repos?type=owner&per_page=100"
    return sorted(r["full_name"] for r in api("GET", path, paginate=True) if r["owner"]["login"] == owner)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo")
    g.add_argument("--owner")
    ap.add_argument("--enforce", action="store_true", help="apply the plan (default: dry run)")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--json", help="write the plan/results as JSON here")
    args = ap.parse_args(argv)
    exclude = {x.strip() for x in args.exclude.split(",") if x.strip()}
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    repos = [args.repo] if args.repo else list_owner_repos(args.owner)
    mode = "ENFORCE" if args.enforce else "DRY RUN (read-only)"
    print(f"sentinel: {mode}, {len(repos)} repo(s)")
    report = []
    errors = 0
    for repo in repos:
        try:
            info = gather(repo)
        except ApiError as exc:
            print(f"\n{repo}\n  ERROR reading repo: {exc}")
            errors += 1
            continue
        actions = plan_repo(info, today, exclude)
        print(f"\n{repo}  (branches: {', '.join(sorted(info['branches'])) or '-'})")
        for a in actions:
            print(f"  {'would ' if not args.enforce and a['type'] not in ('skip', 'report', 'ok') else ''}{describe(a)}")
        entry = {"repo": repo, "branches": sorted(info["branches"]), "plan": [describe(a) for a in actions]}
        if args.enforce:
            results = apply(repo, actions)
            for r in results:
                print(f"  -> {r}")
            errors += sum(r.startswith(("ERROR", "NOT")) for r in results)
            entry["results"] = results
        report.append(entry)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    print(f"\nsentinel: done, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

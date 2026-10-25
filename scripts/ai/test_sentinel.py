import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import sentinel  # noqa: E402

TODAY = "20261025"


def info(**kw):
    base = {"repo": "o/app", "archived": False, "fork": False, "default_branch": "main",
            "allow_auto_merge": True, "delete_branch_on_merge": False,
            "branches": {"main": "a" * 40, "staging": "b" * 40}, "open_pr_heads": {},
            "main_protection": {"contexts": sorted(sentinel.DEFAULT_REQUIRED_CHECKS), "pr_required": True,
                                "force_pushes": False, "deletions": False},
            "rulesets": [sentinel.STAGING_RULESET, sentinel.CREATION_RULESET],
            "guardrails": True, "config": {}, "errors": []}
    base.update(kw)
    return base


def types(actions):
    return [a["type"] for a in actions]


class PlanTest(unittest.TestCase):
    def test_compliant_repo_is_noop(self):
        self.assertEqual(types(sentinel.plan_repo(info(), TODAY)), ["ok"])

    def test_creates_missing_staging(self):
        acts = sentinel.plan_repo(info(branches={"main": "a" * 40}), TODAY)
        self.assertEqual(acts[0], {"type": "create_staging", "sha": "a" * 40})

    def test_stray_branch_archived_before_delete(self):
        acts = sentinel.plan_repo(info(branches={"main": "a" * 40, "staging": "b" * 40, "feature/x": "c" * 40}), TODAY)
        t = types(acts)
        self.assertLess(t.index("archive_branch"), t.index("delete_branch"))
        self.assertEqual(acts[t.index("archive_branch")]["tag"], "archive/feature-x-20261025-ccccccc")

    def test_allowed_branches_kept(self):
        acts = sentinel.plan_repo(info(branches={"main": "a" * 40, "staging": "b" * 40, "dependabot/npm/x": "c" * 40, "gh-pages": "d" * 40}), TODAY)
        self.assertEqual(types(acts), ["ok"])

    def test_branch_with_open_pr_left_alone(self):
        acts = sentinel.plan_repo(info(branches={"main": "a" * 40, "staging": "b" * 40, "wip": "c" * 40}, open_pr_heads={"wip": [4]}), TODAY)
        self.assertNotIn("delete_branch", types(acts))
        self.assertIn("open PR", acts[0]["message"])

    def test_skips_archived_fork_excluded(self):
        self.assertEqual(sentinel.plan_repo(info(archived=True), TODAY)[0]["reason"], "archived")
        self.assertEqual(sentinel.plan_repo(info(fork=True), TODAY)[0]["reason"], "fork")
        self.assertEqual(sentinel.plan_repo(info(), TODAY, {"app"})[0]["reason"], "excluded")

    def test_non_main_default_is_report_only(self):
        acts = sentinel.plan_repo(info(default_branch="master", branches={"master": "a" * 40, "dev": "c" * 40}), TODAY)
        self.assertEqual(types(acts), ["report"])

    def test_settings_fixed(self):
        acts = sentinel.plan_repo(info(delete_branch_on_merge=True), TODAY)
        self.assertEqual(acts[0]["settings"], {"allow_auto_merge": True, "delete_branch_on_merge": False})

    def test_protection_applied_when_missing(self):
        acts = sentinel.plan_repo(info(main_protection="none", rulesets=[]), TODAY)
        self.assertEqual(types(acts), ["protect_main", "upsert_ruleset", "upsert_ruleset"])
        body = acts[0]["body"]
        self.assertEqual(body["required_pull_request_reviews"]["required_approving_review_count"], 0)
        self.assertFalse(body["allow_force_pushes"])
        self.assertIn("source-is-staging", body["required_status_checks"]["contexts"])

    def test_no_required_checks_without_guardrails(self):
        acts = sentinel.plan_repo(info(guardrails=False, main_protection="none"), TODAY)
        self.assertNotIn("protect_main", types(acts))

    def test_private_free_plan_reported(self):
        acts = sentinel.plan_repo(info(main_protection="unavailable", rulesets=[]), TODAY)
        self.assertEqual(types(acts), ["report"])

    def test_allows_actions_prs(self):
        acts = sentinel.plan_repo(info(actions_can_open_prs=False), TODAY)
        self.assertEqual(types(acts), ["allow_actions_prs"])

    def test_protection_not_strict(self):
        self.assertFalse(sentinel.main_protection(["x"])["required_status_checks"]["strict"])

    def test_rulesets_unavailable_reported(self):
        acts = sentinel.plan_repo(info(rulesets=None), TODAY)
        self.assertEqual(types(acts), ["report"])
        self.assertIn("rulesets unavailable", acts[0]["message"])

    def test_repo_config_overrides_checks(self):
        acts = sentinel.plan_repo(info(main_protection="none", config={"required_checks": ["guardrails"]}), TODAY)
        self.assertEqual(acts[0]["body"]["required_status_checks"]["contexts"], ["guardrails"])

    def test_creation_ruleset_excludes_allowed(self):
        body = sentinel.creation_ruleset(["dependabot/*", "gh-pages"])
        self.assertIn("refs/heads/dependabot/**/*", body["conditions"]["ref_name"]["exclude"])
        self.assertIn("refs/heads/staging", body["conditions"]["ref_name"]["exclude"])


class ApplyTest(unittest.TestCase):
    def test_delete_only_after_verified_tag(self):
        calls = []

        def api(method, path, body=None, paginate=False):
            calls.append((method, path))
            if method == "GET" and "/git/ref/tags/" in path:
                return {"object": {"sha": "c" * 40}}
            return {}

        acts = [{"type": "archive_branch", "branch": "x", "sha": "c" * 40, "tag": "archive/x"},
                {"type": "delete_branch", "branch": "x", "tag": "archive/x"}]
        res = sentinel.apply("o/app", acts, api)
        self.assertEqual(calls[-1], ("DELETE", "repos/o/app/git/refs/heads/x"))
        self.assertIn("restore", res[-1])

    def test_failed_archive_blocks_delete(self):
        def api(method, path, body=None, paginate=False):
            if method == "POST":
                raise sentinel.ApiError(403, "forbidden")
            if method == "DELETE":
                raise AssertionError("must not delete")
            return {}

        acts = [{"type": "archive_branch", "branch": "x", "sha": "c" * 40, "tag": "archive/x"},
                {"type": "delete_branch", "branch": "x", "tag": "archive/x"}]
        res = sentinel.apply("o/app", acts, api)
        self.assertTrue(res[0].startswith("ERROR"))
        self.assertTrue(res[1].startswith("NOT deleting"))

    def test_ruleset_upsert_updates_existing(self):
        calls = []

        def api(method, path, body=None, paginate=False):
            calls.append((method, path))
            if paginate:
                return [{"name": sentinel.STAGING_RULESET, "id": 7}]
            return {}

        sentinel.apply("o/app", [{"type": "upsert_ruleset", "body": sentinel.staging_ruleset()}], api)
        self.assertIn(("PUT", "repos/o/app/rulesets/7"), calls)


if __name__ == "__main__":
    unittest.main()

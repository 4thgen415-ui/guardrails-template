"""Unit tests for editlint.py. Run: python3 -m unittest discover -s scripts/ai -p 'test_*.py'"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import editlint  # noqa: E402

BASE_TS = """import { db } from "./db";

// Looks up one user. Returns null when missing.
export async function getUserById(id: string) {
  return db.get(id);
}

/** Lists users for an org. */
export async function listUsers(orgId: string) {
  return db.list(orgId);
}

export class BillingClient {
  // Creates a Stripe checkout session.
  async createCheckout(priceId: string) {
    return priceId;
  }

  async cancel(id: string) {
    return id;
  }
}
"""


class GitRepo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def write(self, rel, content, binary=False):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode())

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def close(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()


class EditlintTests(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo()
        self.repo.write("src/user-service.ts", BASE_TS)
        self.repo.commit("base")
        self.repo.git("switch", "-q", "-c", "staging")

    def tearDown(self):
        self.repo.close()

    def lint(self, body=None):
        self.repo.commit("change")
        with contextlib.redirect_stdout(io.StringIO()):
            return editlint.run("main", "HEAD", body)

    def test_unscoped_modification_fails(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("return db.get(id);", "return db.get(id) ?? null;"))
        fails = self.lint()
        self.assertTrue(any("'getUserById' modified" in f for f in fails), fails)

    def test_scoped_modification_via_pr_body_passes(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("return db.get(id);", "return db.get(id) ?? null;"))
        body = "Fix lookup\n\n```change-scope\nsymbols: [getUserById]\n```\n"
        self.assertEqual(self.lint(body), [])

    def test_scope_file_used_when_changed(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("return db.get(id);", "return db.get(id) ?? null;"))
        self.repo.write(".ai/change-scope.yml", "mode: feature\nsymbols:\n  - getUserById\n")
        self.assertEqual(self.lint(), [])

    def test_method_change_needs_method_in_scope(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("return priceId;", "return `p_${priceId}`;"))
        fails = self.lint()
        self.assertTrue(any("'BillingClient.createCheckout' modified" in f for f in fails), fails)
        self.assertFalse(any("'BillingClient' modified" in f for f in fails), fails)

    def test_method_in_scope_passes(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("return priceId;", "return `p_${priceId}`;"))
        self.assertEqual(self.lint("```change-scope\nsymbols: [BillingClient.createCheckout]\n```"), [])

    def test_deleted_symbol_fails(self):
        start = BASE_TS.index("/** Lists users")
        end = BASE_TS.index("export class")
        self.repo.write("src/user-service.ts", BASE_TS[:start] + BASE_TS[end:])
        fails = self.lint()
        self.assertTrue(any("'listUsers' deleted" in f for f in fails), fails)

    def test_lost_comment_fails(self):
        src = BASE_TS.replace('import { db } from "./db";\n', '// Data access goes through ./db only.\nimport { db } from "./db";\n')
        self.repo.write("src/user-service.ts", src)
        self.repo.commit("add module comment")
        self.repo.git("branch", "-f", "main", "HEAD")
        self.repo.write("src/user-service.ts", BASE_TS)
        fails = self.lint()
        self.assertTrue(any("comment removed" in f for f in fails), fails)

    def test_edit_comments_allows_header_rewrite(self):
        src = BASE_TS.replace('import { db } from "./db";\n', '// Data access goes through ./db only.\nimport { db } from "./db";\n')
        self.repo.write("src/user-service.ts", src)
        self.repo.commit("add module comment")
        self.repo.git("branch", "-f", "main", "HEAD")
        self.repo.write("src/user-service.ts", src.replace("./db only.", "./db and its cache."))
        self.assertEqual(self.lint("```change-scope\nedit_comments: [src/user-service.ts]\n```"), [])

    def test_lost_comment_in_unscoped_method_fails_even_if_class_scoped(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("  // Creates a Stripe checkout session.\n", ""))
        fails = self.lint("```change-scope\nsymbols: [BillingClient]\n```")
        self.assertTrue(any("comment removed" in f for f in fails), fails)

    def test_comment_inside_scoped_symbol_may_change(self):
        new = BASE_TS.replace("// Looks up one user. Returns null when missing.\n", "// Looks up one user by primary key.\n")
        self.repo.write("src/user-service.ts", new)
        self.assertEqual(self.lint("```change-scope\nsymbols: [getUserById]\n```"), [])

    def test_line_ending_change_fails(self):
        self.repo.write("src/user-service.ts", BASE_TS.replace("\n", "\r\n"))
        fails = self.lint("```change-scope\nsymbols: [getUserById, listUsers, BillingClient, BillingClient.createCheckout, BillingClient.cancel]\n```")
        self.assertTrue(any("line endings" in f for f in fails), fails)

    def test_budget_exceeded_fails(self):
        self.repo.write("src/big.ts", "".join(f"export const v{i} = {i};\n" for i in range(50)))
        fails = self.lint("```change-scope\nmax_changed_lines: 20\n```")
        self.assertTrue(any("budget" in f for f in fails), fails)

    def test_new_symbol_within_budget_passes(self):
        self.repo.write("src/user-service.ts", BASE_TS + "\nexport function getUserEmail(id: string) {\n  return id;\n}\n")
        self.assertEqual(self.lint(), [])

    def test_file_delete_fails_unless_listed(self):
        self.repo.git("rm", "-q", "src/user-service.ts")
        fails = self.lint()
        self.assertTrue(any("file deleted" in f for f in fails), fails)

    def test_paths_restriction(self):
        self.repo.write("src/other.ts", "export const a = 1;\n")
        fails = self.lint("```change-scope\npaths: [src/billing/*]\n```")
        self.assertTrue(any("outside declared paths" in f for f in fails), fails)

    def test_refactor_split_identical_move_passes(self):
        start = BASE_TS.index("export class")
        cls = BASE_TS[start:]
        self.repo.write("src/user-service.ts", BASE_TS[:start].rstrip("\n") + "\n")
        self.repo.write("src/billing/billing-client.ts", cls)
        self.assertEqual(self.lint("```change-scope\nmode: refactor-split\n```"), [])

    def test_refactor_split_altered_move_fails(self):
        start = BASE_TS.index("export class")
        cls = BASE_TS[start:].replace("return id;", "return id.trim();")
        self.repo.write("src/user-service.ts", BASE_TS[:start].rstrip("\n") + "\n")
        self.repo.write("src/billing/billing-client.ts", cls)
        fails = self.lint("```change-scope\nmode: refactor-split\n```")
        self.assertTrue(any("does not reappear byte-identical" in f for f in fails), fails)

    def test_test_files_ignored_by_symbol_checks(self):
        self.repo.write("src/user-service.test.ts", "it('x', () => {});\n")
        self.assertEqual(self.lint(), [])

    def test_bad_mode_raises(self):
        self.repo.write("src/a.ts", "export const a = 1;\n")
        self.repo.commit("x")
        with self.assertRaises(ValueError):
            editlint.run("main", "HEAD", "```change-scope\nmode: yolo\n```")


class SymbolExtractionTests(unittest.TestCase):
    def test_python_symbols(self):
        src = "class A:\n    def f(self):\n        return 1\n\n    def g(self):\n        return 2\n\ndef h():\n    pass\n"
        syms = editlint.extract_symbols(src, "m.py")
        self.assertEqual(set(syms), {"A", "A.f", "A.g", "h"})

    def test_ts_symbols(self):
        syms = editlint.extract_symbols(BASE_TS, "a.ts")
        self.assertEqual(
            set(syms),
            {"getUserById", "listUsers", "BillingClient", "BillingClient.createCheckout", "BillingClient.cancel"},
        )


if __name__ == "__main__":
    unittest.main()

"""Unit tests for module_lint.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_lint  # noqa: E402


class ModuleLintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], check=True)
        self.write(".ai/config.yml", "module_max_lines: 20\nmodule_max_symbols: 3\nsource_dirs: [src]\n")
        self.write("src/billing/index.ts", 'export { createCheckout } from "./checkout";\n')
        self.write("src/billing/checkout.ts", 'import { stripeFetch } from "./stripe-client";\nexport function createCheckout() { return stripeFetch(); }\n')
        self.write("src/billing/stripe-client.ts", "export function stripeFetch() { return 1; }\n")
        self.write("src/routes/checkout-route.ts", 'import { createCheckout } from "../billing";\nexport const route = createCheckout;\n')
        self.commit("base")
        subprocess.run(["git", "switch", "-q", "-c", "staging"], check=True)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg):
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], check=True)

    def run_lint(self, base="main"):
        return module_lint.lint(self.root, base)

    def test_clean_modules_pass(self):
        self.assertEqual(self.run_lint(), ([], []))

    def test_new_deep_import_fails(self):
        self.write("src/routes/refund-route.ts", 'import { stripeFetch } from "../billing/stripe-client";\nexport const r = stripeFetch;\n')
        self.commit("deep")
        fails, _ = self.run_lint()
        self.assertTrue(any("deep import '../billing/stripe-client'" in f for f in fails), fails)

    def test_root_alias_deep_import_fails(self):
        self.write("src/routes/a.ts", 'import { stripeFetch } from "src/billing/stripe-client";\n')
        self.commit("deep alias")
        fails, _ = self.run_lint()
        self.assertTrue(any("bypasses the public index of src/billing/" in f for f in fails), fails)

    def test_internal_import_within_module_ok(self):
        fails, warns = self.run_lint()
        self.assertFalse(any("checkout.ts" in m for m in fails + warns))

    def test_existing_deep_import_only_warns(self):
        self.write("src/routes/old.ts", 'import { stripeFetch } from "../billing/stripe-client";\n')
        self.commit("old deep")
        subprocess.run(["git", "branch", "-f", "main", "HEAD"], check=True)
        self.write("src/routes/old.ts", 'import { stripeFetch } from "../billing/stripe-client";\nexport const x = 1;\n')
        self.commit("touch")
        fails, warns = self.run_lint()
        self.assertEqual(fails, [])
        self.assertTrue(any("deep import" in w for w in warns), warns)

    def test_new_oversized_file_fails(self):
        self.write("src/worker.ts", "".join(f"export const v{i} = {i};\n" for i in range(5)))
        self.commit("big")
        fails, _ = self.run_lint()
        self.assertTrue(any("src/worker.ts" in f and "top-level symbols" in f for f in fails), fails)

    def test_existing_oversized_file_warns(self):
        self.write("src/legacy.ts", "\n".join(f"// line {i}" for i in range(30)) + "\n")
        self.commit("legacy")
        subprocess.run(["git", "branch", "-f", "main", "HEAD"], check=True)
        self.write("src/legacy.ts", "\n".join(f"// line {i}" for i in range(31)) + "\n")
        self.commit("grow")
        fails, warns = self.run_lint()
        self.assertEqual(fails, [])
        self.assertTrue(any("src/legacy.ts" in w for w in warns), warns)

    def test_python_deep_import(self):
        self.write("src/app/__init__.py", "")
        self.write("src/app/billing/__init__.py", "from .stripe_client import charge\n")
        self.write("src/app/billing/stripe_client.py", "def charge():\n    return 1\n")
        self.write("src/app/routes/__init__.py", "")
        self.write("src/app/routes/pay.py", "from app.billing.stripe_client import charge\n")
        self.write("src/app/routes/ok.py", "from app.billing import charge\n")
        self.commit("py")
        fails, _ = self.run_lint()
        self.assertTrue(any("pay.py" in f for f in fails), fails)
        self.assertFalse(any("ok.py" in f for f in fails), fails)

    def test_tests_may_import_internals(self):
        self.write("src/billing/stripe-client.test.ts", 'import { stripeFetch } from "./stripe-client";\nimport { x } from "../routes/checkout-route";\n')
        self.commit("t")
        self.assertEqual(self.run_lint()[0], [])

    def test_report_mode_never_fails(self):
        self.write("src/routes/refund-route.ts", 'import { stripeFetch } from "../billing/stripe-client";\n')
        self.commit("deep")
        fails, warns = self.run_lint(base=None)
        self.assertEqual(fails, [])
        self.assertTrue(warns)


if __name__ == "__main__":
    unittest.main()

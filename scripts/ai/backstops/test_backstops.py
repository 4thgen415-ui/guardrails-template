"""Unit tests for backstops.py. Run: python3 -m unittest discover -s scripts/ai/backstops -p 'test_*.py'"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backstops  # noqa: E402


class PatternTests(unittest.TestCase):
    def test_fake_url_flagged(self):
        self.assertTrue(backstops.url_findings("see https://example.com/docs", "x"))
        self.assertTrue(backstops.url_findings("see https://github.com/path/to/repo", "x"))

    def test_real_url_not_flagged_offline(self):
        self.assertEqual(backstops.url_findings("see https://developers.cloudflare.com/d1/", "x"), [])

    def test_online_check_flags_dns_failure(self):
        with mock.patch.object(backstops.socket, "getaddrinfo", side_effect=backstops.socket.gaierror):
            out = backstops.url_findings("https://made-up-docs.dev/api", "x", check_online=True)
        self.assertTrue(any("does not resolve" in f for f in out), out)

    def test_dangling_citation(self):
        self.assertTrue(backstops.citation_findings("D1 supports this [1].", "x"))
        self.assertEqual(backstops.citation_findings("D1 supports this [1].\n\n[1]: https://developers.cloudflare.com/d1/", "x"), [])

    def test_markdown_links_and_arrays_are_not_citations(self):
        self.assertEqual(backstops.citation_findings("see [docs](https://a.dev) and rows[0]", "x"), [])

    def test_placeholder_ids(self):
        for line in ['const id = "abc123";', "where id = 'user_123'", "00000000-0000-0000-0000-000000000000", "fetch(`/users/<user-id>`)"]:
            self.assertTrue(backstops.placeholder_id_findings([(1, line)], "f.ts"), line)
        self.assertEqual(backstops.placeholder_id_findings([(1, "const id = row.id;")], "f.ts"), [])
        self.assertEqual(backstops.placeholder_id_findings([(1, 'const id = "abc123"; // backstops: allow')], "f.ts"), [])

    def test_claims_need_evidence(self):
        self.assertTrue(backstops.claim_findings("Fixed it, all tests pass.", "PR"))
        self.assertEqual(backstops.claim_findings("All tests pass: https://github.com/o/r/actions/runs/123", "PR"), [])
        self.assertEqual(backstops.claim_findings("All tests pass.\n```\n12 passed\n```", "PR"), [])
        self.assertEqual(backstops.claim_findings("Adds getUserById.", "PR"), [])


class HookTests(unittest.TestCase):
    def run_main(self, args, stdin):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = backstops.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_post_hook_blocks_placeholder_id(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/user-repo.ts", "content": 'return { id: "user_123" };'}}
        code, _, err = self.run_main(["hook-post"], json.dumps(payload))
        self.assertEqual(code, 2)
        self.assertIn("placeholder ID", err)

    def test_post_hook_allows_clean_edit(self):
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "src/a.ts", "new_string": "return rows.map((r) => r.id);"}}
        self.assertEqual(self.run_main(["hook-post"], json.dumps(payload))[0], 0)

    def test_post_hook_ignores_tests(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/a.test.ts", "content": 'const id = "user_123";'}}
        self.assertEqual(self.run_main(["hook-post"], json.dumps(payload))[0], 0)

    def _transcript(self, events):
        fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    @staticmethod
    def _assistant(*blocks):
        return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}

    def test_stop_hook_blocks_unverified_claim(self):
        path = self._transcript([
            self._assistant({"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.ts"}}),
            self._assistant({"type": "text", "text": "Done, all tests pass."}),
        ])
        code, out, _ = self.run_main(["hook-stop"], json.dumps({"transcript_path": path}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_hook_allows_verified_claim(self):
        path = self._transcript([
            self._assistant({"type": "tool_use", "name": "Edit", "input": {"file_path": "src/a.ts"}}),
            self._assistant({"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}),
            self._assistant({"type": "text", "text": "Done, all tests pass."}),
        ])
        code, out, _ = self.run_main(["hook-stop"], json.dumps({"transcript_path": path}))
        self.assertEqual((code, out), (0, ""))

    def test_stop_hook_verify_before_edit_is_not_evidence(self):
        path = self._transcript([
            self._assistant({"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}),
            self._assistant({"type": "tool_use", "name": "Write", "input": {"file_path": "src/a.ts"}}),
            self._assistant({"type": "text", "text": "Tests are passing."}),
        ])
        _, out, _ = self.run_main(["hook-stop"], json.dumps({"transcript_path": path}))
        self.assertIn("block", out)

    def test_stop_hook_no_loop(self):
        _, out, _ = self.run_main(["hook-stop"], json.dumps({"stop_hook_active": True, "transcript_path": "/nonexistent"}))
        self.assertEqual(out, "")


class DiffModeTests(unittest.TestCase):
    def test_diff_mode(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            self.addCleanup(os.chdir, cwd)
            for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
                subprocess.run(["git", *a], check=True)
            Path("README.md").write_text("x\n")
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-qm", "base"], check=True)
            subprocess.run(["git", "switch", "-qc", "staging"], check=True)
            Path("src").mkdir()
            Path("src/repo.ts").write_text('export const fallbackId = "abc123";\n')
            subprocess.run(["git", "add", "-A"], check=True)
            subprocess.run(["git", "commit", "-qm", "c"], check=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"PR_BODY": "All tests pass."}):
                code = backstops.main(["diff", "--base", "main"])
            self.assertEqual(code, 1)
            self.assertIn("placeholder ID", out.getvalue())
            self.assertIn("without evidence", out.getvalue())


if __name__ == "__main__":
    unittest.main()

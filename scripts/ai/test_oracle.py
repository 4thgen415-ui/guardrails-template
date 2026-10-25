import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(__file__))
import oracle  # noqa: E402


class OracleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".ai/oracles"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, spec):
        with open(os.path.join(self.root, ".ai/oracles", name), "w", encoding="utf-8") as fh:
            json.dump(spec, fh)

    def run_main(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = oracle.main(["--root", self.root])
        return code, buf.getvalue()

    def test_exact_match_passes(self):
        self.write("a.json", {"cmd": "printf 'hi\\n'", "expect_stdout": "hi\n"})
        self.assertEqual(self.run_main()[0], 0)

    def test_mismatch_fails(self):
        self.write("a.json", {"cmd": "printf 'fake data\\n'", "expect_stdout": "No users found.\n"})
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("expected", out)

    def test_exit_code_checked(self):
        self.write("a.json", {"cmd": "exit 3", "expect_exit": 0})
        self.assertEqual(self.run_main()[0], 1)

    def test_contains(self):
        self.write("a.json", {"cmd": "echo total=42", "expect_stdout_contains": "total=42"})
        self.assertEqual(self.run_main()[0], 0)

    def test_invalid_spec_fails(self):
        self.write("a.json", {"name": "no cmd"})
        self.assertEqual(self.run_main()[0], 1)

    def test_no_oracles_passes(self):
        self.assertEqual(self.run_main()[0], 0)


if __name__ == "__main__":
    unittest.main()

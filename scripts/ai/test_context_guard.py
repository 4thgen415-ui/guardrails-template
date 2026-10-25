import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import context_guard  # noqa: E402


class ContextGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self.file = os.path.join(self.d, "a.ts")
        with open(self.file, "w") as fh:
            fh.write("\n".join(f"line {i}" for i in range(40)) + "\n")
        self.transcript = os.path.join(self.d, "t.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def transcript_with(self, *reads):
        with open(self.transcript, "w") as fh:
            for p in reads:
                fh.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": p}}]}}) + "\n")

    def payload(self, tool, **inp):
        return {"tool_name": tool, "cwd": self.d, "transcript_path": self.transcript,
                "tool_input": {"file_path": self.file, **inp}}

    def test_edit_without_read_blocked(self):
        self.transcript_with()
        self.assertIn("not Read", context_guard.check(self.payload("Edit", old_string="a", new_string="b")))

    def test_edit_after_read_allowed(self):
        self.transcript_with(self.file)
        self.assertIsNone(context_guard.check(self.payload("Edit", old_string="a", new_string="b")))

    def test_new_file_allowed(self):
        self.transcript_with()
        p = self.payload("Write", content="x\n")
        p["tool_input"]["file_path"] = os.path.join(self.d, "new.ts")
        self.assertIsNone(context_guard.check(p))

    def test_shrinking_write_blocked(self):
        self.transcript_with(self.file)
        self.assertIn("Whole-file", context_guard.check(self.payload("Write", content="short\n")))

    def test_full_write_allowed(self):
        self.transcript_with(self.file)
        content = "\n".join(f"line {i}" for i in range(41)) + "\n"
        self.assertIsNone(context_guard.check(self.payload("Write", content=content)))

    def test_missing_transcript_fails_open(self):
        p = self.payload("Edit", old_string="a", new_string="b")
        p["transcript_path"] = os.path.join(self.d, "missing.jsonl")
        self.assertIsNone(context_guard.check(p))


if __name__ == "__main__":
    unittest.main()

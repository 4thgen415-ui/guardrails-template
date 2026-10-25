import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import autonomy  # noqa: E402

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def ev(event, days_ago):
    return {"ts": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": event}


class AutonomyTest(unittest.TestCase):
    def test_no_history_is_standard(self):
        self.assertEqual(autonomy.compute_level([], NOW), 1)

    def test_recent_revert_is_probation(self):
        self.assertEqual(autonomy.compute_level([ev("merged", 3), ev("auto_revert", 2)], NOW), 0)

    def test_old_revert_expires(self):
        self.assertEqual(autonomy.compute_level([ev("auto_revert", 30), ev("merged", 1)], NOW), 1)

    def test_streak_promotes(self):
        events = [ev("merged", 20 - i) for i in range(10)]
        self.assertEqual(autonomy.compute_level(events, NOW), 2)

    def test_strike_resets_streak(self):
        events = [ev("merged", 20 - i) for i in range(10)] + [ev("strike", 1)]
        self.assertEqual(autonomy.compute_level(events, NOW), 1)

    def test_parses_comment_bodies(self):
        text = '<!-- ai-activity --> {"ts": "2026-10-24T00:00:00Z", "event": "retry_cap"}\nnoise\n'
        events = autonomy.events_from_text(text)
        self.assertEqual(events[0]["event"], "retry_cap")

    def test_cli_output(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            autonomy.main([])
        self.assertIn("level=1", buf.getvalue())
        self.assertIn("budget_multiplier=1.0", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

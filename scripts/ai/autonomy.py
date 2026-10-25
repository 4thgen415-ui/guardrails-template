#!/usr/bin/env python3
"""Progressive autonomy: derive the agent's autonomy level from its track record.

Events come from the activity log issue (written by pr-loop.sh `log`). Each
event is a JSON object with at least `ts` (ISO 8601 UTC) and `event`.

Levels (higher = more freedom, always without human gates):
  0 probation  - any auto_revert or retry_cap in the last PROBATION_DAYS days:
                 change budget x0.5, dual-model review required, no self-heal.
  1 standard   - default: budget x1, self-heal on.
  2 trusted    - PROMOTE_AFTER consecutive merges with no strike since:
                 budget x2, self-heal on.

Usage:
  autonomy.py --events FILE        (JSON list, or comment bodies one per line)
  autonomy.py --repo OWNER/NAME    (reads the activity log issue with `gh`)
Prints key=value lines suitable for $GITHUB_OUTPUT.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

PROBATION_DAYS = 14
PROMOTE_AFTER = 10
LEVELS = {
    0: {"name": "probation", "budget_multiplier": 0.5, "self_heal": False, "dual_review_required": True},
    1: {"name": "standard", "budget_multiplier": 1.0, "self_heal": True, "dual_review_required": False},
    2: {"name": "trusted", "budget_multiplier": 2.0, "self_heal": True, "dual_review_required": False},
}
EVENT_RE = re.compile(r"<!-- ai-activity -->\s*(\{.*\})")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_level(events: list[dict], now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    events = sorted((e for e in events if "ts" in e and "event" in e), key=lambda e: parse_ts(e["ts"]))
    cutoff = now - timedelta(days=PROBATION_DAYS)
    if any(e["event"] in ("auto_revert", "retry_cap") and parse_ts(e["ts"]) >= cutoff for e in events):
        return 0
    streak = 0
    for e in events:
        if e["event"] == "merged":
            streak += 1
        elif e["event"] in ("strike", "auto_revert", "retry_cap", "self_heal"):
            streak = 0
    return 2 if streak >= PROMOTE_AFTER else 1


def events_from_text(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    out = []
    for match in EVENT_RE.finditer(text):
        try:
            out.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def events_from_repo(repo: str) -> list[dict]:
    issue = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--label", "ai-activity-log", "--state", "all",
         "--limit", "1", "--json", "number", "--jq", ".[0].number // empty"],
        capture_output=True, text=True, check=False).stdout.strip()
    if not issue:
        return []
    bodies = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{issue}/comments", "--paginate", "--jq", ".[].body"],
        capture_output=True, text=True, check=False).stdout
    return events_from_text(bodies)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events")
    ap.add_argument("--repo")
    args = ap.parse_args(argv)
    if args.events:
        with open(args.events, encoding="utf-8") as fh:
            events = events_from_text(fh.read())
    elif args.repo:
        events = events_from_repo(args.repo)
    else:
        events = []
    level = compute_level(events)
    info = LEVELS[level]
    print(f"level={level}")
    print(f"name={info['name']}")
    print(f"budget_multiplier={info['budget_multiplier']}")
    print(f"self_heal={'true' if info['self_heal'] else 'false'}")
    print(f"dual_review_required={'true' if info['dual_review_required'] else 'false'}")
    print(f"events={len(events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

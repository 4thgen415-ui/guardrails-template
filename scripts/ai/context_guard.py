#!/usr/bin/env python3
"""Context guard (Claude Code PreToolUse hook for Edit|Write|MultiEdit).

Blocks edits made without the file in context, the usual cause of invented
code and silently dropped content:
  1. Editing or overwriting an existing file that was not Read (or written)
     earlier in this session, per the session transcript.
  2. A Write that replaces an existing file of 20+ lines with less than half
     as many lines (a whole-file rewrite that drops content). Use Edit instead.

Exit 2 blocks the tool call and shows stderr to the agent. If the transcript
cannot be read the guard allows the call (CI remains the final authority).
"""
from __future__ import annotations

import json
import os
import sys

SHRINK_MIN_LINES = 20
SHRINK_RATIO = 0.5


def seen_paths(transcript_path: str) -> set[str] | None:
    try:
        fh = open(transcript_path, encoding="utf-8")
    except OSError:
        return None
    seen: set[str] = set()
    with fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in ("Read", "Write", "Edit", "MultiEdit"):
                    path = (item.get("input") or {}).get("file_path")
                    if path:
                        seen.add(os.path.realpath(path))
    return seen


def check(payload: dict) -> str | None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path")
    if tool not in ("Edit", "Write", "MultiEdit") or not path:
        return None
    cwd = payload.get("cwd") or os.getcwd()
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd, path))
    if not os.path.exists(full):
        return None  # new file: nothing to lose
    seen = seen_paths(payload.get("transcript_path", ""))
    if seen is not None and full not in seen:
        return (f"context-guard: {path} was not Read in this session. Read the file first, "
                "then edit only the symbols you were asked to change.")
    if tool == "Write":
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                old_lines = len(fh.read().splitlines())
        except OSError:
            return None
        new_lines = len(str(tool_input.get("content", "")).splitlines())
        if old_lines >= SHRINK_MIN_LINES and new_lines < old_lines * SHRINK_RATIO:
            return (f"context-guard: Write would replace {old_lines} lines of {path} with {new_lines}. "
                    "Whole-file rewrites that drop content are blocked; use Edit on the named symbols.")
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    problem = check(payload)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

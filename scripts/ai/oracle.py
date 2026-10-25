#!/usr/bin/env python3
"""Oracle layer: owner-written expected outputs that the agent cannot change.

Each .ai/oracles/*.json file is a contract:
  {"name": "...", "cmd": "shell command", "expect_exit": 0,
   "expect_stdout": "exact text"   (or "expect_stdout_contains": "substring")}

Oracle files count as test files: they are read-only during implementation
(pre-edit-guard, pre-commit, check-tests-untouched). An agent can only pass an
oracle by making the real command produce the real expected output.

Usage: oracle.py [--dir .ai/oracles] [--root .]
Exit 0 all pass (or no oracles), 1 any mismatch.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    if "cmd" not in spec:
        raise ValueError(f"{path}: missing 'cmd'")
    if "expect_stdout" not in spec and "expect_stdout_contains" not in spec and "expect_exit" not in spec:
        raise ValueError(f"{path}: needs expect_exit, expect_stdout or expect_stdout_contains")
    return spec


def run_oracle(spec: dict, root: str, timeout: int = 120) -> list[str]:
    try:
        proc = subprocess.run(spec["cmd"], shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [f"timed out after {timeout}s"]
    problems = []
    want_exit = int(spec.get("expect_exit", 0))
    if proc.returncode != want_exit:
        problems.append(f"exit {proc.returncode}, expected {want_exit}; stderr: {proc.stderr.strip()[:300]}")
    if "expect_stdout" in spec and proc.stdout != spec["expect_stdout"]:
        problems.append(f"stdout {proc.stdout!r}, expected {spec['expect_stdout']!r}")
    if "expect_stdout_contains" in spec and spec["expect_stdout_contains"] not in proc.stdout:
        problems.append(f"stdout does not contain {spec['expect_stdout_contains']!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".ai/oracles")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(args.root, args.dir, "*.json")))
    if not files:
        print("oracle: no oracles defined")
        return 0
    failed = 0
    for path in files:
        try:
            spec = load(path)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"FAIL {path}: {exc}")
            failed += 1
            continue
        problems = run_oracle(spec, args.root)
        label = spec.get("name", os.path.basename(path))
        if problems:
            failed += 1
            print(f"FAIL {label}: " + "; ".join(problems))
        else:
            print(f"ok   {label}")
    print(f"oracle: {len(files) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

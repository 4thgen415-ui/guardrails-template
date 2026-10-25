#!/usr/bin/env python3
"""Deterministic anti-hallucination backstops (stdlib only).

Flags:
  * fabricated URLs: placeholder hosts/paths (example.com, /path/to/, your-domain),
    and with --check-urls, URLs that do not resolve or return 404/410;
  * dangling citations: [1] / [^1] references with no matching definition;
  * "tests pass"-style claims with no evidence (a CI run URL or a fenced output
    block in the PR body; a test/lint/build command run after the last edit in a
    Claude Code session);
  * placeholder IDs in code (user_123, "abc123", all-zero UUIDs, <user-id>).
Per-line opt-out: put `backstops: allow` on the line.

Modes:
  backstops.py diff --base REF [--pr-body-file F] [--check-urls]   # CI / local
  backstops.py hook-post   < hook-json    # Claude Code PostToolUse (Edit|Write|MultiEdit)
  backstops.py hook-stop   < hook-json    # Claude Code Stop
  backstops.py text FILE...               # scan arbitrary text/markdown files
Exit: 0 clean, 1 findings (diff/text), 2 findings in PostToolUse hook (blocks).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ALLOW = "backstops: allow"
CODE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".rs", ".sql")
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)/|\.(test|spec)\.[cm]?[jt]sx?$|_test\.(go|py)$|(^|/)test_[^/]*\.py$|^\.ai/oracles/")
SKIP_RE = re.compile(r"^(scripts/ai/|\.githooks/|\.github/|\.claude/|\.cursor/)")

URL_RE = re.compile(r"https?://[^\s<>()\"'`\]]+")
FAKE_HOSTS = {
    "example.com", "example.org", "example.net", "foo.com", "bar.com", "foo.bar", "test.com",
    "domain.com", "yourdomain.com", "your-domain.com", "yoursite.com", "placeholder.com", "website.com",
}
FAKE_PATH_RE = re.compile(r"/path/to/|/your[-_]|/xxx|/\.\.\.|%3C|<|/link-here|/insert", re.I)

PLACEHOLDER_ID_RES = [
    re.compile(r"\b(?:user|usr|cus|cust|org|acct|account|item|order|prod|product|price|sub|sess|session|tenant)_(?:123|1234|12345|abc|abc123|xxx+|test|example|placeholder|dummy|fake)\b", re.I),
    re.compile(r"[\"'](?:abc123|123456|12345|xxx+|placeholder[-_]?id|your[-_]?id|dummy[-_]?id|fake[-_]?id|example[-_]?id|some[-_]?id)[\"']", re.I),
    re.compile(r"\b0{8}-0{4}-0{4}-0{4}-0{12}\b|\b12345678-1234-1234-1234-123456789012\b"),
    re.compile(r"<(?:id|user[-_]?id|your[-_][\w-]+|insert[-_][\w-]+)>", re.I),
]

CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:the\s+)?(?:unit\s+)?tests?\s+(?:now\s+|all\s+)?(?:pass(?:es|ed|ing)?|are\s+(?:green|passing))\b"
    r"|\b(?:build|lint|typecheck|ci)\s+(?:now\s+)?(?:pass(?:es|ed)?|succeed(?:s|ed)?|is\s+(?:green|clean))\b"
    r"|\b(?:fully\s+)?verified\s+(?:that\s+)?(?:it|this|everything)\b|\bworks\s+(?:now|correctly|as expected)\b",
    re.I,
)
EVIDENCE_RE = re.compile(r"/actions/runs/\d+|```[\s\S]+?```")
VERIFY_CMD_RE = re.compile(
    r"\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|lint|typecheck|build|check)\b|\b(vitest|jest|pytest|tsc|eslint|go test|cargo test)\b|scripts/ai/"
)


def is_code(path: str) -> bool:
    return path.endswith(CODE_EXT) and not TEST_RE.search(path) and not SKIP_RE.search(path)


def url_findings(text: str, where: str, check_online: bool = False) -> list[str]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for url in URL_RE.findall(line):
            url = url.rstrip(".,;:")
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            bare = host[4:] if host.startswith("www.") else host
            if bare in FAKE_HOSTS or bare.endswith((".example", ".invalid", ".test")) or FAKE_PATH_RE.search(url):
                out.append(f"{where}:{lineno}: placeholder/fabricated URL {url}")
            elif check_online:
                problem = probe_url(url)
                if problem:
                    out.append(f"{where}:{lineno}: URL {url} {problem}")
    return out


def probe_url(url: str, timeout: float = 8.0) -> str | None:
    """Return a problem description for dead links; None if it resolves (or the check is inconclusive)."""
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror:
        return "does not resolve (DNS)"
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "guardrails-backstops/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return None
    except urllib.error.HTTPError as exc:
        return f"returned HTTP {exc.code}" if exc.code in (404, 410) else None
    except Exception:  # timeouts, TLS, rate limits: inconclusive, do not fail
        return None


def citation_findings(text: str, where: str) -> list[str]:
    out = []
    defined = set(re.findall(r"^\s*\[(\^?\w+)\]:\s*\S+", text, re.M))
    used = set(re.findall(r"(?<![\w\]])\[(\^?\d+)\](?![(:\[])", text))
    for ref in sorted(used - defined):
        out.append(f"{where}: citation [{ref}] has no matching reference definition")
    return out


def placeholder_id_findings(lines: list[tuple[int, str]], where: str) -> list[str]:
    out = []
    for lineno, line in lines:
        if ALLOW in line:
            continue
        for rx in PLACEHOLDER_ID_RES:
            m = rx.search(line)
            if m:
                out.append(f"{where}:{lineno}: placeholder ID {m.group(0)!r} (use a real value from data, or return an honest empty result)")
                break
    return out


def claim_findings(text: str, where: str) -> list[str]:
    if not CLAIM_RE.search(text or ""):
        return []
    if EVIDENCE_RE.search(text):
        return []
    claim = CLAIM_RE.search(text).group(0)
    return [f"{where}: claim {claim!r} without evidence (link the CI run /actions/runs/<id> or paste the command output in a code block)"]


def added_lines_by_file(base: str) -> dict[str, list[tuple[int, str]]]:
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "-U0", "--no-renames", mb, "HEAD"], check=True, capture_output=True, text=True).stdout
    result: dict[str, list[tuple[int, str]]] = {}
    current = None
    lineno = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
        if m:
            lineno = int(m.group(1))
            continue
        if current and line.startswith("+"):
            result.setdefault(current, []).append((lineno, line[1:]))
            lineno += 1
    return result


def cmd_diff(args) -> int:
    findings: list[str] = []
    for path, lines in added_lines_by_file(args.base).items():
        if SKIP_RE.search(path):
            continue
        text = "\n".join(l for _, l in lines)
        if is_code(path):
            findings += placeholder_id_findings(lines, path)
        if not TEST_RE.search(path):
            for f in url_findings(text, path, args.check_urls):
                findings.append(f)
        if path.endswith(".md"):
            full = Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).exists() else text
            findings += citation_findings(full, path)
    body = None
    if args.pr_body_file:
        body = Path(args.pr_body_file).read_text(encoding="utf-8")
    elif os.environ.get("PR_BODY") is not None:
        body = os.environ["PR_BODY"]
    if body:
        findings += url_findings(body, "PR body", args.check_urls)
        findings += citation_findings(body, "PR body")
        findings += claim_findings(body, "PR body")
    for f in findings:
        print(f"FAIL {f}")
    print(f"backstops: {len(findings)} finding(s)")
    return 1 if findings else 0


def cmd_text(args) -> int:
    findings = []
    for p in args.files:
        text = Path(p).read_text(encoding="utf-8", errors="replace")
        findings += url_findings(text, p) + citation_findings(text, p) + claim_findings(text, p)
        if is_code(p):
            findings += placeholder_id_findings(list(enumerate(text.splitlines(), 1)), p)
    for f in findings:
        print(f"FAIL {f}")
    return 1 if findings else 0


def cmd_hook_post(stdin_text: str) -> int:
    data = json.loads(stdin_text or "{}")
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ""
    proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
    rel = path[len(proj) + 1 :] if proj and path.startswith(proj + "/") else path
    parts = [ti.get("content"), ti.get("new_string")] + [e.get("new_string") for e in ti.get("edits") or []]
    text = "\n".join(p for p in parts if p)
    findings = []
    if is_code(rel):
        findings += placeholder_id_findings(list(enumerate(text.splitlines(), 1)), rel)
    if not SKIP_RE.search(rel) and not TEST_RE.search(rel):
        findings += url_findings(text, rel)
    if findings:
        print("Backstops flagged the edit you just made:", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print("Fix it now: use real values from data/config, or return an honest empty result.", file=sys.stderr)
        return 2
    return 0


def _transcript_events(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return


def cmd_hook_stop(stdin_text: str) -> int:
    data = json.loads(stdin_text or "{}")
    if data.get("stop_hook_active"):
        return 0  # already asked once this turn; avoid an infinite loop
    last_text = ""
    last_edit = -1
    last_verify = -1
    for i, ev in enumerate(_transcript_events(data.get("transcript_path", ""))):
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                last_text = block.get("text", "")
            elif block.get("type") == "tool_use":
                name = block.get("name")
                if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                    last_edit = i
                elif name == "Bash" and VERIFY_CMD_RE.search((block.get("input") or {}).get("command", "")):
                    last_verify = i
    claim = CLAIM_RE.search(last_text)
    reasons = []
    if claim and last_verify <= last_edit:
        reasons.append(
            f"You claimed {claim.group(0)!r} but no test/lint/typecheck/build command ran after your last edit. "
            "Run the Verification commands from AGENTS.md and show their real output."
        )
    reasons += url_findings(last_text, "final message")
    reasons += citation_findings(last_text, "final message")
    if reasons:
        print(json.dumps({"decision": "block", "reason": " ".join(reasons)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("diff")
    d.add_argument("--base", required=True)
    d.add_argument("--pr-body-file")
    d.add_argument("--check-urls", action="store_true")
    t = sub.add_parser("text")
    t.add_argument("files", nargs="+")
    sub.add_parser("hook-post")
    sub.add_parser("hook-stop")
    args = ap.parse_args(argv)
    if args.mode == "diff":
        return cmd_diff(args)
    if args.mode == "text":
        return cmd_text(args)
    stdin_text = sys.stdin.read()
    if args.mode == "hook-post":
        return cmd_hook_post(stdin_text)
    return cmd_hook_stop(stdin_text)


if __name__ == "__main__":
    sys.exit(main())

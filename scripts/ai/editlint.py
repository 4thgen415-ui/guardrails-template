#!/usr/bin/env python3
"""editlint: blast-radius lint for agent changes (stdlib only).

Compares BASE (merge-base with --base) against HEAD for every changed file and
FAILS when:
  * an existing symbol (function, class, method, top-level const/type) that is
    not on the allowlist is deleted or modified;
  * a comment present in BASE is missing in HEAD (outside allowlisted symbols);
  * a file's line endings, final newline, or BOM change;
  * the change exceeds the budget (changed lines / files);
  * a file outside the declared `paths` changes, or a file is deleted without
    being listed in `delete_files`;
  * mode refactor-split: a symbol removed from one file does not reappear
    byte-identical in another file.

The allowlist and budget come from a ```change-scope block in the PR body
(--pr-body-file or $PR_BODY), else from .ai/change-scope.yml when that file is
changed in the range, else the strict defaults (no symbols may change).

Usage: python3 scripts/ai/editlint.py --base origin/main [--head HEAD]
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_config import extract_pr_block, parse_simple_yaml  # noqa: E402

SCOPE_FILE = ".ai/change-scope.yml"
DEFAULT_SCOPE = {
    "mode": "feature",
    "symbols": [],
    "paths": [],
    "delete_files": [],
    "max_changed_lines": 300,
    "max_files": 15,
}
CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"}
HASH_COMMENT_EXT = {".py", ".sh", ".yml", ".yaml", ".toml"}
BUDGET_IGNORE = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "uv.lock", "poetry.lock")
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e)/|\.(test|spec)\.[cm]?[jt]sx?$|_test\.(go|py)$|(^|/)test_[^/]*\.py$|^\.ai/oracles/"
)
GUARDRAIL_RE = re.compile(r"^(scripts/ai/|\.githooks/|\.github/|\.claude/|\.cursor/)")

JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "with", "else", "do", "try", "new", "typeof", "await"}
JS_TOP = [
    re.compile(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
    re.compile(r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)"),
]
JS_METHOD = re.compile(
    r"^(\s+)(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*([A-Za-z_$#][\w$]*)\s*(?:<[^>]*>)?\s*\("
)
PY_TOP = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
PY_METHOD = re.compile(r"^(\s+)(?:async\s+)?def\s+([A-Za-z_]\w*)")


def is_test_file(path: str) -> bool:
    return not GUARDRAIL_RE.search(path) and bool(TEST_RE.search(path))


@dataclass
class Symbol:
    name: str
    start: int  # inclusive line index
    end: int  # exclusive
    own_text: str  # text excluding child spans (for classes)
    children: list = field(default_factory=list)


def _is_comment_or_decorator(line: str, ext: str) -> bool:
    s = line.strip()
    if ext == ".py":
        return s.startswith("#") or s.startswith("@")
    return s.startswith(("//", "/*", "*", "@"))


def _spans(lines: list[str], starts: list[tuple[int, str]], end_limit: int, ext: str):
    """Turn sorted (line, name) starts into (name, start, end) spans with leading comments attached."""
    spans = []
    adj = []
    for idx, name in starts:
        s = idx
        while s - 1 >= 0 and lines[s - 1].strip() and _is_comment_or_decorator(lines[s - 1], ext):
            s -= 1
        adj.append((s, idx, name))
    for i, (s, _idx, name) in enumerate(adj):
        e = adj[i + 1][0] if i + 1 < len(adj) else end_limit
        while e > s + 1 and not lines[e - 1].strip():
            e -= 1
        spans.append((name, s, e))
    return spans


def extract_symbols(text: str, path: str) -> dict[str, Symbol]:
    ext = Path(path).suffix
    if ext not in CODE_EXT:
        return {}
    lines = text.splitlines()
    top: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not line or line[0] in " \t":
            continue
        if ext == ".py":
            m = PY_TOP.match(line)
            if m:
                top.append((i, m.group(1)))
        else:
            for rx in JS_TOP:
                m = rx.match(line)
                if m:
                    top.append((i, m.group(1)))
                    break
    symbols: dict[str, Symbol] = {}
    for name, s, e in _spans(lines, top, len(lines), ext):
        body = lines[s:e]
        is_class = bool(re.search(r"\bclass\s+" + re.escape(name) + r"\b", "\n".join(body[:5])))
        child_spans = []
        if is_class:
            starts = []
            indent = None
            for j in range(s + 1, e):
                line = lines[j]
                m = (PY_METHOD if ext == ".py" else JS_METHOD).match(line)
                if not m:
                    continue
                if ext != ".py" and (m.group(2) in JS_KEYWORDS or not line.rstrip().endswith(("{", "("))):
                    continue
                if indent is None:
                    indent = m.group(1)
                if m.group(1) == indent:
                    starts.append((j, m.group(2)))
            # The class's closing line(s) stay with the class itself.
            close = e
            while close > s and lines[close - 1].strip() in ("}", "};", ""):
                close -= 1
            child_spans = _spans(lines, starts, close, ext)
        own = list(body)
        for cname, cs, ce in reversed(child_spans):
            del own[cs - s : ce - s]
            full = f"{name}.{cname}"
            symbols[full] = Symbol(full, cs, ce, "\n".join(lines[cs:ce]))
        symbols[name] = Symbol(name, s, e, "\n".join(own), [f"{name}.{c[0]}" for c in child_spans])
    return symbols


def extract_comments(text: str, path: str) -> list[tuple[int, str]]:
    ext = Path(path).suffix
    out = []
    in_block = False
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if ext in HASH_COMMENT_EXT:
            if s.startswith("#") and not s.startswith("#!"):
                out.append((i, s))
            elif " #" in line and ext == ".py":
                out.append((i, line[line.index(" #") :].strip()))
            continue
        if ext not in CODE_EXT:
            continue
        if in_block or s.startswith("/*"):
            out.append((i, s))
            in_block = "*/" not in s
            continue
        if s.startswith("//"):
            out.append((i, s))
        elif " //" in line:
            out.append((i, line[line.index(" //") :].strip()))
    return [(i, c) for i, c in out if c.strip("/#* ")]


def eol_style(data: bytes) -> tuple[str, bool, bool]:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    style = "none" if crlf == lf == 0 else "crlf" if lf == 0 else "lf" if crlf == 0 else "mixed"
    return style, data.endswith(b"\n"), data.startswith(b"\xef\xbb\xbf")


def git(*args: str, check: bool = True) -> bytes:
    return subprocess.run(["git", *args], check=check, capture_output=True).stdout


def git_show(rev: str, path: str) -> bytes | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def load_scope(base: str, head: str, changed: list[str], pr_body: str | None) -> tuple[dict, str]:
    scope = dict(DEFAULT_SCOPE)
    source = "defaults (no symbols may change)"
    block = extract_pr_block(pr_body or "", "change-scope")
    if block is not None:
        scope.update(parse_simple_yaml(block))
        source = "PR body change-scope block"
    elif SCOPE_FILE in changed:
        data = git_show(head, SCOPE_FILE)
        if data is not None:
            scope.update(parse_simple_yaml(data.decode("utf-8", "replace")))
            source = SCOPE_FILE
    if scope.get("mode") not in ("feature", "refactor-split"):
        raise ValueError(f"unknown mode {scope.get('mode')!r}")
    return scope, source


def changed_line_count(a: str, b: str) -> int:
    n = 0
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            n += 1
    return n


def run(base: str, head: str, pr_body: str | None) -> list[str]:
    merge_base = git("merge-base", base, head).decode().strip()
    raw = git("diff", "--name-status", "--no-renames", merge_base, head).decode()
    entries = []
    for line in raw.splitlines():
        parts = line.split("\t")
        entries.append((parts[0][0], parts[-1]))
    changed = [p for _, p in entries]
    scope, source = load_scope(merge_base, head, changed, pr_body)
    allowed = set(scope.get("symbols") or [])
    print(f"editlint: {len(entries)} changed file(s); scope from {source}; mode={scope['mode']}")

    failures: list[str] = []
    budget_lines = 0
    budget_files = 0
    removed_symbols: list[tuple[str, str, str]] = []  # (path, name, text)
    head_texts: dict[str, str] = {}

    for status, path in entries:
        if path.startswith(".ai/"):
            continue
        paths = scope.get("paths") or []
        if paths and not any(fnmatch.fnmatch(path, g) for g in paths):
            failures.append(f"{path}: outside declared paths {paths}")
        old_b = git_show(merge_base, path) if status != "A" else b""
        new_b = git_show(head, path) if status != "D" else b""
        old_b = old_b or b""
        new_b = new_b or b""
        if b"\0" in old_b[:8000] or b"\0" in new_b[:8000]:
            continue
        old, new = old_b.decode("utf-8", "replace"), new_b.decode("utf-8", "replace")
        head_texts[path] = new
        test = is_test_file(path)
        if not test and not path.endswith(BUDGET_IGNORE):
            budget_files += 1
            budget_lines += changed_line_count(old, new)
        if status == "D" and path not in (scope.get("delete_files") or []) and scope["mode"] != "refactor-split":
            failures.append(f"{path}: file deleted but not listed in delete_files")
        if status in ("M", "T"):
            if eol_style(old_b) != eol_style(new_b):
                o, n = eol_style(old_b), eol_style(new_b)
                failures.append(f"{path}: line endings/final newline/BOM changed {o} -> {n}")
        if test or status == "A":
            continue
        old_syms = extract_symbols(old, path)
        new_syms = extract_symbols(new, path)
        for name, sym in old_syms.items():
            if name in allowed:
                continue
            if name not in new_syms:
                removed_symbols.append((path, name, "\n".join(old.splitlines()[sym.start : sym.end])))
            elif new_syms[name].own_text != sym.own_text:
                failures.append(f"{path}: symbol '{name}' modified but not in scope symbols")
        # Comments: every BASE comment outside allowlisted symbols must survive.
        # (refactor-split checks comments across all files below instead.)
        if scope["mode"] == "refactor-split":
            continue
        exempt: set[int] = set()
        for name in allowed:
            if name in old_syms:
                sym = old_syms[name]
                rng = set(range(sym.start, sym.end))
                for child in sym.children:
                    if child not in allowed and child in old_syms:
                        rng -= set(range(old_syms[child].start, old_syms[child].end))
                exempt.update(rng)
        base_comments = Counter(c for i, c in extract_comments(old, path) if i not in exempt)
        head_comments = Counter(c for _, c in extract_comments(new, path))
        for c, n in base_comments.items():
            if head_comments[c] < n:
                failures.append(f"{path}: comment removed: {c[:80]!r}")

    if scope["mode"] == "refactor-split":
        all_head = "\n".join(head_texts.values())
        for path, name, text in removed_symbols:
            if text.strip() and text not in all_head:
                failures.append(f"{path}: refactor-split moved symbol '{name}' but it does not reappear byte-identical")
        for status, path in entries:
            if status == "A" or is_test_file(path) or path.startswith(".ai/"):
                continue
            old = (git_show(merge_base, path) or b"").decode("utf-8", "replace")
            for c in {c for _, c in extract_comments(old, path)}:
                if c not in all_head:
                    failures.append(f"{path}: comment lost during split: {c[:80]!r}")
    else:
        for path, name, _ in removed_symbols:
            failures.append(f"{path}: symbol '{name}' deleted but not in scope symbols")

    # Progressive autonomy (scripts/ai/autonomy.py) scales the budget: 0.5 / 1 / 2.
    try:
        mult = float(os.environ.get("AI_BUDGET_MULTIPLIER", "1") or "1")
    except ValueError:
        mult = 1.0
    if mult != 1.0:
        scope = dict(scope)
        scope["max_changed_lines"] = max(1, int(int(scope["max_changed_lines"]) * mult))
        scope["max_files"] = max(1, int(int(scope["max_files"]) * mult))
    if budget_lines > int(scope["max_changed_lines"]):
        failures.append(f"budget: {budget_lines} changed lines > max_changed_lines {scope['max_changed_lines']}")
    if budget_files > int(scope["max_files"]):
        failures.append(f"budget: {budget_files} files > max_files {scope['max_files']}")
    print(f"editlint: budget used {budget_lines}/{scope['max_changed_lines']} lines, {budget_files}/{scope['max_files']} files")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--pr-body-file")
    args = ap.parse_args(argv)
    body = os.environ.get("PR_BODY")
    if args.pr_body_file:
        body = Path(args.pr_body_file).read_text(encoding="utf-8")
    try:
        failures = run(args.base, args.head, body)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"editlint: ERROR {exc}", file=sys.stderr)
        return 2
    for f in failures:
        print(f"FAIL {f}")
    if failures:
        print(f"editlint: {len(failures)} failure(s). Declare intended symbols in {SCOPE_FILE} or a ```change-scope PR block, or narrow the change.")
        return 1
    print("editlint: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

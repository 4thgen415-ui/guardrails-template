#!/usr/bin/env python3
"""module_lint: keep code modular (stdlib only). See AGENTS.md "## Modules".

Checks source files under `source_dirs` from .ai/config.yml:
  * size budget: more than `module_max_lines` lines or `module_max_symbols`
    top-level symbols. FAIL for files added in the range, WARN for existing ones.
  * deep imports: importing a module's internals instead of its public index
    (index.ts / __init__.py). FAIL when the import line is added in the range,
    WARN when it already existed.
Test files are skipped (tests may import internals).

Usage:
  python3 scripts/ai/module_lint.py --base origin/main   # PR / pre-push mode
  python3 scripts/ai/module_lint.py                      # report-only, all files
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ai_config import parse_simple_yaml  # noqa: E402
from editlint import extract_symbols, is_test_file  # noqa: E402

DEFAULTS = {
    "module_max_lines": 300,
    "module_max_symbols": 10,
    "source_dirs": ["src"],
    "index_files": ["index.ts", "index.tsx", "index.js", "index.mjs", "__init__.py"],
}
CODE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py")
JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)["']([^"']+)["']""")
PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import|import\s+([\w.]+))")
TS_EXTS = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]


def load_config(root: Path) -> dict:
    cfg = dict(DEFAULTS)
    p = root / ".ai" / "config.yml"
    if p.exists():
        cfg.update(parse_simple_yaml(p.read_text(encoding="utf-8")))
    return cfg


def has_index(root: Path, rel_dir: str, index_files: list[str]) -> bool:
    return any((root / rel_dir / name).is_file() for name in index_files)


def resolve_target(root: Path, importer: str, spec: str, source_dirs: list[str], py: bool) -> str | None:
    """Return the repo-relative path (no extension) an import points to, or None if external."""
    if py:
        dots = len(spec) - len(spec.lstrip("."))
        name = spec.lstrip(".")
        if dots:
            base = posixpath.dirname(importer)
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            return posixpath.normpath(posixpath.join(base, *name.split("."))) if name else base
        first = name.split(".")[0]
        for d in [""] + list(source_dirs):
            cand = posixpath.join(d, first) if d else first
            if (root / cand).exists() or (root / (cand + ".py")).exists():
                return posixpath.join(d, *name.split(".")) if d else posixpath.join(*name.split("."))
        return None
    if spec.startswith("."):
        return posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    for d in source_dirs:
        if spec == d or spec.startswith(d + "/"):
            return posixpath.normpath(spec)
    return None


def deep_import_violation(root: Path, importer: str, target: str, index_files: list[str]) -> str | None:
    """If `target` reaches inside a module (dir with an index) the importer is not part of, return that module."""
    target_is_module = (root / target).is_dir() and has_index(root, target, index_files)
    parts = target.split("/")
    importer_dir = posixpath.dirname(importer)
    for i in range(1, len(parts)):
        ancestor = "/".join(parts[:i])
        if ancestor == target and target_is_module:
            break
        inside = importer_dir == ancestor or importer_dir.startswith(ancestor + "/")
        if not inside and has_index(root, ancestor, index_files):
            return ancestor
    return None


def file_imports(text: str, py: bool):
    for lineno, line in enumerate(text.splitlines(), 1):
        if py:
            m = PY_IMPORT.match(line)
            if m:
                yield lineno, line, (m.group(1) or m.group(2))
        else:
            for m in JS_IMPORT.finditer(line):
                yield lineno, line, m.group(1)


def git_lines(*args: str) -> list[str]:
    r = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x]


def added_lines(merge_base: str, path: str) -> set[str]:
    out = subprocess.run(
        ["git", "diff", "-U0", merge_base, "--", path], check=True, capture_output=True, text=True
    ).stdout
    return {line[1:] for line in out.splitlines() if line.startswith("+") and not line.startswith("+++")}


def lint(root: Path, base: str | None) -> tuple[list[str], list[str]]:
    cfg = load_config(root)
    src_dirs = [d.rstrip("/") for d in cfg["source_dirs"]]
    idx = list(cfg["index_files"])
    max_lines, max_syms = int(cfg["module_max_lines"]), int(cfg["module_max_symbols"])

    files = [
        f
        for f in git_lines("ls-files", "-co", "--exclude-standard")
        if f.endswith(CODE_EXT) and any(f.startswith(d + "/") for d in src_dirs) and (root / f).is_file()
    ]
    new_files: set[str] = set()
    changed: set[str] = set()
    merge_base = None
    if base:
        merge_base = git_lines("merge-base", base, "HEAD")[0]
        for line in git_lines("diff", "--name-status", "--no-renames", merge_base):
            status, path = line.split("\t")[0], line.split("\t")[-1]
            changed.add(path)
            if status.startswith("A"):
                new_files.add(path)
        # untracked files count as new too (local pre-push use)
        new_files.update(git_lines("ls-files", "-o", "--exclude-standard"))

    fails, warns = [], []
    for f in sorted(files):
        if is_test_file(f):
            continue
        text = (root / f).read_text(encoding="utf-8", errors="replace")
        n_lines = len(text.splitlines())
        n_syms = len([s for s in extract_symbols(text, f) if "." not in s])
        if n_lines > max_lines or n_syms > max_syms:
            msg = f"{f}: {n_lines} lines / {n_syms} top-level symbols (budget {max_lines} / {max_syms}); split it (refactor-split)"
            (fails if f in new_files else warns).append(msg)
        py = f.endswith(".py")
        added = added_lines(merge_base, f) if merge_base and f in changed and f not in new_files else None
        for lineno, line, spec in file_imports(text, py):
            target = resolve_target(root, f, spec, src_dirs, py)
            if not target:
                continue
            module = deep_import_violation(root, f, target, idx)
            if not module:
                continue
            msg = f"{f}:{lineno}: deep import '{spec}' bypasses the public index of {module}/ (import from the module instead)"
            is_new = f in new_files or (added is not None and line in added)
            (fails if is_new else warns).append(msg)
    return fails, warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="compare against this ref; without it, report-only")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.root or subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())
    os.chdir(root)
    fails, warns = lint(root, args.base)
    for w in warns:
        print(f"WARN {w}")
    for f in fails:
        print(f"FAIL {f}")
    print(f"module_lint: {len(fails)} failure(s), {len(warns)} warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

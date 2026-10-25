"""Tiny stdlib-only reader for the flat YAML used in .ai/*.yml and PR-body blocks.

Supported (deliberately small, so no PyYAML dependency):
    key: value            # str / int / true / false
    key: [a, b, "c"]      # inline list
    key:                  # block list
      - a
      - b
Anything else raises ValueError so a malformed scope never silently passes.
"""

from __future__ import annotations

import re

_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_simple_yaml(text: str) -> dict:
    data: dict = {}
    current_list_key = None
    for lineno, line in enumerate(text.splitlines(), 1):
        line = _strip_comment(line)
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") or stripped == "-":
            if current_list_key is None or not line.startswith((" ", "\t", "-")):
                raise ValueError(f"line {lineno}: list item without a key")
            data[current_list_key].append(_scalar(stripped[1:]))
            continue
        if line[0] in " \t":
            raise ValueError(f"line {lineno}: nested mappings are not supported")
        m = _KEY.match(line)
        if not m:
            raise ValueError(f"line {lineno}: expected 'key: value'")
        key, value = m.group(1), m.group(2).strip()
        if value == "":
            data[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_scalar(v) for v in inner.split(",")] if inner else []
            current_list_key = None
        else:
            data[key] = _scalar(value)
            current_list_key = None
    return data


def extract_pr_block(body: str, name: str) -> str | None:
    """Return the text inside a ```<name> ... ``` fenced block in a PR body."""
    m = re.search(r"```" + re.escape(name) + r"[ \t]*\r?\n(.*?)```", body or "", re.S)
    return m.group(1) if m else None

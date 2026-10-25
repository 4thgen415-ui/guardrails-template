#!/usr/bin/env python3
"""Package registry check: every dependency a change adds must really exist.

Catches hallucinated or typo-squatted packages before install:
  - npm (package.json dependency fields) via https://registry.npmjs.org/<name>
  - PyPI (requirements*.txt) via https://pypi.org/pypi/<name>/json
FAIL when a package is not in the registry, when a pinned exact version does
not exist, or when the package is younger than package_min_age_days
(.ai/config.yml). Network or registry errors fail closed (exit 3).
Socket (AGENTS.md "Security tools") still scores the package; this check only
proves it exists and is not brand new.

Usage:
  package_check.py --base BASE_SHA        (diff BASE..HEAD)
  package_check.py npm:left-pad pypi:requests==2.32.3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from ai_config import parse_simple_yaml  # noqa: E402

DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(==\s*([\w.]+))?")
EXACT_NPM = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")


class RegistryError(Exception):
    pass


def git_show(rev: str, path: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def npm_deps(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for field in DEP_FIELDS:
        for name, spec in (data.get(field) or {}).items():
            out[name] = str(spec)
    return out


def pypi_deps(text: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = REQ_RE.match(line)
        if m:
            out[m.group(1).lower()] = m.group(3) or ""
    return out


def added_deps(base: str) -> list[tuple[str, str, str]]:
    files = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True).stdout.split()
    found: list[tuple[str, str, str]] = []
    for path in files:
        name = os.path.basename(path)
        if name == "package.json" and "node_modules/" not in path:
            old, new = npm_deps(git_show(base, path)), npm_deps(git_show("HEAD", path))
            eco = "npm"
        elif re.fullmatch(r"requirements[\w.-]*\.txt", name):
            old, new = pypi_deps(git_show(base, path)), pypi_deps(git_show("HEAD", path))
            eco = "pypi"
        else:
            continue
        for dep, spec in new.items():
            if dep not in old or old[dep] != spec:
                found.append((eco, dep, spec))
    return found


def fetch_json(url: str, opener=urllib.request.urlopen) -> dict | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "guardrails-package-check"})
    try:
        with opener(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RegistryError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{url}: {exc}") from exc


def check_one(eco: str, name: str, spec: str, min_age_days: int, allow: set[str], now: datetime, opener=urllib.request.urlopen) -> str | None:
    if eco == "npm":
        if spec.startswith(("file:", "link:", "workspace:", "git", "http", "github:")):
            return None
        meta = fetch_json("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@"), opener)
        if meta is None:
            return f"npm:{name} does not exist on registry.npmjs.org (hallucinated or typo?)"
        created = (meta.get("time") or {}).get("created")
        versions = meta.get("versions") or {}
        pin = spec.lstrip("=")
        if EXACT_NPM.match(pin) and pin not in versions:
            return f"npm:{name}@{pin} is not a published version"
    else:
        meta = fetch_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json", opener)
        if meta is None:
            return f"pypi:{name} does not exist on pypi.org (hallucinated or typo?)"
        releases = meta.get("releases") or {}
        if spec and spec not in releases:
            return f"pypi:{name}=={spec} is not a published version"
        uploads = [f.get("upload_time_iso_8601") for files in releases.values() for f in files if f.get("upload_time_iso_8601")]
        created = min(uploads) if uploads else None
    if name in allow or not created:
        return None
    age = (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).days
    if age < min_age_days:
        return f"{eco}:{name} was first published {age} day(s) ago (< {min_age_days}); too new to trust"
    return None


def main(argv: list[str] | None = None, opener=urllib.request.urlopen, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--config", default=".ai/config.yml")
    ap.add_argument("packages", nargs="*", help="eco:name[==version]")
    args = ap.parse_args(argv)
    cfg = {}
    if os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as fh:
            cfg = parse_simple_yaml(fh.read())
    min_age = int(cfg.get("package_min_age_days", 30))
    allow = set(cfg.get("package_age_allow") or [])
    deps: list[tuple[str, str, str]] = []
    if args.base:
        deps += added_deps(args.base)
    for item in args.packages:
        eco, _, rest = item.partition(":")
        name, _, ver = rest.partition("==")
        deps.append((eco, name, ver))
    if not deps:
        print("package-check: no added dependencies")
        return 0
    now = now or datetime.now(timezone.utc)
    failures = []
    try:
        for eco, name, spec in deps:
            problem = check_one(eco, name, spec, min_age, allow, now, opener)
            print(f"{'FAIL' if problem else 'ok  '} {eco}:{name} {spec}".rstrip() + (f" - {problem}" if problem else ""))
            if problem:
                failures.append(problem)
    except RegistryError as exc:
        print(f"package-check: registry error, failing closed: {exc}")
        return 3
    print(f"package-check: {len(deps) - len(failures)} ok, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

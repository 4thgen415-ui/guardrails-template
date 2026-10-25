import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import package_check  # noqa: E402

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
REGISTRY = {
    "https://registry.npmjs.org/hono": {"time": {"created": "2021-12-01T00:00:00Z"}, "versions": {"4.6.0": {}}},
    "https://registry.npmjs.org/@clerk%2Fbackend": {"time": {"created": "2022-01-01T00:00:00Z"}, "versions": {"1.0.0": {}}},
    "https://registry.npmjs.org/fresh-pkg": {"time": {"created": "2026-10-20T00:00:00Z"}, "versions": {"0.0.1": {}}},
    "https://pypi.org/pypi/requests/json": {"releases": {"2.32.3": [{"upload_time_iso_8601": "2011-02-14T00:00:00Z"}]}},
}


class Resp:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout=0):
    url = req.full_url
    if url == "https://registry.npmjs.org/boom":
        raise urllib.error.URLError("network down")
    if url not in REGISTRY:
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    return Resp(REGISTRY[url])


def run(*pkgs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = package_check.main(["--config", "/nonexistent", *pkgs], opener=opener, now=NOW)
    return code, buf.getvalue()


class PackageCheckTest(unittest.TestCase):
    def test_real_packages_pass(self):
        self.assertEqual(run("npm:hono==4.6.0", "npm:@clerk/backend", "pypi:requests==2.32.3")[0], 0)

    def test_hallucinated_package_fails(self):
        code, out = run("npm:cloudflare-d1-orm-helper")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)

    def test_missing_version_fails(self):
        self.assertEqual(run("npm:hono==9.9.9")[0], 1)

    def test_too_new_fails(self):
        code, out = run("npm:fresh-pkg")
        self.assertEqual(code, 1)
        self.assertIn("too new", out)

    def test_network_error_fails_closed(self):
        self.assertEqual(run("npm:boom")[0], 3)

    def test_parsers(self):
        self.assertEqual(package_check.npm_deps('{"dependencies": {"a": "^1"}, "devDependencies": {"b": "2.0.0"}}'), {"a": "^1", "b": "2.0.0"})
        self.assertEqual(package_check.pypi_deps("requests==2.32.3\n# c\nflask>=3\n-r other.txt\n"), {"requests": "2.32.3", "flask": ""})

    def test_nothing_added(self):
        self.assertEqual(run()[0], 0)


if __name__ == "__main__":
    unittest.main()

"""pyproject.toml guards (parsed with regexes: tomllib needs Python 3.11, CI runs 3.9)."""
import importlib
import os
import re
import unittest

import engram
from tests.daemon_helpers import ROOT

with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
    TEXT = f.read()


def section(name):
    m = re.search(r"^\[%s\]\n(.*?)(?=^\[|\Z)" % re.escape(name), TEXT, re.S | re.M)
    assert m, name
    return m.group(1)


class PyprojectTest(unittest.TestCase):
    def test_no_runtime_dependencies(self):
        proj = section("project")
        self.assertRegex(proj, r"(?m)^dependencies\s*=\s*\[\s*\]")
        self.assertNotIn("optional-dependencies", TEXT)
        self.assertNotIn("[project.optional", TEXT)

    def test_setuptools_is_build_requirement_only(self):
        self.assertRegex(section("build-system"), r'requires\s*=\s*\["setuptools>=61"\]')

    def test_version_is_dynamic_from_package(self):
        self.assertIn('dynamic = ["version"]', section("project"))
        self.assertIn('attr = "engram.__version__"', section("tool.setuptools.dynamic"))
        self.assertRegex(engram.__version__, r"^\d+\.\d+\.\d+")

    def test_metadata(self):
        proj = section("project")
        self.assertIn('name = "engram-memory"', proj)
        self.assertIn('requires-python = ">=3.9"', proj)
        self.assertIn("Apache-2.0", proj)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))

    def test_console_scripts_resolve_to_callables(self):
        scripts = dict(re.findall(r'(?m)^([\w-]+)\s*=\s*"([\w.]+:\w+)"', section("project.scripts")))
        self.assertEqual(scripts, {
            "mnemo": "engram.cli:main", "engram-daemon": "engram.daemon:main",
            "engram-hook": "engram.hooks.engram_hook:main",
            "engram-mcp": "engram.mcp_server:main"})
        for target in scripts.values():
            mod, func = target.split(":")
            self.assertTrue(callable(getattr(importlib.import_module(mod), func)), target)

    def test_discovery_limited_to_engram(self):
        self.assertIn('include = ["engram*"]', section("tool.setuptools.packages.find"))

    def test_build_artifacts_are_gitignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().split()
        for entry in ("dist/", "build/", "*.egg-info/", "*.pyc"):
            self.assertIn(entry, ignored)


if __name__ == "__main__":
    unittest.main()

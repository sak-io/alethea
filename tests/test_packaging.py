"""pyproject.toml guards (parsed with regexes: tomllib needs Python 3.11, CI runs 3.9)."""
import importlib
import os
import re
import unittest

import alethea
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
        # T25: the only optional extra is the opt-in local embedder; nothing at runtime.
        extras = re.findall(r"(?m)^\[project\.optional-dependencies\]\s*\n((?:[^\[\n].*\n?)*)", TEXT)
        self.assertEqual(len(extras), 1)
        self.assertEqual(re.findall(r"(?m)^(\w+)\s*=", extras[0]), ["local"])
        self.assertIn("fastembed", extras[0])

    def test_setuptools_is_build_requirement_only(self):
        self.assertRegex(section("build-system"), r'requires\s*=\s*\["setuptools>=61"\]')

    def test_version_is_dynamic_from_package(self):
        self.assertIn('dynamic = ["version"]', section("project"))
        self.assertIn('attr = "alethea.__version__"', section("tool.setuptools.dynamic"))
        self.assertRegex(alethea.__version__, r"^\d+\.\d+\.\d+")

    def test_metadata(self):
        proj = section("project")
        self.assertIn('name = "alethea"', proj)
        self.assertIn('requires-python = ">=3.9"', proj)
        self.assertIn("Apache-2.0", proj)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))

    def test_console_scripts_resolve_to_callables(self):
        scripts = dict(re.findall(r'(?m)^([\w-]+)\s*=\s*"([\w.]+:\w+)"', section("project.scripts")))
        self.assertEqual(scripts, {
            "mnemo": "alethea.cli:main", "alethea-daemon": "alethea.daemon:main",
            "alethea-hook": "alethea.hooks.alethea_hook:main",
            "alethea-mcp": "alethea.mcp_server:main"})
        for target in scripts.values():
            mod, func = target.split(":")
            self.assertTrue(callable(getattr(importlib.import_module(mod), func)), target)

    def test_discovery_limited_to_alethea(self):
        self.assertIn('include = ["alethea*"]', section("tool.setuptools.packages.find"))

    def test_build_artifacts_are_gitignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().split()
        for entry in ("dist/", "build/", "*.egg-info/", "*.pyc"):
            self.assertIn(entry, ignored)


if __name__ == "__main__":
    unittest.main()

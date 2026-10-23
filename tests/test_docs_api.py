"""Drift guard for docs/api.md: every daemon verb, MCP tool, `mnemo` subcommand and fixed daemon
error message in the code must be documented, and the review guide must list the new docs."""
import os
import re
import unittest

from engram import cli, daemon, mcp_server
from tests.daemon_helpers import ROOT

API = os.path.join(ROOT, "docs", "api.md")
DAEMON_SRC = os.path.join(ROOT, "engram", "daemon.py")
# Messages that exist in the source but are deliberately not part of the contract.
UNDOCUMENTED_OK = {"unsupported"}  # `UNSUPPORTED` is an empty tuple: no verb can be rejected this way


def _api():
    with open(API, encoding="utf-8") as f:
        return f.read()


def _section(text, start, end):
    a = text.index(start)
    b = text.index(end, a + len(start)) if end else len(text)
    return text[a:b]


class DocsApiDriftTest(unittest.TestCase):
    def test_every_daemon_verb_has_a_table_row(self):
        verbs = sorted(n[2:] for n in dir(daemon.Engram) if n.startswith("v_"))
        self.assertGreaterEqual(len(verbs), 14)
        table = _section(_api(), "### Verbs", "## 2.")
        for v in verbs:
            self.assertIn("| `%s` |" % v, table, "daemon verb %r missing from docs/api.md" % v)

    def test_verb_table_has_no_stale_verbs(self):
        table = _section(_api(), "### Verbs", "Activity bookkeeping")
        documented = set(re.findall(r"^\| `([a-z_]+)` \|", table, re.M))
        implemented = {n[2:] for n in dir(daemon.Engram) if n.startswith("v_")}
        self.assertEqual(documented, implemented)

    def test_every_mcp_tool_is_documented(self):
        tools = set(mcp_server.TOOLS) | {"engram_consolidate_pending", "engram_distill_submit"}
        section = _section(_api(), "## 2. MCP server", "## 3.")
        for t in tools:
            self.assertIn("| `%s` |" % t, section, "MCP tool %r missing from docs/api.md" % t)
        listed = set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M))
        self.assertEqual(listed, tools)

    def test_every_mnemo_subcommand_is_documented(self):
        section = _section(_api(), "### Commands", "## 4.")
        for name in cli.COMMANDS:
            self.assertTrue(re.search(r"^\| `%s[ `]" % re.escape(name), section, re.M),
                            "mnemo %s missing from docs/api.md" % name)

    def test_every_mnemo_exit_code_is_documented(self):
        section = _section(_api(), "### Exit codes", "### Commands")
        for code in (cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_USAGE, cli.EXIT_OFFLINE, cli.EXIT_NA):
            self.assertRegex(section, re.compile(r"^\| %d \|" % code, re.M))

    def test_every_fixed_daemon_error_is_documented(self):
        with open(DAEMON_SRC, encoding="utf-8") as f:
            src = f.read()
        found = set(re.findall(r'_err\("([^"%]+)"', src))
        found |= set(re.findall(r'BadRequest\("([^"%]+)"', src))
        found |= set(re.findall(r'return None, "([^"%]+)"', src))
        self.assertIn("unknown verb", found)
        api = _api()
        for msg in sorted(found - UNDOCUMENTED_OK):
            self.assertIn(msg, api, "daemon error %r missing from docs/api.md" % msg)

    def test_new_docs_are_in_the_review_guide(self):
        with open(os.path.join(ROOT, "docs", "review-guide.md"), encoding="utf-8") as f:
            guide = f.read()
        for doc in ("docs/architecture.md", "docs/api.md", "docs/threat-model.md",
                    "docs/traction-checklist.md"):
            self.assertIn(doc, guide)
            self.assertTrue(os.path.exists(os.path.join(ROOT, doc)))


if __name__ == "__main__":
    unittest.main()

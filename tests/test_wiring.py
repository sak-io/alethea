"""T23: Claude Code wiring files (.claude/settings.json, .mcp.json, integrations/claude-code).

Hook command lines are run exactly as configured, from another cwd, offline (ENGRAM_PORT=1 turns
autostart off; the home is a tracked temp dir so nothing touches the real ~/.engram)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from tests.daemon_helpers import ROOT, make_home

SETTINGS = os.path.join(ROOT, ".claude", "settings.json")
MCP = os.path.join(ROOT, ".mcp.json")
SNIPPET = os.path.join(ROOT, "integrations", "claude-code", "CLAUDE.snippet.md")
README = os.path.join(ROOT, "integrations", "claude-code", "README.md")
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreCompact")

# autocraft's pre-approved permission list as of the branch base (must stay untouched)
ORIGINAL_ALLOW = [
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git switch:*)", "Bash(git checkout:*)",
    "Bash(git merge:*)", "Bash(git worktree:*)", "Bash(git branch:*)", "Bash(git fetch:*)",
    "Bash(git push -u origin:*)", "Bash(python3 .autocraft/framework/bin/autocraft:*)",
    "Bash(python3 -m pytest:*)", "Bash(pytest:*)", "Bash(npm test:*)", "Bash(npm run:*)",
    "Bash(ls:*)"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def expand(s):
    return s.replace("${CLAUDE_PROJECT_DIR}", ROOT)


def hook_handlers(settings):
    for event in EVENTS:
        for group in settings["hooks"][event]:
            for h in group["hooks"]:
                yield event, group, h


def argv_of(handler):
    return [expand(handler["command"])] + [expand(a) for a in handler.get("args", [])]


def env_for(home):
    return {"PATH": os.environ.get("PATH", ""), "CLAUDE_PROJECT_DIR": ROOT, "ENGRAM_HOME": home,
            "ENGRAM_PORT": "1", "PYTHONDONTWRITEBYTECODE": "1"}


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.s = load(SETTINGS)

    def test_autocraft_permissions_preserved(self):
        self.assertEqual(self.s["permissions"]["allow"], ORIGINAL_ALLOW)
        self.assertEqual(set(self.s), {"permissions", "hooks"})
        self.assertEqual(list(self.s["permissions"]), ["allow"])

    def test_exactly_the_five_events_are_wired(self):
        self.assertEqual(set(self.s["hooks"]), set(EVENTS))

    def test_each_command_resolves_to_a_file_in_the_repo(self):
        for event, _, h in hook_handlers(self.s):
            self.assertEqual(h["type"], "command", event)
            argv = argv_of(h)
            files = [a for a in argv[1:] if a.endswith(".py")]
            self.assertEqual(len(files), 1, event)
            self.assertTrue(os.path.isfile(files[0]), files[0])
            self.assertTrue(os.path.realpath(files[0]).startswith(os.path.realpath(ROOT) + os.sep))
            self.assertIn("${CLAUDE_PROJECT_DIR}", " ".join(h.get("args", [])) + h["command"])

    def test_timeouts_are_seconds_within_hook_deadline(self):
        for event, _, h in hook_handlers(self.s):
            t = h["timeout"]
            self.assertIsInstance(t, int, event)
            self.assertGreaterEqual(t, 2, event)  # hook's own watchdog deadline is 2 s
            self.assertLessEqual(t, 2 if event == "SessionEnd" else 5, event)

    def test_session_start_matches_all_sources(self):
        for group in self.s["hooks"]["SessionStart"]:
            self.assertIn(group.get("matcher", "*"), ("", "*"))

    def test_hook_command_lines_run_offline_exit_0_with_empty_stdout(self):
        home = make_home()
        payloads = {
            "SessionStart": {"source": "startup"},
            "UserPromptSubmit": {"prompt": "we decided to use PostgreSQL"},
            "Stop": {"last_assistant_message": "ok", "stop_hook_active": False},
            "SessionEnd": {"reason": "other"},
            "PreCompact": {"trigger": "auto"},
        }
        other_cwd = tempfile.gettempdir()
        for event, _, h in hook_handlers(self.s):
            payload = dict(payloads[event], hook_event_name=event, session_id="s-wiring",
                           cwd=other_cwd, transcript_path="/nonexistent")
            p = subprocess.run(argv_of(h), input=json.dumps(payload).encode(), cwd=other_cwd,
                               env=env_for(home), capture_output=True, timeout=15)
            self.assertEqual(p.returncode, 0, (event, p.stderr))
            self.assertEqual(p.stdout, b"", event)


class McpConfigTest(unittest.TestCase):
    def test_engram_server_launches_and_lists_tools(self):
        cfg = load(MCP)["mcpServers"]["engram"]
        argv = [expand(cfg["command"])] + [expand(a) for a in cfg.get("args", [])]
        self.assertTrue(os.path.isfile(argv[-1]), argv)
        env = env_for(make_home())
        env.update({k: expand(v) for k, v in cfg.get("env", {}).items()})
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        data = "".join(json.dumps(m) + "\n" for m in msgs).encode()
        p = subprocess.run(argv, input=data, cwd=tempfile.gettempdir(), env=env,
                           capture_output=True, timeout=20)
        replies = {}
        for line in p.stdout.decode().splitlines():
            r = json.loads(line)
            replies[r.get("id")] = r
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "engram")
        names = {t["name"] for t in replies[2]["result"]["tools"]}
        self.assertTrue({"recall", "remember"} <= names, names)


class SnippetTest(unittest.TestCase):
    def setUp(self):
        with open(SNIPPET, encoding="utf-8") as f:
            self.lines = f.read().splitlines()

    def split_fenced(self):
        inside, outside, fenced = False, [], []
        for ln in self.lines:
            if ln.lstrip().startswith("```"):
                inside = not inside
                continue
            (fenced if inside else outside).append(ln)
        return outside, fenced

    def test_exactly_one_directive_sentence(self):
        outside, _ = self.split_fenced()
        directive = [ln for ln in outside if "`recall`" in ln and "`remember`" in ln]
        self.assertEqual(len(directive), 1)
        d = directive[0]
        self.assertEqual(len(re.findall(r"[.!?](\s|$)", d)), 1, d)
        for phrase in ("unfamiliar domain logic", "architecture decisions", "bug investigations",
                       "decision is finalized", "non-obvious cause"):
            self.assertIn(phrase, d)

    def test_import_line_only_inside_a_fence(self):
        outside, fenced = self.split_fenced()
        self.assertIn("@.claude/engram_learned.md", [ln.strip() for ln in fenced])
        for ln in outside:
            self.assertFalse(ln.lstrip().startswith("@"), ln)
            self.assertNotRegex(ln, r"(^|\s)@\.claude/engram_learned\.md")

    def test_states_export_rules_is_t24_and_may_not_exist(self):
        text = "\n".join(self.lines)
        self.assertIn("mnemo export-rules", text)
        self.assertIn("T24", text)
        self.assertIn("may not exist", text)


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        with open(README, encoding="utf-8") as f:
            self.text = f.read()

    def test_sections_present(self):
        for word in ("/ABSOLUTE/PATH/TO/engram", "mnemo status", "Disable", "Privacy",
                     "Cloud sessions", "ENGRAM_HOME", "~/.engram", "T24"):
            self.assertIn(word, self.text)

    def test_referenced_repo_paths_exist(self):
        for rel in re.findall(r"/ABSOLUTE/PATH/TO/engram/([\w./-]+)", self.text):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_does_not_claim_features_that_do_not_exist(self):
        for banned in ("pip install", "docker", "installer", "reembed", "Cursor", "LLM",
                       "encrypt", "mnemo install"):
            self.assertNotIn(banned.lower(), self.text.lower(), banned)

    def test_json_blocks_parse_and_match_the_documented_shape(self):
        blocks = re.findall(r"```json\n(.*?)```", self.text, re.S)
        self.assertEqual(len(blocks), 2)
        hooks = json.loads(blocks[0])["hooks"]
        self.assertEqual(set(hooks), set(EVENTS))
        self.assertEqual(json.loads(blocks[1])["mcpServers"]["engram"]["command"], "python3")


if __name__ == "__main__":
    unittest.main()

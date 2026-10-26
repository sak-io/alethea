"""Static guards for the Docker sidecar (T28, R29, G14). No docker needed: files are parsed.

Plus a real daemon process started with the environment the compose file sets (as a plain
process, not a container) to prove token auth over TCP and that unauthenticated calls fail.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest

from tests.daemon_helpers import ROOT, make_home


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


COMPOSE = read("docker-compose.yml")
DOCKERFILE = read("Dockerfile")
IGNORE = [ln.strip() for ln in read(".dockerignore").splitlines()
          if ln.strip() and not ln.startswith("#")]
MNEMO = os.path.join(ROOT, "scripts", "mnemo")


def code_lines(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def compose_env():
    """Parse the `environment:` mapping of the compose file (simple `KEY: value` lines)."""
    m = re.search(r"(?m)^    environment:\n((?:      .*\n)+)", COMPOSE)
    assert m, "environment block missing"
    env = {}
    for ln in m.group(1).splitlines():
        ln = re.sub(r"\s+#.*$", "", ln.strip())
        k, _, v = ln.partition(":")
        env[k.strip()] = v.strip().strip('"')
    return env


class ComposeTest(unittest.TestCase):
    def test_ports_publish_on_loopback_only(self):
        m = re.search(r"(?m)^    ports:\n((?:      - .*\n)+)", COMPOSE)
        self.assertTrue(m)
        entries = [re.sub(r"\s+#.*$", "", ln.strip()[2:]).strip().strip('"') for ln in m.group(1).splitlines()]
        self.assertTrue(entries)
        for e in entries:
            self.assertTrue(e.startswith("127.0.0.1:"), "non-loopback publish: %r" % e)
        self.assertEqual(len(re.findall(r"(?m)^\s+ports:", COMPOSE)), 1)

    def test_token_is_required_and_has_no_default(self):
        tok = compose_env()["ALETHEA_TOKEN"]
        self.assertRegex(tok, r"^\$\{ALETHEA_TOKEN:\?.+\}$")  # compose fails when unset/empty
        self.assertNotIn(":-", tok)  # no default value

    def test_no_literal_secret_in_compose(self):
        for ln in code_lines(COMPOSE):
            if "TOKEN" in ln.upper() and ":" in ln:
                self.assertIn("${", ln)

    def test_container_mode_environment(self):
        env = compose_env()
        self.assertEqual(env["ALETHEA_CONTAINER"], "1")
        self.assertEqual(env["ALETHEA_BIND"].split()[0], "0.0.0.0")
        self.assertEqual(env["ALETHEA_HOME"], "/data")
        self.assertTrue(env["ALETHEA_PORT"].isdigit())

    def test_restart_policy_volume_and_hardening(self):
        self.assertRegex(COMPOSE, r"(?m)^    restart: (unless-stopped|always|on-failure)")
        self.assertRegex(COMPOSE, r"(?m)^      - alethea-data:/data$")
        self.assertRegex(COMPOSE, r"(?m)^  alethea-data:")
        self.assertIn("no-new-privileges:true", COMPOSE)
        self.assertRegex(COMPOSE, r"cap_drop: \[ALL\]")
        self.assertNotRegex(COMPOSE, r"(?m)^\s+privileged:\s*true")
        self.assertNotRegex(COMPOSE, r"network_mode:\s*host")

    def test_container_port_matches_dockerfile(self):
        port = compose_env()["ALETHEA_PORT"]
        self.assertIn("ALETHEA_PORT=" + port, DOCKERFILE)
        self.assertIn(":%s\"" % port, COMPOSE)


class DockerfileTest(unittest.TestCase):
    def test_runs_as_non_root(self):
        users = re.findall(r"(?m)^USER\s+(\S+)", DOCKERFILE)
        self.assertTrue(users)
        self.assertNotIn(users[-1].split(":")[0], ("root", "0"))
        self.assertGreater(DOCKERFILE.rfind("\nUSER "), DOCKERFILE.rfind("\nRUN "))

    def test_no_secrets_baked_in(self):
        lines = code_lines(DOCKERFILE)
        for ln in lines:
            if re.match(r"(ENV|ARG)\b", ln):
                self.assertNotRegex(ln, r"ALETHEA_TOKEN", ln)
            self.assertFalse(re.match(r"ADD\b", ln), "use COPY, not ADD: %r" % ln)
            self.assertNotRegex(ln, r"(?i)^COPY.*(\.env|token|\.pem|\.key)", ln)
            self.assertNotRegex(ln, r"^COPY\s+(--\S+\s+)*\.\s")  # no whole-context copy
        self.assertNotRegex("\n".join(lines), r"(?i)(password|secret|api_key)\s*=")

    def test_stdlib_only_image(self):
        self.assertNotRegex("\n".join(code_lines(DOCKERFILE)),
                            r"pip3?\s+install|apt-get\s+install|requirements")

    def test_healthcheck_volume_and_command(self):
        self.assertRegex(DOCKERFILE, r"(?m)^HEALTHCHECK .*\\\n\s+CMD .*mnemo.*status")
        self.assertRegex(DOCKERFILE, r'(?m)^VOLUME \["/data"\]')
        self.assertIn("ALETHEA_CONTAINER=1", DOCKERFILE)
        self.assertRegex(DOCKERFILE, r'(?m)^CMD \["python3", "-m", "alethea\.daemon"\]')

    def test_copied_paths_exist_and_are_not_ignored(self):
        srcs = re.findall(r"(?m)^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+$", DOCKERFILE)
        self.assertTrue(srcs)
        for src in srcs:
            self.assertTrue(os.path.exists(os.path.join(ROOT, src)), src)
            self.assertNotIn(src.split("/")[0], IGNORE)


class DockerignoreTest(unittest.TestCase):
    def test_excludes_sensitive_and_bulky_paths(self):
        for needed in (".git", ".env", "tests", "*.db", ".alethea", "token", "*.pem", "*.key"):
            self.assertIn(needed, IGNORE)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "POSIX only")
class ContainerModeProcessTest(unittest.TestCase):
    """The daemon with the compose environment (bind adjusted to loopback) as a plain process."""

    TOKEN = "t28-token-" + "x" * 24

    def start(self, token):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ALETHEA_")}
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env.update(ALETHEA_CONTAINER=compose_env()["ALETHEA_CONTAINER"], ALETHEA_BIND="127.0.0.1",
                   ALETHEA_PORT=str(self.port), ALETHEA_HOME=make_home())
        if token:
            env["ALETHEA_TOKEN"] = token
        self.env = env
        return subprocess.Popen([sys.executable, "-m", "alethea.daemon"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def call(self, req):
        for _ in range(100):
            try:
                s = socket.create_connection(("127.0.0.1", self.port), 2)
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("daemon not reachable")
        with s:
            s.sendall((json.dumps(req) + "\n").encode())
            return json.loads(s.makefile().readline())

    def stop(self, p):
        p.terminate()
        try:
            p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()

    def mnemo_status(self, env):
        return subprocess.run([sys.executable, MNEMO, "status", "--strict"], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_refuses_to_start_without_token(self):
        p = self.start(None)
        _, err = p.communicate(timeout=15)
        self.assertEqual(p.returncode, 2)
        self.assertIn("token", err)

    def test_token_auth_over_tcp(self):
        p = self.start(self.TOKEN)
        self.addCleanup(self.stop, p)
        self.assertEqual(self.call({"verb": "ping", "token": self.TOKEN}), {"ok": True, "pong": True})
        for bad in ({"verb": "status"}, {"verb": "dump"}, {"verb": "status", "token": "z" * 24}):
            r = self.call(bad)
            self.assertFalse(r["ok"], bad)
            self.assertIn(r["error"], ("auth", "unauthorized"))
        cli = self.mnemo_status(self.env)  # exactly what the HEALTHCHECK runs
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertNotIn(self.TOKEN, cli.stdout + cli.stderr)
        self.assertNotEqual(self.mnemo_status(dict(self.env, ALETHEA_TOKEN="w" * 24)).returncode, 0)


if __name__ == "__main__":
    unittest.main()

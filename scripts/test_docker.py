"""Live Docker check (T28). Skips unless a docker daemon actually answers.

    python3 -m unittest scripts.test_docker -v

Builds the image, runs it with a random token published on 127.0.0.1 only, then pings and
runs status from the host with and without the token, and waits for the health check.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def docker_ok():
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


@unittest.skipUnless(docker_ok(), "no working docker daemon (live check UNVERIFIED)")
class DockerLiveTest(unittest.TestCase):
    def test_build_run_ping_and_auth(self):
        tag = "engram-sidecar-test:%d" % os.getpid()
        name = "engram-t28-%d" % os.getpid()
        token = secrets.token_urlsafe(32)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        subprocess.run(["docker", "build", "-t", tag, ROOT], check=True, timeout=600)
        self.addCleanup(subprocess.run, ["docker", "rmi", "-f", tag], capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", "-v", name], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--name", name, "-e", "ENGRAM_TOKEN=" + token,
                        "-p", "127.0.0.1:%d:47600" % port, tag], check=True, timeout=60)

        def call(req):
            with socket.create_connection(("127.0.0.1", port), 3) as c:
                c.sendall((json.dumps(req) + "\n").encode())
                return json.loads(c.makefile().readline())

        for _ in range(60):
            try:
                if call({"verb": "ping", "token": token}).get("pong"):
                    break
            except (OSError, ValueError):
                time.sleep(0.5)
        else:
            self.fail("container never answered")
        self.assertFalse(call({"verb": "status"})["ok"])
        health = ""
        for _ in range(40):
            health = subprocess.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", name],
                                    capture_output=True, text=True).stdout.strip()
            if health == "healthy":
                break
            time.sleep(1)
        self.assertEqual(health, "healthy")
        uid = subprocess.run(["docker", "exec", name, "id", "-u"], capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(uid, "0")
        bad = subprocess.run(["docker", "run", "--rm", tag], capture_output=True, text=True, timeout=60)
        self.assertEqual(bad.returncode, 2)  # no token: refuses to start


if __name__ == "__main__":
    unittest.main()

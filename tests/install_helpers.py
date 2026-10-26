"""Helpers: an 'installed' Alethea (a venv whose site-packages holds a copy of the package, no
source tree on any path) and a poisoned project directory containing a fake `alethea` package.
The venv has no pip and needs no network. It uses the interpreter running the tests, so the
suite exercises 3.9 when started with 3.9."""
import atexit
import glob
import os
import shutil
import subprocess
import sys
import tempfile

from tests.daemon_helpers import ROOT

MARKER = "PWNED"
_state = {}


def _cleanup():
    for d in _state.get("dirs", []):
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def _tmp():
    d = tempfile.mkdtemp(prefix="eh")
    _state.setdefault("dirs", []).append(d)
    return d


def installed_python():
    """Path of a venv python that has `alethea` installed in site-packages (cached)."""
    if "py" not in _state:
        venv = os.path.join(_tmp(), "venv")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True,
                       capture_output=True)
        site = glob.glob(os.path.join(venv, "lib", "python*", "site-packages"))[0]
        shutil.copytree(os.path.join(ROOT, "alethea"), os.path.join(site, "alethea"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        _state["py"] = os.path.join(venv, "bin", "python")
    return _state["py"]




def poisoned_cwd():
    """A project directory with a fake alethea package that drops a marker file when imported
    or run as any of the modules Alethea's own commands execute."""
    d = os.path.join(_tmp(), "project")
    os.makedirs(os.path.join(d, "alethea", "hooks"))
    fake = "open(%r, 'a').write('x')\n" % os.path.join(d, MARKER)  # absolute: any cwd
    for rel in ("__init__.py", "hooks/__init__.py", "hooks/alethea_hook.py", "daemon.py",
                "mcp_server.py", "cli.py", "export.py", "client.py"):
        with open(os.path.join(d, "alethea", rel), "w") as f:
            f.write(fake + "def main():\n    return 0\n")
    return d


def pwned(cwd):
    return os.path.exists(os.path.join(cwd, MARKER))

"""Fail-soft client (R23, G18, G19). Stdlib only; imports nothing from the engine.

Hard rule: no public call ever raises or blocks past its total deadline. Any failure
(down, hung, killed mid-reply, garbage, oversize, auth) -> {"ok": False, "offline": True}.

Wire: one JSON object per line: {"verb": ..., "token": ..., **params}; one JSON line back.
Discovery: Unix socket <home>/engram.sock, else TCP from ENGRAM_HOST/ENGRAM_PORT or
<home>/daemon.json ({"host","port"}). Token: ENGRAM_TOKEN env, else 0600 file <home>/token.
"""
import fcntl
import json
import os
import socket
import subprocess
import sys
import time

OFFLINE = {"ok": False, "offline": True}
TIMEOUT_HOOK = 2.0
TIMEOUT_MCP = 5.0
TIMEOUT_CLI = 10.0
MAX_RESPONSE_BYTES = 8 << 20
MAX_REQUEST_BYTES = 1 << 20
AUTOSTART_COOLDOWN_S = 10.0
_warned_token = False
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _offline():
    return dict(OFFLINE)


def _env(env):
    return os.environ if env is None else env


def _home(env):
    return os.path.expanduser(_env(env).get("ENGRAM_HOME") or "~/.engram")


def _container(env):
    return str(_env(env).get("ENGRAM_CONTAINER", "")).strip().lower() in ("1", "true", "yes", "on")


def _token(env, home):
    tok = _env(env).get("ENGRAM_TOKEN")
    if tok:
        return tok
    path = os.path.join(home, "token")
    try:
        if os.stat(path).st_mode & 0o077:  # group/world-readable: do not trust or send
            global _warned_token
            if not _warned_token:
                _warned_token = True
                sys.stderr.write("engram: ignoring token file with loose permissions "
                                 "(chmod 600 it)\n")
            return None
        with open(path, encoding="utf-8") as f:
            return f.read(512).strip() or None
    except OSError:
        return None


def _tcp_target(env, home):
    """Return (host, port) or None."""
    e = _env(env)
    host, port = e.get("ENGRAM_HOST"), e.get("ENGRAM_PORT")
    if not port:
        try:
            with open(os.path.join(home, "daemon.json"), encoding="utf-8") as f:
                d = json.load(f)
            host = host or d.get("host")
            port = d.get("port")
        except (OSError, ValueError, AttributeError):
            return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < port < 65536:
        return None
    return (host or "127.0.0.1", port)


def _token_allowed(host, env):
    if host in _LOOPBACK:
        return True
    return str(_env(env).get("ENGRAM_ALLOW_REMOTE_TOKEN", "")).strip().lower() in ("1", "true", "yes")


def _endpoints(env, home):
    """Ordered (kind, address) candidates."""
    out = []
    if not _container(env) and hasattr(socket, "AF_UNIX"):
        out.append(("unix", os.path.join(home, "engram.sock")))
    t = _tcp_target(env, home)
    if t:
        out.append(("tcp", t))
    return out


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise socket.timeout("deadline")
    return left


def _exchange(kind, addr, payload, deadline):
    fam = socket.AF_UNIX if kind == "unix" else socket.AF_INET
    if kind == "tcp" and ":" in addr[0]:
        fam = socket.AF_INET6
    s = socket.socket(fam, socket.SOCK_STREAM)
    try:
        s.settimeout(_remaining(deadline))
        s.connect(addr)
        s.settimeout(_remaining(deadline))
        s.sendall(payload)
        buf = bytearray()
        while True:
            s.settimeout(_remaining(deadline))
            chunk = s.recv(65536)
            if not chunk:
                raise OSError("closed before newline")
            buf += chunk
            nl = buf.find(b"\n")
            if nl >= 0:
                return bytes(buf[:nl])
            if len(buf) > MAX_RESPONSE_BYTES:
                raise OSError("oversize response")
    finally:
        try:
            s.close()
        except Exception:
            pass


def call(verb, params=None, timeout=TIMEOUT_HOOK, env=None):
    """Send one request; never raises; returns the daemon's dict or the offline dict."""
    try:
        deadline = time.monotonic() + max(0.05, float(timeout))
        home = _home(env)
        req = dict(params) if isinstance(params, dict) else {}
        req["verb"] = str(verb)
        tok = _token(env, home)
        for kind, addr in _endpoints(env, home):
            r = dict(req)
            if tok:
                if kind == "unix" or _token_allowed(addr[0], env):
                    r["token"] = tok
                else:
                    continue  # never send the token to a non-loopback host unless configured
            payload = json.dumps(r, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(payload) > MAX_REQUEST_BYTES:
                return _offline()
            try:
                line = _exchange(kind, addr, payload, deadline)
                resp = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if isinstance(resp, dict):
                if "ok" not in resp:  # valid JSON but not our protocol
                    continue
                return resp
        return _offline()
    except BaseException as e:  # only interpreter-exit signals may propagate
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return _offline()


def hook_call(verb, params=None, env=None):
    return call(verb, params, TIMEOUT_HOOK, env)


def mcp_call(verb, params=None, env=None):
    return call(verb, params, TIMEOUT_MCP, env)


def cli_call(verb, params=None, env=None):
    return call(verb, params, TIMEOUT_CLI, env)


def ping(timeout=TIMEOUT_HOOK, env=None):
    r = call("ping", None, timeout, env)
    return bool(r.get("ok"))


def autostart(env=None, wait=2.0, cmd=None):
    """Spawn the daemon detached if it is down; True if it answers afterwards.

    No-op (False) in TCP/container mode. Single-flight via flock on <home>/autostart.lock
    plus a cooldown stamp, so concurrent sessions cannot fork-bomb. Never raises or hangs.
    """
    lock = None
    try:
        if _container(env) or _env(env).get("ENGRAM_PORT"):
            return False
        deadline = time.monotonic() + max(0.1, float(wait))
        if ping(timeout=min(0.5, wait), env=env):
            return True
        home = _home(env)
        os.makedirs(home, mode=0o700, exist_ok=True)
        lock = open(os.path.join(home, "autostart.lock"), "a+")
        got = True
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            got = False
        if got:
            stamp = os.path.join(home, "autostart.stamp")
            try:
                recent = time.time() - os.path.getmtime(stamp) < AUTOSTART_COOLDOWN_S
            except OSError:
                recent = False
            if not recent and not ping(timeout=0.3, env=env):
                with open(stamp, "w") as f:
                    f.write(str(os.getpid()))
                e = dict(_env(env))
                pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                e["PYTHONPATH"] = pkg_parent + os.pathsep + e.get("PYTHONPATH", "")
                subprocess.Popen(cmd or [sys.executable, "-m", "engram.daemon"], env=e,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True,
                                 close_fds=True)
        # bounded retries: poll until the deadline (also covers losers of the lock)
        while time.monotonic() < deadline:
            time.sleep(0.1)
            if ping(timeout=min(0.3, max(0.05, deadline - time.monotonic())), env=env):
                return True
        return False
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return False
    finally:
        try:
            if lock:
                lock.close()
        except Exception:
            pass

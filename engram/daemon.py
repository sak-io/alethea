"""Engram daemon (R22): the only writer. NDJSON over a Unix socket.

`Engram` dispatches verbs to encode/recall/consolidate behind ONE FairLock (ADR 0005):
verbs hold it for their whole (short) duration; `sleep_cycle` takes it per batch only, so
a long sleep cannot starve other verbs. `Server` owns transport; every transport funnels
through `handle_request(ctx, request)` (T17 adds a TCP listener + token check there).
Wire format: see engram/client.py. Stdlib only.
"""
import contextlib
import errno
import fcntl
import json
import math
import os
import secrets
import signal
import socket
import sys
import threading
import time

from engram import __version__, consolidate, encode, gitctx, recall, vectorize
from engram.config import MAX_REQUEST_BYTES, ensure_private_dir, home_dir, load_config
from engram.cortex import Cortex
from engram.fairlock import FairLock

MAX_CONTENT_CHARS = 200_000
MAX_CUE_CHARS = 4096
MAX_FIELD_CHARS = 4096
MAX_K = 50
DUMP_DEFAULT, DUMP_MAX, DUMP_CONTENT_CHARS = 100, 500, 2000
READ_TIMEOUT_S = 5.0
SEND_TIMEOUT_S = 10.0
MAX_CONNECTIONS = 64
SLEEP_TICK_S = 1.0
SLEEP_BUDGET_S = 120.0
UNSUPPORTED = ("export_rules", "reembed", "pending")
_KINDS = ("episodic", "semantic", "procedural", "identity")
_PUBLIC = ("id kind content gist keywords salience strength access_count created_at "
           "last_access status session_id cwd repo_root embedder_id meta").split()


class BadRequest(ValueError):
    pass


def _err(msg, **kw):
    d = {"ok": False, "error": msg}
    d.update(kw)
    return d


def _str(p, key, required=False, maxlen=MAX_FIELD_CHARS):
    v = p.get(key)
    if v is None:
        if required:
            raise BadRequest("missing parameter: %s" % key)
        return None
    if not isinstance(v, str):
        raise BadRequest("%s must be a string" % key)
    if required and not v.strip():
        raise BadRequest("%s must not be empty" % key)
    if len(v) > maxlen:
        raise BadRequest("%s too long (max %d chars)" % (key, maxlen))
    return v


def _num(p, key, lo, hi, default=None):
    v = p.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise BadRequest("%s must be a number" % key)
    if not lo <= v <= hi:
        raise BadRequest("%s out of range [%s, %s]" % (key, lo, hi))
    return v


def _bool(p, key, default=False):
    v = p.get(key)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise BadRequest("%s must be a boolean" % key)
    return v


def _public(m):
    d = {k: m.get(k) for k in _PUBLIC}
    for k in ("score", "via"):
        if k in m:
            d[k] = m[k]
    return d


def _clean_json(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean_json(v) for v in o]
    if isinstance(o, (bytes, bytearray)):
        return None
    return o


def encode_reply(resp):
    try:
        return json.dumps(resp, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    except (ValueError, TypeError):
        try:
            return json.dumps(_clean_json(resp), separators=(",", ":"),
                              default=str).encode("utf-8") + b"\n"
        except Exception:
            return b'{"ok":false,"error":"internal"}\n'


class Engram:
    def __init__(self, home=None, env=None, clock=time.time, mono=time.monotonic,
                 distiller=None, cortex=None):
        self.env = os.environ if env is None else env
        self.home = ensure_private_dir(home or home_dir(self.env))
        cfg = load_config(self.env, self.home)
        self.sleep_interval = max(0, cfg["ENGRAM_SLEEP_INTERVAL"])
        self.idle_sleep = max(0, cfg["ENGRAM_IDLE_SLEEP"])
        self.clock, self.mono = clock, mono
        self.distiller = distiller
        self.lock = FairLock()
        self.cortex = cortex or Cortex(self.home / "engram.db", clock=clock)
        self.started = mono()
        self.last_activity = mono()
        self.last_sleep_mono = mono()
        self._activity_since_sleep = False
        self._sleeping = threading.Lock()
        self._bg = set()
        self.verbs = {
            "remember": self.v_remember, "recall": self.v_recall, "observe": self.v_observe,
            "briefing": self.v_briefing, "reflect": self.v_reflect, "forget": self.v_forget,
            "sleep": self.v_sleep, "session_end": self.v_session_end,
            "status": self.v_status, "dump": self.v_dump, "ping": self.v_ping}

    # dispatch

    def handle(self, verb, params):
        """Never raises. Returns a JSON-able dict."""
        try:
            if not isinstance(verb, str) or not verb:
                return _err("missing verb")
            if verb in UNSUPPORTED:
                return _err("unsupported")
            fn = self.verbs.get(verb)
            if fn is None:
                return _err("unknown verb")
            if verb not in ("ping", "status"):
                self.last_activity = self.mono()
                self._activity_since_sleep = True
            return fn(params)
        except BadRequest as e:
            return _err(str(e))
        except Exception as e:  # never leak a traceback
            sys.stderr.write("engram: verb %s failed: %s\n" % (verb, type(e).__name__))
            return _err("internal error")

    def _scope(self, p):
        """(repo_root, cwd): caller-supplied root wins; gitctx only as a fallback."""
        cwd = _str(p, "cwd")
        root = _str(p, "repo_root")
        if root is None and cwd:
            root = gitctx.repo_root(cwd)
        return root, cwd

    def _write_kw(self, p):
        root, cwd = self._scope(p)
        return {"session_id": _str(p, "session_id"), "cwd": cwd, "repo_root": root}

    # verbs

    def v_ping(self, p):
        return {"ok": True, "pong": True}

    def v_remember(self, p):
        content = _str(p, "content", True, MAX_CONTENT_CHARS)
        kind = _str(p, "kind")
        if kind is not None and kind not in _KINDS:
            raise BadRequest("unknown kind")
        sal = _num(p, "salience", 0.0, 1.0)
        meta = p.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise BadRequest("meta must be an object")
        kw = self._write_kw(p)
        with self.lock:
            r = encode.remember(self.cortex, content, kind=kind, salience=sal, meta=meta, **kw)
        return dict(r, ok=True)

    def v_reflect(self, p):
        content = _str(p, "content", True, MAX_CONTENT_CHARS)
        cited = p.get("cited_ids", [])
        if (not isinstance(cited, list) or len(cited) > 50
                or not all(isinstance(c, str) and len(c) <= 128 for c in cited)):
            raise BadRequest("cited_ids must be a list of at most 50 id strings")
        kw = self._write_kw(p)
        with self.lock:
            r = encode.reflect(self.cortex, content, cited_ids=cited, **kw)
        return dict(r, ok=True)

    def v_observe(self, p):
        content = _str(p, "content", True, MAX_CONTENT_CHARS)
        kw = self._write_kw(p)
        role = _str(p, "role", maxlen=64)
        with self.lock:
            if kw["session_id"]:
                self.cortex.session_start(kw["session_id"], kw["cwd"], kw["repo_root"])
            r = encode.observe(self.cortex, content, role=role, **kw)
        return {"ok": True, "journal_id": r["journal_id"], "promoted": r["promoted"]}

    def v_recall(self, p):
        cue = _str(p, "cue", maxlen=MAX_CUE_CHARS) or _str(p, "query", True, MAX_CUE_CHARS)
        k = int(_num(p, "k", 1, MAX_K, recall.K))
        with self.lock:
            res = recall.recall(self.cortex, cue, k=k, reinforce=_bool(p, "reinforce", True))
        return {"ok": True, "results": [_public(m) for m in res]}

    def v_briefing(self, p):
        root, cwd = self._scope(p)
        with self.lock:
            b = recall.briefing(self.cortex, repo_root=root, cwd=cwd)
        return {"ok": True, "text": b["text"], "count": len(b["items"])}

    def v_forget(self, p):
        mid = _str(p, "id", True, 128)
        with self.lock:
            done = encode.forget(self.cortex, mid)
        return {"ok": True, "forgotten": bool(done)} if done else _err("not found")

    def _live_sessions(self):
        with self.lock:
            return tuple(r[0] for r in self.cortex.db.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL"))

    def _run_sleep(self, **kw):
        """Single-flight sleep_cycle; the lock is only taken per batch inside it."""
        if not self._sleeping.acquire(blocking=False):
            return {"ok": True, "skipped": "already running"}
        try:
            kw.setdefault("budget_s", SLEEP_BUDGET_S)
            res = consolidate.sleep_cycle(self.cortex, distiller=self.distiller,
                                          lock=self.lock, **kw)
            self._activity_since_sleep = False
            self.last_sleep_mono = self.mono()
            return res
        finally:
            self._sleeping.release()

    def v_sleep(self, p):
        deep = _bool(p, "deep", True)
        only = _bool(p, "consolidate_only", False)
        budget = _num(p, "budget_s", 0.1, 3600.0, SLEEP_BUDGET_S)
        return self._run_sleep(deep=deep, consolidate_only=only, budget_s=budget,
                               keep_sessions=self._live_sessions())

    def v_session_end(self, p):
        sid = _str(p, "session_id", True, 256)
        with self.lock:
            self.cortex.session_end(sid)
        keep = tuple(s for s in self._live_sessions() if s != sid)
        if _bool(p, "wait", False):
            self._run_sleep(consolidate_only=True, keep_sessions=keep)
            return {"ok": True, "consolidated": True}
        t = threading.Thread(target=self._bg_run, daemon=True, name="session-end",
                             args=(dict(consolidate_only=True, keep_sessions=keep),))
        self._bg.add(t)
        t.start()
        return {"ok": True, "scheduled": True}

    def _bg_run(self, kw):
        try:
            self._run_sleep(**kw)
        except Exception as e:
            sys.stderr.write("engram: background sleep failed: %s\n" % type(e).__name__)
        finally:
            self._bg.discard(threading.current_thread())

    def v_status(self, p):
        with self.lock:
            db = self.cortex.db
            counts = {}
            for kind, status, n in db.execute(
                    "SELECT kind,status,COUNT(*) FROM memories GROUP BY kind,status"):
                counts.setdefault(kind, {})[status] = n
            pending = db.execute(
                "SELECT COUNT(*) FROM journal WHERE consolidated=0").fetchone()[0]
            last = self.cortex.meta_get("last_sleep")
            schema = self.cortex.schema_version
        try:
            last = float(last) if last is not None else None
        except ValueError:
            last = None
        return {"ok": True, "version": __version__, "schema_version": schema,
                "embedder_id": vectorize.EMBEDDER_ID, "fts": self.cortex.fts_available,
                "counts": counts, "total": sum(sum(v.values()) for v in counts.values()),
                "journal_pending": pending, "last_sleep": last, "pid": os.getpid(),
                "uptime_s": round(self.mono() - self.started, 1),
                "sleep_interval_s": self.sleep_interval, "idle_sleep_s": self.idle_sleep}

    def v_dump(self, p):
        limit = int(_num(p, "limit", 1, DUMP_MAX, DUMP_DEFAULT))
        offset = int(_num(p, "offset", 0, 10 ** 9, 0))
        kind, status = _str(p, "kind", maxlen=32), _str(p, "status", maxlen=32)
        where, args = [], []
        for col, v in (("kind", kind), ("status", status)):
            if v:
                where.append(col + "=?")
                args.append(v)
        sql = "SELECT id FROM memories%s ORDER BY created_at, id LIMIT ? OFFSET ?" % (
            " WHERE " + " AND ".join(where) if where else "")
        with self.lock:
            ids = [r[0] for r in self.cortex.db.execute(sql, args + [limit, offset])]
            mems = self.cortex.get_many(ids)
        out = []
        for i in ids:
            d = _public(mems[i]) if i in mems else None
            if d:
                d["content"] = (d["content"] or "")[:DUMP_CONTENT_CHARS]
                out.append(d)
        return {"ok": True, "memories": out, "limit": limit, "offset": offset}

    # background sleep loop

    def sleep_due(self):
        """Timer rule: >= interval since last sleep AND >= idle_sleep of inactivity AND
        something happened since (nothing to consolidate otherwise)."""
        now = self.mono()
        return (self._activity_since_sleep
                and now - self.last_sleep_mono >= self.sleep_interval
                and now - self.last_activity >= self.idle_sleep)

    def sleep_tick(self):
        """One loop iteration (tests call this with an injected clock). Never raises."""
        try:
            if self.sleep_due():
                self._run_sleep(keep_sessions=self._live_sessions())
                return True
        except Exception as e:
            sys.stderr.write("engram: sleep loop error: %s\n" % type(e).__name__)
        return False

    def _sleep_loop(self, stop, tick=SLEEP_TICK_S):
        while not stop.wait(tick):
            self.sleep_tick()

    def close(self):
        got = self.lock.acquire(timeout=5)
        try:
            with contextlib.suppress(Exception):
                self.cortex.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with contextlib.suppress(Exception):
                self.cortex.close()
        finally:
            if got:
                self.lock.release()


class ConnCtx:
    """Per-connection context handed to handle_request (T17: TCP + auth state)."""

    def __init__(self, transport, peer=None, authenticated=True):
        self.transport, self.peer, self.authenticated = transport, peer, authenticated


def handle_request(engram, ctx, request):
    """Single entry for every transport. `request` is the decoded JSON value."""
    if not isinstance(request, dict):
        return _err("request must be an object")
    params = dict(request)
    params.pop("token", None)  # never reaches verbs, logs or replies
    verb = params.pop("verb", None)
    return engram.handle(verb, params)


class Server:
    def __init__(self, engram, home=None, read_timeout=None, max_conns=MAX_CONNECTIONS,
                 max_request=MAX_REQUEST_BYTES):
        self.engram = engram
        self.home = ensure_private_dir(home) if home else engram.home
        self.sock_path = str(self.home / "engram.sock")
        self.read_timeout = READ_TIMEOUT_S if read_timeout is None else read_timeout
        self.max_request = max_request
        self.slots = threading.BoundedSemaphore(max_conns)
        self.stop = threading.Event()
        self.listener = None
        self._lockf = None
        self._threads = []

    # lifecycle

    def acquire_instance(self):
        """True if we are the single instance (lock + socket); False if another won."""
        lf = open(self.home / "daemon.lock", "a+")
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lf.close()
            return False
        self._lockf = lf
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1.0)
            probe.connect(self.sock_path)
            lf.close()
            self._lockf = None
            return False  # someone is answering on the socket
        except OSError:
            pass  # stale (or absent): we hold the lock, so safe to remove
        finally:
            probe.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.sock_path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            s.bind(self.sock_path)
        except OSError as e:
            s.close()
            self._release_lock()
            if e.errno == errno.EADDRINUSE:
                return False
            raise
        finally:
            os.umask(old)
        os.chmod(self.sock_path, 0o600)
        s.listen(32)
        s.settimeout(0.5)
        self.listener = s
        self._ensure_token()
        self._write_discovery()
        return True

    def _ensure_token(self):
        path = self.home / "token"
        if os.environ.get("ENGRAM_TOKEN") or path.exists():
            return
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))

    def _write_discovery(self):
        tmp = self.home / ("daemon.json.%d" % os.getpid())
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "socket": self.sock_path,
                       "version": __version__}, f)
        os.replace(str(tmp), str(self.home / "daemon.json"))

    def _release_lock(self):
        if self._lockf:
            with contextlib.suppress(Exception):
                self._lockf.close()
            self._lockf = None

    def start(self):
        """Start accept loop + sleep loop threads (after acquire_instance() is True)."""
        for target, name in ((self._accept_loop, "accept"),
                             (lambda: self.engram._sleep_loop(self.stop), "sleep-loop")):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            self._threads.append(t)

    def serve_forever(self):
        self.start()
        while not self.stop.wait(0.2):
            pass
        self.shutdown()

    def shutdown(self):
        self.stop.set()
        if self.listener:
            with contextlib.suppress(Exception):
                self.listener.close()
            self.listener = None
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=3)
        if self._lockf:  # only the owner cleans up files
            for name in ("engram.sock", "daemon.json"):
                with contextlib.suppress(OSError):
                    os.unlink(str(self.home / name))
            self.engram.close()
            self._release_lock()

    # connections

    def _accept_loop(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop.is_set() or self.listener is None:
                    return
                time.sleep(0.05)
                continue
            if not self.slots.acquire(blocking=False):
                self._reply_close(conn, _err("busy"))
                continue
            try:
                threading.Thread(target=self._serve_conn, args=(conn,), daemon=True,
                                 name="conn").start()
            except Exception:
                self.slots.release()
                with contextlib.suppress(Exception):
                    conn.close()

    @staticmethod
    def _reply_close(conn, resp):
        try:
            conn.settimeout(1.0)
            conn.sendall(encode_reply(resp))
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                conn.close()

    def _read_line(self, conn, buf):
        """Return (line_bytes|None, error|None). None/None = clean EOF."""
        deadline = time.monotonic() + self.read_timeout
        while True:
            nl = buf.find(b"\n")
            if nl >= 0:
                if nl > self.max_request:
                    return None, "request too large"
                line = bytes(buf[:nl])
                del buf[:nl + 1]
                return line, None
            if len(buf) > self.max_request:
                return None, "request too large"
            left = deadline - time.monotonic()
            if left <= 0:
                return None, "read timeout" if buf else None
            conn.settimeout(left)
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                return None, "read timeout" if buf else None
            if not chunk:
                return None, None
            buf += chunk

    def _serve_conn(self, conn):
        ctx = ConnCtx("unix")
        buf = bytearray()
        try:
            while not self.stop.is_set():
                line, error = self._read_line(conn, buf)
                if error:
                    self._send(conn, _err(error))
                    return
                if line is None:
                    return
                try:
                    request = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._send(conn, _err("invalid json"))
                    return
                if not self._send(conn, handle_request(self.engram, ctx, request)):
                    return
        except Exception as e:
            sys.stderr.write("engram: connection error: %s\n" % type(e).__name__)
        finally:
            with contextlib.suppress(Exception):
                conn.close()
            self.slots.release()

    @staticmethod
    def _send(conn, resp):
        try:
            conn.settimeout(SEND_TIMEOUT_S)
            conn.sendall(encode_reply(resp))
            return True
        except Exception:
            return False


def main(argv=None):
    server = Server(None, home=home_dir())
    try:
        if not server.acquire_instance():
            return 0  # another daemon won (G18)
        server.engram = Engram(home=server.home)
    except Exception as e:
        sys.stderr.write("engram: cannot start: %s\n" % e)
        return 1

    def _term(signum, frame):
        server.stop.set()

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Engram daemon (R22): the only writer. NDJSON over a Unix socket.

`Engram` dispatches verbs to encode/recall/consolidate behind ONE FairLock (ADR 0005):
verbs hold it for their whole (short) duration; `sleep_cycle` takes it per batch only, so
a long sleep cannot starve other verbs. `Server` owns transport; every transport funnels
through `handle_request(ctx, request)` (T17 adds a TCP listener + token check there).
Wire format: see engram/client.py. Stdlib only.
"""
import contextlib
import errno
import hashlib
import hmac
import ipaddress
import json
import math
import os
import secrets
import signal
import socket
import sys
import threading
import time

try:  # POSIX only
    import fcntl
except ImportError:
    fcntl = None

from engram import __version__, consolidate, encode, export, gitctx, recall, vectorize
from engram.config import (MAX_REQUEST_BYTES, ensure_private_dir, home_dir, llm_mode,
                           load_config)
from engram.cortex import Cortex
from engram.fairlock import FairLock

MAX_CONTENT_CHARS = 200_000
MAX_CUE_CHARS = 4096
MAX_FIELD_CHARS = 4096
MAX_K = 50
DUMP_DEFAULT, DUMP_MAX, DUMP_CONTENT_CHARS = 100, 500, 2000
READ_TIMEOUT_S = 5.0
SEND_TIMEOUT_S = 10.0
FIRST_BYTE_S = 1.5
SLOT_WAIT_S = 0.25
MAX_CONNECTIONS = 64
SLEEP_TICK_S = 1.0
SLEEP_BUDGET_S = 120.0
UNIX_PATH_MAX = 104
MIN_TOKEN_LEN = 16
AUTH_TABLE_MAX = 1024
UNSUPPORTED = ("reembed",)
_KINDS = ("episodic", "semantic", "procedural", "identity")
_PUBLIC = ("id kind content gist keywords salience strength access_count created_at "
           "last_access status session_id cwd repo_root embedder_id meta").split()


class BadRequest(ValueError):
    pass


class StartupError(Exception):
    """Configuration the daemon refuses to start with (message never contains a token)."""


def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


class AuthLimiter:
    """Failed-auth throttling that can never lock out a correct token.

    A correct token is always served. Failures only (1) get a growing, capped delay on the
    failing reply, by how many recent failures that peer IP had, and (2) draw from a global
    token bucket; over the bucket the connection is dropped without a reply or any work."""

    def __init__(self, clock=time.monotonic, delay_unit=0.05, delay_cap=2.0,
                 rate=20.0, burst=20.0, forget_s=30.0):
        self.clock, self.unit, self.cap = clock, delay_unit, delay_cap
        self.rate, self.burst, self.forget_s = rate, burst, forget_s
        self._lock = threading.Lock()
        self._state = {}  # peer -> [recent_fails, last_fail_time]
        self._tokens, self._t = burst, None

    def delay_for(self, n):
        return min(self.cap, self.unit * 2.0 ** (max(n, 1) - 1)) if n < 64 else self.cap

    def fail(self, peer):
        """Record a failure. -> delay in seconds for the failing reply, or None = drop."""
        now = self.clock()
        with self._lock:
            if self._t is not None:
                self._tokens = min(self.burst, self._tokens + (now - self._t) * self.rate)
            self._t = now
            if self._tokens < 1.0:
                return None
            self._tokens -= 1.0
            st = self._state.get(peer)
            if st is None:
                if len(self._state) >= AUTH_TABLE_MAX:
                    self._state.pop(next(iter(self._state)))
                st = self._state[peer] = [0, now]
            elif now - st[1] > self.forget_s:
                st[0] = 0
            st[0] += 1
            st[1] = now
            return self.delay_for(st[0])

    def ok(self, peer):
        with self._lock:
            self._state.pop(peer, None)



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
        warn_reserved(cfg)
        vectorize.configure(cfg["ENGRAM_EMBEDDER"], quiet=True, background=True,
                            load_timeout=cfg["ENGRAM_EMBEDDER_LOAD_TIMEOUT"],
                            embed_timeout=cfg["ENGRAM_EMBEDDER_TIMEOUT"])  # lazy: nothing optional is imported yet
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
        self.llm, self.llm_error = None, None
        self._init_llm(cfg)
        self.verbs = {
            "remember": self.v_remember, "recall": self.v_recall, "observe": self.v_observe,
            "briefing": self.v_briefing, "reflect": self.v_reflect, "forget": self.v_forget,
            "sleep": self.v_sleep, "session_end": self.v_session_end,
            "status": self.v_status, "dump": self.v_dump, "ping": self.v_ping,
            "export_rules": self.v_export_rules, "pending": self.v_pending,
            "distill_submit": self.v_distill_submit}

    def _init_llm(self, cfg):
        """Optional LLM distillation (T27, ADR 0012). With the flag off nothing is imported,
        started or queued. An invalid direct-API configuration leaves the feature OFF (the
        extractive path continues) with a visible reason; it never opens a connection."""
        mode = llm_mode(cfg)
        if mode == "off":
            return
        from engram import distill  # lazy: not imported while the flag is off
        adapter = None
        if mode == "api":
            from engram import llm_api
            try:
                adapter = llm_api.build_adapter(cfg, self.env, self.mono)
            except llm_api.ConfigError as e:
                self.llm_error = "api mode disabled: %s" % e
                sys.stderr.write("engram: %s\n" % self.llm_error)
                return
        self.llm = distill.Service(self.cortex, self.lock, cfg, mode, adapter, self.mono)

    def _llm_host(self):
        if self.llm is None:
            raise BadRequest("LLM distillation is off (set ENGRAM_LLM_CONSOLIDATE=1)")
        if self.llm.mode != "host":
            raise BadRequest("distillation runs inside the daemon (api mode)")
        return self.llm

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
            if verb not in ("ping", "status", "export_rules"):
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
        emb = encode.embed_for(content)  # before the lock: an embedder is never called under it
        with self.lock:
            r = encode.remember(self.cortex, content, kind=kind, salience=sal, meta=meta,
                                embedded=emb, **kw)
        return dict(r, ok=True)

    def v_reflect(self, p):
        content = _str(p, "content", True, MAX_CONTENT_CHARS)
        cited = p.get("cited_ids", [])
        if (not isinstance(cited, list) or len(cited) > 50
                or not all(isinstance(c, str) and len(c) <= 128 for c in cited)):
            raise BadRequest("cited_ids must be a list of at most 50 id strings")
        kw = self._write_kw(p)
        emb = encode.embed_for(content)
        with self.lock:
            r = encode.reflect(self.cortex, content, cited_ids=cited, embedded=emb, **kw)
        return dict(r, ok=True)

    def v_observe(self, p):
        content = _str(p, "content", True, MAX_CONTENT_CHARS)
        kw = self._write_kw(p)
        role = _str(p, "role", maxlen=64)
        emb = encode.embed_for(content) if encode.would_promote(content) else None
        with self.lock:
            if kw["session_id"]:
                self.cortex.session_start(kw["session_id"], kw["cwd"], kw["repo_root"])
            r = encode.observe(self.cortex, content, role=role, embedded=emb, **kw)
        return {"ok": True, "journal_id": r["journal_id"], "promoted": r["promoted"]}

    def v_recall(self, p):
        cue = _str(p, "cue", maxlen=MAX_CUE_CHARS) or _str(p, "query", True, MAX_CUE_CHARS)
        k = int(_num(p, "k", 1, MAX_K, recall.K))
        scope = _str(p, "scope", maxlen=16) or "all"
        if scope not in recall.SCOPES:
            raise BadRequest("scope must be one of: all, repo")
        root, cwd = self._scope(p)
        emb = recall.embed_cue(cue)  # before the lock
        with self.lock:
            res = recall.recall(self.cortex, cue, k=k, reinforce=_bool(p, "reinforce", True),
                                repo_root=root, cwd=cwd, scope=scope, embedded=emb)
        return {"ok": True, "results": [_public(m) for m in res]}

    def v_briefing(self, p):
        root, cwd = self._scope(p)
        with self.lock:
            b = recall.briefing(self.cortex, repo_root=root, cwd=cwd)
        return {"ok": True, "text": b["text"], "count": len(b["items"])}

    def v_export_rules(self, p):
        """Render-only (R25): returns plain items for repo_root. It never accepts or uses a
        path/target/directory and never writes a file; the client writes into its own repo."""
        for k in export.FORBIDDEN_PARAMS:
            if k in p:
                raise BadRequest("parameter not accepted: %s" % k)
        root = _str(p, "repo_root", True)
        if not export.valid_repo_root(root):
            raise BadRequest("repo_root must be an absolute path without '..'")
        with self.lock:
            items = export.select_items(self.cortex, root)
        return {"ok": True, "items": items, "count": len(items)}

    def v_pending(self, p):
        """Host-callback mode: queued episodes as inert, redacted, size-capped plain text."""
        svc = self._llm_host()
        return svc.pending(int(_num(p, "n", 1, 5, 3)))

    def v_distill_submit(self, p):
        """The ONLY way distilled bullets are accepted (ADR 0012): validated, then every bullet
        goes through the full encode() gate; never deliberate, never identity."""
        svc = self._llm_host()
        eid = _str(p, "episode_id", True, 32)
        bullets = p.get("bullets")
        if not isinstance(bullets, list):
            raise BadRequest("bullets must be a list of strings")
        from engram import distill
        try:
            return svc.submit(eid, bullets)
        except distill.Invalid as e:
            raise BadRequest(str(e))

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
            api = kw.pop("api_ok", False)
            llm_kw = {}
            if self.llm is not None:
                llm_kw["llm"] = self.llm
                if api and self.llm.adapter is not None:
                    llm_kw["llm_api"] = self.llm.api_cycle
            res = consolidate.sleep_cycle(self.cortex, distiller=self.distiller,
                                          lock=self.lock, **llm_kw, **kw)
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
            dedup = encode.dedup_cap_state(self.cortex)
            distiller = self.llm.status() if self.llm is not None else {"mode": "off"}
        if self.llm_error:
            distiller["error"] = self.llm_error
        try:
            last = float(last) if last is not None else None
        except ValueError:
            last = None
        return {"ok": True, "version": __version__, "schema_version": schema,
                **vectorize.status(), "fts": self.cortex.fts_available,
                "counts": counts, "total": sum(sum(v.values()) for v in counts.values()),
                "journal_pending": pending, "dedup": dedup, "distiller": distiller, "last_sleep": last, "pid": os.getpid(),
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
                self._run_sleep(keep_sessions=self._live_sessions(), api_ok=True)
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
    """Per-connection context handed to handle_request. Unix connections are trusted
    (0600 socket in a 0700 dir). TCP connections carry `token` (bytes or None = no auth
    configured) and a limiter; every TCP request is checked, none is trusted by history."""

    def __init__(self, transport, peer=None, authenticated=True, token=None, limiter=None):
        self.transport, self.peer, self.authenticated = transport, peer, authenticated
        self.token, self.limiter = token, limiter
        self.close = False  # set when the connection must be dropped after the reply
        self.delay, self.drop = 0.0, False


def _token_ok(expected, given):
    if not isinstance(given, str):
        return False
    a = hashlib.sha256(expected).digest()  # equal length: compare_digest leaks nothing
    b = hashlib.sha256(given.encode("utf-8", "surrogatepass")).digest()
    return hmac.compare_digest(a, b)


def _authorize(ctx, request):
    """None if allowed, else the error reply. Never echoes or logs the token. The
    constant-time compare ALWAYS runs: a correct token is served whatever happened before."""
    if ctx.transport != "tcp" or ctx.token is None:
        return None
    if _token_ok(ctx.token, request.get("token") if isinstance(request, dict) else None):
        ctx.authenticated = True
        if ctx.limiter is not None:
            ctx.limiter.ok(ctx.peer)
        return None
    ctx.authenticated = False
    ctx.close = True
    if ctx.limiter is not None:
        ctx.delay = ctx.limiter.fail(ctx.peer)
        if ctx.delay is None:
            ctx.drop = True  # over the global failure budget: no reply, no more work
    if (isinstance(request, dict) and request.get("verb") == "ping"
            and "token" not in request):
        return _err("unauthorized")
    return _err("auth")


def handle_request(engram, ctx, request):
    """Single entry for every transport. `request` is the decoded JSON value."""
    denied = _authorize(ctx, request)
    if denied is not None:
        return denied
    if not isinstance(request, dict):
        return _err("request must be an object")
    params = dict(request)
    params.pop("token", None)  # never reaches verbs, logs or replies
    verb = params.pop("verb", None)
    return engram.handle(verb, params)


class Server:
    def __init__(self, engram, home=None, read_timeout=None, max_conns=MAX_CONNECTIONS,
                 max_request=MAX_REQUEST_BYTES, env=None, auth_clock=time.monotonic,
                 auth_limits=None):
        self.engram = engram
        self.env = (engram.env if engram is not None else os.environ) if env is None else env
        self.home = ensure_private_dir(home) if home else engram.home
        self.limiter = AuthLimiter(auth_clock, **(auth_limits or {}))
        self.tcp_listener = None
        self.tcp_addr = None
        self.token = None
        self.fallback = False
        self.sock_path = str(self.home / "engram.sock")
        self.read_timeout = READ_TIMEOUT_S if read_timeout is None else read_timeout
        self.max_request = max_request
        # `pending` bounds sockets (cheap; idle ones are dropped after FIRST_BYTE_S);
        # `slots` bounds requests being executed, so idle sockets can never starve work.
        self.pending = threading.BoundedSemaphore(max(max_conns * 8, 512))
        self.slots = threading.BoundedSemaphore(max_conns)
        self.first_byte_timeout = min(FIRST_BYTE_S, self.read_timeout)
        self.stop = threading.Event()
        self.listener = None
        self._lockf = None
        self._threads = []

    # lifecycle

    def _read_token(self):
        tok = self.env.get("ENGRAM_TOKEN")
        if tok:
            return tok
        try:
            with open(self.home / "token", encoding="utf-8") as f:
                return f.read(512).strip() or None
        except OSError:
            return None

    def _plan(self):
        """Validate TCP/container policy before touching any file."""
        e = self.env
        container = _truthy(e.get("ENGRAM_CONTAINER"))
        bind, port = e.get("ENGRAM_BIND") or None, e.get("ENGRAM_PORT") or None
        if port is not None:
            try:
                port = int(port)
                if not 0 <= port < 65536:
                    raise ValueError
            except ValueError:
                raise StartupError("ENGRAM_PORT must be an integer in 0..65535")
        wants_tcp = container or bind is not None or port is not None
        if container and (bind is None or port is None):
            raise StartupError("container mode needs explicit ENGRAM_BIND and ENGRAM_PORT")
        host = bind or "127.0.0.1"
        if wants_tcp:
            tok = self._read_token()
            if tok is not None and len(tok.encode("utf-8")) < MIN_TOKEN_LEN:
                raise StartupError("ENGRAM_TOKEN must be at least %d bytes" % MIN_TOKEN_LEN)
            if (container or not _is_loopback(host)) and tok is None:
                raise StartupError("a token (ENGRAM_TOKEN, >= %d bytes) is required in "
                                   "container mode or on a non-loopback bind" % MIN_TOKEN_LEN)
        return container, host, (port or 0), wants_tcp

    def acquire_instance(self):
        """True if we are the single instance (lock + listeners); False if another won.
        Raises StartupError when the bind/token policy refuses the configuration."""
        container, host, port, wants_tcp = self._plan()
        lf = open(self.home / "daemon.lock", "a+")
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lf.close()
            return False
        self._lockf = lf
        with contextlib.suppress(OSError):
            os.chmod(self.home / "daemon.lock", 0o600)
        try:
            if not container:
                r = self._bind_unix()
                if r is False:
                    return False
                if r is None:  # AF_UNIX unusable: loopback TCP fallback
                    self.fallback = wants_tcp = True
                    host = host if _is_loopback(host) else "127.0.0.1"
            if wants_tcp:
                self._bind_tcp(host, port)
            if self.listener:
                self._ensure_token()
            self._write_discovery()
            return True
        except BaseException:
            self._close_listeners()
            self._release_lock()
            raise

    def _bind_unix(self):
        """True = bound, False = another daemon owns it, None = fall back to TCP."""
        if len(self.sock_path.encode()) > UNIX_PATH_MAX:
            return None
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1.0)
            probe.connect(self.sock_path)
            self._release_lock()
            return False  # someone is answering on the socket
        except OSError:
            pass  # stale (or absent): we hold the lock, so safe to remove
        finally:
            probe.close()
        with contextlib.suppress(OSError):
            os.unlink(self.sock_path)
        s = None
        old = os.umask(0o177)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.sock_path)
            os.chmod(self.sock_path, 0o600)
            s.listen(512)
        except OSError as e:
            if s:
                s.close()
            if e.errno == errno.EADDRINUSE:
                self._release_lock()
                return False
            return None
        finally:
            os.umask(old)
        s.settimeout(0.5)
        self.listener = s
        return True

    def _bind_tcp(self, host, port):
        token = self._read_token()
        if token is None:
            if not _is_loopback(host):
                raise StartupError("a token is required on a non-loopback bind")
            self._write_new_token()
            token = self._read_token()
        if token is None or len(token.encode("utf-8")) < MIN_TOKEN_LEN:
            raise StartupError("a token of at least %d bytes is required" % MIN_TOKEN_LEN)
        self.token = token.encode("utf-8")
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        s = socket.socket(fam, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            s.listen(512)
        except OSError as e:
            s.close()
            raise StartupError("cannot bind %s:%s (%s)" % (host, port, e.strerror or e))
        s.settimeout(0.5)
        self.tcp_listener = s
        self.tcp_addr = (host, s.getsockname()[1])

    def _close_listeners(self):
        for name in ("listener", "tcp_listener"):
            s = getattr(self, name)
            setattr(self, name, None)
            if s:
                with contextlib.suppress(Exception):
                    s.shutdown(socket.SHUT_RDWR)  # wakes a blocked accept() at once (Linux)
                with contextlib.suppress(Exception):
                    s.close()

    def _ensure_token(self):
        if self.env.get("ENGRAM_TOKEN") or (self.home / "token").exists():
            return
        self._write_new_token()

    def _write_new_token(self):
        path = self.home / "token"
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))

    def _write_discovery(self):
        tmp = self.home / ("daemon.json.%d" % os.getpid())
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            d = {"pid": os.getpid(), "socket": self.sock_path if self.listener else None,
                 "version": __version__}
            if self.tcp_addr:
                h = self.tcp_addr[0]
                d["host"] = "::1" if h == "::" else "127.0.0.1" if h in ("0.0.0.0", "") else h
                d["port"] = self.tcp_addr[1]
            json.dump(d, f)
        os.replace(str(tmp), str(self.home / "daemon.json"))

    def _release_lock(self):
        if self._lockf:
            with contextlib.suppress(Exception):
                self._lockf.close()
            self._lockf = None

    def start(self):
        """Start accept loop + sleep loop threads (after acquire_instance() is True)."""
        loops = [(lambda: self.engram._sleep_loop(self.stop), "sleep-loop")]
        if self.listener:
            loops.insert(0, (lambda: self._accept_loop(self.listener, "unix"), "accept"))
        if self.tcp_listener:
            loops.insert(0, (lambda: self._accept_loop(self.tcp_listener, "tcp"), "accept-tcp"))
        for target, name in loops:
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
        self._close_listeners()
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

    def _accept_loop(self, listener, transport):
        while not self.stop.is_set():
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop.is_set() or listener not in (self.listener, self.tcp_listener):
                    return
                time.sleep(0.05)
                continue
            if not self.pending.acquire(blocking=False):
                self._reply_close(conn, _err("busy"))
                continue
            try:
                threading.Thread(target=self._serve_conn, args=(conn, transport, addr), daemon=True,
                                 name="conn").start()
            except Exception:
                self.pending.release()
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
        started = bool(buf)
        deadline = time.monotonic() + (self.read_timeout if started else self.first_byte_timeout)
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
            if not started:  # first byte seen: the whole line gets the full deadline
                started = True
                deadline = time.monotonic() + self.read_timeout

    def _serve_conn(self, conn, transport="unix", addr=None):
        if transport == "tcp":
            peer = addr[0] if addr else None
            ctx = ConnCtx("tcp", peer, False, self.token, self.limiter)
        else:
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
                except (ValueError, UnicodeDecodeError, RecursionError):
                    self._send(conn, _err("invalid json"))
                    return
                if not self.slots.acquire(timeout=SLOT_WAIT_S):
                    self._send(conn, _err("busy"))
                    return
                try:
                    resp = handle_request(self.engram, ctx, request)
                finally:
                    self.slots.release()
                if ctx.drop:
                    return
                if ctx.delay:
                    self.stop.wait(ctx.delay)  # slows only this (failing) connection
                if not self._send(conn, resp) or ctx.close:
                    return
        except Exception as e:
            sys.stderr.write("engram: connection error: %s\n" % type(e).__name__)
        finally:
            with contextlib.suppress(Exception):
                conn.close()
            self.pending.release()

    @staticmethod
    def _send(conn, resp):
        try:
            conn.settimeout(SEND_TIMEOUT_S)
            conn.sendall(encode_reply(resp))
            return True
        except Exception:
            return False


SUPPORTED = fcntl is not None and hasattr(socket, "AF_UNIX")
UNSUPPORTED_MSG = "unsupported platform: POSIX only (needs fcntl and AF_UNIX)"
_reserved_warned = False


def warn_reserved(cfg):
    """One stderr line (once per process) if an unknown embedder or a reserved option is set."""
    global _reserved_warned
    if _reserved_warned:
        return
    bad = []
    try:
        vectorize.parse_spec(cfg.get("ENGRAM_EMBEDDER", "hash"))
    except vectorize.UnknownEmbedder:
        bad.append("ENGRAM_EMBEDDER=%s (unknown embedder; valid: hash, fastembed:<model>)"
                   % cfg["ENGRAM_EMBEDDER"])
    if bad:
        _reserved_warned = True
        sys.stderr.write("engram: reserved option(s) not implemented yet, ignored: %s\n"
                         % "; ".join(bad))


def main(argv=None):
    if not SUPPORTED:
        sys.stderr.write("engram: %s\n" % UNSUPPORTED_MSG)
        return 2
    server = Server(None, home=home_dir())
    try:
        if not server.acquire_instance():
            return 0  # another daemon won (G18)
        server.engram = Engram(home=server.home)
    except StartupError as e:
        sys.stderr.write("engram: refusing to start: %s\n" % e)
        return 2
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

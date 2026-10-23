"""Embedding, stemming, cosine/jaccard, embedder registry. Stdlib only (T§4).

Optional embedders (T25) live in engram.embedders_ext and are imported only when selected by
ENGRAM_EMBEDDER; every failure falls back to the hashed embedder (see docs/adr/0010).
"""
import hashlib
import importlib
import math
import operator
import re
import sys
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from array import array

SALT = b"engram-v1"
DIM = 512
EMBEDDER_ID = "hashed-ngram-v1:512"
W_UNI, W_BI, W_GRAM = 1.0, 0.5, 0.12

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text):
    """Lowercase alphanumeric tokens. No length floor (identifiers like 'v2', 'x')."""
    return _TOKEN_RE.findall((text or "").lower())


_SUFFIXES = sorted(
    ["ization", "ational", "ement", "ments", "ment", "ness",
     "ingly", "edly", "ing", "ed", "ful", "ous", "ive", "able", "ible", "ize",
     "ise", "ion", "ly", "er", "al"],
    key=len, reverse=True)


@lru_cache(maxsize=65536)
def stem(word):
    """Plural first, then longest suffix. Stems keep >= 3 chars; short words untouched."""
    w = word.lower()
    if len(w) <= 3 or not w.isalpha():
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "i"
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    if w.endswith("y") and len(w) > 3:
        w = w[:-1] + "i"
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    if w.endswith("at") and len(w) > 5:
        w = w[:-2]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
        w = w[:-1]
    return w


def stem_tokens(text):
    return [stem(t) for t in tokenize(text)]


@lru_cache(maxsize=262144)
def _bucket(feature):
    d = hashlib.blake2b(feature.encode("utf-8"), digest_size=8, salt=SALT).digest()
    n = int.from_bytes(d, "big")
    return n % DIM, (1.0 if (n >> 40) & 1 else -1.0)


def _features(text):
    toks = stem_tokens(text)
    for t in toks:
        yield "u:" + t, W_UNI
    for a, b in zip(toks, toks[1:]):
        yield "b:" + a + " " + b, W_BI
    squashed = " ".join(toks)
    for i in range(len(squashed) - 3):
        yield "g:" + squashed[i:i + 4], W_GRAM


def embed_floats(text):
    vec = [0.0] * DIM
    for feat, w in _features(text):
        idx, sign = _bucket(feat)
        vec[idx] += sign * w
    norm = math.sqrt(sum(x * x for x in vec))
    if norm:
        vec = [x / norm for x in vec]
    return vec


def to_bytes(vec):
    return array("f", vec).tobytes()


def from_bytes(blob):
    a = array("f")
    a.frombytes(bytes(blob))
    return list(a)


if hasattr(math, "sumprod"):  # C-speed dot product (Python 3.12+)
    _dot = math.sumprod
else:
    def _dot(a, b):
        return sum(map(operator.mul, a, b))


DECODE_CACHE_ENTRIES = 1024
DECODE_CACHE_FLOATS = 1 << 20   # ~32 MB of Python floats at most, whatever the dimension
_dec = OrderedDict()
_dec_floats = 0
_dec_lock = threading.Lock()


def _decode(blob):
    """(floats, norm) for an immutable bytes blob; cached because dedup rescans the same
    recent vectors on every write. The cache is bounded by entries AND by total decoded
    floats, so 1536-d vectors cannot grow it past the budget the 512-d vectors had."""
    global _dec_floats
    with _dec_lock:
        hit = _dec.get(blob)
        if hit is not None:
            _dec.move_to_end(blob)
            return hit
    a = array("f")
    a.frombytes(blob)
    v = a.tolist()
    res = (v, math.sqrt(_dot(v, v)))
    if len(v) <= DECODE_CACHE_FLOATS // 4:
        with _dec_lock:
            if blob not in _dec:
                _dec[blob] = res
                _dec_floats += len(v)
            while _dec and (len(_dec) > DECODE_CACHE_ENTRIES or _dec_floats > DECODE_CACHE_FLOATS):
                _, (old, _n) = _dec.popitem(last=False)
                _dec_floats -= len(old)
    return res


def cosine(a, b):
    """Cosine of two vectors (bytes or float sequences). Zero/mismatched -> 0.0."""
    if isinstance(a, (bytes, bytearray, memoryview)):
        a, na = _decode(a if type(a) is bytes else bytes(a))
    else:
        na = math.sqrt(_dot(a, a))
    if isinstance(b, (bytes, bytearray, memoryview)):
        b, nb = _decode(b if type(b) is bytes else bytes(b))
    else:
        nb = math.sqrt(_dot(b, b))
    if len(a) != len(b):
        return 0.0
    if not na or not nb:
        return 0.0
    return max(-1.0, min(1.0, _dot(a, b) / (na * nb)))


def cosine_many(query, blobs):
    """[cosine(query, b) for b in blobs] with the query decoded once.

    The query (a short cue) is sparse: only its non-zero coordinates are multiplied,
    which gives the same dot product (zero terms contribute nothing) several times faster.
    """
    q, nq = _decode(query if type(query) is bytes else bytes(query))
    idx = [i for i, x in enumerate(q) if x]
    qv = [q[i] for i in idx]
    pick = operator.itemgetter(*idx) if len(idx) > 1 else None
    out = []
    for b in blobs:
        v, n = _decode(b if type(b) is bytes else bytes(b))
        if len(v) != len(q) or not nq or not n or not idx:
            out.append(0.0)
            continue
        dot = _dot(qv, pick(v)) if pick else q[idx[0]] * v[idx[0]]
        out.append(max(-1.0, min(1.0, dot / (nq * n))))
    return out


def jaccard(a, b):
    """Jaccard over stemmed token sets of two strings (or pre-built iterables)."""
    sa = set(stem_tokens(a)) if isinstance(a, str) else set(a)
    sb = set(stem_tokens(b)) if isinstance(b, str) else set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Embedder:
    """Protocol: `id: str`, `dim: int`, `embed(text) -> bytes | Sequence[float]`.

    Optional: `embed_many(texts) -> list of the same`. Each embedder declares its own
    thresholds; the defaults below are the conservative hashed-n-gram values and stay in
    force until a model's cosine distribution has been measured (ADR 0010). Consumers read
    them through `tuning()`, and `encode.threshold()` never goes below 0.88 whatever is declared.
    """
    id = ""
    dim = 0
    NEAR_DUPLICATE = 0.88      # merge threshold on similarity
    SEM_GAIN = 1.8             # recall: semantic scale factor (sem * gain, capped at 1)
    SEM_MIN_COS = 0.30         # recall: cosine at which a hit counts as semantic
    RECOMBINE_LO = 0.34        # consolidate: recombine band
    RECOMBINE_HI = 0.88

    def embed(self, text):
        raise NotImplementedError


class HashedNgramEmbedder(Embedder):
    id = EMBEDDER_ID
    dim = DIM

    def embed(self, text):
        return to_bytes(embed_floats(text))


@lru_cache(maxsize=4096)
def _embed_cached(text):
    return to_bytes(embed_floats(text))  # bytes are immutable: safe to share


def embed(text):
    """The built-in hashed embedder (always available, dependency-free)."""
    return _embed_cached(text)


# --- embedder registry, background loader, bounded calls, circuit breaker (T25, ADR 0010) ---
#
# Two modes. LIBRARY mode (default; tests, scripts): the optional embedder is loaded lazily on
# first use and called directly. DAEMON mode (configure(..., background=True)): nothing that
# can block ever runs on a request thread. The model loads on a background thread (hashed
# vectors meanwhile), each embedding call runs on ONE bounded worker with a per-call timeout
# and a queue of one (a request that finds the worker busy uses hashed at once), and a circuit
# breaker bypasses the optional embedder entirely after repeated failures, probing it again
# from the worker thread after a growing cooldown. The daemon also computes vectors BEFORE it
# takes the engine lock, so the lock is never held across an embedder call.

class UnknownEmbedder(ValueError):
    """ENGRAM_EMBEDDER names no registered embedder."""


class _Bad(Exception):
    """An embedder returned something unusable; `kind` is the failure kind to log."""
    def __init__(self, kind):
        Exception.__init__(self, kind)
        self.kind = kind


# scheme -> (module, factory name), imported only when that scheme is selected. T26 adds
# "openai": ("engram.embedders_ext", "make_openai") here (or calls register_scheme()).
_LAZY = {"fastembed": ("engram.embedders_ext", "make_fastembed")}
_SCHEMES = {}          # scheme -> factory(arg) -> Embedder (register_scheme(); tests, plugins)
RETRY_S = 300.0        # library mode: after a failed load, no retry before this many seconds
EMBED_TIMEOUT = 1.0    # daemon mode: seconds one embedding call may take (ENGRAM_EMBEDDER_TIMEOUT)
LOAD_TIMEOUT = 120.0   # daemon mode: seconds the model load may take (ENGRAM_EMBEDDER_LOAD_TIMEOUT)
BREAKER_N = 3          # consecutive failures that open the breaker
COOLDOWN_MIN = 30.0    # first cooldown; doubles per consecutive opening
COOLDOWN_MAX = 600.0
_CACHE_MAX = 1024

# Sane ranges for declared thresholds; anything else (NaN, inf, out of range) = the default.
_RANGES = {"NEAR_DUPLICATE": (0.0, 1.0), "SEM_GAIN": (0.1, 10.0), "SEM_MIN_COS": (0.0, 1.0),
           "RECOMBINE_LO": (0.0, 1.0), "RECOMBINE_HI": (0.0, 1.0)}

_lock = threading.RLock()
_spec = "hash"
_async = False
_inst = None           # loaded optional embedder, or None
_state = "idle"        # idle (library, not loaded) | loading | ready | failed
_reason = None         # "<kind> <ExcClass>" of the last failure
_retry_at = 0.0        # monotonic: next load retry (failed) / next probe (breaker open)
_loader = None         # token of the loader thread whose result still counts
_load_deadline = 0.0
_load_failures = 0
_failures = 0          # consecutive call failures
_open_until = 0.0      # breaker open until this monotonic time (0 = closed)
_cooldown = 0.0
_probing = False
_failing = False       # last attempt (load or call) failed
_fallbacks = 0
_logged = set()
_dense_cache = OrderedDict()
_jobs = None           # worker queue; the worker exits when it is no longer the current one
_busy_since = None


def register_scheme(name, factory):
    """Register `factory(arg) -> Embedder` for ENGRAM_EMBEDDER=<name>:<arg>."""
    _SCHEMES[str(name).strip().lower()] = factory


def parse_spec(spec):
    """(scheme, arg). 'hash' (default) -> ('hash', ''). Raises UnknownEmbedder."""
    raw = str(spec if spec is not None else "").strip()
    if not raw or raw.lower() == "hash":
        return "hash", ""
    scheme, sep, arg = raw.partition(":")
    scheme = scheme.strip().lower()
    if not sep or not arg.strip() or (scheme not in _SCHEMES and scheme not in _LAZY):
        raise UnknownEmbedder("unknown embedder")
    return scheme, arg.strip()


def _log_once(kind, spec, exc=None):
    """One stderr line per process per failure kind. Never carries memory text: only the
    spec, the kind and the exception CLASS name (messages can echo inputs)."""
    with _lock:
        if kind in _logged:
            return
        _logged.add(kind)
    extra = ""
    if kind == "unavailable":
        extra = "; install the optional package (pip install 'engram-memory[local]')"
    elif kind == "unknown":
        extra = "; valid: hash, fastembed:<model>"
    try:
        sys.stderr.write("engram: embedder %s failed (%s%s); using the hashed embedder instead%s\n"
                         % (spec, kind, " " + type(exc).__name__ if exc is not None else "", extra))
        sys.stderr.flush()
    except Exception:
        pass


def _num(v, default, lo=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) and v >= lo else default


def configure(spec, quiet=False, background=False, load_timeout=None, embed_timeout=None):
    """Select the embedder. Never raises; bad input means hashed + one log line (`quiet`: the
    caller already reported an unknown value). `background=True` is daemon mode: the model
    load starts now on a daemon thread and every later call is bounded (see module comment)."""
    global _spec, _inst, _state, _reason, _retry_at, _failing, _async, _loader, _load_failures
    global _failures, _open_until, _cooldown, _probing, LOAD_TIMEOUT, EMBED_TIMEOUT
    with _lock:
        _spec = str(spec if spec is not None else "hash").strip() or "hash"
        _async = bool(background)
        _inst, _state, _reason, _retry_at, _failing, _loader = None, "idle", None, 0.0, False, None
        _load_failures = _failures = 0
        _open_until = _cooldown = 0.0
        _probing = False
        _dense_cache.clear()
        if load_timeout is not None:
            LOAD_TIMEOUT = _num(load_timeout, 120.0, 0.001)
        if embed_timeout is not None:
            EMBED_TIMEOUT = _num(embed_timeout, 1.0, 0.001)
    try:
        parse_spec(_spec)
    except UnknownEmbedder as e:
        if not quiet:
            _log_once("unknown", _spec, e)
        with _lock:
            _failing, _state, _reason = True, "failed", "unknown UnknownEmbedder"
            _retry_at = float("inf")
        return
    if _async and not _is_hash():
        with _lock:
            _start_loader()


def _is_hash():
    return _spec.lower() == "hash"


def _factory(scheme):
    f = _SCHEMES.get(scheme)
    if f is not None:
        return f
    mod, name = _LAZY[scheme]
    return getattr(importlib.import_module(mod), name)


def _load():
    scheme, arg = parse_spec(_spec)
    inst = _factory(scheme)(arg)
    if not (isinstance(getattr(inst, "id", None), str) and inst.id
            and isinstance(getattr(inst, "dim", None), int) and inst.dim > 0):
        raise _Bad("load")
    return inst


def _classify(e):
    if isinstance(e, UnknownEmbedder):
        return "unknown"
    if isinstance(e, _Bad):
        return e.kind
    if isinstance(e, ImportError):
        return "unavailable"
    return "load"


def _fail_load(e, kind=None):
    """Record a failed load (caller holds _lock) and schedule the next background retry."""
    global _state, _reason, _retry_at, _failing, _load_failures, _loader
    kind = kind or _classify(e)
    _load_failures += 1
    _state, _failing, _loader = "failed", True, None
    _reason = "%s %s" % (kind, type(e).__name__ if e is not None else "")
    back = min(COOLDOWN_MIN * (2 ** (_load_failures - 1)), COOLDOWN_MAX)
    _retry_at = time.monotonic() + (back if _async else RETRY_S)
    return kind


def _start_loader():
    """Daemon mode (caller holds _lock): load the model on a background thread."""
    global _state, _loader, _load_deadline
    token = object()
    _state, _loader = "loading", token
    _load_deadline = time.monotonic() + LOAD_TIMEOUT
    threading.Thread(target=_load_main, args=(token,), name="engram-embedder-load",
                     daemon=True).start()


def _load_main(token):
    global _inst, _state, _failing, _reason, _load_failures
    err = None
    try:
        inst = _load()
    except BaseException as e:  # noqa: BLE001  (a plugin can raise anything)
        inst, err = None, e
    kind = None
    with _lock:
        if _loader is not token:   # abandoned (deadline passed) or reconfigured: discard
            return
        if err is not None:
            kind = _fail_load(err)
        else:
            _inst, _state, _failing, _reason, _load_failures = inst, "ready", False, None, 0
    if kind:
        _log_once(kind, _spec, None if isinstance(err, _Bad) else err)


def _tick():
    """Daemon mode housekeeping, O(1), never blocks: enforce the load deadline and start a
    due background retry (caller does not hold a model call)."""
    global _loader
    if _is_hash():
        return
    kind = exc = None
    with _lock:
        now = time.monotonic()
        if _state == "loading" and now > _load_deadline:
            _loader = None  # abandon the stuck thread; it is never joined
            kind = _fail_load(TimeoutError(), "load-timeout")
            exc = TimeoutError()
        elif _state == "failed" and now >= _retry_at:
            _start_loader()
    if kind:
        _log_once(kind, _spec, exc)


def _current():
    """The usable optional embedder or None (= hashed). Library mode loads it here, lazily;
    daemon mode only reports what the background loader and the breaker allow."""
    global _inst, _state, _failing
    if _is_hash():
        return None
    if _async:
        _tick()
        with _lock:
            return _inst if _state == "ready" else None
    kind = exc = None
    with _lock:
        if _inst is not None:
            return _inst
        if time.monotonic() < _retry_at:
            return None
        try:
            _inst = _load()
            _state, _failing = "ready", False
            return _inst
        except Exception as e:
            exc = e
            kind = _fail_load(e)
    _log_once(kind, _spec, None if isinstance(exc, _Bad) else exc)
    return None


def wait_settled(timeout=10.0):
    """Block until a background load is no longer in progress (tests and tools only)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if _state != "loading":
                return True
        time.sleep(0.01)
    return False


def _blob(vec, dim):
    """Validated float32 blob of exactly `dim` finite, non-zero values; else _Bad."""
    try:
        if isinstance(vec, (bytes, bytearray, memoryview)):
            if len(vec) % 4:
                raise _Bad("dimension")
            vals = from_bytes(vec)
        else:
            vals = [float(x) for x in vec]
    except _Bad:
        raise
    except Exception:
        raise _Bad("error")
    if len(vals) != dim:
        raise _Bad("dimension")
    norm2 = 0.0
    for x in vals:
        if not math.isfinite(x):
            raise _Bad("nonfinite")
        norm2 += x * x
    if not math.isfinite(norm2) or norm2 <= 0.0:
        raise _Bad("nonfinite")
    return to_bytes(vals)


def _raw_blobs(inst, todo):
    many = getattr(inst, "embed_many", None)
    raw = list(many(todo)) if many is not None else [inst.embed(t) for t in todo]
    if len(raw) != len(todo):
        raise _Bad("error")
    return [_blob(v, inst.dim) for v in raw]


# --- the single bounded worker (daemon mode) ---

class _Job:
    __slots__ = ("inst", "texts", "done", "result", "error", "probe")

    def __init__(self, inst, texts, probe=False):
        self.inst, self.texts, self.probe = inst, texts, probe
        self.done, self.result, self.error = threading.Event(), None, None


def _worker_main(q):
    global _busy_since
    while True:
        job = q.get()
        try:
            job.result = _raw_blobs(job.inst, job.texts)
        except BaseException as e:  # noqa: BLE001
            job.error = e
        with _lock:
            if _jobs is q:
                _busy_since = None
        job.done.set()
        if job.probe:
            _probe_done(job)
        with _lock:
            if _jobs is not q:
                return  # replaced while stuck: exit instead of serving a dead queue


def _submit(job):
    """Hand `job` to the worker. False when it is busy (queue of one): the caller falls back
    to hashed at once instead of waiting behind a slow call. Caller holds _lock."""
    global _jobs, _busy_since
    import queue
    if _jobs is None:
        _jobs = queue.Queue(maxsize=1)
        threading.Thread(target=_worker_main, args=(_jobs,), name="engram-embedder",
                         daemon=True).start()
    if _busy_since is not None:
        return False
    _busy_since = time.monotonic()
    _jobs.put_nowait(job)
    return True


def _replace_worker():
    """The worker has been stuck past a whole cooldown: abandon it (it is a daemon thread and
    exits by itself if its call ever returns) and let the next submit start a fresh one."""
    global _jobs, _busy_since
    _jobs, _busy_since = None, None


def _open_breaker(now):
    global _open_until, _cooldown, _failures
    _cooldown = COOLDOWN_MIN if not _cooldown else min(_cooldown * 2, COOLDOWN_MAX)
    _open_until, _failures = now + _cooldown, 0


def _probe_done(job):
    global _probing, _failing, _failures, _open_until, _cooldown
    kind = None
    with _lock:
        _probing = False
        if job.error is None:
            _failing, _failures, _open_until, _cooldown = False, 0, 0.0, 0.0
        else:
            _open_breaker(time.monotonic())
            kind = job.error.kind if isinstance(job.error, _Bad) else "error"
    if kind:
        _log_once(kind, _spec, None if isinstance(job.error, _Bad) else job.error)


def _note_failure(kind, exc):
    global _failing, _failures
    with _lock:
        _failing = True
        _failures += 1
        if _failures >= BREAKER_N:
            _open_breaker(time.monotonic())
    _log_once(kind, _spec, exc)


def _dense(inst, texts):
    """Vectors for `texts` from the optional embedder; None = use hashed for this call."""
    global _probing, _failing, _failures
    out, todo = {}, []
    with _lock:
        for t in texts:
            hit = _dense_cache.get((inst.id, t))
            if hit is not None:
                _dense_cache.move_to_end((inst.id, t))
                out[t] = hit
            elif t not in todo:
                todo.append(t)
    if todo:
        if not _async:
            blobs = _raw_blobs(inst, todo)          # library mode: direct, exceptions propagate
        else:
            blobs = _bounded(inst, todo)
            if blobs is None:
                return None
        with _lock:
            for t, b in zip(todo, blobs):
                out[t] = b
                _dense_cache[(inst.id, t)] = b
            while len(_dense_cache) > _CACHE_MAX:
                _dense_cache.popitem(last=False)
    return [out[t] for t in texts]


def _bounded(inst, todo):
    """Daemon mode: one worker call with a timeout, behind the circuit breaker."""
    global _probing, _failing, _failures
    now = time.monotonic()
    with _lock:
        if _open_until:
            if now < _open_until or _probing:
                return None                          # breaker open: no call, no waiting
            if _busy_since is not None:
                _replace_worker()                    # a whole cooldown and still stuck
            _probing = True                          # cooldown over: ONE probe, off this thread
            if not _submit(_Job(inst, ["engram embedder probe"], probe=True)):
                _probing = False
                _open_breaker(now)
            return None
        if _busy_since is not None:
            stuck = now - _busy_since > EMBED_TIMEOUT
        else:
            stuck = False
        if _busy_since is not None:
            job = None
        else:
            job = _Job(inst, todo)
            _submit(job)
    if job is None:
        if stuck:
            _note_failure("timeout", None)           # an earlier call is still hung
        return None
    if not job.done.wait(EMBED_TIMEOUT):
        _note_failure("timeout", None)
        return None
    if job.error is not None:
        e = job.error
        _note_failure(e.kind if isinstance(e, _Bad) else "error", None if isinstance(e, _Bad) else e)
        return None
    with _lock:
        _failing, _failures = False, 0
    return job.result


def _fell_back():
    global _fallbacks
    with _lock:
        _fallbacks += 1


def _configured_prefix():
    try:
        scheme, _arg = parse_spec(_spec)
    except UnknownEmbedder:
        return None
    return None if scheme == "hash" else scheme + ":"


def embed_batch(texts, embedder_id=None):
    """(embedder_id_used, [blob, ...]) for `texts`, all from ONE embedder. Never raises, and in
    daemon mode never waits longer than the embed timeout (usually it does not wait at all).

    embedder_id None = the configured embedder. If it is loading, unavailable, circuit-broken
    or misbehaves, the hashed embedder answers for this call and the returned id is the HASHED
    id: callers store and compare vectors under the id that really produced them, and a
    configured-but-unavailable embedder never lends its id to hashed vectors. An explicit id
    that is not the loaded embedder is a legacy/test label: hashed vectors under that label,
    except that ids in the configured embedder's own namespace (for example `fastembed:...`)
    count as "unavailable" and get the hashed id.
    """
    global _failing
    texts = list(texts)
    if embedder_id == EMBEDDER_ID:
        return EMBEDDER_ID, [embed(t) for t in texts]
    inst = None
    if embedder_id is None:
        inst = _current()
    else:
        with _lock:
            if _inst is not None and _inst.id == embedder_id and not _is_hash() and \
                    (_state == "ready" or not _async):
                inst = _inst
    if inst is not None:
        try:
            vecs = _dense(inst, texts)
        except Exception as e:
            kind = e.kind if isinstance(e, _Bad) else "error"
            with _lock:
                _failing = True
            _log_once(kind, _spec, None if isinstance(e, _Bad) else e)
            vecs = None
        if vecs is not None:
            if not _async:
                with _lock:
                    _failing = False
            return inst.id, vecs
        _fell_back()
        return EMBEDDER_ID, [embed(t) for t in texts]
    if embedder_id is None:
        if not _is_hash():
            _fell_back()
        return EMBEDDER_ID, [embed(t) for t in texts]
    prefix = _configured_prefix()
    if prefix and embedder_id.startswith(prefix):
        _fell_back()
        return EMBEDDER_ID, [embed(t) for t in texts]
    return embedder_id, [embed(t) for t in texts]


def is_loaded_id(embedder_id):
    """True when `embedder_id` is the id of the loaded optional embedder (not hashed)."""
    with _lock:
        return _inst is not None and _inst.id == embedder_id and embedder_id != EMBEDDER_ID


def tuning(embedder_id, name, default):
    """Threshold `name` (NEAR_DUPLICATE, SEM_GAIN, SEM_MIN_COS, RECOMBINE_LO/HI) declared by
    the loaded optional embedder with this id; `default` for hashed, every unknown id and every
    declared value that is NaN/inf/out of its sane range."""
    with _lock:
        inst = _inst
    if inst is not None and inst.id == embedder_id and embedder_id != EMBEDDER_ID:
        v = getattr(inst, name, None)
        lo, hi = _RANGES.get(name, (float("-inf"), float("inf")))
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) \
                and lo <= v <= hi:
            return float(v)
    return default


def status():
    """Plain dict for `mnemo status` / `engram_status`. Never loads a model or blocks."""
    if _async:
        _tick()
    with _lock:
        now = time.monotonic()
        dense = not _is_hash()
        ready = _inst is not None and _state == "ready"
        broken = bool(_open_until)
        failing = dense and (_state == "failed" or broken or (ready and _failing))
        if not dense:
            state = "hash"
        elif _async and _state == "loading":
            state = "loading"
        elif failing:
            state = "falling-back"
        elif ready:
            state = "ready"
        else:
            state = "not-loaded"
        retry = None
        if dense and _state == "failed" and _retry_at != float("inf"):
            retry = max(0.0, _retry_at - now)
        elif dense and broken:
            retry = max(0.0, _open_until - now)
        return {"embedder_id": _inst.id if (ready and not failing) else EMBEDDER_ID,
                "embedder_configured": _spec, "embedder_state": state,
                "embedder_falling_back": bool(failing or state == "loading"),
                "embedder_reason": _reason if (failing or state == "loading") and _reason else
                ("circuit breaker open" if broken else None),
                "embedder_retry_in_s": None if retry is None else round(retry, 1),
                "embedder_fallbacks": _fallbacks,
                "embedder_dim": _inst.dim if ready else DIM}


def _reset_for_tests():
    global _spec, _inst, _state, _reason, _retry_at, _failing, _fallbacks, _async, _loader
    global _load_failures, _failures, _open_until, _cooldown, _probing, _jobs, _busy_since
    global LOAD_TIMEOUT, EMBED_TIMEOUT
    with _lock:
        _spec, _inst, _state, _reason, _retry_at = "hash", None, "idle", None, 0.0
        _failing, _fallbacks, _async, _loader = False, 0, False, None
        _load_failures = _failures = 0
        _open_until = _cooldown = 0.0
        _probing = False
        _jobs, _busy_since = None, None
        LOAD_TIMEOUT, EMBED_TIMEOUT = 120.0, 1.0
        _logged.clear()
        _dense_cache.clear()
        _SCHEMES.clear()

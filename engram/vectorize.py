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


@lru_cache(maxsize=1024)
def _decode(blob):
    """(floats, norm) for an immutable bytes blob; cached because dedup rescans the
    same recent vectors on every write."""
    a = array("f")
    a.frombytes(blob)
    v = a.tolist()
    return v, math.sqrt(_dot(v, v))


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


# --- embedder registry, lazy loader, fail-soft (T25, ADR 0010) ---------------------------

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
RETRY_S = 300.0        # after a failed load, do not retry before this many seconds
_CACHE_MAX = 1024

_lock = threading.RLock()
_spec = "hash"
_inst = None           # loaded optional embedder, or None
_retry_at = 0.0
_failing = False       # last attempt (load or call) failed
_fallbacks = 0
_logged = set()
_dense_cache = OrderedDict()


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


def configure(spec, quiet=False):
    """Select the embedder (daemon start). Never raises; bad input means hashed + one log line
    (`quiet`: the caller already reported an unknown value)."""
    global _spec, _inst, _retry_at, _failing
    with _lock:
        _spec = str(spec if spec is not None else "hash").strip() or "hash"
        _inst, _retry_at, _failing = None, 0.0, False
        _dense_cache.clear()
    try:
        parse_spec(_spec)
    except UnknownEmbedder as e:
        if not quiet:
            _log_once("unknown", _spec, e)
        with _lock:
            _failing = True


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


def _current():
    """The loaded optional embedder, loading it lazily; None = use the hashed one."""
    global _inst, _retry_at, _failing
    with _lock:
        if _is_hash():
            return None
        if _inst is not None:
            return _inst
        if time.monotonic() < _retry_at:
            return None
        try:
            _inst = _load()
            _failing = False
            return _inst
        except UnknownEmbedder as e:
            kind, exc = "unknown", e
        except ImportError as e:
            kind, exc = "unavailable", e
        except _Bad as e:
            kind, exc = e.kind, None
        except Exception as e:
            kind, exc = "load", e
        _failing = True
        _retry_at = time.monotonic() + RETRY_S
    _log_once(kind, _spec, exc)
    return None


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


def _dense(inst, texts):
    """Vectors for `texts` from an optional embedder, validated; raises on any problem."""
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
        many = getattr(inst, "embed_many", None)
        raw = list(many(todo)) if many is not None else [inst.embed(t) for t in todo]
        if len(raw) != len(todo):
            raise _Bad("error")
        blobs = [_blob(v, inst.dim) for v in raw]
        with _lock:
            for t, b in zip(todo, blobs):
                out[t] = b
                _dense_cache[(inst.id, t)] = b
            while len(_dense_cache) > _CACHE_MAX:
                _dense_cache.popitem(last=False)
    return [out[t] for t in texts]


def _fell_back():
    global _fallbacks
    with _lock:
        _fallbacks += 1


def embed_batch(texts, embedder_id=None):
    """(embedder_id_used, [blob, ...]) for `texts`, all from ONE embedder. Never raises.

    embedder_id None = the configured embedder. If it is unavailable or misbehaves, the
    hashed embedder answers for this call and the returned id says so, so callers store and
    compare vectors under the id that really produced them. An explicit id that is not the
    loaded embedder (legacy/test label) gets hashed vectors under that label.
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
            if _inst is not None and _inst.id == embedder_id and not _is_hash():
                inst = _inst
    if inst is not None:
        try:
            vecs = _dense(inst, texts)
            with _lock:
                _failing = False
            return inst.id, vecs
        except Exception as e:
            kind = e.kind if isinstance(e, _Bad) else "error"
            with _lock:
                _failing = True
            _log_once(kind, _spec, None if isinstance(e, _Bad) else e)
            _fell_back()
            return EMBEDDER_ID, [embed(t) for t in texts]
    if embedder_id is None:
        if not _is_hash():
            _fell_back()
        return EMBEDDER_ID, [embed(t) for t in texts]
    return embedder_id, [embed(t) for t in texts]


def tuning(embedder_id, name, default):
    """Threshold `name` (NEAR_DUPLICATE, SEM_GAIN, SEM_MIN_COS, RECOMBINE_LO/HI) declared by
    the loaded optional embedder with this id; `default` for hashed and every unknown id."""
    with _lock:
        inst = _inst
    if inst is not None and inst.id == embedder_id and embedder_id != EMBEDDER_ID:
        v = getattr(inst, name, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            return float(v)
    return default


def status():
    """Plain dict for `mnemo status` / `engram_status`. Does not load a model."""
    with _lock:
        dense = not _is_hash()
        loaded = _inst is not None
        failing = dense and _failing
        if not dense:
            state = "hash"
        elif failing:
            state = "falling-back"
        else:
            state = "active" if loaded else "not-loaded"
        return {"embedder_id": _inst.id if (loaded and not failing) else EMBEDDER_ID,
                "embedder_configured": _spec, "embedder_state": state,
                "embedder_falling_back": bool(failing),
                "embedder_fallbacks": _fallbacks,
                "embedder_dim": _inst.dim if loaded else DIM}


def _reset_for_tests():
    global _spec, _inst, _retry_at, _failing, _fallbacks
    with _lock:
        _spec, _inst, _retry_at, _failing, _fallbacks = "hash", None, 0.0, False, 0
        _logged.clear()
        _dense_cache.clear()
        _SCHEMES.clear()

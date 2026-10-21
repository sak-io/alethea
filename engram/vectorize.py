"""Embedding, stemming, cosine/jaccard. Stdlib only, no internal deps (T§4)."""
import hashlib
import math
import re
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


def cosine(a, b):
    """Cosine of two vectors (bytes or float sequences). Zero/mismatched -> 0.0."""
    if isinstance(a, (bytes, bytearray, memoryview)):
        a = from_bytes(a)
    if isinstance(b, (bytes, bytearray, memoryview)):
        b = from_bytes(b)
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def jaccard(a, b):
    """Jaccard over stemmed token sets of two strings (or pre-built iterables)."""
    sa = set(stem_tokens(a)) if isinstance(a, str) else set(a)
    sb = set(stem_tokens(b)) if isinstance(b, str) else set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class Embedder:
    """Protocol: `id: str`, `dim: int`, `embed(text) -> bytes | Sequence[float]`."""
    id = ""
    dim = 0

    def embed(self, text):
        raise NotImplementedError


class HashedNgramEmbedder(Embedder):
    id = EMBEDDER_ID
    dim = DIM

    def embed(self, text):
        return to_bytes(embed_floats(text))


def embed(text):
    return HashedNgramEmbedder().embed(text)

"""Deterministic FAKE dense embedder: what real sentence embedders do wrong, on purpose.

Real sentence models put "enable X" / "disable X", swapped entities, swapped numbers, version
bumps, single-character identifier changes and a sentence with a negation inserted at cosine
0.95-0.99, because those differences barely move a mean-pooled contextual vector. This class
reproduces that failure mode with no model and no third-party package, so the dedup safety
suites can prove the vetoes (which work on text, never on vectors) hold under a dense
embedder:

* antonym pairs collapse to one concept; negators carry ~no weight;
* every identifier-shaped token (digits, versions, paths, camelCase, snake_case, dotted names)
  collapses to the single concept "<ident>";
* known entity names (staging/prod, alice/bob, mysql/postgres ...) collapse to "<entity>";
* the remaining content words get their own pseudo-random direction, so unrelated sentences
  stay far apart and true restatements stay at cosine ~1.0.

Vectors are `dim`-dimensional (default 384; tests also use 1536), unit length, derived from
blake2b only, so they are identical on every Python version.
"""
import hashlib
import math
import re

from alethea import vectorize

_RAW = re.compile(r"[A-Za-z0-9_./\\:~#@-]+")
_CAMEL = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2,}[a-z]")

_ANTONYM_GROUPS = [
    ("enable", "disable", "enabled", "disabled"), ("allow", "deny", "allowed", "denied", "forbid", "block"),
    ("add", "remove"), ("on", "off"), ("true", "false"), ("start", "stop"), ("open", "close"),
    ("include", "exclude"), ("accept", "reject"), ("increase", "decrease"), ("before", "after"),
    ("always", "never"), ("require", "forbid"), ("grant", "revoke"), ("lock", "unlock"),
    ("public", "private"), ("sync", "async"), ("up", "down"), ("pass", "fail"),
    ("create", "delete"), ("install", "uninstall"), ("show", "hide"), ("push", "pull"),
    ("encrypt", "decrypt"), ("valid", "invalid"), ("success", "failure"),
]
_CONCEPT = {}
for _g in _ANTONYM_GROUPS:
    for _w in _g:
        _CONCEPT.setdefault(_w, "ant:" + _g[0])

_NEGATORS = frozenset(["no", "not", "never", "none", "nothing", "without", "cannot", "cant", "dont",
                       "doesnt", "didnt", "isnt", "arent", "wasnt", "werent", "wont", "wouldnt",
                       "shouldnt", "couldnt", "mustnt", "hasnt", "havent", "hadnt"])
_ENTITIES = frozenset(["staging", "prod", "production", "dev", "alice", "bob", "carol", "dave",
                       "mysql", "postgres", "redis", "frontend", "backend"])
_STOP = frozenset("a an the to of in on at by for from with and or is are was were be it this that "
                  "we do does did as into than then so if when they them their".split())
W_NEG, W_STOP, W_IDENT, W_ENTITY, W_WORD = 0.05, 0.15, 0.6, 0.6, 1.0


def _is_ident(tok):
    return (any(c.isdigit() for c in tok) or any(c in tok for c in "/\\_.-")
            or bool(_CAMEL.search(tok)) or (len(tok) > 1 and tok.isupper()))


def _concepts(text):
    """[(concept, weight)] for a sentence."""
    out = []
    for m in _RAW.findall((text or "").replace("'", "").replace("\u2019", "")):
        tok = m.strip(".:-#@~")
        if not tok:
            continue
        low = tok.lower()
        if _is_ident(tok) and low not in _CONCEPT and low not in _STOP:
            out.append(("<ident>", W_IDENT))
        elif low in _NEGATORS:
            out.append(("<neg>", W_NEG))
        elif low in _CONCEPT:
            out.append((_CONCEPT[low], W_WORD))
        elif low in _ENTITIES:
            out.append(("<entity>", W_ENTITY))
        elif low in _STOP:
            out.append(("stop:" + low, W_STOP))
        else:
            out.append(("w:" + low, W_WORD))
    return out


def _direction(concept, dim):
    vals = []
    block = 0
    while len(vals) < dim:
        d = hashlib.blake2b(("%s|%d" % (concept, block)).encode("utf-8"), digest_size=64,
                            salt=b"fake-dense-v1").digest()
        vals.extend((b - 127.5) / 74.0 for b in d)
        block += 1
    vals = vals[:dim]
    n = math.sqrt(sum(v * v for v in vals))
    return [v / n for v in vals]


_DIRS = {}


def fake_vector(text, dim=384):
    acc = [0.0] * dim
    for concept, w in _concepts(text):
        key = (concept, dim)
        d = _DIRS.get(key)
        if d is None:
            d = _DIRS[key] = _direction(concept, dim)
        for i in range(dim):
            acc[i] += w * d[i]
    n = math.sqrt(sum(v * v for v in acc))
    if not n:
        acc[0], n = 1.0, 1.0  # empty text: any fixed unit vector
    return [v / n for v in acc]


class FakeDenseEmbedder(vectorize.Embedder):
    """Registered via vectorize.register_scheme("fake", make_fake): ALETHEA_EMBEDDER=fake:<dim>."""

    def __init__(self, dim=384):
        self.dim = int(dim)
        self.id = "fake-dense-v1:%d" % self.dim
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        return fake_vector(text, self.dim)

    def embed_many(self, texts):
        return [self.embed(t) for t in texts]


def make_fake(arg):
    return FakeDenseEmbedder(int(arg or 384))


def cosine(a, b):
    return vectorize.cosine(fake_vector(a), fake_vector(b))

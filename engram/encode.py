"""Encoding: similarity engine with vetoes (R9-R11) and scoring (R12), T§5.

The encode() gate arrives in a later task.
"""
import re

from engram import markers, vectorize
from engram.vectorize import cosine, jaccard  # noqa: F401  (module-level so tests can stub)

NEAR_DUPLICATE = 0.88
VETO_FACTOR = 0.55

# Per-embedder thresholds; dense embedders (T25) register theirs here.
_THRESHOLDS = {}

_RAW_TOKEN = re.compile(r"[A-Za-z0-9_./\\:~#@-]+")
_CAMEL = re.compile(r"[a-z0-9][A-Z]|[A-Z]{2,}[a-z]")

_NEGATIONS = frozenset([
    "no", "not", "never", "none", "nothing", "nobody", "neither", "nor", "without",
    "cannot", "cant", "dont", "doesnt", "didnt", "isnt", "arent", "wasnt", "werent",
    "wont", "wouldnt", "shouldnt", "couldnt", "mustnt", "hasnt", "havent", "hadnt",
])
_ANTONYM_PAIRS = [
    ("enable", "disable"), ("allow", "deny"), ("allow", "forbid"), ("allow", "block"),
    ("add", "remove"), ("on", "off"), ("true", "false"), ("start", "stop"),
    ("open", "close"), ("include", "exclude"), ("accept", "reject"),
    ("increase", "decrease"), ("before", "after"), ("always", "never"),
    ("require", "forbid"), ("grant", "revoke"), ("lock", "unlock"),
    ("public", "private"), ("sync", "async"), ("up", "down"), ("min", "max"),
    ("minimum", "maximum"), ("pass", "fail"), ("success", "failure"),
    ("create", "delete"), ("install", "uninstall"), ("show", "hide"),
    ("pause", "resume"), ("push", "pull"), ("encrypt", "decrypt"),
    ("allowed", "denied"), ("enabled", "disabled"), ("valid", "invalid"),
]
_ANTONYM_MAP = {}
for _x, _y in _ANTONYM_PAIRS:
    _ANTONYM_MAP.setdefault(_x, set()).add(_y)
    _ANTONYM_MAP.setdefault(_y, set()).add(_x)


def _raw_tokens(text):
    """Original-case tokens (punctuation trimmed), keyed by lowercase form."""
    out = {}
    for m in _RAW_TOKEN.findall(text or ""):
        t = m.strip(".:-#@~")
        if t:
            out.setdefault(t.lower(), t)
    return out


def _words(text):
    """Lowercase word set with apostrophes folded (isn't -> isnt)."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", "").replace("’", "")))


def _identifier_veto(a, b):
    ta, tb = _raw_tokens(a), _raw_tokens(b)
    for k in set(ta) ^ set(tb):
        tok = ta.get(k) or tb.get(k)
        if any(c.isdigit() for c in tok) or "/" in tok or "\\" in tok or _CAMEL.search(tok):
            return True
    return False


def _negation_veto(a, b):
    wa, wb = _words(a), _words(b)
    if (wa & _NEGATIONS) != (wb & _NEGATIONS):
        return True
    da, db = wa - wb, wb - wa
    for w in da:
        if _ANTONYM_MAP.get(w, frozenset()) & db:
            return True
    return False


def _substitution_veto(a, b):
    """Both sides carry tokens the other lacks: a swap (staging/prod, alice/bob),
    not a pure addition. Lexical overlap cannot tell a swapped entity from a
    restatement, so damp it (false negatives are safe)."""
    wa, wb = set(vectorize.stem_tokens(a)), set(vectorize.stem_tokens(b))
    return bool(wa - wb) and bool(wb - wa)


def threshold(embedder=None):
    """Near-duplicate threshold for an embedder id (hook for dense embedders).

    Never below NEAR_DUPLICATE: a looser threshold risks silent false merges.
    """
    return max(NEAR_DUPLICATE, _THRESHOLDS.get(embedder, NEAR_DUPLICATE))


def register_threshold(embedder, value):
    _THRESHOLDS[embedder] = float(value)


def similarity(a, b, embedder=None):
    """max(cosine, jaccard), damped x0.55 by identifier and negation/antonym vetoes.

    Vetoes apply under every embedder. Only the default hashed embedder is
    available until T25; `embedder` selects the threshold, not the vectors.
    """
    s = max(cosine(vectorize.embed(a), vectorize.embed(b)), jaccard(a, b))
    if s <= 0.0:
        return 0.0
    ident = _identifier_veto(a, b)
    if ident:
        s *= VETO_FACTOR
    neg = _negation_veto(a, b)
    if neg:
        s *= VETO_FACTOR
    if not neg and not ident and _substitution_veto(a, b):
        s *= VETO_FACTOR
    return s


def is_near_duplicate(a, b, embedder=None):
    return similarity(a, b, embedder=embedder) >= threshold(embedder)


def _nwords(text):
    return len(re.findall(r"\S+", text or ""))


def _length_adjust(score, text):
    n = _nwords(text)
    if n < markers.SHORT_WORDS:
        return score * markers.SHORT_PENALTY
    if n >= markers.LONG_WORDS:
        return score + markers.LONG_BONUS
    return score


def amygdala(text):
    """Affective salience in [0, 1]: summed marker weights, length-adjusted.

    Only the first markers.MAX_CHARS characters are scored (cost bound).
    """
    if not text or not text.strip():
        return 0.0
    text = text[:markers.MAX_CHARS]
    raw = sum(w for _, w, rx in markers.AMYGDALA if rx.search(text))
    if raw <= 0.0:
        return 0.0
    return max(0.0, min(1.0, _length_adjust(raw, text)))


def durability(text):
    """Union of amygdala() with affect-free durable signals, in [0, 1]."""
    if not text or not text.strip():
        return 0.0
    text = text[:markers.MAX_CHARS]
    signals = [markers.IDENTITY, markers.DECISION, markers.QUANTITY, markers.CAUSAL, markers.RULE]
    n = sum(1 for rx in signals if rx.search(text))
    content = 0.0
    if n:
        content = _length_adjust(min(1.0, 0.45 + 0.1 * (n - 1)), text)
    return max(0.0, min(1.0, max(amygdala(text), content)))


def classify(text):
    """Route to a kind: identity, procedural, semantic, else episodic."""
    t = (text or "")[:markers.MAX_CHARS]
    if markers.CLASS_IDENTITY.search(t):
        return "identity"
    if markers.CLASS_PROCEDURAL.search(t):
        return "procedural"
    if markers.CLASS_SEMANTIC.search(t):
        return "semantic"
    return "episodic"


def novelty(text, candidates=None, embedder=None):
    """1 - best similarity against candidate texts (not rank-normalized score).

    `candidates` is a callable returning candidate texts (injected in tests);
    when None, recall.candidates is imported here (deferred, ADR 0002).
    """
    if candidates is None:
        from engram import recall  # deferred: recall imports encode
        candidates = recall.candidates
    items = candidates(text) if callable(candidates) else candidates
    text = (text or "")[:markers.MAX_CHARS]
    best = 0.0
    for c in items or ():
        if isinstance(c, dict):
            c = c.get("text", "")
        c = (c or "")[:markers.MAX_CHARS]
        best = max(best, similarity(text, c, embedder=embedder))
    return max(0.0, 1.0 - best)


def compare_vectors(a, b):
    """Compare (embedder_id, vector) pairs; differing ids are never compared."""
    if a[0] != b[0]:
        return None
    return vectorize.cosine(a[1], b[1])

"""Encoding: similarity engine with vetoes (R9-R11), scoring (R12), the encode()
gate and observe() (R13, R14), T§5. Every ingestion point redacts first (G16).
"""
import os
import re

from engram import markers, vectorize
from engram.redact import redact
from engram.vectorize import cosine, jaccard  # noqa: F401  (module-level so tests can stub)

NEAR_DUPLICATE = 0.88
ENCODE_THRESHOLD = 0.34
OBSERVE_DURABILITY = 0.45
REINFORCE_STEP = 0.5
IDENTITY_FLOOR = 0.62
DELIBERATE_FLOOR = 0.55
GIST_CHARS = 200
# Per-turn input cap in bytes (env ENGRAM_MAX_TURN_BYTES), applied before redact/journal.
MAX_TURN_BYTES = 256 * 1024
TRUNCATION_MARKER = "\n[TRUNCATED]"
MAX_KEYWORDS = 12
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


def _identifier_shaped(tok):
    """digits, path separators, camel/Pascal, snake_case, kebab/dotted names, ALL_CAPS."""
    return (any(c.isdigit() for c in tok) or "/" in tok or "\\" in tok
            or "_" in tok or "-" in tok or "." in tok
            or bool(_CAMEL.search(tok)) or (len(tok) > 1 and tok.isupper()))


def _identifier_veto(a, b):
    ta, tb = _raw_tokens(a), _raw_tokens(b)
    for k in set(ta) & set(tb):  # case-only / inserted-capital differences
        x, y = ta[k], tb[k]
        if x != y and (_CAMEL.search(x) or _CAMEL.search(y) or "_" in x or "_" in y):
            return True
    for k in set(ta) ^ set(tb):
        tok = ta.get(k) or tb.get(k)
        if _identifier_shaped(tok):
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


def _order_veto(a, b):
    """Same bag of stemmed tokens (>= 0.95 multiset overlap) in a different order:
    role swaps ("Alice reports to Bob" / "Bob reports to Alice") that no set-based
    signal can see. A pure reordering restatement therefore does not merge (ADR 0007)."""
    sa, sb = vectorize.stem_tokens(a), vectorize.stem_tokens(b)
    if sa == sb or not sa or not sb:
        return False
    from collections import Counter
    ca, cb = Counter(sa), Counter(sb)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return union > 0 and inter / union >= 0.95


def threshold(embedder=None):
    """Near-duplicate threshold for an embedder id (hook for dense embedders).

    Never below NEAR_DUPLICATE: a looser threshold risks silent false merges.
    """
    return max(NEAR_DUPLICATE, _THRESHOLDS.get(embedder, NEAR_DUPLICATE))


def register_threshold(embedder, value):
    _THRESHOLDS[embedder] = float(value)


def _raw_similarity(a, b):
    return max(cosine(vectorize.embed(a), vectorize.embed(b)), jaccard(a, b))


def similarity(a, b, embedder=None):
    """max(cosine, jaccard), damped x0.55 by identifier and negation/antonym vetoes.

    Vetoes apply under every embedder. Only the default hashed embedder is
    available until T25; `embedder` selects the threshold, not the vectors.
    """
    s = _raw_similarity(a, b)
    if s <= 0.0:
        return 0.0
    ident = _identifier_veto(a, b)
    if ident:
        s *= VETO_FACTOR
    neg = _negation_veto(a, b)
    if neg:
        s *= VETO_FACTOR
    if not neg and not ident and (_substitution_veto(a, b) or _order_veto(a, b)):
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


# --- the encode() gate (R13) -------------------------------------------------

_TRIVIAL = re.compile(
    r"^\s*(?:ok(?:ay)?|k|kk|thanks?|thank\s+you|thx|ty|lol|lgtm|yes|yeah|yep|yup|no|nope|nah|"
    r"sure|cool|nice|great|good|fine|hi|hello|hey|bye|got\s+it|sounds\s+good|go\s+ahead|"
    r"continue|done|hmm+|ah+|oh+|wow|haha+|:\)|👍)[\s.!?,]*$", re.IGNORECASE)


def max_turn_bytes():
    try:
        return max(1024, int(os.environ.get("ENGRAM_MAX_TURN_BYTES", MAX_TURN_BYTES)))
    except ValueError:
        return MAX_TURN_BYTES


def _clean(content):
    """Cap size first (cost bound), then redact; truncation is marked."""
    if not isinstance(content, str):
        return ""
    cap = max_turn_bytes()
    cut = len(content) > cap  # cheap pre-cut by chars before encoding
    if cut:
        content = content[:cap]
    raw = content.encode("utf-8", errors="replace")
    if len(raw) > cap:
        content = raw[:cap].decode("utf-8", errors="ignore")
        cut = True
    if cut:
        content += TRUNCATION_MARKER
    return redact(content)


def _r(v):
    return redact(v) if isinstance(v, str) else v


def _redact_meta(v):
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: _redact_meta(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_redact_meta(x) for x in v]
    return v


def _keywords(text):
    seen, out = [], set()
    for t in vectorize.tokenize(text):
        if len(t) > 2 and t not in out:
            out.add(t)
            seen.append(t)
    return " ".join(seen[:MAX_KEYWORDS])


def _gist(text):
    g = " ".join(text.split())
    return g if len(g) <= GIST_CHARS else g[:GIST_CHARS].rstrip() + "..."


def _best_duplicate(cortex, content, embedder_id):
    """(best_similarity, memory) over same-embedder recall candidates."""
    from engram import recall  # deferred: recall imports encode (ADR 0002)
    best, best_m, best_i = 0.0, None, -1
    pool = []
    for i, m in enumerate(recall.candidates(cortex, content, embedder_id=embedder_id)):
        if m.get("embedder_id") != embedder_id:
            continue  # never compare across embedders
        a, b = content[:markers.MAX_CHARS], (m["content"] or "")[:markers.MAX_CHARS]
        pool.append((-_raw_similarity(a, b), i, a, b, m))
    # Vetoes only damp (x < 1), so similarity <= raw. Visit by descending raw and stop
    # once raw < best: the result (best score and, on ties, the earliest candidate) is
    # identical to scoring every candidate, but the veto work is skipped.
    pool.sort(key=lambda t: (t[0], t[1]))
    for nraw, i, a, b, m in pool:
        if -nraw < best:
            break
        s = similarity(a, b, embedder=embedder_id)
        if s > best or (s == best and best_m is not None and i < best_i):
            best, best_m, best_i = s, m, i
    return best, best_m


def encode(cortex, content, kind=None, salience=None, force=False, deliberate=False,
           session_id=None, cwd=None, repo_root=None, meta=None,
           embedder_id=vectorize.EMBEDDER_ID):
    """The encode gate. Returns {"status": "created"|"reinforced"|"rejected", ...}.

    Content, gist, keywords and meta are redacted before anything is stored.
    """
    content = _clean(content)
    if not content.strip():
        return {"status": "rejected", "reason": "empty"}
    if _TRIVIAL.match(content) and not (force or deliberate):
        return {"status": "rejected", "reason": "trivial"}
    best, dup = _best_duplicate(cortex, content, embedder_id)
    if dup is not None and best >= threshold(embedder_id):
        cortex.reinforce(dup["id"], REINFORCE_STEP)
        return {"status": "reinforced", "id": dup["id"], "similarity": best}
    surprise = max(0.0, min(1.0, 1.0 - best))
    explicit = salience is not None
    affect = max(0.0, min(1.0, float(salience))) if explicit else amygdala(content)
    effective = affect * (0.45 + 0.55 * surprise)
    if explicit:
        effective = max(effective, affect * 0.9)
    kind = kind or classify(content)
    if kind == "identity":
        effective = max(effective, IDENTITY_FLOOR)
    if deliberate:
        effective = max(effective, DELIBERATE_FLOOR)
    effective = max(0.0, min(1.0, effective))
    if effective < ENCODE_THRESHOLD and not (force or deliberate):
        return {"status": "rejected", "reason": "low_salience", "salience": effective}
    session_id, cwd, repo_root = _r(session_id), _r(cwd), _r(repo_root)
    mid = cortex.insert(
        kind, content, gist=_gist(content), keywords=_keywords(content), salience=effective,
        session_id=session_id, cwd=cwd, repo_root=repo_root,
        vector=vectorize.embed(content), embedder_id=embedder_id,
        meta=_redact_meta(meta or {}))
    return {"status": "created", "id": mid, "kind": kind, "salience": effective}


def remember(cortex, content, **kw):
    """Voluntary write: deliberate encode."""
    kw["deliberate"] = True
    return encode(cortex, content, **kw)


def reflect(cortex, content, cited_ids=(), **kw):
    """Deliberate write linked by `abstracts` edges to the cited memories."""
    kw["deliberate"] = True
    kw.setdefault("kind", "semantic")
    res = encode(cortex, content, **kw)
    if res["status"] == "created":
        for cid in cited_ids or ():
            if cid != res["id"] and cortex.get(cid) is not None:
                cortex.link(res["id"], cid, "abstracts")
    return res


def forget(cortex, mid):
    """Suppress (not delete) a memory, stamping meta['suppressed_at']."""
    m = cortex.get(mid)
    if m is None:
        return False
    meta = dict(m["meta"])
    meta["suppressed_at"] = cortex.clock()
    cortex.update(mid, meta=meta, status="suppressed")
    return True


def observe(cortex, content, session_id=None, role=None, cwd=None, repo_root=None,
            embedder_id=vectorize.EMBEDDER_ID):
    """Involuntary pathway: always journal (redacted); promote if durable."""
    content = _clean(content)
    if not content.strip():
        return {"journal_id": None, "promoted": None}
    session_id, role, cwd, repo_root = _r(session_id), _r(role), _r(cwd), _r(repo_root)
    jid = cortex.journal_add(content, session_id=session_id, role=role, cwd=cwd,
                             repo_root=repo_root)
    promoted = None
    if durability(content) >= OBSERVE_DURABILITY:
        promoted = encode(cortex, content, session_id=session_id, cwd=cwd,
                          repo_root=repo_root, embedder_id=embedder_id)
    return {"journal_id": jid, "promoted": promoted}

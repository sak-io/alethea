"""Retrieval: FTS + vector fusion, reinforcement, identity cue (R15, R16, T§6).

All regexes are linear-time (no nested/overlapping quantifiers) and every input
is length-capped before it reaches a regex, the tokenizer or FTS.
"""
import math
import re

from engram import vectorize

MAX_CUE_CHARS = 2000
MAX_QUERY_TOKENS = 32
MAX_IDENTITY_CHARS = 200
K = 8
POOL = 20
W_LEX, W_SEM, SEM_GAIN = 0.55, 0.45, 1.8
HIT_BOOST, SPREAD_BOOST = 0.6, 0.35
SPREAD_TOP, SPREAD_DAMP = 3, 0.5

STOPWORDS = frozenset(
    "a an the and or but if of to in on at by for with from as is are was were be been "
    "am do does did have has had i me my we our you your it its this that these those "
    "what which who whom how when where why can could would should will shall may might "
    "about into than then so not no".split())

# Literal alternatives with single-space separators: linear, no repetition.
_IDENTITY_CUE = re.compile(
    r"\b(?:who am i|who are you|who is this|who are we|who i am|what is my name"
    r"|whats my name|what do you know about me|tell me about me|about me"
    r"|who am i talking to|who are you talking to|do you know me|do you remember me)\b")


def _cap(cue):
    return cue[:MAX_CUE_CHARS] if isinstance(cue, str) else ""


def is_identity_cue(cue):
    c = _cap(cue)[:MAX_IDENTITY_CHARS].lower()
    c = " ".join(c.replace("?", " ").replace("'", "").split())
    return bool(_IDENTITY_CUE.search(c))


def _fts_query(cue):
    """Forgiving OR over quoted raw+stemmed non-stopword tokens (no FTS syntax passes)."""
    seen, out = set(), []
    for t in vectorize.tokenize(_cap(cue)):
        if t in STOPWORDS:
            continue
        for v in (t, vectorize.stem(t)):
            if v and v not in seen:
                seen.add(v)
                out.append('"%s"' % v)
        if len(out) >= MAX_QUERY_TOKENS:
            break
    return " OR ".join(out[:MAX_QUERY_TOKENS])


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 700)))
    e = math.exp(max(x, -700))
    return e / (1.0 + e)


def candidates(cortex, cue, pool=POOL, embedder_id=vectorize.EMBEDDER_ID):
    """Fused, scored active memories (dicts + lex/sem/relevance/score), best first.

    No reinforcement. Vector channel: only rows with the same embedder_id among the
    max(pool*4, 600) most recently touched; FTS covers the whole corpus.
    """
    cue = _cap(cue)
    if not cue.strip():
        return []
    pool = max(1, int(pool))
    lex = {}
    q = _fts_query(cue)
    if q:
        active = []
        for mid in cortex.search_fts(q, limit=pool * 4):
            m = cortex.get(mid)
            if m is not None and m["status"] == "active":
                active.append(mid)
        n = len(active)
        for i, mid in enumerate(active):
            lex[mid] = 1.0 - i / n
    sem = {}
    rows = cortex.db.execute(
        "SELECT id, vector FROM memories WHERE status='active' AND embedder_id=? "
        "AND vector IS NOT NULL ORDER BY last_access DESC, rowid DESC LIMIT ?",
        (embedder_id, max(pool * 4, 600))).fetchall()
    qv = vectorize.embed(cue)
    for r in rows:
        s = vectorize.cosine(qv, r["vector"])
        if s > 0:
            sem[r["id"]] = s
    now = cortex.clock()
    out = []
    for mid in set(lex) | set(sem):
        m = cortex.get(mid)
        if m is None or m["status"] != "active":
            continue
        rel = W_LEX * lex.get(mid, 0.0) + W_SEM * min(1.0, sem.get(mid, 0.0) * SEM_GAIN)
        act = cortex.activation(m, now)
        m.update(lex=lex.get(mid, 0.0), sem=sem.get(mid, 0.0), relevance=rel,
                 score=rel * (0.55 + 0.45 * _sigmoid(act)))
        out.append(m)
    out.sort(key=lambda m: (-m["score"], m["id"]))
    return out[:pool]


def spread(cortex, hits, limit=K):
    """2-hop spreading activation from the top direct hits (minimal; refined later)."""
    seen = {h["id"] for h in hits}
    found = {}
    frontier = [(h["id"], h["score"]) for h in hits[:SPREAD_TOP]]
    for _hop in range(2):
        nxt = []
        for src, sc in frontier:
            for e in cortex.edges_from(src):
                dst = e["dst"]
                s = sc * e["weight"] * SPREAD_DAMP
                if dst in seen or s <= found.get(dst, 0.0):
                    continue
                found[dst] = s
                nxt.append((dst, s))
        frontier = nxt
    out = []
    for dst, s in sorted(found.items(), key=lambda kv: (-kv[1], kv[0])):
        m = cortex.get(dst)
        if m is not None and m["status"] == "active":
            m["score"] = s
            out.append(m)
    return out[:limit]


def _identity_rows(cortex, k):
    now = cortex.clock()
    rows = [cortex.get(r["id"]) for r in cortex.db.execute(
        "SELECT id FROM memories WHERE kind='identity' AND status='active'")]
    rows.sort(key=lambda m: (-cortex.activation(m, now), m["id"]))
    for m in rows[:k]:
        m["score"] = 1.0
    return rows[:k]


def recall(cortex, cue, k=K, pool=POOL, reinforce=True, embedder_id=vectorize.EMBEDDER_ID):
    """Return up to k results, each with `via` in {identity, hit, spread}; reinforce them."""
    k = max(1, int(k))
    results, seen = [], set()
    if is_identity_cue(cue):
        for m in _identity_rows(cortex, k):
            m["via"] = "identity"
            seen.add(m["id"])
            results.append(m)
    hits = candidates(cortex, cue, pool=pool, embedder_id=embedder_id)
    for m in hits:
        if len(results) >= k:
            break
        if m["id"] not in seen:
            m["via"] = "hit"
            seen.add(m["id"])
            results.append(m)
    if len(results) < k:
        for m in spread(cortex, hits, limit=k):
            if len(results) >= k:
                break
            if m["id"] not in seen:
                m["via"] = "spread"
                seen.add(m["id"])
                results.append(m)
    if reinforce:
        for m in results:
            cortex.reinforce(m["id"], SPREAD_BOOST if m["via"] == "spread" else HIT_BOOST)
    return results

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
# Relevance floors (tuned on tests/data/recall_corpus.py plus the off-topic negatives in
# tests/test_recall_regression.py; tests/data/recall_heldout.py was never tuned against).
# Measured: unrelated-pair cosines max 0.26 on the hashed n-gram embedder, related pairs
# median 0.70 with ~7% below 0.30. So a hit counts as "semantic" at cosine >= SEM_MIN_COS.
# FTS ranks are normalised (the top full-text hit always has lex 1.0 however weak), so a
# lexical hit is judged on how much of the cue it covers instead: the matched fraction of
# distinct non-stopword cue stems must reach LEX_MIN_COVER (all of them for cues of
# <= LEX_ALL_UP_TO stems). A hit passes if EITHER test passes. encode's dedup path calls
# candidates(relevance_floor=False) and sees every neighbour.
SEM_MIN_COS = 0.30
REL_MIN = W_SEM * min(1.0, SEM_MIN_COS * SEM_GAIN)  # equivalent relevance floor
LEX_MIN_COVER, LEX_ALL_UP_TO = 0.3, 0
# Same-repo soft boost: multiplies the score; activation/relevance still dominate.
SCOPE_BOOST = 1.15
SCOPES = ("all", "repo")

STOPWORDS = frozenset(
    "a an the and or but if of to in on at by for with from as is are was were be been "
    "am do does did have has had i me my we our you your it its this that these those "
    "what which who whom how when where why can could would should will shall may might "
    "about into than then so not no he she him his her hers they them their there here "
    "up out off over very just also long much many".split())

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


def _cue_stems(cue):
    return {vectorize.stem(t) for t in vectorize.tokenize(_cap(cue)) if t not in STOPWORDS}


def _covers(m, stems):
    """Does memory m contain enough of the cue's distinct stems (see LEX_MIN_COVER)?"""
    if not stems:
        return True
    n = len(stems)
    need = n if n <= LEX_ALL_UP_TO else math.ceil(n * LEX_MIN_COVER)
    text = " ".join((m.get("content") or "", m.get("gist") or "", m.get("keywords") or ""))
    have = {vectorize.stem(t) for t in vectorize.tokenize(text[:8192])}
    got = len(stems & have)
    if got < need:  # a stem of >= 5 chars also matches a longer word it prefixes (postgres/postgresql)
        rest = [t for t in stems - have if len(t) >= 5]
        got += sum(1 for t in rest if any(h.startswith(t) for h in have))
    return got >= need


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-min(x, 700)))
    e = math.exp(max(x, -700))
    return e / (1.0 + e)


def candidates(cortex, cue, pool=POOL, embedder_id=vectorize.EMBEDDER_ID, relevance_floor=True):
    """Fused, scored active memories (dicts + lex/sem/relevance/score), best first.

    No reinforcement. Vector channel: only rows with the same embedder_id among the
    max(pool*4, 600) most recently touched; FTS covers the whole corpus. `relevance_floor=False`
    disables the semantic-only relevance floor (encode's novelty/dedup wants every neighbour).
    """
    cue = _cap(cue)
    if not cue.strip():
        return []
    pool = max(1, int(pool))
    lex = {}
    seen = {}  # id -> memory dict fetched once (avoids a second get per hit)
    q = _fts_query(cue)
    if q:
        hits = cortex.search_fts(q, limit=pool * 4)
        seen = cortex.get_many(hits)
        active = [mid for mid in hits if mid in seen and seen[mid]["status"] == "active"]
        n = len(active)
        for i, mid in enumerate(active):
            lex[mid] = 1.0 - i / n
    sem = {}
    rows = cortex.db.execute(
        "SELECT id, vector FROM memories WHERE status='active' AND embedder_id=? "
        "AND vector IS NOT NULL ORDER BY last_access DESC, rowid DESC LIMIT ?",
        (embedder_id, max(pool * 4, 600))).fetchall()
    qv = vectorize.embed(cue)
    vecs = [r["vector"] for r in rows]
    for r, s in zip(rows, vectorize.cosine_many(qv, vecs)):
        if s > 0:
            sem[r["id"]] = s
    # Floor test uses the cue minus stopwords: "what is the ..." alone scores cosine ~0.3
    # against unrelated text, which is function-word noise, not meaning.
    sem_floor = {}
    if relevance_floor:
        core = " ".join(t for t in vectorize.tokenize(cue) if t not in STOPWORDS)
        if core and core != " ".join(vectorize.tokenize(cue)):
            sem_floor = {r["id"]: s for r, s in zip(rows, vectorize.cosine_many(
                vectorize.embed(core), vecs))}
        else:
            sem_floor = sem
    now = cortex.clock()
    # score = rel * f with f in [0.55, 1], so score <= rel: visit ids by descending rel
    # and stop once rel is below the pool-th best score. Exact top-`pool`, but only the
    # few leading ids pay for a row fetch (was one get() per vector hit).
    rels = {mid: W_LEX * lex.get(mid, 0.0) + W_SEM * min(1.0, sem.get(mid, 0.0) * SEM_GAIN)
            for mid in set(lex) | set(sem)}
    out = []
    floor = -1.0
    stems = _cue_stems(cue) if relevance_floor else ()
    for mid in sorted(rels, key=lambda i: (-rels[i], i)):
        rel = rels[mid]
        sem_ok = (sem_floor.get(mid, 0.0) if relevance_floor else sem.get(mid, 0.0)) >= SEM_MIN_COS
        if relevance_floor and not sem_ok and mid not in lex:
            continue
        if len(out) >= pool and rel < floor:
            break
        m = seen.get(mid) or cortex.get(mid)
        if m is None or m["status"] != "active":
            continue
        if relevance_floor and not sem_ok and not _covers(m, stems):
            continue
        act = cortex.activation(m, now)
        m.update(lex=lex.get(mid, 0.0), sem=sem.get(mid, 0.0), relevance=rel,
                 score=rel * (0.55 + 0.45 * _sigmoid(act)))
        out.append(m)
        if len(out) >= pool:
            floor = sorted((x["score"] for x in out), reverse=True)[pool - 1]
    out.sort(key=lambda m: (-m["score"], m["id"]))
    return out[:pool]


def spread(cortex, hits, limit=K):
    """2-hop spreading activation from the top direct hits (R15).

    Score decays by edge weight and SPREAD_DAMP per hop; direct hits are excluded;
    only active memories are returned or propagated through. No reinforcement.
    """
    seen = {h["id"] for h in hits}
    found = {}
    frontier = [(h["id"], h["score"]) for h in hits[:SPREAD_TOP]]
    for _hop in range(2):
        nxt = []
        for src, sc in frontier:
            for e in cortex.edges_from(src):
                dst = e["dst"]
                s = sc * min(max(e["weight"], 0.0), 1.0) * SPREAD_DAMP
                if dst in seen or s <= found.get(dst, 0.0):
                    continue
                dm = cortex.get(dst)
                if dm is None or dm["status"] != "active":
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


def recall(cortex, cue, k=K, pool=POOL, reinforce=True, embedder_id=vectorize.EMBEDDER_ID,
           repo_root=None, cwd=None, scope="all"):
    """Return up to k results, each with `via` in {identity, hit, spread}; reinforce them.

    Same-repo hits get a SCOPE_BOOST score multiplier (soft); scope="repo" additionally
    drops everything not in the session's repo/cwd (hard filter).
    """
    k = max(1, int(k))
    if scope not in SCOPES:
        raise ValueError("scope must be one of %s" % ", ".join(SCOPES))
    hard = scope == "repo"

    def in_scope(m):
        return _scope(m, repo_root, cwd) == "repo"
    results, seen = [], set()
    if is_identity_cue(cue):
        for m in _identity_rows(cortex, k):
            if hard and not in_scope(m):
                continue
            m["via"] = "identity"
            seen.add(m["id"])
            results.append(m)
    hits = candidates(cortex, cue, pool=pool, embedder_id=embedder_id)
    if repo_root or cwd:
        for m in hits:
            if in_scope(m):
                m["score"] *= SCOPE_BOOST
        hits.sort(key=lambda m: (-m["score"], m["id"]))
    if hard:
        hits = [m for m in hits if in_scope(m)]
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
            if m["id"] not in seen and not (hard and not in_scope(m)):
                m["via"] = "spread"
                seen.add(m["id"])
                results.append(m)
    if reinforce:
        for m in results:
            cortex.reinforce(m["id"], SPREAD_BOOST if m["via"] == "spread" else HIT_BOOST)
    return results


BRIEF_IDENTITY, BRIEF_FACTS, BRIEF_EPISODES = 5, 8, 3
BRIEF_MAX_BYTES = 4096
BRIEF_LINE_CHARS = 300
REPO_BOOST = 0.5


def _scope(mem, repo_root, cwd):
    """'repo' if in the session's repo/cwd, 'none' if unscoped, 'other' otherwise."""
    if repo_root:
        if mem.get("repo_root") == repo_root:
            return "repo"
        return "none" if not (mem.get("repo_root") or mem.get("cwd")) else "other"
    if cwd:
        if mem.get("cwd") == cwd:
            return "repo"
        return "none" if not (mem.get("cwd") or mem.get("repo_root")) else "other"
    return "none"


def briefing(cortex, repo_root=None, cwd=None, max_bytes=BRIEF_MAX_BYTES):
    """Session-start briefing (R17). Does NOT reinforce anything (G6).

    Order: identity (<=5), top-activation semantic+procedural (<=8; repo-scoped get
    REPO_BOOST, unscoped and other-repo stay eligible), recent episodic (<=3, same
    repo/cwd or unscoped). Active only. Returns {"text", "items"}; text <= max_bytes UTF-8.
    """
    now = cortex.clock()

    def active(kinds):
        q = ",".join("?" * len(kinds))
        return [cortex.get(r["id"]) for r in cortex.db.execute(
            "SELECT id FROM memories WHERE status='active' AND kind IN (%s)" % q, kinds)]

    items = [("identity", m) for m in _identity_rows(cortex, BRIEF_IDENTITY)]
    facts = []
    for m in active(("semantic", "procedural")):
        a = cortex.activation(m, now)
        if _scope(m, repo_root, cwd) == "repo":
            a += REPO_BOOST
        facts.append((-a, m["id"], m))
    facts.sort(key=lambda t: t[:2])
    items += [("fact", t[2]) for t in facts[:BRIEF_FACTS]]
    eps = []
    for m in active(("episodic",)):
        sc = _scope(m, repo_root, cwd)
        if sc == "other":
            continue
        t = m["last_access"] if m["last_access"] is not None else m["created_at"]
        eps.append((-(t + (REPO_BOOST * 86400.0 if sc == "repo" else 0.0)), m["id"], m))
    eps.sort(key=lambda t: t[:2])
    items += [("episode", t[2]) for t in eps[:BRIEF_EPISODES]]

    lines, used, out = [], 0, []
    for label, m in items:
        body = " ".join((m.get("gist") or m["content"])[:BRIEF_LINE_CHARS * 4].split())
        line = "[%s] %s\n" % (label, body[:BRIEF_LINE_CHARS])
        n = len(line.encode("utf-8"))
        if used + n > max_bytes:
            line = line.encode("utf-8")[:max(max_bytes - used, 0)].decode("utf-8", "ignore")
            if line:
                lines.append(line)
                out.append(m)
            break
        lines.append(line)
        out.append(m)
        used += n
    return {"text": "".join(lines), "items": out}

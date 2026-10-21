"""Consolidation stages 3-4: downscale (synaptic homeostasis) and recombine (REM-like).

ADR 0003: decay acts on strength, ages are in days, per-kind DOWNSCALE_RATE.
ADR 0005: the optional `lock` (context manager) is taken per batch, never for a run.
Fairness: releasing and immediately re-acquiring a plain (unfair) RLock starves other
threads, so callers that share the lock with request threads must pass a
`engram.fairlock.FairLock` (FIFO handoff).
All time comes from the injectable clock (`now` argument or `cortex.clock`).
"""
import contextlib
import itertools
import json
import math
import time

from engram import encode
from engram.config import DOWNSCALE_INTERVAL_S
from engram.cortex import (ARCHIVE_ACTIVATION, DAY_S, DOWNSCALE_FACTOR,
                           DOWNSCALE_RATE, MIN_STRENGTH)

MAX_PERIOD_DAYS = 14.0
PRUNE_AFTER_DAYS = 45.0
SUPPRESSED_PRUNE_DAYS = 90.0
BATCH = 200
RECOMBINE_TOP = 60
RECOMBINE_MAX_LINKS = 40
RECOMBINE_LO = 0.34
RECOMBINE_HI = 0.88
RECOMBINE_KINDS = ("semantic", "procedural", "identity")


def _lock(lock):
    return lock if lock is not None else contextlib.nullcontext()


def _elapsed_s(raw, now):
    """Seconds since last_downscale, or None if missing/corrupt/NaN/future."""
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(last) or last < 0 or last > now:
        return None
    return now - last


def _delete(cortex, mid):
    cortex.db.execute("DELETE FROM edges WHERE src=? OR dst=?", (mid, mid))
    cortex.delete(mid)


def downscale(cortex, now=None, lock=None):
    """Returns dict(ran, periods, archived, deleted). No-op inside the 6 h gate.

    Missing/corrupt/future last_downscale: 0 elapsed time (no shrink), marker reset to now.

    Crash semantics (deliberate): `last_downscale` is written BEFORE decay is applied, so a
    crash mid-run loses that period's decay instead of applying it twice on restart. Decay is
    a gentle, repeatable nudge; double-shrinking a memory is the worse failure.
    """
    now = float(cortex.clock() if now is None else now)
    out = {"ran": False, "periods": 0.0, "archived": 0, "deleted": 0}
    with _lock(lock):
        raw = cortex.meta_get("last_downscale")
        elapsed = _elapsed_s(raw, now)
        if elapsed is not None and elapsed < DOWNSCALE_INTERVAL_S:
            return out
        elapsed = elapsed or 0.0  # invalid marker: 0 elapsed, reset below
        cortex.meta_set("last_downscale", repr(now))
    days = min(elapsed / DAY_S, MAX_PERIOD_DAYS)
    out["ran"] = True
    out["periods"] = days
    ids = [r["id"] for r in cortex.db.execute(
        "SELECT id FROM memories WHERE kind != 'identity' AND status='active' ORDER BY id")]
    for i in range(0, len(ids), BATCH):
        with _lock(lock):
            for mid in ids[i:i + BATCH]:
                m = cortex.get(mid)
                if m is None or m["kind"] == "identity" or m["status"] != "active":
                    continue
                if days > 0:
                    factor = DOWNSCALE_FACTOR ** (days * DOWNSCALE_RATE.get(m["kind"], 1.0))
                    m["strength"] = max(MIN_STRENGTH, m["strength"] * factor)
                    cortex.db.execute("UPDATE memories SET strength=? WHERE id=?",
                                      (m["strength"], mid))
                if cortex.activation(m, now=now) < ARCHIVE_ACTIVATION:
                    meta = dict(m["meta"])
                    meta["archived_at"] = now
                    cortex.db.execute(
                        "UPDATE memories SET status='archived', meta=? WHERE id=?",
                        (json.dumps(meta), mid))
                    out["archived"] += 1
            cortex.db.commit()
    with _lock(lock):
        out["deleted"] = _prune(cortex, now)
    return out


def _prune(cortex, now):
    """Hard-delete eligible archived/suppressed rows.

    Depends on absolute timestamps: a forward clock jump can bulk-delete eligible rows.
    A row with no archived_at/suppressed_at stamp is stamped with now and kept this sweep.
    """
    n = 0
    rows = cortex.db.execute(
        "SELECT id, kind, status, access_count, last_access, meta FROM memories "
        "WHERE kind != 'identity' AND status IN ('archived','suppressed')").fetchall()
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except ValueError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if r["status"] == "archived":
            if r["kind"] != "episodic" or r["access_count"] > 1:
                continue  # zero-access = never reinforced beyond creation
            limit, key = PRUNE_AFTER_DAYS, "archived_at"
        else:
            limit, key = SUPPRESSED_PRUNE_DAYS, "suppressed_at"
        since = meta.get(key)
        if isinstance(since, bool) or not isinstance(since, (int, float)) \
                or not math.isfinite(since):
            meta[key] = now
            cortex.db.execute("UPDATE memories SET meta=? WHERE id=?",
                              (json.dumps(meta), r["id"]))
            continue
        if (now - since) / DAY_S >= limit:
            _delete(cortex, r["id"])
            n += 1
    cortex.db.commit()
    return n


def recombine(cortex, lock=None):
    """Add 'recombined' edges for clearly-related pairs. Returns number of new links."""
    marks = ",".join("?" * len(RECOMBINE_KINDS))
    with _lock(lock):
        rows = cortex.db.execute(
            "SELECT id, content, embedder_id FROM memories WHERE status='active' "
            "AND kind IN (%s) ORDER BY salience DESC, id LIMIT ?" % marks,
            (*RECOMBINE_KINDS, RECOMBINE_TOP)).fetchall()
    added = 0
    for a, b in itertools.combinations(rows, 2):
        if added >= RECOMBINE_MAX_LINKS:
            break
        if a["embedder_id"] != b["embedder_id"]:
            continue
        s = encode.similarity(a["content"], b["content"], embedder=a["embedder_id"])
        if not (RECOMBINE_LO <= s <= RECOMBINE_HI):
            continue
        with _lock(lock):
            if (cortex.edge_weight(a["id"], b["id"], "recombined") is not None
                    or cortex.edge_weight(b["id"], a["id"], "recombined") is not None):
                continue
            cortex.link(a["id"], b["id"], "recombined")
        added += 1
    return added


# --- stages 1-2: replay and extractive gist (R18, T§7) ----------------------
# All text handling is linear: line/char scans and str methods, no nested or
# backtracking regexes, with hard caps on every dimension.

EPISODE_GAP = 15 * 60.0
MIN_EPISODE_TURNS = 4
TOP_SENTENCES = 6
JACCARD_DEDUP = 0.6
MAX_TURN_CHARS = 20_000      # per-turn scan cap
MAX_EPISODE_TURNS = 100      # first 50 + last 50 are distilled
MAX_LINE_CHARS = 1_000
MAX_SENTENCE_CHARS = 300
MAX_CANDIDATES = 400
MAX_SUMMARY_CHARS = 1_500
LONG_OUTPUT_LINES = 8
MIN_GIST_DURABILITY = 0.0   # zero-durability lines only fill slots if nothing else qualifies
MAX_ATTEMPTS = 3
_FENCES = ("```", "~~~")
_BULLETS = ("- ", "* ", "+ ", "• ", "– ")
_DIFF_START = ("diff --git", "@@ ")
_DIFF_HEAD = ("diff --git", "--- ", "+++ ", "@@", "index ")
_ERR_WORDS = ("error", "exception", "failed", "fatal", "panic", "denied", "traceback")


def _is_err(line):
    low = line.lower()
    return any(w in low for w in _ERR_WORDS)


def _is_frame(stripped):
    return stripped.startswith("at ") or stripped.startswith('File "')


def _command(line):
    s = line.lstrip()
    return s if s.startswith("$ ") else None


def _flush_block(out, cmd, last_err):
    if cmd:
        out.append("ran " + cmd[2:].strip()[:120])
    if last_err:
        out.append(last_err.strip())


def prefilter(text):
    """Collapse code fences, diffs, stack traces and long terminal output.

    Keeps at most the command and the final error line of each collapsed block.
    Returns a list of prose lines. Linear time; input is capped.
    """
    if not isinstance(text, str):
        return []
    out = []
    in_fence = in_diff = in_trace = False
    cmd = last_err = None
    run = []  # consecutive prose-or-output lines; collapsed if implausibly long

    def flush_run():
        if len(run) > LONG_OUTPUT_LINES * 2:
            errs = [x for x in run if _is_err(x)]
            _flush_block(out, None, errs[-1] if errs else None)
        else:
            out.extend(run)
        del run[:]

    def end_cmd():
        nonlocal cmd, last_err
        if len(run) > LONG_OUTPUT_LINES:
            _flush_block(out, cmd, last_err)
        else:
            _flush_block(out, cmd, None)
            out.extend(run)
        del run[:]
        cmd = last_err = None

    for raw in text[:MAX_TURN_CHARS].splitlines():
        line = raw[:MAX_LINE_CHARS]
        s = line.strip()
        if in_fence:
            if s.startswith(_FENCES):
                in_fence = False
                _flush_block(out, cmd, last_err)
                cmd = last_err = None
            else:
                c = _command(line)
                if c and cmd is None:
                    cmd = c
                if _is_err(line):
                    last_err = line
            continue
        if s.startswith(_FENCES):
            if cmd is not None:
                end_cmd()
            flush_run()
            in_fence, in_diff, in_trace = True, False, False
            cmd = last_err = None
            continue
        if in_diff:
            if s and (s.startswith(_DIFF_HEAD) or line[0] in "+- \\"):
                continue
            in_diff = False
        if s.startswith(_DIFF_START):
            flush_run()
            in_diff = True
            continue
        if in_trace:
            if not s:
                in_trace = False
            elif line[0] in " \t" or _is_frame(s):
                pass
            else:
                in_trace = False
                out.append(s)  # final error line
            continue
        if s.startswith("Traceback (most recent call last)"):
            flush_run()
            in_trace = True
            continue
        if line[:1] in (" ", "\t") and _is_frame(s):
            flush_run()  # JS/Java: keep the error line above the frames
            in_trace = True
            continue
        c = _command(line)
        if c:
            if cmd is not None:
                end_cmd()
            flush_run()
            cmd, last_err = c, None
            continue
        if cmd is not None:
            if s:
                if _is_err(line):
                    last_err = line
                run.append(s)
                continue
            end_cmd()
            continue
        if not s:
            flush_run()
            continue
        if s.startswith(_BULLETS) or s[0].isdigit():
            flush_run()
            out.append(s)
            continue
        run.append(s)
    if cmd is not None:
        end_cmd()
    elif in_fence:
        _flush_block(out, None, last_err)
    flush_run()
    return out


def _strip_bullet(s):
    s = s.strip()
    for b in _BULLETS:
        if s.startswith(b):
            return s[len(b):].strip()
    i = 0
    while i < len(s) and i < 4 and s[i].isdigit():
        i += 1
    if 0 < i < len(s) and s[i] in ".)" and s[i + 1:i + 2] == " ":
        return s[i + 2:].strip()
    return s


def split_sentences(lines):
    """Split on line boundaries, bullets and . ! ? followed by whitespace."""
    out = []
    for line in lines:
        line = _strip_bullet(line)
        start, n = 0, len(line)
        for i, ch in enumerate(line):
            if ch in ".!?" and (i + 1 == n or line[i + 1] in " \t"):
                piece = line[start:i + 1].strip()
                start = i + 1
                if piece:
                    out.append(piece)
        tail = line[start:].strip()
        if tail:
            out.append(tail)
        if len(out) > MAX_CANDIDATES * 4:
            break
    res = []
    for p in out:
        if len(p) > MAX_SENTENCE_CHARS:
            p = p[:MAX_SENTENCE_CHARS].rstrip() + "..."
        if len(p) >= 12 and any(c.isalpha() for c in p):
            res.append(p)
    return res


def _words(s):
    w, cur = set(), []
    for ch in s.lower()[:MAX_SENTENCE_CHARS + 3]:
        if ch.isalnum() or ch == "_":
            cur.append(ch)
        elif cur:
            w.add("".join(cur))
            cur = []
    if cur:
        w.add("".join(cur))
    return w


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def extract_gist(turns, top=TOP_SENTENCES):
    """Extractive distillation of an episode's turn texts.

    Returns [(sentence, durability)] in chronological order, at most `top`.
    """
    turns = list(turns)
    if len(turns) > MAX_EPISODE_TURNS:
        h = MAX_EPISODE_TURNS // 2
        turns = turns[:h] + turns[-h:]
    cands, seen = [], set()
    for t in turns:
        for s in split_sentences(prefilter(t)):
            key = s.lower()
            if key not in seen and len(cands) < MAX_CANDIDATES:
                seen.add(key)
                cands.append((s, encode.durability(s), len(cands)))
    useful = [c for c in cands if c[1] > MIN_GIST_DURABILITY]
    ranked = sorted(useful or cands, key=lambda c: (-c[1], c[2]))
    kept = []
    for s, d, pos in ranked:
        w = _words(s)
        if any(_jaccard(w, kw) > JACCARD_DEDUP for _, _, _, kw in kept):
            continue
        kept.append((s, d, pos, w))
        if len(kept) >= top:
            break
    kept.sort(key=lambda k: k[2])
    return [(s, d) for s, d, _, _ in kept]


class Distiller:
    """Protocol: distill(turn_texts) -> [(sentence, durability)]. Extractive by default."""

    def distill(self, turns):
        return extract_gist(turns)


def replay(cortex, limit=None):
    """Group pending journal rows by session; split on gaps > EPISODE_GAP.

    Returns episodes: dict(session_id, cwd, repo_root, rows, start, end).
    """
    by_sess = {}
    for r in cortex.journal_pending(limit):
        by_sess.setdefault(r["session_id"], []).append(r)
    episodes = []
    for sid, rs in by_sess.items():
        rs.sort(key=lambda r: (r["created_at"], r["id"]))
        cur = []
        for r in rs:
            if cur and r["created_at"] - cur[-1]["created_at"] > EPISODE_GAP:
                episodes.append(_episode(sid, cur))
                cur = []
            cur.append(r)
        if cur:
            episodes.append(_episode(sid, cur))
    episodes.sort(key=lambda e: (e["start"], e["rows"][0]["id"]))
    return episodes


def _episode(sid, rows):
    return {"session_id": sid, "rows": rows, "start": rows[0]["created_at"],
            "end": rows[-1]["created_at"],
            "cwd": next((r["cwd"] for r in rows if r["cwd"]), None),
            "repo_root": next((r["repo_root"] for r in rows if r["repo_root"]), None)}


def _episode_salience(turns):
    return max((encode.durability(t[:MAX_TURN_CHARS]) for t in turns[:MAX_EPISODE_TURNS]),
               default=0.0)


def _chunks(ep):
    """Split an episode into consecutive MAX_EPISODE_TURNS-row sub-episodes."""
    rows = ep["rows"]
    if len(rows) <= MAX_EPISODE_TURNS:
        return [ep]
    return [_episode(ep["session_id"], rows[i:i + MAX_EPISODE_TURNS])
            for i in range(0, len(rows), MAX_EPISODE_TURNS)]


def _meta_del(cortex, *keys):
    for k in keys:
        cortex.db.execute("DELETE FROM engram_meta WHERE key=?", (k,))
    cortex.db.commit()


def consolidate_episodes(cortex, distiller=None, lock=None, limit=None, should_stop=None):
    """Distil pending journal episodes into an episodic summary + semantic facts.

    Everything goes through encode() (redaction, dedup vetoes, thresholds). Journal rows
    are marked consolidated only after the episode (or 100-turn chunk) is written; a
    failure leaves them pending for retry. Writes are checkpointed in engram_meta so a
    retry does not re-reinforce finished steps, and after MAX_ATTEMPTS failures the
    episode is poisoned and skipped. `should_stop()` is polled between episodes (batches);
    when true the run ends cleanly with the rest still pending. Returns dict(episodes,
    skipped, summaries, facts, errors, poisoned, stopped).
    """
    distiller = distiller or Distiller()
    out = {"episodes": 0, "skipped": 0, "summaries": 0, "facts": 0, "errors": 0,
           "poisoned": 0, "stopped": False}
    with _lock(lock):
        episodes = replay(cortex, limit)
    for whole in episodes:
        for ep in _chunks(whole):
            if should_stop is not None and should_stop():
                out["stopped"] = True
                return out
            ids = [r["id"] for r in ep["rows"]]
            turns = [r["content"] or "" for r in ep["rows"]]
            tag = str(ids[0])
            k_poison, k_att, k_prog = ("consol_poison:" + tag, "consol_attempts:" + tag,
                                       "consol_progress:" + tag)
            out["episodes"] += 1
            try:
                with _lock(lock):
                    if cortex.meta_get(k_poison):
                        out["poisoned"] += 1
                        continue
                    if (len(turns) < MIN_EPISODE_TURNS
                            and _episode_salience(turns) < encode.ENCODE_THRESHOLD):
                        cortex.journal_mark_consolidated(ids)
                        out["skipped"] += 1
                        continue
                    try:
                        attempts = int(cortex.meta_get(k_att, "0")) + 1
                    except ValueError:
                        attempts = 1
                    if attempts > MAX_ATTEMPTS:
                        cortex.meta_set(k_poison, "1")
                        out["poisoned"] += 1
                        continue
                    cortex.meta_set(k_att, attempts)
                # The distiller (possibly slow, e.g. an LLM) runs OUTSIDE the lock: it only
                # sees the already-read turn texts, never the cortex.
                try:
                    gist = distiller.distill(turns)
                except Exception:
                    gist = extract_gist(turns)
                with _lock(lock):
                    _write_episode(cortex, ep, ids, gist, out, k_prog)
                    cortex.journal_mark_consolidated(ids)
                    _meta_del(cortex, k_att, k_prog)
            except Exception:
                out["errors"] += 1  # rows stay pending: retried (up to MAX_ATTEMPTS)
    return out


def _progress(cortex, key):
    try:
        d = json.loads(cortex.meta_get(key) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _write_episode(cortex, ep, ids, gist, out, key):
    if not gist:
        return
    prog = _progress(cortex, key)  # {"eid": id, "done": [indexes of finished facts]}
    common = dict(session_id=ep["session_id"], cwd=ep["cwd"], repo_root=ep["repo_root"])
    eid = prog.get("eid")
    if eid is None:
        text = ("Episode (%d turns): " % len(ids)
                + " ".join(s for s, _ in gist))[:MAX_SUMMARY_CHARS]
        res = encode.encode(
            cortex, text, kind="episodic", force=True,
            meta={"episode": {"turns": len(ids), "start": ep["start"], "end": ep["end"],
                              "first_journal_id": ids[0], "last_journal_id": ids[-1]}},
            **common)
        if res["status"] == "rejected":
            return  # nothing storable (empty after redaction)
        eid = res["id"]
        if res["status"] == "created":
            out["summaries"] += 1
        prog = {"eid": eid, "done": []}
        cortex.meta_set(key, json.dumps(prog))
    for i, (s, d) in enumerate(gist):
        if d < encode.OBSERVE_DURABILITY or i in prog["done"]:
            continue
        kind = encode.classify(s)
        r = encode.encode(cortex, s, kind="semantic" if kind == "episodic" else kind, **common)
        if r["status"] in ("created", "reinforced") and r["id"] != eid:
            if r["status"] == "created":
                out["facts"] += 1
            cortex.link(r["id"], eid, "abstracts")
        prog["done"].append(i)
        cortex.meta_set(key, json.dumps(prog))


# --- journal retention (G16) and the sleep cycle (R21, T§7) ------------------

JOURNAL_CONSOLIDATED_DAYS = 30.0     # owner decision G16
JOURNAL_UNCONSOLIDATED_DAYS = 90.0   # owner-approved addition
PURGE_BATCH = 500


def purge_journal(cortex, now=None, lock=None, keep_sessions=()):
    """Delete consolidated journal rows older than 30 d and unconsolidated older than 90 d.

    Rows of `keep_sessions` (the live session) are never purged. Checkpoint/poison meta
    keys of purged rows are removed with them. Batched, lock per batch.
    Returns dict(consolidated, unconsolidated). Idempotent.
    """
    now = float(cortex.clock() if now is None else now)
    keep = tuple(keep_sessions or ())
    skip = " AND (session_id IS NULL OR session_id NOT IN (%s))" % ",".join("?" * len(keep)) \
        if keep else ""
    out = {"consolidated": 0, "unconsolidated": 0}
    for key, flag, days in (("consolidated", 1, JOURNAL_CONSOLIDATED_DAYS),
                            ("unconsolidated", 0, JOURNAL_UNCONSOLIDATED_DAYS)):
        cutoff = now - days * DAY_S
        while True:
            with _lock(lock):
                ids = [r["id"] for r in cortex.db.execute(
                    "SELECT id FROM journal WHERE consolidated=? AND created_at<?" + skip
                    + " ORDER BY id LIMIT ?", (flag, cutoff, *keep, PURGE_BATCH))]
                if not ids:
                    break
                marks = ",".join("?" * len(ids))
                cortex.db.execute("DELETE FROM journal WHERE id IN (%s)" % marks, ids)
                # episode checkpoints are keyed by the episode's first journal id
                for pre in ("consol_poison:", "consol_attempts:", "consol_progress:"):
                    cortex.db.execute("DELETE FROM engram_meta WHERE key IN (%s)"
                                      % marks, [pre + str(i) for i in ids])
                cortex.db.commit()
            out[key] += len(ids)
    return out


def sleep_cycle(cortex, deep=True, consolidate_only=False, now=None, distiller=None,
                lock=None, budget_s=None, timer=time.monotonic, keep_sessions=()):
    """replay+gist -> downscale -> recombine, plus journal purge.

    Triggers: timer = defaults; PreCompact = `deep=False` (skips downscale, recombine);
    SessionEnd = `consolidate_only=True` (consolidate_episodes alone, no purge).
    Each stage is isolated: an exception is recorded under result["errors"][stage] and
    the remaining stages still run; nothing is raised. `keep_sessions`: session ids whose
    journal rows are never purged (the live session). `lock` (a context manager) is
    taken per batch inside the stages, never across a stage. `budget_s` is a wall-clock
    budget on `timer`: consolidation stops between episodes and later stages are skipped
    (result["stopped"]=True); unfinished journal rows stay pending (resumable).
    Returns dict(ok, deep, consolidate_only, stopped, stages={name: counts + "ms"},
    errors={name: "Type: msg"}, ms).
    """
    t0 = timer()
    deadline = None if budget_s is None else t0 + float(budget_s)

    def over():
        return deadline is not None and timer() >= deadline

    res = {"ok": True, "deep": bool(deep) and not consolidate_only,
           "consolidate_only": bool(consolidate_only), "stopped": False,
           "stages": {}, "errors": {}, "ms": 0.0}
    try:
        t_now = float(cortex.clock() if now is None else now)
    except Exception:
        t_now = None

    plan = [("consolidate", lambda: consolidate_episodes(
        cortex, distiller=distiller, lock=lock, should_stop=over))]
    if not consolidate_only:
        plan.append(("purge", lambda: purge_journal(cortex, t_now, lock=lock,
                                                       keep_sessions=keep_sessions)))
        if deep:
            plan.append(("downscale", lambda: downscale(cortex, t_now, lock=lock)))
            plan.append(("recombine", lambda: {"links": recombine(cortex, lock=lock)}))
    for name, fn in plan:
        if over() and name != "consolidate":
            res["stopped"] = True
            res["stages"][name] = {"skipped": "budget", "ms": 0.0}
            continue
        ts = timer()
        try:
            out = dict(fn())
        except Exception as e:  # isolate: later stages still run
            out = {}
            res["errors"][name] = "%s: %s" % (type(e).__name__, e)
            res["ok"] = False
        if out.get("stopped"):
            res["stopped"] = True
        out["ms"] = round((timer() - ts) * 1000.0, 3)
        res["stages"][name] = out
    if t_now is not None and not consolidate_only:
        try:
            with _lock(lock):
                cortex.meta_set("last_sleep", repr(t_now))
        except Exception as e:
            res["errors"]["last_sleep"] = "%s: %s" % (type(e).__name__, e)
            res["ok"] = False
    res["ms"] = round((timer() - t0) * 1000.0, 3)
    return res

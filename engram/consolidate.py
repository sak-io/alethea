"""Consolidation stages 3-4: downscale (synaptic homeostasis) and recombine (REM-like).

ADR 0003: decay acts on strength, ages are in days, per-kind DOWNSCALE_RATE.
ADR 0005: the optional `lock` (context manager) is taken per batch, never for a run.
All time comes from the injectable clock (`now` argument or `cortex.clock`).
"""
import contextlib
import itertools
import json
import math

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

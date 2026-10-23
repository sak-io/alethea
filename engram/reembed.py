"""Resumable, non-blocking re-embedding of stored vectors (T26, ADR 0011).

`Reembedder` rewrites `vector` and `embedder_id` (and nothing else) of active memories whose
embedder_id differs from the CONFIGURED and READY embedder. Rules:

* The engine lock is never held while embedding: a batch's rows are read under the lock, the
  vectors are computed outside it (bounded worker, breaker: ADR 0010), then the lock is taken
  briefly to write the whole batch and the cursor in ONE transaction.
* Resumable and idempotent: the cursor, counters and settings live in `engram_meta` (key
  `reembed`) and are committed with each batch, so a restart or kill -9 resumes at the next
  batch; re-running skips rows already under the target id. A write only lands if the row's
  content and embedder_id are still what was embedded (compare-and-set), so a concurrent edit
  is never overwritten with a stale vector; it is picked up by the final sweep.
* Background thread, low priority: it sleeps between batches so other verbs stay fast.
* One run at a time. A failed batch is retried with backoff; after FAIL_LIMIT consecutive
  failures the run pauses with a reason (nothing is lost; `start` resumes).
* A batch whose vectors came back under another id (the embedder fell back to hashed) is a
  failure: a vector is never stored under an id that did not produce it.
* IDENTITY memories are skipped unless `include_identity` (they are the most personal).
Memory text is never logged. Until a row is migrated it is reachable by the lexical channel only.
"""
import json
import sys
import threading
import time

from engram import vectorize

DEFAULT_BATCH = 32
MAX_BATCH = 64
PAUSE_S = 0.2                      # sleep between batches (keeps recall/remember latency low)
FAIL_LIMIT = 5                     # consecutive failed batches before the run pauses
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 8.0)
MAX_SWEEPS = 3                     # passes over rows skipped by a concurrent change
META_KEY = "reembed"
CHAR_CAP = 8000                    # what the remote adapter sends per memory at most
_PERSIST = ("state", "target", "cursor", "done", "total", "include_identity", "batch",
            "started_at", "last_error", "sweeps", "host")


class Reembedder:
    def __init__(self, engram):
        self.e = engram
        self._m = threading.Lock()              # protects self.st (never held across I/O)
        self._stop = threading.Event()
        self._closing = False                   # daemon shutdown: leave a running run "running"
        self._thread = None
        self._ctl = threading.Lock()            # serialises start/cancel/resume
        self._t0 = self._done0 = None
        self.st = self._load()

    # --- persistence (callers hold the engine lock for DB access) ---

    def _load(self):
        try:
            with self.e.lock:
                raw = self.e.cortex.meta_get(META_KEY)
            st = json.loads(raw) if raw else {}
        except Exception:
            st = {}
        out = {"state": "idle", "target": None, "cursor": 0, "done": 0, "total": 0,
               "include_identity": False, "batch": DEFAULT_BATCH, "started_at": None,
               "last_error": None, "sweeps": 0, "host": None}
        if isinstance(st, dict):
            for k in _PERSIST:
                if k in st:
                    out[k] = st[k]
        if out["state"] not in ("idle", "running", "paused", "done", "cancelled"):
            out["state"] = "idle"
        out["failures"] = 0
        return out

    def _blob(self):
        return json.dumps({k: self.st[k] for k in _PERSIST})

    def _persist(self):
        """Own transaction (state changes outside a batch). Takes the engine lock briefly."""
        with self._m:
            blob = self._blob()
        with self.e.lock:
            self.e.cortex.meta_set(META_KEY, blob)

    # --- the configured embedder ---

    @staticmethod
    def target():
        s = vectorize.status()
        state = s.get("embedder_state")
        t = {"id": None, "ready": False, "loading": state == "loading",
             "reason": s.get("embedder_reason") or state, "remote": bool(s.get("embedder_remote")),
             "host": s.get("embedder_remote_host"), "model": s.get("embedder_remote_model"),
             "configured": s.get("embedder_configured")}
        if state == "hash":
            t["id"], t["ready"], t["reason"] = vectorize.EMBEDDER_ID, True, None
        elif state == "ready":
            t["id"], t["ready"], t["reason"] = s.get("embedder_id"), True, None
        return t

    def _nudge(self):
        """The breaker's recovery probe only runs when something asks for an embedding. If the
        embedder is merely falling back, ask once (a constant string; bounded, breaker-aware, no
        network while the cooldown runs) so a healed server is noticed, then look again."""
        t = self.target()
        if not t["ready"] and not t["loading"] and t["configured"]:
            vectorize.embed_batch(["engram reembed readiness probe"])
            end = time.monotonic() + vectorize.EMBED_TIMEOUT + 0.1
            while vectorize.probing() and time.monotonic() < end:
                time.sleep(0.02)
            t = self.target()
        return t

    # --- queries ---

    @staticmethod
    def _where(include_identity):
        return ("status='active' AND (embedder_id IS NOT ? OR vector IS NULL)"
                + ("" if include_identity else " AND kind!='identity'"))

    def plan(self):
        """What a run would do (read-only; sends nothing)."""
        t = self.target()
        out = {"target": t["id"], "ready": t["ready"], "reason": t["reason"], "remote": t["remote"],
               "host": t["host"], "model": t["model"], "memories": 0, "chars": 0,
               "identity_excluded": 0, "needs_confirm": t["remote"], "item_char_cap": CHAR_CAP}
        if not t["ready"]:
            return out
        with self.e.lock:
            db = self.e.cortex.db
            for ident in (False, True):
                n, c = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(MIN(LENGTH(content), %d)), 0) FROM memories "
                    "WHERE %s" % (CHAR_CAP, self._where(ident)), (t["id"],)).fetchone()
                if not ident:
                    out["memories"], out["chars"] = n, c
                else:
                    out["identity_excluded"] = n - out["memories"]
                    out["with_identity"] = {"memories": n, "chars": c}
        return out

    def status(self):
        with self._m:
            st = dict(self.st)
            t0, d0 = self._t0, self._done0
        alive = self._thread is not None and self._thread.is_alive()
        rate = eta = None
        if alive and t0 is not None:
            el = time.monotonic() - t0
            if el > 0 and st["done"] > d0:
                rate = (st["done"] - d0) / el
                if rate > 0 and st["total"] > st["done"]:
                    eta = (st["total"] - st["done"]) / rate
        # a persisted "running" with no thread (before auto-resume) is shown as running
        return {"state": st["state"], "target": st["target"], "done": st["done"],
                "total": st["total"], "cursor": st["cursor"], "rate_per_s": None if rate is None
                else round(rate, 2), "eta_s": None if eta is None else round(eta, 1),
                "last_error": st["last_error"], "include_identity": st["include_identity"],
                "batch": st["batch"], "failures": st["failures"], "active": alive}

    # --- control ---

    def start(self, confirm=False, batch=DEFAULT_BATCH, include_identity=False):
        """-> (ok, error_or_None). Refuses unless the target is ready and, for a remote target,
        the caller passed confirm=True."""
        with self._ctl:
            return self._start(confirm, batch, include_identity)

    def _start(self, confirm, batch, include_identity):
        t = self._nudge()
        if not t["ready"]:
            return False, "embedder not ready: %s" % (t["reason"] or "unknown")
        if t["remote"] and not confirm:
            return False, "confirmation required: memory text would be sent to %s" % (
                t["host"] or "a remote host")
        batch = max(1, min(int(batch), MAX_BATCH))
        with self._m:
            if self._thread is not None and self._thread.is_alive():
                return False, "a reembed run is already in progress"
            st = self.st
            resume = (st["state"] in ("running", "paused") and st["target"] == t["id"]
                      and st.get("host") == t["host"]
                      and bool(st["include_identity"]) == bool(include_identity))
        if not resume:
            with self.e.lock:
                total = self.e.cortex.db.execute(
                    "SELECT COUNT(*) FROM memories WHERE " + self._where(include_identity),
                    (t["id"],)).fetchone()[0]
            with self._m:
                self.st.update(target=t["id"], host=t["host"], cursor=0, done=0, total=total,
                               started_at=time.time(), sweeps=0)
        with self._m:
            self.st.update(state="running", include_identity=bool(include_identity),
                           batch=batch, last_error=None, failures=0)
        self._persist()
        self._spawn()
        return True, None

    def _spawn(self):
        self._stop = threading.Event()
        with self._m:
            self._t0, self._done0 = time.monotonic(), self.st["done"]
        th = threading.Thread(target=self._run, args=(self._stop,), name="engram-reembed",
                              daemon=True)
        self._thread = th
        th.start()

    def resume_if_running(self):
        """Daemon start: a run that was `running` when the process died continues (it was
        confirmed then; the loop re-validates the target and the consent on every batch)."""
        with self._ctl:
            if self.st["state"] == "running" and self.st["target"]:
                self._spawn()

    def cancel(self):
        with self._ctl:
            self._cancel()

    def _cancel(self):
        th = self._thread
        self._stop.set()
        if th is not None and th.is_alive():
            th.join(timeout=3.0)
        with self._m:
            was = self.st["state"] in ("running", "paused")
            if was:
                self.st["state"] = "cancelled"
        if was:
            self._persist()

    def close(self):
        th = self._thread
        self._closing = True
        self._stop.set()
        if th is not None and th.is_alive():
            th.join(timeout=3.0)

    # --- the worker ---

    def _fail(self, stop, reason):
        with self._m:
            self.st["failures"] += 1
            self.st["last_error"] = reason
            n = self.st["failures"]
        if n >= FAIL_LIMIT:
            with self._m:
                self.st["state"] = "paused"
            self._persist()
            return True
        stop.wait(BACKOFF_S[min(n - 1, len(BACKOFF_S) - 1)])
        return False

    def _finish(self, state):
        with self._m:
            self.st["state"] = state
            if state == "done":
                self.st["last_error"] = None
        self._persist()

    def _run(self, stop):
        try:
            self._loop(stop)
        except Exception as e:  # never leave a half-state and never print memory text
            sys.stderr.write("engram: reembed stopped: %s\n" % type(e).__name__)
            with self._m:
                self.st.update(state="paused", last_error="internal error %s" % type(e).__name__)
            try:
                self._persist()
            except Exception:
                pass

    def _loop(self, stop):
        while not stop.is_set():
            t = self._nudge()
            with self._m:
                want, ident, batch = self.st["target"], self.st["include_identity"], self.st["batch"]
                cursor = self.st["cursor"]
            if t["loading"]:
                stop.wait(0.5)               # a background load in progress is not a failure
                continue
            if not t["ready"]:
                if self._fail(stop, "embedder not ready: %s" % (t["reason"] or "unknown")):
                    return
                continue
            if t["id"] != want:
                with self._m:
                    self.st.update(state="paused",
                                   last_error="configured embedder changed; run reembed again")
                self._persist()
                return
            with self._m:
                was_host = self.st.get("host")
            if was_host != t["host"]:
                # consent was given for another endpoint: never send there without a fresh --yes
                with self._m:
                    self.st.update(state="paused", last_error=(
                        "embedder host changed (was %s, now %s); run `mnemo reembed --yes` again "
                        "to confirm the new host" % (was_host or "local", t["host"] or "local")))
                self._persist()
                return
            with self.e.lock:
                rows = self.e.cortex.db.execute(
                    "SELECT rowid, id, content, embedder_id FROM memories WHERE rowid>? AND "
                    + self._where(ident) + " ORDER BY rowid LIMIT ?",
                    (cursor, want, batch)).fetchall()
            if not rows:
                with self.e.lock:
                    left = self.e.cortex.db.execute(
                        "SELECT COUNT(*) FROM memories WHERE rowid<=? AND " + self._where(ident),
                        (cursor, want)).fetchone()[0]
                with self._m:
                    sweeps = self.st["sweeps"]
                if left and cursor > 0 and sweeps < MAX_SWEEPS:
                    with self._m:
                        self.st.update(cursor=0, sweeps=sweeps + 1,
                                       total=self.st["done"] + left)
                    self._persist()
                    continue
                self._finish("done")
                return
            texts = [r[2] for r in rows]
            used, vecs = vectorize.embed_batch(texts, want)      # NO engine lock held
            if stop.is_set():
                continue                     # cancelled while embedding: write nothing
            if used != want or len(vecs) != len(rows):
                s = vectorize.status()
                if self._fail(stop, "embedding failed: %s" % (s.get("embedder_reason")
                                                              or "embedder fell back")):
                    return
                continue
            self._write(rows, vecs, want)
            stop.wait(PAUSE_S)
        if self._closing:
            return      # shutting down: the persisted state stays "running" and resumes next start
        with self._m:
            if self.st["state"] == "running":
                self.st["state"] = "cancelled"
        self._persist()

    def _write(self, rows, vecs, want):
        """One transaction: the vectors, the cursor and the counters (all or nothing)."""
        with self._m:
            prev = (self.st["cursor"], self.st["done"])
        applied = 0
        with self.e.lock:
            db = self.e.cortex.db
            try:
                for r, v in zip(rows, vecs):
                    cur = db.execute(
                        "UPDATE memories SET vector=?, embedder_id=? WHERE id=? AND content=? "
                        "AND embedder_id IS ?", (v, want, r[1], r[2], r[3]))
                    applied += cur.rowcount
                with self._m:
                    # `done` counts rows actually migrated; the cursor always passes the batch
                    self.st.update(cursor=rows[-1][0], done=prev[1] + applied,
                                   failures=0, last_error=None)
                    blob = self._blob()
                db.execute("INSERT INTO engram_meta (key,value) VALUES (?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (META_KEY, blob))
                db.commit()
            except BaseException:
                db.rollback()
                with self._m:       # memory stays consistent with the rolled-back transaction
                    self.st.update(cursor=prev[0], done=prev[1])
                raise

"""LLM-assisted consolidation (T27, ADR 0012). Imported only when ALETHEA_LLM_CONSOLIDATE is on.

Episode text is attacker-influenced and whatever a model returns is UNTRUSTED. The design
rules that follow from that, enforced here:
  * the ONLY way model text becomes memory is `Service.submit`: bounds, a plain-render, then
    the full encode() gate for every bullet; never `remember`, never `deliberate`, never
    identity or episodic, always stamped meta {source, distilled_from};
  * a model-written memory is not promotable (briefing, exported rules) until it has been
    reinforced by use (encode.withheld); a re-submission that the gate merges into an existing
    memory does NOT count as reinforcement (encode(reinforce=False) leaves the target untouched);
  * the daemon never calls a model itself in host mode; in api mode the call happens in the
    idle sleep cycle, outside every lock (llm_api.py);
  * an episode is marked consolidated only after a bullet was accepted or the host sent an
    explicit empty list; otherwise it stays pending and falls back to the extractive path
    when its TTL runs out.
State is kept in alethea_meta (no schema change): `llm_pending:<first journal id>` (queued
episode with expiry, listed flag, attempts) and `llm_done:<id>` (the result, for idempotent
re-submission). Both are removed with the journal rows (consolidate.purge_journal).
"""
import json
import re
import time
from collections import deque

from alethea import consolidate, encode, render
from alethea.redact import redact

PENDING = "llm_pending:"
DONE = "llm_done:"
DAY_KEY = "llm_api_day"
SOURCE_HOST, SOURCE_API = "llm-host", "llm-api"

MAX_BULLETS = 6
MAX_BULLET_CHARS = 300
QUEUE_MAX = 20                   # queued episodes; the oldest fall back to extractive
LIST_DEFAULT, LIST_MAX = 3, 5
ITEM_CHARS = 300                 # one transcript turn as shown to a model
TRANSCRIPT_CHARS = 6000          # whole transcript block shown to a host model
API_INPUT_CHARS = 12000          # whole transcript block sent to an API model
MAX_SUBMITS = 3                  # fruitless submissions before the episode goes extractive
MAX_API_ATTEMPTS = 3
RATE_WINDOW_S = 3600.0
RATE_SESSION = 10                # submits per session per hour
RATE_GLOBAL = 40                 # submits per hour, all sessions
_EID = re.compile(r"^[0-9]{1,18}$")


class Invalid(ValueError):
    """A rejected request. The message is fixed text and never echoes submitted content."""


def _loads(raw):
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except (TypeError, ValueError):
        return None


class Service:
    def __init__(self, cortex, lock, cfg, mode, adapter=None, mono=time.monotonic):
        self.cortex, self.lock, self.mode, self.adapter = cortex, lock, mode, adapter
        self.mono = mono
        self.clock = cortex.clock
        self.ttl = max(60.0, float(cfg.get("ALETHEA_LLM_PENDING_TTL") or 3 * 86400))
        self.budget = max(1, int(cfg.get("ALETHEA_LLM_BUDGET") or 5))
        self.daily_cap = max(1, int(cfg.get("ALETHEA_LLM_DAILY_CAP") or 50))
        self._sess_rate = {}
        self._global_rate = deque()

    # ---- records (callers hold the engine lock) ---------------------------------------------

    def _get(self, key):
        return _loads(self.cortex.meta_get(key))

    def _put(self, key, rec):
        self.cortex.meta_set(key, json.dumps(rec, separators=(",", ":")))

    def _del(self, *keys):
        for k in keys:
            self.cortex.db.execute("DELETE FROM alethea_meta WHERE key=?", (k,))
        self.cortex.db.commit()

    def _all_pending(self):
        """[(eid, rec)] oldest first (queued_at, then id)."""
        rows = self.cortex.db.execute(
            "SELECT key, value FROM alethea_meta WHERE key >= ? AND key < ?",
            (PENDING, PENDING[:-1] + ";")).fetchall()
        out = []
        for r in rows:
            rec = _loads(r["value"])
            if rec is not None:
                out.append((r["key"][len(PENDING):], rec))
        out.sort(key=lambda t: (t[1].get("queued_at", 0.0), len(t[0]), t[0]))
        return out

    def _live(self, now):
        return [(e, r) for e, r in self._all_pending() if r.get("expires_at", 0) > now]

    def _rows(self, rec):
        ids = [i for i in rec.get("ids", []) if isinstance(i, int)]
        if not ids:
            return []
        return self.cortex.db.execute(
            "SELECT id, role, content, created_at FROM journal WHERE consolidated=0 AND id IN "
            "(%s) ORDER BY id" % ",".join("?" * len(ids)), ids).fetchall()

    # ---- hooks used by consolidate_episodes (lock held by the caller) ---------------------

    def admit(self, cortex, ep, ids):
        """'queue' (stay pending), 'queued' (newly queued) or 'extractive' (TTL passed or
        evicted: take the normal path now)."""
        now = float(self.clock())
        eid = str(ids[0])
        rec = self._get(PENDING + eid)
        if rec is not None:
            return "queue" if rec.get("expires_at", 0) > now else "extractive"
        if self._get(DONE + eid) is not None:
            return "extractive"
        live = self._live(now)
        while len(live) >= QUEUE_MAX:  # full: the oldest falls back to extractive
            old_id, old = live.pop(0)
            old["expires_at"] = 0.0
            self._put(PENDING + old_id, old)
        self._put(PENDING + eid, {
            "queued_at": now, "expires_at": now + self.ttl, "listed_at": None, "submits": 0,
            "api_attempts": 0, "ids": list(ids), "session_id": ep.get("session_id"),
            "cwd": ep.get("cwd"), "repo_root": ep.get("repo_root"),
            "start": ep.get("start"), "end": ep.get("end")})
        return "queued"

    def release(self, cortex, tag):
        """The episode went the extractive way: forget its queue record."""
        self._del(PENDING + str(tag))

    # ---- transcript rendering ---------------------------------------------------------------

    @staticmethod
    def transcript(rows, total_cap):
        """The episode as inert data: header + one plain list item per turn. Redacted, `<`/`>`/
        backticks/`@` neutralised, leading markdown markers stripped, control and zero-width
        characters dropped, each item and the whole block size-capped; when it does not fit the
        most durable turns are kept (chronological order)."""
        items = []
        for i, r in enumerate(rows):
            text = " ".join(consolidate.prefilter((r["content"] or "")[:consolidate.MAX_TURN_CHARS]))
            text = redact(text)
            role = render.plain(r["role"] or "", 16)
            line = render.list_item((role + ": " if role else "") + text, ITEM_CHARS)
            if line:
                items.append((i, line.replace("@", "＠"), encode.durability(text[:2000])))
        budget = total_cap - len(render.TRANSCRIPT_HEADER) - 1
        keep, used = [], 0
        for i, line, d in sorted(items, key=lambda t: (-t[2], t[0])):
            if used + len(line) + 1 > budget:
                continue
            keep.append((i, line))
            used += len(line) + 1
        if not keep:
            return ""
        keep.sort()
        return render.TRANSCRIPT_HEADER + "\n" + "\n".join(l for _, l in keep)

    # ---- host mode: the pending verb ------------------------------------------------------

    def pending(self, n=LIST_DEFAULT):
        now = float(self.clock())
        n = max(1, min(int(n), LIST_MAX))
        chosen = []
        with self.lock:
            for eid, rec in self._live(now):
                if len(chosen) >= n:
                    break
                rows = self._rows(rec)
                if not rows:
                    self._del(PENDING + eid)  # nothing left to distil
                    continue
                rec["listed_at"] = now
                self._put(PENDING + eid, rec)
                chosen.append((eid, rec, rows))
        eps = []
        for eid, rec, rows in chosen:  # rendering (redaction) happens outside the lock
            text = self.transcript(rows, TRANSCRIPT_CHARS)
            eps.append({"id": eid, "session_id": render.plain(rec.get("session_id") or "", 64),
                        "start": rec.get("start"), "end": rec.get("end"), "turns": len(rows),
                        "expires_in_s": max(0, int(rec["expires_at"] - now)),
                        "text": text})
        return {"ok": True, "episodes": eps, "count": len(eps), "mode": self.mode,
                "max_bullets": MAX_BULLETS, "max_bullet_chars": MAX_BULLET_CHARS}

    # ---- the only door: submit ----------------------------------------------------------------

    @staticmethod
    def validate(bullets):
        """-> list of plain bullet strings (deduplicated). Raises Invalid. Whole-request: one bad
        bullet rejects the submission, nothing is written."""
        if not isinstance(bullets, list):
            raise Invalid("bullets must be a list of strings")
        if len(bullets) > MAX_BULLETS:
            raise Invalid("too many bullets (max %d)" % MAX_BULLETS)
        out, seen = [], set()
        for i, b in enumerate(bullets):
            if not isinstance(b, str) or not b.strip():
                raise Invalid("bullet %d must be a non-empty string" % i)
            if len(b) > MAX_BULLET_CHARS:
                raise Invalid("bullet %d too long (max %d chars)" % (i, MAX_BULLET_CHARS))
            if not b.isprintable():
                raise Invalid("bullet %d contains control or invisible characters" % i)
            p = render.plain(b, MAX_BULLET_CHARS)
            if not p:
                raise Invalid("bullet %d is empty after rendering" % i)
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        return out

    def _rate_ok(self, session, now):
        """Record one submission and say whether it fits the per-session and global hourly caps."""
        while self._global_rate and now - self._global_rate[0] > RATE_WINDOW_S:
            self._global_rate.popleft()
        q = self._sess_rate.setdefault(session or "", deque())
        while q and now - q[0] > RATE_WINDOW_S:
            q.popleft()
        if len(self._sess_rate) > 256:  # bounded table
            for k in [k for k, v in self._sess_rate.items() if not v][:128]:
                del self._sess_rate[k]
        if len(q) >= RATE_SESSION or len(self._global_rate) >= RATE_GLOBAL:
            return False
        q.append(now)
        self._global_rate.append(now)
        return True

    def submit(self, episode_id, bullets, source=SOURCE_HOST):
        """Validate and apply. Returns the result dict; raises Invalid with a fixed message.
        Idempotent per episode once it has been consolidated."""
        if not isinstance(episode_id, str) or not _EID.match(episode_id):
            raise Invalid("episode_id must be an id returned by pending")
        host = source == SOURCE_HOST
        with self.lock:
            done = self._get(DONE + episode_id)
            if done is not None:
                return dict(done, replayed=True)
            rec = self._get(PENDING + episode_id)
        now = float(self.clock())
        if rec is None or rec.get("expires_at", 0) <= now:
            raise Invalid("unknown, expired or already consolidated episode")
        if host and not rec.get("listed_at"):
            raise Invalid("episode was not listed by pending")
        texts = self.validate(bullets)
        if host:
            with self.lock:
                ok = self._rate_ok(rec.get("session_id"), self.mono())
            if not ok:
                raise Invalid("rate limit reached; try again later")
        pre = [encode.embed_for(t) for t in texts]  # embedder calls stay outside the lock
        with self.lock:
            done = self._get(DONE + episode_id)  # a concurrent submit may have finished first
            if done is not None:
                return dict(done, replayed=True)
            rec = self._get(PENDING + episode_id)
            if rec is None or rec.get("expires_at", 0) <= now:
                raise Invalid("unknown, expired or already consolidated episode")
            res = self._apply(episode_id, rec, texts, pre, source, now)
        return res

    def _apply(self, eid, rec, texts, pre, source, now):
        c = self.cortex
        created, merged, rejected, mids = 0, 0, [], []
        for i, (t, emb) in enumerate(zip(texts, pre)):
            kind = "procedural" if encode.classify(t) == "procedural" else "semantic"
            r = encode.encode(
                c, t, kind=kind, embedded=emb, session_id=rec.get("session_id"),
                cwd=rec.get("cwd"), repo_root=rec.get("repo_root"),
                meta={"source": source, "distilled_from": eid}, reinforce=False)
            st = r["status"]
            if st == "created":
                created += 1
                mids.append(r["id"])
            elif st == "reinforced":
                merged += 1
                mids.append(r["id"])
            else:
                rejected.append({"index": i, "reason": str(r.get("reason") or "rejected")[:24]})
        accepted = created + merged
        final = accepted > 0 or not texts
        out = {"ok": True, "episode_id": eid, "accepted": accepted, "created": created,
               "merged": merged, "rejected": rejected, "ids": mids, "source": source,
               "consolidated": final}
        if final:
            c.journal_mark_consolidated(self._rows_ids(rec))
            self._put(DONE + eid, out)
            self._del(PENDING + eid)
        else:
            rec["submits"] = int(rec.get("submits", 0)) + 1
            if rec["submits"] >= MAX_SUBMITS:
                rec["expires_at"] = 0.0  # fruitless: the extractive path takes over
            self._put(PENDING + eid, rec)
        return out

    def _rows_ids(self, rec):
        return [r["id"] for r in self._rows(rec)]

    # ---- api mode: runs in the idle sleep cycle, never under a lock ----------------------

    def _reserve_call(self, now):
        """Daily cap, persisted so a restart cannot reset it. Lock held."""
        day = time.strftime("%Y%m%d", time.gmtime(now))
        d, _, n = (self.cortex.meta_get(DAY_KEY) or "").partition(":")
        n = int(n) if n.isdigit() and d == day else 0
        if n >= self.daily_cap:
            return False
        self.cortex.meta_set(DAY_KEY, "%s:%d" % (day, n + 1))
        return True

    def calls_today(self):
        day = time.strftime("%Y%m%d", time.gmtime(float(self.clock())))
        d, _, n = (self.cortex.meta_get(DAY_KEY) or "").partition(":")
        return int(n) if n.isdigit() and d == day else 0

    def _api_failed(self, eid, kind, now):
        rec = self._get(PENDING + eid)
        if rec is None:
            return
        rec["api_attempts"] = int(rec.get("api_attempts", 0)) + 1
        rec["last_error"] = str(kind)[:24]
        rec["next_try"] = now + 300.0 * rec["api_attempts"]
        if rec["api_attempts"] >= MAX_API_ATTEMPTS:
            rec["expires_at"] = 0.0  # extractive fallback
        self._put(PENDING + eid, rec)

    def api_cycle(self, should_stop=None):
        """Distil up to `budget` queued episodes through the adapter. Called only from the idle
        sleep cycle. Every failure leaves the episode queued (and eventually extractive)."""
        out = {"attempted": 0, "accepted": 0, "failed": 0, "skipped": None, "stopped": False}
        ad = self.adapter
        if ad is None:
            return out
        now = float(self.clock())
        with self.lock:
            todo = [(e, r) for e, r in self._live(now) if r.get("next_try", 0) <= now]
        for eid, rec in todo:
            if out["attempted"] >= self.budget:
                break
            if should_stop is not None and should_stop():
                out["stopped"] = True
                break
            if not ad.breaker.allow():
                out["skipped"] = "breaker"
                break
            with self.lock:
                if not self._reserve_call(now):
                    out["skipped"] = "daily_cap"
                    break
                rows = self._rows(rec)
                if not rows:
                    self._del(PENDING + eid)
                    continue
            text = self.transcript(rows, API_INPUT_CHARS)  # redacted, inert, capped
            out["attempted"] += 1
            if not text:
                continue
            try:
                bullets = ad.distill(text)  # network I/O: no lock held, bounded by a timeout
                res = self.submit(eid, bullets, SOURCE_API)
            except Exception as e:
                kind = getattr(e, "kind", None) or ("rejected" if isinstance(e, Invalid)
                                                    else "error")
                with self.lock:
                    self._api_failed(eid, kind, now)
                out["failed"] += 1
                continue
            if res.get("consolidated"):
                out["accepted"] += 1
            else:
                with self.lock:
                    self._api_failed(eid, "nothing_accepted", now)
                out["failed"] += 1
        return out

    # ---- status ---------------------------------------------------------------------------------

    def status(self):
        """Caller holds the lock (cheap queries only)."""
        now = float(self.clock())
        pend = self._all_pending()
        queued = [r for _, r in pend if r.get("expires_at", 0) > now]
        oldest = min((r.get("queued_at", now) for r in queued), default=None)
        d = {"mode": self.mode, "queue_length": len(queued),
             "oldest_pending_age_s": None if oldest is None else max(0, int(now - oldest)),
             "ttl_s": int(self.ttl)}
        if self.adapter is not None:
            ad = self.adapter
            d.update({"provider": ad.provider, "host": ad.host,
                      "notice": "transcripts are sent to %s" % ad.host,
                      "breaker": ad.breaker.state(), "calls_today": self.calls_today(),
                      "daily_cap": self.daily_cap, "budget_per_cycle": self.budget})
        return d

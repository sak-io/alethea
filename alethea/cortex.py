"""Persistent layer: SQLite schema, storage API, edges, journal, sessions, meta.

One connection; the caller holds the lock. No activation logic here.
"""
import json
import math
import os
import sqlite3
import time
import uuid
from pathlib import Path

from engram.config import SCHEMA_VERSION, ensure_private_dir

KINDS = ("episodic", "semantic", "procedural", "identity")
DECAY = {"episodic": 0.5, "semantic": 0.2, "procedural": 0.1, "identity": 0.0}
STATUSES = ("active", "archived", "suppressed")
EDGE_STEP = 0.15
ARCHIVE_ACTIVATION = -3.2
DAY_S = 86400.0
MIN_AGE_DAYS = 1.0 / 1440  # 1-minute floor
MIN_STRENGTH = 1e-3
DOWNSCALE_FACTOR = 0.93
# Downscale periods per day, per kind (ADR 0003 item 4: d_kind/0.5). Uniform option: all 1.0.
DOWNSCALE_RATE = {"episodic": 1.0, "semantic": 0.4, "procedural": 0.2, "identity": 0.0}
UNIFORM_RATE = {"episodic": 1.0, "semantic": 1.0, "procedural": 1.0, "identity": 0.0}
RECENCY_WEIGHT = 0.3
RECENCY_TAU_DAYS = 7.0


def _finite(x, default):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def base_activation(kind, strength, salience, age_days, since_access_days=None):
    """ADR 0003: B = ln(n_eff/(1-d)) - d*ln(age) + 1.6*(s-0.5) + recency; n_eff = strength.

    Always finite: NaN/inf/negative inputs fall back to floors/defaults.
    Recency term: RECENCY_WEIGHT * exp(-days_since_access / RECENCY_TAU_DAYS) = 0.3*exp(-days/7).
    """
    if kind not in DECAY:
        raise ValueError("unknown kind: %r" % (kind,))
    d = DECAY[kind]
    n = max(_finite(strength, MIN_STRENGTH), MIN_STRENGTH)
    age = max(_finite(age_days, MIN_AGE_DAYS), MIN_AGE_DAYS)
    b = math.log(n / (1.0 - d)) - (d * math.log(age) if d else 0.0)
    b += 1.6 * (min(max(_finite(salience, 0.5), 0.0), 1.0) - 0.5)
    since = max(_finite(since_access_days, age), 0.0)
    b += RECENCY_WEIGHT * math.exp(-since / RECENCY_TAU_DAYS)
    return b

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL,
  gist TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '',
  salience REAL NOT NULL DEFAULT 0.5, strength REAL NOT NULL DEFAULT 1.0,
  access_count INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
  last_access REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  session_id TEXT, cwd TEXT, repo_root TEXT, vector BLOB,
  embedder_id TEXT, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_mem_repo ON memories(repo_root);
CREATE INDEX IF NOT EXISTS idx_mem_kind_status ON memories(kind, status);
CREATE INDEX IF NOT EXISTS idx_mem_vec ON memories(embedder_id, status, last_access);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  weight REAL NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (src, dst, kind)
);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
  content TEXT NOT NULL, cwd TEXT, repo_root TEXT, created_at REAL NOT NULL,
  consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, cwd TEXT, repo_root TEXT,
  started_at REAL NOT NULL, ended_at REAL, meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS engram_meta (key TEXT PRIMARY KEY, value TEXT);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
  content, gist, keywords, content='memories', content_rowid='rowid',
  tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
  INSERT INTO mem_fts(rowid, content, gist, keywords)
  VALUES (new.rowid, new.content, new.gist, new.keywords);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
  INSERT INTO mem_fts(mem_fts, rowid, content, gist, keywords)
  VALUES ('delete', old.rowid, old.content, old.gist, old.keywords);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
  INSERT INTO mem_fts(mem_fts, rowid, content, gist, keywords)
  VALUES ('delete', old.rowid, old.content, old.gist, old.keywords);
  INSERT INTO mem_fts(rowid, content, gist, keywords)
  VALUES (new.rowid, new.content, new.gist, new.keywords);
END;
"""

_COLS = ("id kind content gist keywords salience strength access_count created_at "
         "last_access status session_id cwd repo_root vector embedder_id meta").split()


class SchemaVersionError(RuntimeError):
    """The database was written by a newer Engram; refuse to open it."""


class Cortex:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        on_disk = str(path) != ":memory:"
        if on_disk:  # bare ':memory:' has no directory/file to protect
            path = Path(path)
            ensure_private_dir(path.parent)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        try:
            stored = int(self.meta_get("schema_version") or 0)
        except ValueError:
            stored = 0
        if stored > SCHEMA_VERSION:
            self.db.close()
            raise SchemaVersionError(
                "database %s has schema version %d, newer than this Engram supports (%d); "
                "upgrade Engram or use a different ENGRAM_HOME" % (path, stored, SCHEMA_VERSION))
        self.fts_available = True
        try:
            self.db.executescript(_FTS)
        except sqlite3.OperationalError:
            self.fts_available = False  # G17: degrade, caller falls back to LIKE/vector
        if self.meta_get("schema_version") is None:
            self.meta_set("schema_version", str(SCHEMA_VERSION))
        self.db.commit()
        if on_disk:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.chmod(str(path) + suffix, 0o600)
                except OSError:
                    pass

    @property
    def schema_version(self):
        return int(self.meta_get("schema_version") or 0)

    def close(self):
        self.db.close()

    # memories
    def insert(self, kind, content, gist="", keywords="", salience=0.5, strength=1.0,
               session_id=None, cwd=None, repo_root=None, vector=None,
               embedder_id=None, meta=None, id=None):
        if kind not in KINDS:
            raise ValueError("unknown kind: %r" % (kind,))
        now = self.clock()
        mid = id or uuid.uuid4().hex
        if isinstance(keywords, (list, tuple)):
            keywords = " ".join(keywords)
        self.db.execute(
            "INSERT INTO memories (id,kind,content,gist,keywords,salience,strength,"
            "access_count,created_at,last_access,status,session_id,cwd,repo_root,"
            "vector,embedder_id,meta) VALUES (?,?,?,?,?,?,?,1,?,?,'active',?,?,?,?,?,?)",
            (mid, kind, content, gist, keywords, salience, strength, now, now,
             session_id, cwd, repo_root, vector, embedder_id, json.dumps(meta or {})))
        self.db.commit()
        return mid

    def get(self, mid):
        r = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        return d

    def get_many(self, mids):
        """{id: memory dict} for the ids that exist (one query per 500 ids)."""
        mids = list(mids)
        out = {}
        for i in range(0, len(mids), 500):
            part = mids[i:i + 500]
            for r in self.db.execute("SELECT * FROM memories WHERE id IN (%s)"
                                     % ",".join("?" * len(part)), part):
                d = dict(r)
                d["meta"] = json.loads(d["meta"] or "{}")
                out[d["id"]] = d
        return out

    def reinforce(self, mid, amount=1.0):
        """Bump strength and access_count together; refresh last_access."""
        cur = self.db.execute(
            "UPDATE memories SET strength=strength+?, access_count=access_count+1, "
            "last_access=? WHERE id=?", (amount, self.clock(), mid))
        self.db.commit()
        return cur.rowcount > 0

    def activation(self, mem, now=None):
        """Activation of a memory (dict or id) at `now` (default: injected clock)."""
        if isinstance(mem, str):
            mem = self.get(mem)
            if mem is None:
                raise KeyError("no such memory")
        now = _finite(self.clock() if now is None else now, 0.0)
        created = _finite(mem["created_at"], now)
        last = _finite(mem["last_access"], created)
        age = max(now - created, 0.0) / DAY_S
        since = max(now - last, 0.0) / DAY_S
        return base_activation(mem["kind"], mem["strength"], mem["salience"], age, since)

    def set_status(self, mid, status):
        if status not in STATUSES:
            raise ValueError("unknown status: %r" % (status,))
        cur = self.db.execute("UPDATE memories SET status=? WHERE id=?", (status, mid))
        self.db.commit()
        return cur.rowcount > 0

    def update(self, mid, **fields):
        bad = set(fields) - set(_COLS) - {"id"}
        if bad or not fields:
            raise ValueError("bad fields: %r" % sorted(bad))
        if "meta" in fields and not isinstance(fields["meta"], str):
            fields["meta"] = json.dumps(fields["meta"])
        sets = ", ".join("%s=?" % k for k in fields)
        self.db.execute("UPDATE memories SET %s WHERE id=?" % sets,
                        (*fields.values(), mid))
        self.db.commit()

    def delete(self, mid):
        self.db.execute("DELETE FROM memories WHERE id=?", (mid,))
        self.db.commit()

    def search_fts(self, query, limit=20, recent=None):
        """Ids by FTS rank. `recent=N` ranks only the N newest rows (rowid window): bm25
        is computed for every matching row, so on a large, word-overlapping corpus the
        unbounded query is O(n); the window bounds it. Exact when the table has <= N rows."""
        if not self.fts_available:
            return []
        sql = ("SELECT m.id FROM mem_fts JOIN memories m ON m.rowid=mem_fts.rowid "
               "WHERE mem_fts MATCH ?")
        args = [query]
        if recent is not None:
            top = self.db.execute("SELECT MAX(rowid) FROM memories").fetchone()[0] or 0
            sql += " AND mem_fts.rowid > ?"
            args.append(top - max(1, int(recent)))
        try:
            rows = self.db.execute(sql + " ORDER BY rank LIMIT ?", args + [limit]).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["id"] for r in rows]

    # edges
    def link(self, src, dst, kind="assoc"):
        now = self.clock()
        self.db.execute(
            "INSERT INTO edges (src,dst,kind,weight,created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(src,dst,kind) DO UPDATE SET weight=MIN(1.0, weight+?)",
            (src, dst, kind, EDGE_STEP, now, EDGE_STEP))
        self.db.commit()
        return self.edge_weight(src, dst, kind)

    def edge_weight(self, src, dst, kind="assoc"):
        r = self.db.execute("SELECT weight FROM edges WHERE src=? AND dst=? AND kind=?",
                            (src, dst, kind)).fetchone()
        return r["weight"] if r else None

    def edges_from(self, src):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM edges WHERE src=? ORDER BY weight DESC", (src,))]

    # journal
    def journal_add(self, content, session_id=None, role=None, cwd=None, repo_root=None):
        cur = self.db.execute(
            "INSERT INTO journal (session_id,role,content,cwd,repo_root,created_at) "
            "VALUES (?,?,?,?,?,?)", (session_id, role, content, cwd, repo_root, self.clock()))
        self.db.commit()
        return cur.lastrowid

    def journal_pending(self, limit=None):
        sql = "SELECT * FROM journal WHERE consolidated=0 ORDER BY id"
        args = ()
        if limit:
            sql += " LIMIT ?"
            args = (limit,)
        return [dict(r) for r in self.db.execute(sql, args)]

    def journal_mark_consolidated(self, ids):
        ids = list(ids)
        if ids:
            self.db.execute("UPDATE journal SET consolidated=1 WHERE id IN (%s)"
                            % ",".join("?" * len(ids)), ids)
            self.db.commit()

    # sessions
    def session_start(self, session_id, cwd=None, repo_root=None):
        self.db.execute("INSERT OR IGNORE INTO sessions (id,cwd,repo_root,started_at) "
                        "VALUES (?,?,?,?)", (session_id, cwd, repo_root, self.clock()))
        self.db.commit()

    def session_end(self, session_id):
        self.db.execute("UPDATE sessions SET ended_at=? WHERE id=?",
                        (self.clock(), session_id))
        self.db.commit()

    def session_get(self, session_id):
        r = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(r) if r else None

    # meta
    def meta_get(self, key, default=None):
        r = self.db.execute("SELECT value FROM engram_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def meta_set(self, key, value):
        self.db.execute("INSERT INTO engram_meta (key,value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, str(value)))
        self.db.commit()

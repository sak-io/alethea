import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from alethea.config import SCHEMA_VERSION
from alethea.cortex import Cortex, KINDS, DECAY, SchemaVersionError


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class CortexTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Cortex(":memory:", clock=self.clock)
        self.addCleanup(self.c.close)

    def test_constants(self):
        self.assertEqual(set(KINDS), set(DECAY))
        self.assertEqual(DECAY["identity"], 0.0)
        self.assertEqual((DECAY["episodic"], DECAY["semantic"], DECAY["procedural"]),
                         (0.5, 0.2, 0.1))

    def test_insert_get_roundtrip_and_null_repo_root(self):
        mid = self.c.insert("semantic", "use sqlite", meta={"a": 1}, vector=b"\x00\x01")
        m = self.c.get(mid)
        self.assertEqual(m["content"], "use sqlite")
        self.assertIsNone(m["repo_root"])
        self.assertEqual(m["meta"], {"a": 1})
        self.assertEqual(m["access_count"], 1)
        self.assertEqual(m["created_at"], 1000.0)
        self.assertIsNone(self.c.get("nope"))

    def test_bad_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.c.insert("bogus", "x")

    def test_repo_root_indexed(self):
        names = [r[1] for r in self.c.db.execute("PRAGMA index_list(memories)")]
        self.assertIn("idx_mem_repo", names)

    def test_reinforce_bumps_strength_and_count(self):
        mid = self.c.insert("semantic", "x", strength=1.0)
        self.clock.t = 2000.0
        self.assertTrue(self.c.reinforce(mid, 0.5))
        m = self.c.get(mid)
        self.assertEqual(m["strength"], 1.5)
        self.assertEqual(m["access_count"], 2)
        self.assertEqual(m["last_access"], 2000.0)
        self.assertFalse(self.c.reinforce("missing"))

    def test_link_upserts_and_caps(self):
        a = self.c.insert("semantic", "a")
        b = self.c.insert("semantic", "b")
        self.assertAlmostEqual(self.c.link(a, b), 0.15)
        self.assertAlmostEqual(self.c.link(a, b), 0.30)
        for _ in range(10):
            w = self.c.link(a, b)
        self.assertEqual(w, 1.0)
        self.assertEqual(len(self.c.edges_from(a)), 1)
        self.assertIsNone(self.c.edge_weight(b, a))

    def test_journal_flow(self):
        i = self.c.journal_add("hello", session_id="s", role="user")
        self.c.journal_add("bye")
        self.assertEqual(len(self.c.journal_pending()), 2)
        self.c.journal_mark_consolidated([i])
        self.assertEqual([r["content"] for r in self.c.journal_pending()], ["bye"])

    def test_sessions(self):
        self.c.session_start("s1", cwd="/x")
        self.clock.t = 1500.0
        self.c.session_end("s1")
        s = self.c.session_get("s1")
        self.assertEqual((s["started_at"], s["ended_at"]), (1000.0, 1500.0))

    def test_meta_and_schema_version(self):
        self.assertEqual(self.c.schema_version, 1)
        self.assertIsNone(self.c.meta_get("k"))
        self.c.meta_set("k", "v1")
        self.c.meta_set("k", "v2")
        self.assertEqual(self.c.meta_get("k"), "v2")


class FtsTest(unittest.TestCase):
    def setUp(self):
        self.c = Cortex(":memory:")
        self.addCleanup(self.c.close)
        if not self.c.fts_available:
            self.skipTest("FTS5 missing")

    def test_insert_update_delete_sync(self):
        mid = self.c.insert("semantic", "postgres migrations", gist="db", keywords=["alembic"])
        self.assertEqual(self.c.search_fts("migration"), [mid])  # porter stemming
        self.assertEqual(self.c.search_fts("alembic"), [mid])
        self.c.update(mid, content="redis caching")
        self.assertEqual(self.c.search_fts("postgres"), [])
        self.assertEqual(self.c.search_fts("caching"), [mid])
        self.c.delete(mid)
        self.assertEqual(self.c.search_fts("redis"), [])

    def test_recent_window_limits_fts_and_is_exact_when_not_hit(self):
        ids = [self.c.insert("semantic", "shared words item%d" % i) for i in range(30)]
        every = self.c.search_fts("shared", limit=100)
        self.assertEqual(sorted(every), sorted(ids))
        self.assertEqual(self.c.search_fts("shared", limit=100, recent=30), every)  # cap not hit
        self.assertEqual(self.c.search_fts("shared", limit=100, recent=100), every)
        self.assertEqual(sorted(self.c.search_fts("shared", limit=100, recent=10)), sorted(ids[-10:]))
        self.assertEqual(self.c.search_fts("shared", limit=100, recent=0), [ids[-1]])  # clamped to 1

    def test_reinforce_does_not_break_fts(self):
        mid = self.c.insert("semantic", "alpha beta")
        self.c.reinforce(mid)
        self.assertEqual(self.c.search_fts("alpha"), [mid])

    def test_malformed_query_returns_empty(self):
        self.assertEqual(self.c.search_fts('"unbalanced'), [])


class DegradeTest(unittest.TestCase):
    def test_fts_missing_degrades(self):
        import alethea.cortex as cx
        orig = cx._FTS
        cx._FTS = "CREATE VIRTUAL TABLE t USING nosuchmodule(x);"
        try:
            c = Cortex(":memory:")
        finally:
            cx._FTS = orig
        self.addCleanup(c.close)
        self.assertFalse(c.fts_available)
        mid = c.insert("semantic", "still works")
        self.assertEqual(c.get(mid)["content"], "still works")
        self.assertEqual(c.search_fts("still"), [])


class FileTest(unittest.TestCase):
    def test_wal_and_private_dir_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "home" / "alethea.db"
            c = Cortex(p)
            self.assertEqual(c.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(stat.S_IMODE(os.stat(p.parent).st_mode), 0o700)
            self.assertEqual(c.db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            for suffix in ("", "-wal", "-shm"):
                f = str(p) + suffix
                if os.path.exists(f):
                    self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600, f)
            mid = c.insert("identity", "I am Ann")
            self.assertTrue(c.set_status(mid, "archived"))
            self.assertFalse(c.set_status("missing", "archived"))
            c.close()
            c2 = Cortex(p)
            self.assertEqual(c2.get(mid)["content"], "I am Ann")
            c2.close()


if __name__ == "__main__":
    unittest.main()


class SchemaVersionTest(unittest.TestCase):
    def test_newer_schema_is_refused_and_older_or_equal_opens(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "alethea.db")
            c = Cortex(path)
            c.meta_set("schema_version", str(SCHEMA_VERSION + 1))
            c.db.commit()
            c.close()
            with self.assertRaises(SchemaVersionError) as cm:
                Cortex(path)
            self.assertIn("newer", str(cm.exception))

            db = sqlite3.connect(path)
            db.execute("UPDATE alethea_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            db.commit()
            db.close()
            Cortex(path).close()

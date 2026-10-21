import os
import sqlite3
import subprocess
import sys
import unittest

from engram import vectorize as v

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = "We use Postgres for the billing database"
SNIPPET = ("from engram import vectorize as v; import sys; "
           "sys.stdout.write(v.embed(%r).hex())" % TEXT)


class TestVectorize(unittest.TestCase):
    def test_embedder_identity_and_shape(self):
        e = v.HashedNgramEmbedder()
        self.assertEqual(e.id, "hashed-ngram-v1:512")
        self.assertEqual(e.dim, 512)
        self.assertEqual(len(e.embed("hello world")), 512 * 4)

    def test_l2_normalized(self):
        vec = v.from_bytes(v.embed("the quick brown fox"))
        self.assertAlmostEqual(sum(x * x for x in vec), 1.0, places=4)

    def test_empty_text_zero_vector(self):
        self.assertEqual(v.cosine(v.embed(""), v.embed("abc def")), 0.0)

    def test_deterministic_across_hashseeds(self):
        outs = set()
        for seed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=ROOT)
            r = subprocess.run([sys.executable, "-c", SNIPPET], env=env,
                               capture_output=True, text=True, cwd=ROOT, check=True)
            outs.add(r.stdout)
        self.assertEqual(outs, {v.embed(TEXT).hex()})

    def test_related_vs_unrelated(self):
        a = "the deploy script restarts the postgres database nightly"
        rel = "nightly the database restart script deploys postgres"
        unrel = "my cat enjoys sleeping on sunny windowsills"
        r = v.cosine(v.embed(a), v.embed(rel))
        u = v.cosine(v.embed(a), v.embed(unrel))
        self.assertGreater(r, 0.3)
        self.assertLess(abs(u), 0.1)
        m = v.cosine(v.embed("we always run database migrations before deploying the service"),
                     v.embed("database migration must run before the service deploys"))
        self.assertTrue(0.3 <= m <= 0.75, m)

    def test_identical_is_one(self):
        self.assertAlmostEqual(v.cosine(v.embed("same text"), v.embed("same text")), 1.0, places=4)

    def test_tokenizer_no_length_floor(self):
        self.assertEqual(v.tokenize("Use v2 of X, a/b"), ["use", "v2", "of", "x", "a", "b"])

    def test_jaccard(self):
        self.assertEqual(v.jaccard("a b c", "a b c"), 1.0)
        self.assertEqual(v.jaccard("", ""), 0.0)
        self.assertAlmostEqual(v.jaccard("cats run", "cat runs fast"), 2 / 3)

    def test_cosine_length_mismatch_safe(self):
        self.assertEqual(v.cosine([1.0], [1.0, 0.0]), 0.0)

    def test_stem_basics(self):
        self.assertEqual(v.stem("tests"), v.stem("test"))
        self.assertEqual(v.stem("Is"), "is")
        self.assertEqual(v.stem("class"), "class")


GROUPS = [
    ["connect", "connected", "connecting", "connects", "connection", "connections"],
    ["test", "tests", "tested", "testing"],
    ["deploy", "deploys", "deployed", "deploying", "deployment"],
    ["query", "queries", "queried"],
    ["configure", "configured", "configuring", "configuration"],
    ["migrate", "migrates", "migrated", "migrating", "migration"],
    ["cache", "caches", "cached", "caching"],
    ["restart", "restarts", "restarted", "restarting"],
]


class TestStemVsPorter(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        try:
            self.db.execute("create virtual table t using fts5(x, tokenize='porter unicode61')")
        except sqlite3.OperationalError:
            self.skipTest("FTS5 unavailable")

    def fts_match(self, a, b):
        self.db.execute("delete from t")
        self.db.execute("insert into t values (?)", (a,))
        return bool(self.db.execute("select 1 from t where t match ?", ('"%s"' % b,)).fetchone())

    def test_agrees_with_porter_on_fixture(self):
        misses = []
        for g in GROUPS:
            for a in g:
                for b in g:
                    if self.fts_match(a, b) and v.stem(a) != v.stem(b):
                        misses.append((a, b, v.stem(a), v.stem(b)))
        self.assertEqual(misses, [])

    def test_no_false_conflation_across_groups(self):
        for i, g in enumerate(GROUPS):
            for h in GROUPS[i + 1:]:
                self.assertNotEqual(v.stem(g[0]), v.stem(h[0]))


if __name__ == "__main__":
    unittest.main()

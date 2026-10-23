"""Embedder plugin: registry, lazy loading, fail-soft, thresholds, dimensions (T25, ADR 0010)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from engram import consolidate, daemon, embedders_ext, recall, vectorize
from engram import encode as enc
from engram.cortex import Cortex
from tests import fake_dense
from tests.daemon_helpers import ROOT, make_home
from tests.embedder_helpers import MINILM, StubTextEmbedding, activated

SECRET = "TOPSECRET-MEMORY-TEXT-4711"


def run_py(code, env=None, timeout=60):
    """Run `code` in an isolated child (-I) with the repo root on sys.path; returns stdout."""
    e = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    e.pop("ENGRAM_EMBEDDER", None)
    e.update(env or {})
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (ROOT, code)],
                       capture_output=True, text=True, env=e, timeout=timeout)
    return r


class Isolated(unittest.TestCase):
    def setUp(self):
        vectorize._reset_for_tests()
        self.addCleanup(vectorize._reset_for_tests)

    def err(self):
        return contextlib.redirect_stderr(io.StringIO())


class Broken(vectorize.Embedder):
    """Configurable misbehaving embedder."""

    def __init__(self, behaviour, dim=384):
        self.dim, self.id, self.behaviour = dim, "broken:%d" % dim, behaviour

    def embed(self, text):
        b = self.behaviour
        if b == "raise":
            raise RuntimeError("model exploded on " + text)  # message echoes the memory text
        if b == "wrongdim":
            return [0.1] * (self.dim + 1)
        if b == "nan":
            return [float("nan")] + [0.1] * (self.dim - 1)
        if b == "inf":
            return [float("inf")] + [0.1] * (self.dim - 1)
        if b == "zero":
            return [0.0] * self.dim
        if b == "garbage":
            return "not a vector"
        if b == "bytes_short":
            return b"\x00\x01\x02"
        return fake_dense.fake_vector(text, self.dim)


class SpecParsingTest(Isolated):
    def test_values(self):
        self.assertEqual(vectorize.parse_spec(None), ("hash", ""))
        self.assertEqual(vectorize.parse_spec(" HASH "), ("hash", ""))
        self.assertEqual(vectorize.parse_spec(MINILM), ("fastembed", "sentence-transformers/all-MiniLM-L6-v2"))
        self.assertEqual(vectorize.parse_spec("FastEmbed:Some/Model")[1], "Some/Model")

    def test_unknown_values_raise(self):
        for bad in ("bert", "openai", "openai:text-embedding-3-small", "fastembed", "fastembed:", ":x"):
            with self.subTest(bad=bad), self.assertRaises(vectorize.UnknownEmbedder):
                vectorize.parse_spec(bad)

    def test_register_scheme_hook(self):
        vectorize.register_scheme("Remote", lambda arg: Broken("ok"))
        self.assertEqual(vectorize.parse_spec("remote:m"), ("remote", "m"))

    def test_unknown_embedder_falls_back_with_one_line_and_never_raises(self):
        with self.err() as buf:
            vectorize.configure("openai:text-embedding-3-small")
            for _ in range(5):
                eid, (v,) = vectorize.embed_batch(["hello world"])
        self.assertEqual(eid, vectorize.EMBEDDER_ID)
        self.assertEqual(v, vectorize.embed("hello world"))
        self.assertEqual(buf.getvalue().count("\n"), 1)
        self.assertIn("unknown", buf.getvalue())
        st = vectorize.status()
        self.assertTrue(st["embedder_falling_back"])
        self.assertEqual(st["embedder_fallbacks"], 5)


class FailSoftTest(Isolated):
    def use(self, behaviour, dim=384):
        inst = Broken(behaviour, dim)
        vectorize.register_scheme("broken", lambda arg: inst)
        vectorize.configure("broken:x")
        return inst

    def test_each_failure_kind_falls_back_logs_once_and_hides_text(self):
        kinds = {"raise": "error", "wrongdim": "dimension", "nan": "nonfinite", "inf": "nonfinite",
                 "zero": "nonfinite", "garbage": "error", "bytes_short": "dimension"}
        for behaviour, kind in kinds.items():
            with self.subTest(behaviour=behaviour):
                vectorize._reset_for_tests()
                self.use(behaviour)
                with self.err() as buf:
                    for i in range(4):
                        eid, (v,) = vectorize.embed_batch([SECRET + str(i)])
                        self.assertEqual(eid, vectorize.EMBEDDER_ID)
                        self.assertEqual(v, vectorize.embed(SECRET + str(i)))
                out = buf.getvalue()
                self.assertEqual(out.count("\n"), 1, out)
                self.assertIn("(%s" % kind, out)
                self.assertNotIn(SECRET, out)
                self.assertNotIn("exploded", out)
                st = vectorize.status()
                self.assertEqual(st["embedder_fallbacks"], 4)
                self.assertTrue(st["embedder_falling_back"])
                self.assertEqual(st["embedder_id"], vectorize.EMBEDDER_ID)

    def test_one_line_per_kind_not_per_call(self):
        self.use("nan")
        with self.err() as buf:
            vectorize.embed_batch(["a b c"])
            vectorize.embed_batch(["d e f"])
        self.assertEqual(buf.getvalue().count("\n"), 1)
        inst = vectorize._inst
        inst.behaviour = "wrongdim"
        with self.err() as buf2:
            vectorize.embed_batch(["g h i"])
            vectorize.embed_batch(["j k l"])
        self.assertEqual(buf2.getvalue().count("\n"), 1)  # a different kind logs once more

    def test_recovers_when_the_embedder_works_again(self):
        inst = self.use("nan")
        with self.err():
            vectorize.embed_batch(["x y z"])
        self.assertTrue(vectorize.status()["embedder_falling_back"])
        inst.behaviour = "ok"
        eid, _ = vectorize.embed_batch(["x y z two"])
        self.assertEqual(eid, inst.id)
        st = vectorize.status()
        self.assertFalse(st["embedder_falling_back"])
        self.assertEqual(st["embedder_id"], inst.id)
        self.assertEqual(st["embedder_fallbacks"], 1)

    def test_missing_package_is_unavailable_with_install_hint(self):
        with mock.patch.dict(sys.modules, {"fastembed": None}), self.err() as buf:
            vectorize.configure(MINILM)
            eid, (v,) = vectorize.embed_batch(["hello"])
        self.assertEqual((eid, v), (vectorize.EMBEDDER_ID, vectorize.embed("hello")))
        out = buf.getvalue()
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("unavailable", out)
        self.assertIn("engram-memory[local]", out)

    def test_model_load_failure_falls_back_and_retries_only_after_backoff(self):
        calls = []

        def factory(arg):
            calls.append(arg)
            raise OSError("403 while downloading " + SECRET)
        vectorize.register_scheme("dl", factory)
        with self.err() as buf:
            vectorize.configure("dl:m")
            for _ in range(5):
                eid, _v = vectorize.embed_batch(["q"])
            self.assertEqual(eid, vectorize.EMBEDDER_ID)
            self.assertEqual(len(calls), 1)  # not retried on every call
            self.assertNotIn(SECRET, buf.getvalue())
            self.assertEqual(buf.getvalue().count("\n"), 1)
            self.assertIn("(load OSError", buf.getvalue())
            with mock.patch.object(vectorize.time, "monotonic", return_value=time.monotonic() + 301):
                vectorize.embed_batch(["q"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(buf.getvalue().count("\n"), 1)  # same kind: still one line

    def test_bad_embedder_object_is_a_load_failure(self):
        class NoDim:
            id = "x"
            dim = 0
        vectorize.register_scheme("nodim", lambda arg: NoDim())
        with self.err():
            vectorize.configure("nodim:x")
            eid, _ = vectorize.embed_batch(["a"])
        self.assertEqual(eid, vectorize.EMBEDDER_ID)

    def test_lexical_search_unaffected_by_a_dead_embedder(self):
        self.use("raise")
        cx = Cortex(":memory:")
        self.addCleanup(cx.close)
        with self.err():
            r = enc.remember(cx, "The billing service uses postgres for invoices")
            self.assertEqual(r["status"], "created")
            hits = recall.recall(cx, "billing postgres invoices", reinforce=False)
        self.assertEqual([h["id"] for h in hits], [r["id"]])
        row = cx.db.execute("SELECT embedder_id, length(vector) FROM memories").fetchone()
        self.assertEqual(tuple(row), (vectorize.EMBEDDER_ID, 4 * vectorize.DIM))

    def test_explicit_unknown_label_keeps_legacy_behaviour(self):
        eid, (v,) = vectorize.embed_batch(["hi"], "other:1")
        self.assertEqual((eid, v), ("other:1", vectorize.embed("hi")))


class LazyImportTest(unittest.TestCase):
    EXTRAS = ("fastembed", "onnxruntime", "numpy", "tokenizers", "huggingface_hub", "engram.embedders_ext")

    def test_default_embedder_imports_no_extras(self):
        code = """
import sys, tempfile, json
from engram import vectorize, encode, recall, consolidate, daemon
from engram.cortex import Cortex
cx = Cortex(":memory:")
encode.remember(cx, "we deploy the billing service every friday")
encode.remember(cx, "we deploy the billing service every friday!")
recall.recall(cx, "when do we deploy billing")
consolidate.recombine(cx)
home = tempfile.mkdtemp(prefix='eg')
e = daemon.Engram(home=home, env={"ENGRAM_HOME": home})
for verb, p in (("remember", {"content": "deploys need review before production"}),
                ("recall", {"cue": "deploy review"}), ("observe", {"content": "we use redis for caching"}),
                ("reflect", {"content": "reviews gate deploys"}), ("briefing", {}), ("sleep", {}),
                ("status", {})):
    r = e.handle(verb, p)
    assert r.get("ok"), (verb, r)
e.cortex.close()
print(json.dumps({"bad": [m for m in %r if m in sys.modules], "state": vectorize.status()["embedder_state"]}))
""" % (list(self.EXTRAS),)
        r = run_py(code)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {"bad": [], "state": "hash"})
        self.assertEqual(r.stderr, "")

    def test_selecting_hash_explicitly_imports_no_extras(self):
        r = run_py("""
import sys
from engram import vectorize
vectorize.configure("hash")
vectorize.embed_batch(["a b c"])
print([m for m in %r if m in sys.modules])
""" % (list(self.EXTRAS),), env={"ENGRAM_EMBEDDER": "hash"})
        self.assertEqual(r.stdout.strip(), "[]", r.stderr)

    def test_embedders_ext_itself_imports_no_third_party_package(self):
        r = run_py("""
import sys
import engram.embedders_ext
print([m for m in ('fastembed','onnxruntime','numpy','tokenizers','huggingface_hub') if m in sys.modules])
""")
        self.assertEqual(r.stdout.strip(), "[]", r.stderr)

    def test_daemon_with_missing_package_never_raises_and_reports_fallback(self):
        r = run_py("""
import sys, tempfile, json
sys.modules['fastembed'] = None   # simulate: package not installed
from engram import daemon
home = tempfile.mkdtemp(prefix='eg')
e = daemon.Engram(home=home, env={"ENGRAM_HOME": home, "ENGRAM_EMBEDDER": "fastembed:sentence-transformers/all-MiniLM-L6-v2"})
a = e.handle("remember", {"content": "the billing service uses postgres"})
b = e.handle("recall", {"cue": "billing postgres"})
s = e.handle("status", {})
e.cortex.close()
print(json.dumps({"a": a["status"], "n": len(b["results"]), "s": {k: s[k] for k in s if k.startswith("embedder")}}))
""")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["a"], "created")
        self.assertEqual(out["n"], 1)
        self.assertEqual(out["s"]["embedder_id"], vectorize.EMBEDDER_ID)
        self.assertTrue(out["s"]["embedder_falling_back"])
        self.assertGreaterEqual(out["s"]["embedder_fallbacks"], 1)
        self.assertEqual(r.stderr.count("\n"), 1, r.stderr)  # one log line for the whole process
        self.assertIn("unavailable", r.stderr)


class RealPackageTest(unittest.TestCase):
    """Uses the real fastembed package when installed; never fails because a model is missing."""

    @classmethod
    def setUpClass(cls):
        r = run_py("import fastembed; print(fastembed.__version__ if hasattr(fastembed, '__version__') else 'ok')")
        if r.returncode != 0:
            raise unittest.SkipTest("fastembed package not installed (pip install 'engram-memory[local]')")

    def test_selected_import_path_loads_the_real_package(self):
        r = run_py("""
import sys
from engram import embedders_ext
TE = embedders_ext._import_text_embedding()
print(TE.__name__, 'fastembed' in sys.modules, 'onnxruntime' in sys.modules or 'numpy' in sys.modules)
""")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split()[0], "TextEmbedding")

    def test_model_that_cannot_be_loaded_falls_back_to_hashed(self):
        cache = tempfile.mkdtemp(prefix="fe")
        r = run_py("""
import sys, tempfile, json
from engram import vectorize
vectorize.configure("fastembed:engram-test/this-model-does-not-exist")
eid, (v,) = vectorize.embed_batch(["hello world"])
print(json.dumps({"eid": eid, "same": v == vectorize.embed("hello world"), "st": vectorize.status()}))
""", env={"HF_HUB_OFFLINE": "1", "FASTEMBED_CACHE_PATH": cache, "HF_HOME": cache}, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["eid"], vectorize.EMBEDDER_ID)
        self.assertTrue(out["same"])
        self.assertTrue(out["st"]["embedder_falling_back"])
        self.assertEqual(r.stderr.count("\n"), 1, r.stderr)
        self.assertIn("(load ", r.stderr)

    def test_real_model_if_available(self):
        """Needs the actual MiniLM weights already cached (set ENGRAM_TEST_REAL_MODEL=1 to allow a
        download); skipped (never failed) when they cannot be loaded."""
        r = run_py("""
import json
from engram import vectorize, embedders_ext
try:
    e = embedders_ext.make_fastembed("sentence-transformers/all-MiniLM-L6-v2")
except Exception as ex:
    print(json.dumps({"skip": type(ex).__name__}))
else:
    a, b = e.embed("enable auth"), e.embed("disable auth")
    print(json.dumps({"dim": e.dim, "id": e.id, "cos": vectorize.cosine(a, b)}))
""", env=({} if os.environ.get("ENGRAM_TEST_REAL_MODEL") else {"HF_HUB_OFFLINE": "1"}), timeout=180)
        out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"skip": "no output"}
        if "skip" in out:
            self.skipTest("real MiniLM model unavailable here (%s); real-model path is UNVERIFIED" % out["skip"])
        self.assertEqual(out["dim"], 384)


class FastEmbedClassTest(Isolated):
    """The real FastEmbedEmbedder class with the model call stubbed."""

    def setUp(self):
        Isolated.setUp(self)
        p = mock.patch.object(embedders_ext, "_import_text_embedding", return_value=StubTextEmbedding)
        p.start()
        self.addCleanup(p.stop)

    def test_id_dim_and_thresholds(self):
        e = embedders_ext.make_fastembed("some/model")
        self.assertEqual((e.id, e.dim), ("fastembed:some/model:384", 384))
        self.assertEqual(embedders_ext.make_fastembed("").model_name, embedders_ext.DEFAULT_FASTEMBED_MODEL)
        # untuned: the conservative hashed defaults
        self.assertEqual((e.NEAR_DUPLICATE, e.SEM_GAIN, e.SEM_MIN_COS, e.RECOMBINE_LO, e.RECOMBINE_HI),
                         (0.88, 1.8, 0.30, 0.34, 0.88))

    def test_selected_by_env_value_and_stored_under_its_id(self):
        vectorize.configure(MINILM)
        cx = Cortex(":memory:")
        self.addCleanup(cx.close)
        r = enc.remember(cx, "The billing service uses postgres for invoices")
        row = cx.db.execute("SELECT embedder_id, length(vector) FROM memories WHERE id=?", (r["id"],)).fetchone()
        self.assertEqual(tuple(row), ("%s:384" % MINILM, 384 * 4))
        st = vectorize.status()
        self.assertEqual((st["embedder_state"], st["embedder_dim"], st["embedder_falling_back"]),
                         ("active", 384, False))

    def test_numpy_like_vectors_are_accepted(self):
        class Arr:  # iterable of numpy-ish scalars
            def __init__(self, v):
                self.v = v

            def __iter__(self):
                return (Fl(x) for x in self.v)

        class Fl:
            def __init__(self, x):
                self.x = x

            def __float__(self):
                return float(self.x)

        class TE(StubTextEmbedding):
            def embed(self, texts):
                for t in texts:
                    yield Arr(fake_dense.fake_vector(t, 384))
        with mock.patch.object(embedders_ext, "_import_text_embedding", return_value=TE):
            vectorize.configure(MINILM)
            eid, (v,) = vectorize.embed_batch(["hello"])
        self.assertTrue(eid.endswith(":384"))
        self.assertEqual(len(v), 384 * 4)

    def test_model_call_error_falls_back_per_call(self):
        class TE(StubTextEmbedding):
            fail = False

            def embed(self, texts):
                if TE.fail:
                    raise RuntimeError("onnx failure " + SECRET)
                return StubTextEmbedding.embed(self, texts)
        with mock.patch.object(embedders_ext, "_import_text_embedding", return_value=TE):
            vectorize.configure(MINILM)
            self.assertTrue(vectorize.embed_batch(["warm"])[0].endswith(":384"))
            TE.fail = True
            with self.err() as buf:
                eid, (v,) = vectorize.embed_batch(["something else"])
                vectorize.embed_batch(["and another"])
            self.assertEqual(eid, vectorize.EMBEDDER_ID)
            self.assertEqual(buf.getvalue().count("\n"), 1)
            self.assertNotIn(SECRET, buf.getvalue())
            TE.fail = False
            self.assertTrue(vectorize.embed_batch(["third"])[0].endswith(":384"))


class ThresholdsTest(Isolated):
    def make(self, **decl):
        cls = type("Tuned", (Broken,), decl)
        inst = cls("ok")
        vectorize.register_scheme("tuned", lambda arg: inst)
        vectorize.configure("tuned:x")
        vectorize.embed_batch(["load"])
        return inst

    def test_defaults_are_the_hashed_values(self):
        inst = self.make()
        self.assertEqual(enc.threshold(inst.id), enc.NEAR_DUPLICATE)
        self.assertEqual(vectorize.tuning(inst.id, "SEM_GAIN", 99), 1.8)
        self.assertEqual(enc.threshold(vectorize.EMBEDDER_ID), 0.88)

    def test_stricter_declared_threshold_is_used_looser_is_clamped(self):
        inst = self.make(NEAR_DUPLICATE=0.95)
        self.assertEqual(enc.threshold(inst.id), 0.95)
        vectorize._reset_for_tests()
        inst = self.make(NEAR_DUPLICATE=0.5)
        self.assertEqual(enc.threshold(inst.id), 0.88)
        vectorize._reset_for_tests()
        inst = self.make(NEAR_DUPLICATE=float("nan"))
        self.assertEqual(enc.threshold(inst.id), 0.88)

    def test_declared_values_apply_to_the_declaring_id_only(self):
        inst = self.make(NEAR_DUPLICATE=0.97, SEM_GAIN=3.0)
        self.assertEqual(vectorize.tuning(inst.id, "SEM_GAIN", 1.8), 3.0)
        self.assertEqual(vectorize.tuning("hashed-ngram-v1:512", "SEM_GAIN", 1.8), 1.8)
        self.assertEqual(vectorize.tuning("someone-else:1", "SEM_GAIN", 1.8), 1.8)
        self.assertEqual(enc.threshold("someone-else:1"), 0.88)

    def test_recall_scale_and_floor_follow_the_embedder(self):
        for gain, floor in ((1.8, 0.30), (0.5, 0.95)):
            vectorize._reset_for_tests()
            inst = self.make(SEM_GAIN=gain, SEM_MIN_COS=floor)
            cx = Cortex(":memory:")
            self.addCleanup(cx.close)
            enc.remember(cx, "quarterly revenue grew by twelve percent")
            hits = recall.candidates(cx, "revenue grew twelve percent growth", relevance_floor=False)
            self.assertEqual(len(hits), 1)
            self.assertAlmostEqual(hits[0]["relevance"], hits[0]["lex"] * 0.55 + 0.45 * min(1.0, hits[0]["sem"] * gain))
            self.assertEqual(inst.SEM_MIN_COS, floor)

    def test_recombine_band_follows_the_embedder(self):
        base = "the billing service stores invoices in postgres and archives old rows nightly"
        rel = "the billing service archives invoices in postgres every night using a cron job"
        for lo, hi, expect in ((0.0, 1.0, 1), (0.999, 1.0, 0)):
            vectorize._reset_for_tests()
            inst = self.make(RECOMBINE_LO=lo, RECOMBINE_HI=hi)
            cx = Cortex(":memory:")
            self.addCleanup(cx.close)
            for t in (base, rel):
                cx.insert("semantic", t, vector=fake_dense.fake_vector(t) and vectorize.embed_batch([t])[1][0],
                          embedder_id=inst.id)
            self.assertEqual(consolidate.recombine(cx), expect)


class DimensionsTest(Isolated):
    def vec(self, text, dim):
        return vectorize.to_bytes(fake_dense.fake_vector(text, dim))

    def test_cosine_and_cosine_many_for_arbitrary_dims(self):
        for dim in (3, 384, 1536):
            with self.subTest(dim=dim):
                a, b = self.vec("alpha beta gamma", dim), self.vec("alpha beta delta", dim)
                self.assertAlmostEqual(vectorize.cosine(a, a), 1.0, places=5)
                dense = vectorize.cosine(a, b)
                self.assertAlmostEqual(vectorize.cosine(fake_dense.fake_vector("alpha beta gamma", dim),
                                                        fake_dense.fake_vector("alpha beta delta", dim)),
                                       dense, places=5)
                got = vectorize.cosine_many(a, [a, b, b"", self.vec("x", dim + 1)])
                self.assertAlmostEqual(got[0], 1.0, places=5)
                self.assertAlmostEqual(got[1], dense, places=5)
                self.assertEqual(got[2], 0.0)
                self.assertEqual(got[3], 0.0)  # other dimension: never a score

    def test_sparse_query_path_matches_dense_math(self):
        q = [0.0] * 1536
        q[3], q[700], q[1500] = 0.5, -0.5, 0.7
        v = fake_dense.fake_vector("some memory text", 1536)
        got = vectorize.cosine_many(vectorize.to_bytes(q), [vectorize.to_bytes(v)])[0]
        self.assertAlmostEqual(got, vectorize.cosine(q, v), places=5)

    def test_encode_recall_dedup_at_each_dimension(self):
        for dim in (384, 1536):
            with self.subTest(dim=dim), activated("fake-dense-%d" % dim) as eid:
                cx = Cortex(":memory:")
                self.addCleanup(cx.close)
                a = enc.remember(cx, "The billing service uses postgres for invoices")
                again = enc.remember(cx, "the billing service uses postgres for invoices.")
                self.assertEqual(again["status"], "reinforced")
                other = enc.remember(cx, "The billing service uses mysql for invoices")
                self.assertEqual(other["status"], "created")
                row = cx.db.execute("SELECT embedder_id, length(vector) FROM memories WHERE id=?",
                                    (a["id"],)).fetchone()
                self.assertEqual(tuple(row), (eid, 4 * dim))
                hits = recall.candidates(cx, "which database stores invoices", relevance_floor=False)
                self.assertTrue(all(h["embedder_id"] == eid for h in hits))
                self.assertGreater(max(h["sem"] for h in hits), 0.0)


class MixedEmbedderRowsTest(Isolated):
    def build(self):
        cx = Cortex(":memory:")
        self.addCleanup(cx.close)
        old = enc.remember(cx, "The billing service uses postgres for invoices")  # hashed row
        return cx, old

    def test_other_ids_are_lexical_only_no_error(self):
        cx, old = self.build()
        with activated("fake-dense-384") as eid:
            self.assertNotEqual(eid, vectorize.EMBEDDER_ID)
            new = enc.remember(cx, "The warehouse robots charge overnight in bay four")
            hits = recall.candidates(cx, "billing postgres invoices", relevance_floor=False)
            by_id = {h["id"]: h for h in hits}
            self.assertIn(old["id"], by_id)           # lexical channel still serves the hashed row
            self.assertEqual(by_id[old["id"]]["sem"], 0.0)   # but never a vector score
            self.assertGreater(by_id[old["id"]]["lex"], 0.0)
            hits2 = recall.recall(cx, "robots charging overnight", reinforce=False)
            self.assertEqual(hits2[0]["id"], new["id"])

    def test_dedup_never_merges_across_ids(self):
        cx, old = self.build()
        with activated("fake-dense-384"):
            r = enc.remember(cx, "The billing service uses postgres for invoices")  # same text, other embedder
            self.assertEqual(r["status"], "created")
        ids = [x[0] for x in cx.db.execute("SELECT DISTINCT embedder_id FROM memories")]
        self.assertEqual(len(ids), 2)

    def test_recombine_skips_pairs_with_different_ids(self):
        cx = Cortex(":memory:")
        self.addCleanup(cx.close)
        base = "the billing service stores invoices in postgres and archives old rows nightly"
        rel = "the billing service archives invoices in postgres every night using a cron job"
        cx.insert("semantic", base, embedder_id="hashed-ngram-v1:512")
        cx.insert("semantic", rel, embedder_id="fake-dense-v1:384")
        self.assertEqual(consolidate.recombine(cx), 0)

    def test_novelty_and_vector_compare_refuse_mixed_ids(self):
        self.assertIsNone(enc.compare_vectors(("fake-dense-v1:384", [1.0] * 384), ("fake-dense-v1:1536", [1.0] * 1536)))
        self.assertEqual(vectorize.cosine([1.0] * 384, [1.0] * 1536), 0.0)

    def test_status_and_reembed_inputs_are_recoverable_from_rows(self):
        """T26 needs (embedder_id, dimension) per row: both are stored and consistent."""
        cx, old = self.build()
        with activated("fake-dense-1536"):
            enc.remember(cx, "The warehouse robots charge overnight in bay four")
        rows = cx.db.execute("SELECT embedder_id, length(vector)/4 FROM memories ORDER BY rowid").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("hashed-ngram-v1:512", 512), ("fake-dense-v1:1536", 1536)])


class StatusAndDaemonTest(Isolated):
    def test_status_shape_for_each_state(self):
        st = vectorize.status()
        self.assertEqual((st["embedder_state"], st["embedder_falling_back"], st["embedder_fallbacks"]),
                         ("hash", False, 0))
        vectorize.register_scheme("fake", fake_dense.make_fake)
        vectorize.configure("fake:384")
        self.assertEqual(vectorize.status()["embedder_state"], "not-loaded")  # status never loads a model
        self.assertEqual(vectorize.status()["embedder_id"], vectorize.EMBEDDER_ID)
        vectorize.embed_batch(["x y"])
        st = vectorize.status()
        self.assertEqual((st["embedder_state"], st["embedder_id"]), ("active", "fake-dense-v1:384"))

    def test_daemon_status_and_cli_text(self):
        from engram import cli, mcp_server
        home = make_home()
        with mock.patch.dict(sys.modules, {"fastembed": None}), self.err():
            e = daemon.Engram(home=home, env={"ENGRAM_HOME": home, "ENGRAM_EMBEDDER": MINILM})
            self.addCleanup(e.cortex.close)
            self.assertEqual(e.handle("remember", {"content": "we deploy every friday afternoon"})["status"],
                             "created")
            s = e.handle("status", {})
        self.assertTrue(s["embedder_falling_back"])
        self.assertEqual(s["embedder_configured"], MINILM)
        self.assertEqual(s["embedder_id"], vectorize.EMBEDDER_ID)
        line = mcp_server.render_status(s)
        self.assertIn("falling_back=True", line)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli._fmt_status(dict(s, pid=1, uptime_s=0, fts=True, schema_version=1, total=1,
                                 journal_pending=0, counts={}))
        self.assertIn("falling back: yes", buf.getvalue())

    def test_default_status_text_unchanged(self):
        from engram import mcp_server
        line = mcp_server.render_status(dict(vectorize.status(), total=0, journal_pending=0))
        self.assertNotIn("falling_back", line)
        self.assertIn("embedder=hashed-ngram-v1:512", line)

    def test_warn_reserved_flags_only_unknown_embedders(self):
        with mock.patch.object(daemon, "_reserved_warned", False):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                daemon.warn_reserved({"ENGRAM_EMBEDDER": "fastembed:some/model"})
                daemon.warn_reserved({"ENGRAM_EMBEDDER": "hash"})
            self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

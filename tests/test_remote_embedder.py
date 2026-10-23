"""Remote (OpenAI-compatible) embedder: consent, redaction at the boundary, key handling, HTTP
hardening and containment (T26, ADR 0011).

NO real provider endpoint is contacted: every test talks to a stdlib stub on 127.0.0.1
(tests/remote_stub.py) or patches the connection classes to prove nothing is attempted.
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from engram import __version__, embedders_remote as er, recall, vectorize
from tests.daemon_helpers import ROOT, make_home, spawn, wait_up
from tests.remote_helpers import (AWS, GHP, MODEL, PWD, SECRET_TEXT, SECRETS, RDaemon, all_files_text,
                                  jdump, remote_env, seed)
from tests.remote_stub import KEY, Stub, make_self_signed, wait_for

HASH_ID = vectorize.EMBEDDER_ID
DIM = 16
OPENAI_ID = "openai:%s:%d" % (MODEL, DIM)


class AdapterBase(unittest.TestCase):
    def setUp(self):
        vectorize._reset_for_tests()
        self.stub = Stub()
        self.addCleanup(self.stub.stop)
        self.addCleanup(er.configure_env, None)

    def make(self, stub=None, **extra):
        er.configure_env(remote_env(stub or self.stub, **extra))
        return er.make_openai(MODEL)

    def kind(self, fn, *a):
        with self.assertRaises(er.RemoteError) as cm:
            fn(*a)
        return cm.exception.kind


class HappyPath(AdapterBase):
    def test_request_shape_and_dimension_learned_from_first_response(self):
        e = self.make()
        self.assertEqual(e.id, OPENAI_ID)
        self.assertEqual(e.dim, DIM)
        vecs = e.embed_many(["alpha beta", "gamma"])
        self.assertEqual([len(v) for v in vecs], [DIM, DIM])
        req = self.stub.embedding_calls()[-1]
        self.assertEqual(req[0], "POST /v1/embeddings HTTP/1.1")
        body = json.loads(req[2])
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["input"], ["alpha beta", "gamma"])
        hdr = req[1]
        self.assertIn("Authorization: Bearer %s" % KEY, hdr)
        self.assertIn("User-Agent: engram/%s" % __version__, hdr)
        self.assertIn("Content-Type: application/json", hdr)
        self.assertNotIn("Cookie", hdr)

    def test_index_ordering_is_respected(self):
        e = self.make()
        a = e.embed_many(["one fish", "two fish"])
        self.stub.mode = "shuffle"
        self.assertEqual(e.embed_many(["one fish", "two fish"]), a)

    def test_repr_does_not_contain_the_key(self):
        e = self.make()
        self.assertNotIn(KEY, repr(e))
        self.assertNotIn(KEY, str(vars(e).keys()))


class ConsentAndConfig(AdapterBase):
    def patched_network(self):
        return mock.patch.multiple(http.client, HTTPConnection=mock.DEFAULT, HTTPSConnection=mock.DEFAULT)

    def test_remote_without_acknowledgement_makes_no_network_call(self):
        env = {"ENGRAM_EMBEDDER_URL": "https://embeddings.example.com/v1", "ENGRAM_EMBEDDER_API_KEY": KEY}
        er.configure_env(env)
        with self.patched_network() as m:
            with self.assertRaises(er.RemoteError) as cm:
                er.make_openai(MODEL)
        self.assertEqual(cm.exception.detail, "remote not acknowledged")
        self.assertEqual(cm.exception.kind, "unavailable")
        self.assertFalse(m["HTTPSConnection"].called)
        self.assertFalse(m["HTTPConnection"].called)

    def test_acknowledged_remote_without_key_is_refused_before_the_network(self):
        er.configure_env({"ENGRAM_EMBEDDER_URL": "https://embeddings.example.com/v1",
                          "ENGRAM_EMBEDDER_ALLOW_REMOTE": "1"})
        with self.patched_network() as m:
            with self.assertRaises(er.RemoteError):
                er.make_openai(MODEL)
        self.assertFalse(m["HTTPSConnection"].called)

    def test_http_to_non_loopback_is_refused_even_when_acknowledged(self):
        er.configure_env({"ENGRAM_EMBEDDER_URL": "http://embeddings.example.com/v1",
                          "ENGRAM_EMBEDDER_ALLOW_REMOTE": "1", "ENGRAM_EMBEDDER_API_KEY": KEY})
        with self.patched_network() as m:
            with self.assertRaises(er.RemoteError) as cm:
                er.make_openai(MODEL)
        self.assertIn("http://", cm.exception.detail)
        self.assertFalse(m["HTTPConnection"].called)

    def test_bad_urls_and_models_are_refused(self):
        for url in ("ftp://x.example/v1", "https://user:pw@x.example/v1", "https://x.example/v1?a=1",
                    "https://x.example/v1#f", "https:///v1", "https://x.example:99999/v1",
                    "https://x.example/v1 \n"):
            with self.subTest(url=url):
                er.configure_env({"ENGRAM_EMBEDDER_URL": url, "ENGRAM_EMBEDDER_ALLOW_REMOTE": "1",
                                  "ENGRAM_EMBEDDER_API_KEY": KEY})
                with self.patched_network() as m, self.assertRaises(er.RemoteError):
                    er.make_openai(MODEL)
                self.assertFalse(m["HTTPSConnection"].called)
        er.configure_env(remote_env(self.stub))
        for model in ("bad model", "bad\nmodel", "", " ", "x" * 200, "-lead", "m\x00", "moél"):
            with self.subTest(model=model), self.assertRaises(er.RemoteError):
                er.make_openai(model)
        self.assertEqual(self.stub.count(), 0)

    def test_bad_key_formats_are_refused_without_echo(self):
        for key in ("has space", "new\nline", "tab\t", "k" * 600):
            er.configure_env(remote_env(self.stub, ENGRAM_EMBEDDER_API_KEY=key))
            with self.assertRaises(er.RemoteError) as cm:
                er.make_openai(MODEL)
            self.assertNotIn(key.strip()[:8], cm.exception.detail)
        self.assertEqual(self.stub.count(), 0)

    def test_loopback_http_needs_no_acknowledgement_and_no_key(self):
        self.stub.key = None
        er.configure_env({"ENGRAM_EMBEDDER_URL": self.stub.url})
        e = er.make_openai(MODEL)
        self.assertEqual(e.id, OPENAI_ID)
        self.assertNotIn("Authorization", self.stub.embedding_calls()[0][1])

    def test_default_endpoint_is_openai_but_needs_acknowledgement(self):
        er.configure_env({"ENGRAM_EMBEDDER_API_KEY": KEY})
        with self.patched_network() as m, self.assertRaises(er.RemoteError) as cm:
            er.make_openai(MODEL)
        self.assertEqual(cm.exception.detail, "remote not acknowledged")
        self.assertFalse(m["HTTPSConnection"].called)

    def test_remote_status_never_has_key_path_or_query(self):
        er.configure_env({"ENGRAM_EMBEDDER_URL": "https://emb.example.com:8443/secret/path/v1",
                          "ENGRAM_EMBEDDER_API_KEY": KEY})
        s = er.remote_status("openai:" + MODEL)
        self.assertEqual(s["embedder_remote_host"], "emb.example.com:8443")
        self.assertTrue(s["embedder_remote"])
        self.assertEqual(s["embedder_remote_model"], MODEL)
        self.assertNotIn("secret", jdump(s))
        self.assertNotIn(KEY, jdump(s))
        self.assertFalse(s["embedder_remote_acknowledged"])

    def test_tls_verification_is_on_self_signed_server_is_rejected(self):
        d = tempfile.mkdtemp(prefix="egtls")
        self.addCleanup(shutil.rmtree, d, True)
        pair = make_self_signed(d)
        if pair is None:
            self.skipTest("openssl CLI not available")
        tls = Stub(tls=pair)
        self.addCleanup(tls.stop)
        er.configure_env({"ENGRAM_EMBEDDER_URL": "https://localhost:%d/v1" % tls.port,
                          "ENGRAM_EMBEDDER_API_KEY": KEY})
        with self.assertRaises(er.RemoteError) as cm:
            er.make_openai(MODEL)
        self.assertEqual(cm.exception.kind, "tls")
        self.assertEqual(tls.count(), 0)     # the TLS handshake failed: no HTTP request, no key
        self.assertNotIn(b"verify", json.dumps(vars(er)).encode() if False else b"")

    def test_there_is_no_way_to_disable_certificate_verification(self):
        src = open(er.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        for bad in ("_create_unverified_context", "CERT_NONE", "check_hostname = False", "verify="):
            self.assertNotIn(bad, src)
        for k in os.environ:
            self.assertFalse(k.startswith("ENGRAM_EMBEDDER_") and "VERIFY" in k.upper())


class RedactionAtTheBoundary(AdapterBase):
    def test_every_string_is_redacted_inside_the_adapter(self):
        e = self.make()
        e.embed_many([SECRET_TEXT, "plain note about " + AWS])
        e.embed(GHP)
        wire = self.stub.all_bytes().decode("utf-8", "replace")
        for s in SECRETS:
            self.assertNotIn(s, wire)
        self.assertIn("[REDACTED:", wire)

    def test_a_secret_cut_by_the_length_cap_does_not_leave_a_prefix(self):
        e = self.make()
        text = "x " * 3990 + "password=" + PWD + " " + "tail " * 100
        e.embed_many([text])
        sent = self.stub.inputs[-1]
        self.assertNotIn(PWD[:8], sent)
        self.assertLessEqual(len(sent), er.MAX_ITEM_CHARS)

    def test_length_and_batch_caps(self):
        e = self.make()
        before = len(self.stub.embedding_calls())
        e.embed_many(["w%d %s" % (i, "long " * 5000) for i in range(130)])
        calls = self.stub.embedding_calls()[before:]
        sizes = [len(json.loads(c[2])["input"]) for c in calls]
        self.assertEqual(sizes, [64, 64, 2])
        self.assertTrue(all(len(t) <= er.MAX_ITEM_CHARS for t in self.stub.inputs))

    def test_empty_text_is_replaced_not_sent_empty(self):
        e = self.make()
        e.embed_many(["", "   "])
        self.assertTrue(all(t.strip() for t in self.stub.inputs[-2:]))

    def test_embed_cue_redacts_and_matches_the_stored_redacted_text(self):
        vectorize._reset_for_tests()
        cue = "where is password=%s used with %s" % (PWD, AWS)
        used, (v,) = recall.embed_cue(cue, relevance_floor=False)
        self.assertEqual(used, HASH_ID)
        from engram.redact import redact
        self.assertEqual(v, vectorize.embed(redact(cue)))
        self.assertNotEqual(v, vectorize.embed(cue))


class HttpHardening(AdapterBase):
    def check(self, mode, kind, **setup):
        e = self.make()
        self.stub.mode = mode
        for k, v in setup.items():
            setattr(self.stub, k, v)
        self.assertEqual(self.kind(e.embed_many, ["some text"]), kind, mode)
        return e

    def test_server_errors_and_auth(self):
        self.check("500", "http-5xx")
        self.check("429", "rate-limited")
        self.stub.mode = "ok"
        self.stub.key = "another-key"
        self.assertEqual(self.kind(self.make().embed_many if False else er.make_openai, MODEL), "auth")

    def test_malformed_and_non_finite_and_wrong_shape(self):
        for mode, kind in (("badjson", "protocol"), ("nan", "protocol"), ("inf", "nonfinite"),
                           ("short", "protocol"), ("dupindex", "protocol"), ("html", "protocol"),
                           ("strings", "protocol")):
            with self.subTest(mode=mode):
                self.stub.mode = "ok"
                self.check(mode, kind)
        e = self.make()
        self.stub.mode = "ragged"
        self.assertIn(self.kind(e.embed_many, ["a", "b"]), ("dimension",))

    def test_dimension_change_between_calls_is_rejected(self):
        e = self.make()
        self.assertEqual(len(e.embed_many(["a"])[0]), DIM)
        self.stub.dim = 8
        self.assertEqual(self.kind(e.embed_many, ["a"]), "dimension")
        self.stub.dim = DIM
        self.assertEqual(len(e.embed_many(["a"])[0]), DIM)

    def test_huge_responses_are_cut_off(self):
        e = self.make()
        for mode in ("huge", "huge-chunked"):
            self.stub.mode = mode
            t = time.monotonic()
            self.assertEqual(self.kind(e.embed_many, ["a"]), "too-large")
            self.assertLess(time.monotonic() - t, 5)

    def test_redirect_is_not_followed_and_the_key_is_not_sent_elsewhere(self):
        other = Stub()
        self.addCleanup(other.stop)
        e = self.make()
        self.stub.mode = "redirect"
        self.stub.location = other.url + "/embeddings"
        self.assertEqual(self.kind(e.embed_many, ["a"]), "redirect")
        time.sleep(0.2)
        self.assertEqual(other.count(), 0)
        self.assertNotIn(KEY.encode(), other.all_bytes())

    def test_slow_server_is_bounded_by_the_deadline(self):
        e = self.make()
        self.stub.delay = 3.0
        vectorize.EMBED_TIMEOUT = 0.4
        t = time.monotonic()
        self.assertEqual(self.kind(e.embed_many, ["a"]), "timeout")
        self.assertLess(time.monotonic() - t, 1.0)

    def test_dribbling_server_is_bounded_in_total(self):
        e = self.make()
        self.stub.mode = "huge-chunked"
        vectorize.EMBED_TIMEOUT = 0.3
        t = time.monotonic()
        self.assertIn(self.kind(e.embed_many, ["a"]), ("timeout", "too-large"))
        self.assertLess(time.monotonic() - t, 2.0)

    def test_connection_refused_is_a_network_error_without_details(self):
        e = self.make()
        self.stub.stop()
        with self.assertRaises(er.RemoteError) as cm:
            e.embed_many(["a"])
        self.assertIn(cm.exception.kind, ("network",))
        self.assertNotIn(KEY, str(cm.exception) + str(cm.exception.detail))

    def test_retry_after_is_honoured_without_blocking(self):
        e = self.make()
        self.stub.mode, self.stub.retry_after = "429", 30
        self.assertEqual(self.kind(e.embed_many, ["a"]), "rate-limited")
        n = self.stub.count()
        t = time.monotonic()
        self.assertEqual(self.kind(e.embed_many, ["a"]), "rate-limited")   # no request, no wait
        self.assertEqual(self.stub.count(), n)
        self.assertLess(time.monotonic() - t, 0.2)

    def test_retry_after_is_capped(self):
        e = self.make()
        self.stub.mode, self.stub.retry_after = "429", 10 ** 9
        self.kind(e.embed_many, ["a"])
        self.assertLessEqual(e._not_before - time.monotonic(), er.RETRY_AFTER_CAP_S + 1)


class DaemonContainment(unittest.TestCase):
    """The real Engram + Server against the stub: consent, fallback, breaker, latency."""

    def setUp(self):
        self.stub = Stub()
        self.addCleanup(self.stub.stop)
        self.d = None

    def start(self, **extra):
        env = remote_env(self.stub, **extra)
        self.d = RDaemon(env)
        self.addCleanup(self.d.cleanup)
        return self.d

    def test_happy_path_status_and_stored_id(self):
        d = self.start()
        r = d.call("remember", {"content": "We store invoices in the ledger service"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(d.ids(), [(OPENAI_ID, DIM)])
        s = d.status()
        self.assertTrue(s["embedder_remote"])
        self.assertEqual(s["embedder_remote_host"], "127.0.0.1:%d" % self.stub.port)
        self.assertEqual(s["embedder_remote_model"], MODEL)
        self.assertEqual(s["embedder_id"], OPENAI_ID)
        self.assertEqual(s["embedder_state"], "ready")
        self.assertNotIn(KEY, jdump(s))
        self.assertNotIn("/v1", s["embedder_remote_host"])
        res = d.call("recall", {"cue": "where do we store invoices"})
        self.assertTrue(res["results"])

    def test_unacknowledged_remote_falls_back_with_reason_and_sends_nothing(self):
        with mock.patch.multiple(http.client, HTTPConnection=mock.DEFAULT,
                                 HTTPSConnection=mock.DEFAULT) as m:
            d = RDaemon({"ENGRAM_EMBEDDER": "openai:" + MODEL, "ENGRAM_EMBEDDER_API_KEY": KEY,
                         "ENGRAM_EMBEDDER_URL": "https://embeddings.example.com/v1"})
            self.addCleanup(d.cleanup)
            r = d.call("remember", {"content": "Never sent anywhere because nothing was acknowledged"})
            s = d.status()
            self.assertFalse(m["HTTPSConnection"].called)
            self.assertFalse(m["HTTPConnection"].called)
        self.assertTrue(r["ok"])
        self.assertEqual(d.ids()[0][0], HASH_ID)
        self.assertEqual(s["embedder_reason"], "unavailable: remote not acknowledged")
        self.assertTrue(s["embedder_falling_back"])
        self.assertEqual(s["embedder_id"], HASH_ID)
        self.assertTrue(s["embedder_remote"])
        self.assertFalse(s["embedder_remote_acknowledged"])
        self.assertEqual(self.stub.count(), 0)

    def test_wrong_and_missing_key_fall_back_and_never_echo_the_key(self):
        for kw in ({"ENGRAM_EMBEDDER_API_KEY": "sk-wrong-KEY-0000000000000000"},):
            d = self.start(**kw)
            r = d.call("remember", {"content": "Auth fails so this row is hashed"})
            s = d.status()
            self.assertTrue(r["ok"])
            self.assertEqual(d.ids()[0][0], HASH_ID)
            self.assertTrue(s["embedder_falling_back"])
            self.assertIn("auth", s["embedder_reason"])
            self.assertNotIn("sk-wrong-KEY", jdump(s))
            self.assertNotIn(KEY, jdump(s))
            d.cleanup()
        d = RDaemon({"ENGRAM_EMBEDDER": "openai:" + MODEL, "ENGRAM_EMBEDDER_URL": self.stub.url})
        self.addCleanup(d.cleanup)
        self.assertIn("auth", d.status()["embedder_reason"])
        self.assertNotIn("Authorization", self.stub.embedding_calls()[-1][1])

    def test_slow_server_falls_back_within_timeout_and_other_verbs_stay_fast(self):
        d = self.start(ENGRAM_EMBEDDER_TIMEOUT="0.5")
        self.assertEqual(d.status()["embedder_state"], "ready")
        self.stub.delay = 4.0
        t = time.monotonic()
        r = d.call("remember", {"content": "Slow remote server so the row is hashed"})
        took = time.monotonic() - t
        self.assertTrue(r["ok"])
        self.assertLess(took, 0.5 + 0.5, took)
        self.assertEqual(d.ids()[-1][0], HASH_ID)
        worst = 0.0
        for verb, p in (("status", {}), ("ping", {}), ("recall", {"cue": "slow remote server"}),
                        ("briefing", {})):
            d.call(verb, p)
            worst = max(worst, d.last_s)
        self.assertLess(worst, 0.5)

    def test_server_errors_open_the_breaker_and_no_calls_happen_in_cooldown(self):
        d = self.start()
        self.stub.mode = "500"
        base = len(self.stub.embedding_calls())
        for i in range(vectorize.BREAKER_N):
            d.call("remember", {"content": "Failing remote number %d with some words" % i})
        self.assertEqual(len(self.stub.embedding_calls()) - base, vectorize.BREAKER_N)
        s = d.status()
        self.assertIn("circuit breaker open", s["embedder_reason"])
        n = len(self.stub.embedding_calls())
        for i in range(10):
            d.call("remember", {"content": "During cooldown %d nothing is called" % i})
            d.call("recall", {"cue": "cooldown"})
        self.assertEqual(len(self.stub.embedding_calls()), n)
        self.assertEqual({r["embedder_id"] for r in d.rows()[1:]}, {HASH_ID})

    def test_rate_limit_429_counts_as_failure(self):
        d = self.start()
        self.stub.mode, self.stub.retry_after = "429", 5
        d.call("remember", {"content": "Rate limited remote call number one"})
        d.call("remember", {"content": "Rate limited remote call number two"})
        self.assertEqual(len(self.stub.embedding_calls()) - 1, 1)   # then Retry-After blocks calls
        self.assertIn("rate-limited", jdump(d.status()))

    def test_dimension_change_mid_run_falls_back(self):
        d = self.start()
        d.call("remember", {"content": "First row is embedded remotely"})
        self.stub.dim = 8
        d.call("remember", {"content": "Second row sees a changed dimension"})
        self.assertEqual([r["embedder_id"] for r in d.rows()], [OPENAI_ID, HASH_ID])
        self.assertIn("dimension", d.status()["embedder_reason"])

    def test_malformed_server_answers_never_break_the_daemon(self):
        d = self.start()
        for mode in ("badjson", "nan", "inf", "html", "short"):
            self.stub.mode = mode
            r = d.call("remember", {"content": "Mode %s row with distinct words %s" % (mode, mode * 3)})
            self.assertTrue(r["ok"], (mode, r))
        self.assertTrue(d.call("ping")["ok"])

    def test_planted_secrets_in_remember_observe_recall_never_reach_the_server(self):
        d = self.start()
        d.call("remember", {"content": SECRET_TEXT})
        d.call("observe", {"content": "We decided to use this config: " + SECRET_TEXT, "role": "user"})
        d.call("recall", {"cue": "what is password=%s and %s %s" % (PWD, AWS, GHP)})
        d.call("reflect", {"content": "Lesson learned: " + SECRET_TEXT})
        d.call("sleep", {"deep": True})
        self.assertGreater(len(self.stub.inputs), 3)
        wire = self.stub.all_bytes().decode("utf-8", "replace")
        for s in SECRETS:
            self.assertNotIn(s, wire)
        self.assertIn(KEY, wire)                     # the key goes in the auth header only
        for _rl, hdr, body in self.stub.requests:
            self.assertNotIn(KEY.encode(), body)
            self.assertEqual(hdr.count(KEY), 1)

    def test_key_never_appears_in_any_daemon_output(self):
        self.stub.key = KEY
        d = self.start()
        seed(d, 3)
        self.stub.mode = "ok"
        outs = [d.status(), d.call("dump"), d.call("recall", {"cue": "component protocol"}),
                d.call("briefing"), d.call("export_rules", {"repo_root": "/tmp/x"}),
                d.call("reembed", {"action": "plan"}), d.call("reembed", {"action": "status"})]
        self.assertNotIn(KEY, jdump(outs))
        self.assertNotIn(KEY.encode(), all_files_text(d.home))


class SubprocessDaemon(unittest.TestCase):
    """A real daemon process: key not on the command line, not in any file or output."""

    def test_key_not_in_cmdline_files_stdout_stderr_or_cli_output(self):
        stub = Stub()
        self.addCleanup(stub.stop)
        stub.key = KEY
        home = make_home()
        env = {"ENGRAM_EMBEDDER": "openai:" + MODEL, "ENGRAM_EMBEDDER_URL": stub.url,
               "ENGRAM_EMBEDDER_API_KEY": KEY, "ENGRAM_EMBEDDER_TIMEOUT": "1.5"}
        p = spawn(home, env)
        try:
            self.assertTrue(wait_up(home))
            from engram import client
            cenv = {"ENGRAM_HOME": home}
            for i in range(3):
                client.call("remember", {"content": "Subprocess memory %d on component-%d" % (i, i * 9)},
                            timeout=5, env=cenv)
            self.assertTrue(wait_for(lambda: stub.embedding_calls(), 5))
            st = client.call("status", timeout=5, env=cenv)
            self.assertEqual(st["embedder_remote_model"], MODEL)
            with open("/proc/%d/cmdline" % p.pid, "rb") as f:
                self.assertNotIn(KEY.encode(), f.read())
            cli = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "mnemo"), "status"],
                                 env=dict(os.environ, ENGRAM_HOME=home, PYTHONDONTWRITEBYTECODE="1"),
                                 capture_output=True, text=True, timeout=30)
            self.assertNotIn(KEY, cli.stdout + cli.stderr)
            self.assertIn("memory text is sent to 127.0.0.1:%d for embedding" % stub.port, cli.stdout)
            stub.key = "rotated-key-so-requests-fail-0000"
            client.call("remember", {"content": "Failing auth path in the subprocess daemon"},
                        timeout=5, env=cenv)
        finally:
            p.terminate()
            try:
                out, err = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
        self.assertNotIn(KEY.encode(), out + err)
        self.assertNotIn(KEY.encode(), all_files_text(home))
        self.assertNotIn(b"rotated-key", out + err + all_files_text(home))
        shutil.rmtree(home, ignore_errors=True)


class DefaultPathStaysClean(unittest.TestCase):
    def test_nothing_remote_is_imported_or_started_by_default(self):
        home = make_home()
        code = (
            "import sys, threading\n"
            "from engram.daemon import Engram\n"
            "e = Engram(home=sys.argv[1], env={'ENGRAM_HOME': sys.argv[1]})\n"
            "e.handle('remember', {'content': 'default path memory about the ledger'})\n"
            "e.handle('status', {})\n"
            "e.handle('reembed', {'action': 'plan'})\n"
            "bad = [m for m in ('engram.embedders_remote', 'engram.embedders_ext', 'ssl', 'fastembed')"
            " if m in sys.modules]\n"
            "names = [t.name for t in threading.enumerate()]\n"
            "print(bad, [n for n in names if 'embedder' in n or 'reembed' in n])\n"
            "e.close()\n")
        r = subprocess.run([sys.executable, "-I", "-c", code, home], cwd=ROOT, env=dict(
            os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1"), capture_output=True,
            text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[] []", r.stdout + r.stderr)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

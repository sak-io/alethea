import os
import time
import unittest

from engram import consolidate, encode
from engram.cortex import Cortex

DATA = os.path.join(os.path.dirname(__file__), "data", "transcripts")


def fixture(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return [t.strip("\n") for t in f.read().split("\n=====\n")]


def gist_text(name):
    return " ".join(s for s, _ in consolidate.extract_gist(fixture(name)))


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class PrefilterTest(unittest.TestCase):
    def test_fence_collapsed_keeps_command_and_last_error(self):
        text = ("Try this:\n```\n$ make build\ncc -c a.c\ncc -c b.c\nb.c:3: error: boom\n"
                "ld: error: undefined symbol foo\n```\nThat is what happened.")
        out = consolidate.prefilter(text)
        self.assertIn("ran make build", out)
        self.assertIn("ld: error: undefined symbol foo", out)
        self.assertNotIn("cc -c a.c", out)
        self.assertIn("That is what happened.", out)

    def test_unterminated_fence_collapses_to_end(self):
        out = consolidate.prefilter("hello there friend\n```\nx = 1\ny = 2\nz = 3")
        self.assertEqual(out, ["hello there friend"])

    def test_diff_dropped_but_bullets_after_it_kept(self):
        out = consolidate.prefilter("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n\n- real bullet here")
        self.assertEqual(out, ["- real bullet here"])

    def test_python_trace_keeps_final_error(self):
        out = consolidate.prefilter(
            'Traceback (most recent call last):\n  File "a.py", line 1\n    x\nKeyError: nope\nafter that')
        self.assertEqual(out, ["KeyError: nope", "after that"])

    def test_js_trace_frames_dropped_error_kept(self):
        out = consolidate.prefilter("Error: kaboom\n    at f (a.js:1:1)\n    at g (a.js:2:2)\nnext line")
        self.assertEqual(out, ["Error: kaboom", "next line"])

    def test_long_terminal_output_collapsed(self):
        text = "$ ls -R\n" + "\n".join("file%d" % i for i in range(40)) + "\nls: error: denied\n\ndone"
        out = consolidate.prefilter(text)
        self.assertEqual(out, ["ran ls -R", "ls: error: denied", "done"])

    def test_sentence_split_on_newlines_bullets_and_punctuation(self):
        s = consolidate.split_sentences(["- first point here. Second point here! Third point here"])
        self.assertEqual(s, ["first point here.", "Second point here!", "Third point here"])


class GistFixtureTest(unittest.TestCase):
    def test_stacktrace(self):
        g = gist_text("stacktrace.txt")
        self.assertIn("pin parser to 2.9", g)
        self.assertIn("never upgrade the parser", g)
        self.assertIn("ValueError: unexpected token", g)
        self.assertNotIn("File \"sync", g)
        self.assertNotIn("pool.js", g)

    def test_diff(self):
        g = gist_text("diff.txt")
        self.assertIn("exponential backoff", g)
        self.assertIn("cap the backoff", g)
        self.assertNotIn("@@", g)
        self.assertNotIn("retries = 5", g)

    def test_terminal(self):
        g = gist_text("terminal.txt")
        self.assertIn("repo_root fallback", g)
        self.assertNotIn("test_c ... ok", g)

    def test_design_chat_keeps_decision_drops_chatter(self):
        g = gist_text("design_chat.txt")
        self.assertIn("decided to use a salted blake2b", g)
        self.assertIn("never compare vectors", g)
        self.assertNotIn("thanks", g)

    def test_at_most_six_and_no_near_duplicates(self):
        turns = ["We decided to pin the parser library to version %d because it broke." % i
                 for i in range(20)] + ["Always run the replay fixtures before every release."]
        g = consolidate.extract_gist(turns)
        self.assertLessEqual(len(g), 6)
        words = [consolidate._words(s) for s, _ in g]
        for i in range(len(words)):
            for j in range(i + 1, len(words)):
                self.assertLessEqual(consolidate._jaccard(words[i], words[j]), 0.6)

    def test_top_six_by_durability(self):
        turns = ["Alpha red tango walked over there.", "Bravo green whisky sang loudly today.",
                 "Charlie blue sierra jumped quickly again.", "Delta amber yankee waited calmly outside.",
                 "Echo violet zulu painted slowly upstairs.", "Foxtrot black papa laughed gently overhead.",
                 "Golf white romeo whispered softly downstairs."]
        turns.append("Never commit secrets because the key leaked last week.")
        g = [s for s, _ in consolidate.extract_gist(turns)]
        self.assertEqual(len(g), 6)
        self.assertIn("Never commit secrets because the key leaked last week.", g)


class AdversarialTest(unittest.TestCase):
    SHAPES = {
        "backticks": "`" * 100_000,
        "dashes": "-" * 100_000,
        "newlines": "\n" * 100_000,
        "dash_lines": "-\n" * 50_000,
        "at_frames": "    at " * 15_000,
        "at_lines": "    at x (a.js:1:1)\n" * 5_000,
        "fence_toggle": "```\n" * 25_000,
        "diff_lines": "diff --git\n@@ \n+\n-\n" * 10_000,
        "dots": ". " * 50_000,
        "words": "a " * 50_000,
        "digits_bullets": "1) " * 30_000,
        "cmd_lines": "$ x\nerror\n" * 20_000,
    }

    def test_each_shape_is_fast(self):
        for name, shape in self.SHAPES.items():
            t = time.time()
            consolidate.extract_gist([shape] * 3)
            self.assertLess(time.time() - t, 3.0, name)

    def test_many_turns_total_100kb(self):
        turns = ["`" * 500 + "\n" + "-" * 500 + "\n    at " * 10] * 100
        t = time.time()
        consolidate.extract_gist(turns)
        self.assertLess(time.time() - t, 3.0)


class ConsolidateTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Cortex(":memory:", clock=self.clock)

    def feed(self, turns, session="s1", step=10.0):
        for t in turns:
            self.clock.t += step
            self.c.journal_add(t, session_id=session, role="user", cwd="/w", repo_root="/w")

    def test_replay_splits_on_gap_per_session(self):
        self.feed(["one one one", "two two two"], "a")
        self.clock.t += consolidate.EPISODE_GAP + 1
        self.feed(["three three"], "a")
        self.feed(["other session"], "b", step=1)
        eps = consolidate.replay(self.c)
        self.assertEqual(sorted((e["session_id"], len(e["rows"])) for e in eps),
                         [("a", 1), ("a", 2), ("b", 1)])

    def test_gap_exactly_15_min_does_not_split(self):
        self.feed(["one one one"], "a")
        self.clock.t += consolidate.EPISODE_GAP
        self.c.journal_add("two two two", session_id="a")
        self.assertEqual(len(consolidate.replay(self.c)), 1)

    def test_short_low_salience_skipped_and_marked(self):
        self.feed(["ok", "thanks a lot", "cool"])
        r = consolidate.consolidate_episodes(self.c)
        self.assertEqual((r["skipped"], r["summaries"]), (1, 0))
        self.assertEqual(self.c.journal_pending(), [])
        self.assertEqual(self.c.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)

    def test_short_but_salient_episode_is_kept(self):
        self.feed(["We decided to pin the parser to 2.9 because 3.0 broke payload headers."])
        r = consolidate.consolidate_episodes(self.c)
        self.assertEqual(r["summaries"], 1)

    def test_summary_facts_and_abstracts_edges(self):
        self.feed(fixture("design_chat.txt"))
        r = consolidate.consolidate_episodes(self.c)
        self.assertEqual((r["summaries"], r["errors"]), (1, 0))
        self.assertGreaterEqual(r["facts"], 1)
        eps = self.c.db.execute("SELECT * FROM memories WHERE kind='episodic'").fetchall()
        self.assertEqual(len(eps), 1)
        self.assertIn("decided to use a salted blake2b", eps[0]["content"])
        edges = self.c.db.execute("SELECT src,dst FROM edges WHERE kind='abstracts'").fetchall()
        self.assertEqual(len(edges), r["facts"])
        self.assertTrue(all(e["dst"] == eps[0]["id"] for e in edges))
        self.assertEqual(self.c.journal_pending(), [])

    def test_idempotent(self):
        self.feed(fixture("stacktrace.txt"))
        consolidate.consolidate_episodes(self.c)
        n = self.c.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        r = consolidate.consolidate_episodes(self.c)
        self.assertEqual(r["episodes"], 0)
        self.assertEqual(self.c.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], n)

    def test_retry_after_failure_leaves_rows_pending_and_no_duplicates(self):
        self.feed(fixture("diff.txt"))

        class Boom(consolidate.Distiller):
            def distill(self, turns):
                return [("We decided to use exponential backoff because it is safer.", 0.6)]

        real = encode.encode
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("disk full")
            return real(*a, **kw)

        encode.encode = flaky
        try:
            r = consolidate.consolidate_episodes(self.c, distiller=Boom())
        finally:
            encode.encode = real
        self.assertEqual(r["errors"], 1)
        self.assertEqual(len(self.c.journal_pending()), 5)
        r = consolidate.consolidate_episodes(self.c, distiller=Boom())
        self.assertEqual(r["errors"], 0)
        self.assertEqual(self.c.journal_pending(), [])
        n = self.c.db.execute(
            "SELECT COUNT(*) FROM memories WHERE content LIKE 'Episode%'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_distiller_failure_falls_back_to_extractive(self):
        class Bad(consolidate.Distiller):
            def distill(self, turns):
                raise RuntimeError("llm down")

        self.feed(fixture("design_chat.txt"))
        r = consolidate.consolidate_episodes(self.c, distiller=Bad())
        self.assertEqual((r["summaries"], r["errors"]), (1, 0))

    def test_secrets_never_stored_unredacted(self):
        key = "AKIA" + "IOSFODNN7EXAMPLE"
        self.feed(["We decided to rotate the key %s because it leaked." % key, "a", "b", "c"])
        consolidate.consolidate_episodes(self.c)
        rows = self.c.db.execute("SELECT content, gist, keywords, meta FROM memories").fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertNotIn("IOSFODNN7EXAMPLE", " ".join(str(x) for x in tuple(r)))

    def test_large_adversarial_episode_consolidates_quickly(self):
        self.feed(["`" * 100_000, "-" * 100_000, "    at x\n" * 12_000, "\n" * 100_000,
                   "We decided to cap the retries because load spiked."])
        t = time.time()
        r = consolidate.consolidate_episodes(self.c)
        self.assertLess(time.time() - t, 10.0)
        self.assertEqual(r["errors"], 0)
        self.assertEqual(self.c.journal_pending(), [])


if __name__ == "__main__":
    unittest.main()

import time
import unittest

from engram.redact import redact, redact_with_kinds
from tests.data.secrets import NEGATIVE, POSITIVE


class RedactTests(unittest.TestCase):
    def test_positive_fixtures(self):
        for kind, text, secret in POSITIVE:
            with self.subTest(kind=kind, text=text[:30]):
                out, kinds = redact_with_kinds(text)
                self.assertNotIn(secret, out)
                self.assertIn("[REDACTED:", out)
                self.assertIn(kind, kinds)

    def test_pem_multiline_keeps_surroundings(self):
        out = redact("a\n-----BEGIN PRIVATE KEY-----\nxx\nyy\n-----END PRIVATE KEY-----\nb")
        self.assertEqual(out, "a\n[REDACTED:private_key]\nb")

    def test_pem_truncated_fails_closed(self):
        out = redact("x -----BEGIN PRIVATE KEY-----\nsecretbody")
        self.assertNotIn("secretbody", out)

    def test_negatives_unchanged(self):
        for t in NEGATIVE:
            with self.subTest(t=t):
                self.assertEqual(redact(t), t)

    def test_idempotent(self):
        for _, text, _ in POSITIVE:
            once = redact(text)
            self.assertEqual(redact(once), once)

    def test_empty(self):
        self.assertEqual(redact(""), "")

    def test_assignment_keeps_key_name(self):
        self.assertEqual(redact("password=hunter2xyz"), "password=[REDACTED:secret_assignment]")

    def test_adversarial_linear_time(self):
        n = 200_000
        cases = [
            "-----BEGIN PRIVATE KEY-----" * (n // 27),
            "eyJ" * (n // 3), "eyJaaaaaa." * (n // 10),
            "token=" * (n // 6), "password " * (n // 9),
            "a" * n, "ghp_" * (n // 4), "https://" * (n // 8),
            "Bearer " * (n // 7), "secret " + "A1" * (n // 2),
            "x:" * (n // 2) + "@", "sk-" * (n // 3), "key" * (n // 3),
            "AKIA" * (n // 4), "a." * (n // 2),
        ]
        for c in cases:
            t0 = time.perf_counter()
            redact(c)
            dt = time.perf_counter() - t0
            self.assertLess(dt, 1.0, (c[:20], dt))


if __name__ == "__main__":
    unittest.main()

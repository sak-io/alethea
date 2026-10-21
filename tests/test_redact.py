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

    def test_adversarial_1mb(self):
        n = 1_000_000
        cases = ["a." * (n // 2), "a-" * (n // 2), "=" * n, "sk-" * (n // 3),
                 "BEGIN" * (n // 5), '"' * n, "'" * n, 'password="' * (n // 10),
                 "token='" * (n // 7), "password: " * (n // 10), "_key=" * (n // 5),
                 "-----BEGIN PRIVATE KEY-----" * (n // 27), "x://" * (n // 4),
                 "x://a:" * (n // 6), "-u " * (n // 3), "key" * (n // 3),
                 "Bearer " * (n // 7), "secret " + "A1" * (n // 2),
                 "eyJ" * (n // 3), "AKIA" * (n // 4), "ghp_" * (n // 4),
                 "x://:" * (n // 5), "token is " * (n // 9)]
        for c in cases:
            t0 = time.perf_counter()
            redact(c)
            dt = time.perf_counter() - t0
            self.assertLess(dt, 1.0, (c[:20], dt))

    def test_new_leaks_closed(self):
        cases = {
            "curl -u admin:Secret123 http://x": "Secret123",
            "curl --user admin:Secret123 http://x": "Secret123",
            '{"password": "correct horse battery"}': "horse",
            'export API_TOKEN="abc def ghi"': "def",
            "password: 'my pass phrase'": "pass phrase",
            "postgres://u:p@ss@host/db": "p@ss",
            "redis://:pw0rd@host:6379": "pw0rd",
            "AIza" + "SyA1234567890abcdefghijklmnopqrstuv": "SyA1234",
            "STRIPE_KEY=abcd1234efgh5678": "abcd1234",
            "MY_SERVICE_CRED=abcd1234efgh": "abcd1234",
            "DB_PASS=hunter2xyz": "hunter2xyz",
            "password is Tr0ub4dor&3xyz": "Tr0ub4dor",
            "password: Tr0ub4dor&3xyz": "3xyz",
            "npm_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8": "a1B2c3",
            "hf_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5": "a1B2c3",
            "SG." + "a1B2c3D4e5F6g7H8i9J0k1.L2m3N4o5P6q7R8s9T0u1": "a1B2c3",
            "SK" + "0123456789abcdef0123456789abcdef": "0123456789ab",
        }
        for text, secret in cases.items():
            with self.subTest(text=text):
                out = redact(text)
                self.assertNotIn(secret, out)
                self.assertEqual(redact(out), out)

    def test_postgres_keeps_host(self):
        self.assertEqual(redact("postgres://u:p@ss@host/db"),
                         "postgres://[REDACTED:url_credentials]@host/db")

    def test_code_not_overredacted(self):
        for t in ["password = input('Password: ')", "token = tokenizer.encode(text)",
                  "secret = None", "auth_token = get_token()", "password: required",
                  "api_key = os.environ['API_KEY']", "token_path=/etc/engram/token",
                  "rotate the password policy", "password = user.password",
                  "api_key = config.api_key", "token = None"]:
            with self.subTest(t=t):
                self.assertEqual(redact(t), t)

    def test_non_str_contract(self):
        self.assertIsNone(redact(None))
        self.assertEqual(redact(5), 5)
        self.assertEqual(redact(b"password=hunter2xyz"), "password=[REDACTED:secret_assignment]")
        self.assertEqual(redact(b"\xff ok"), "\ufffd ok")


if __name__ == "__main__":
    unittest.main()

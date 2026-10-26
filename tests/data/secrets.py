"""Fixtures for redact tests. Fake values assembled at runtime to avoid scanner hits."""
_A = "A" * 0
POSITIVE = [
    ("aws_key", "key id " + "AKIA" + "IOSFODNN7EXAMPLE" + " here", "IOSFODNN7EXAMPLE"),
    ("github_token", "tok ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "ghp_aB3d"),
    ("github_token", "gho_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "gho_aB3d"),
    ("github_token", "github_pat_" + "11ABCDEFG0abcdefghij_klmnopqrstuvwx", "github_pat_11"),
    ("slack_token", "xoxb-" + "123456789012-abcdefABCDEF", "xoxb-1234"),
    ("stripe_key", "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc", "4eC39Hq"),
    ("api_key", "sk-" + "ant-api03-abcDEF123456ghiJKL789012mno", "abcDEF1234"),
    ("api_key", "use sk-" + "proj-Abc123Def456Ghi789Jkl012Mno", "Abc123Def4"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N", "eyJhbGci"),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\nafter", "MIIEow"),
    ("bearer", "Authorization: Bearer abc123.def456-ghi789", "abc123.def456"),
    ("url_credentials", "clone https://user:hunter2pass@example.com/x.git", "hunter2pass"),
    ("secret_assignment", "password=hunter2xyz", "hunter2xyz"),
    ("secret_assignment", 'api_key: "abcd1234efgh"', "abcd1234efgh"),
    ("secret_assignment", "export ALETHEA_TOKEN=s3cr3tvalue99", "s3cr3tvalue99"),
    ("high_entropy", "my credential Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm", "Zx9Qw8Er7"),
]
NEGATIVE = [
    "We discussed the token bucket algorithm and password policy in prose.",
    "commit 3b18e512dba79e4c8300dd08aeb37f8e728b8dad fixed it",
    "id 123e4567-e89b-12d3-a456-426614174000 is a uuid",
    "secret 0123456789abcdef0123456789abcdef01234567",
    "max_tokens: 100 and the bearer authentication scheme",
    "plain text with https://example.com/path and user@example.com",
    "hello world, nothing to see here.",
]

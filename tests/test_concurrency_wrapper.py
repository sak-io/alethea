"""Default-suite wrapper: runs scripts/test_concurrency.py (about 9 s) in CI discovery."""
from scripts.test_concurrency import ConcurrencyTests  # noqa: F401

"""Default-suite wrapper: runs scripts/test_hooks.py (T22) in CI discovery."""
from scripts.test_hooks import LiveDaemonTests, OfflineTests  # noqa: F401

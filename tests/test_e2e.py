"""Default-suite wrapper: runs the T21 end-to-end suite (scripts/test_e2e.py) too."""
from scripts.test_e2e import (ClientLifecycle, DaemonDownFailSoft, Durability,  # noqa: F401
                              McpLifecycle, MnemoLifecycle)

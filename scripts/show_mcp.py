#!/usr/bin/env python3
"""Print the MCP tools the server advertises (offline, no daemon needed)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engram import mcp_server  # noqa: E402

for name, spec in mcp_server.Server().tools().items():
    print("%s: %s" % (name, spec["description"]))
    print("  " + json.dumps(spec["inputSchema"]["properties"], sort_keys=True))

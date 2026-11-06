"""Convenience CLI entrypoint for running the Gmail MCP Server directly."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on sys.path when running main.py directly
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gmail_mcp.server import main

if __name__ == "__main__":
    sys.exit(main())

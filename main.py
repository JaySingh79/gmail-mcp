"""Convenience CLI entrypoint for running the Gmail MCP Server directly."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on sys.path when running main.py directly
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from gmail_mcp.server import main

if __name__ == "__main__":
    if "--auth" in sys.argv:
        from gmail_mcp.auth.oauth import main as auth_main
        sys.argv.remove("--auth")
        if "--login" not in sys.argv and "--status" not in sys.argv:
            sys.argv.append("--login")
        sys.exit(auth_main())
    sys.exit(main())

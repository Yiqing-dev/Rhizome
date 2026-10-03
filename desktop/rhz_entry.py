# SPDX-License-Identifier: Apache-2.0
"""PyInstaller entry point: `rhz.exe` is the backend server, the CLI and the MCP server at once."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # never crash on console output that the current code page cannot encode (e.g. zh text, cp1252)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass
    from rhizome.cli import app

    app(prog_name="rhz")

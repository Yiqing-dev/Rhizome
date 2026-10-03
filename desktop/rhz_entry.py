# SPDX-License-Identifier: Apache-2.0
"""PyInstaller entry point: `rhz.exe` is the backend server, the CLI and the MCP server at once."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Diagnostics: RHIZOME_HANG_DUMP=<seconds> prints every thread's stack to stderr if the process
    # is still running after that long (used by CI to locate hangs; off by default).
    import os

    _hang = os.environ.get("RHIZOME_HANG_DUMP")
    if _hang and _hang.isdigit() and sys.stderr is not None:
        import faulthandler

        faulthandler.dump_traceback_later(int(_hang), repeat=False, file=sys.stderr, exit=False)
    # Redirected output (log files, pipes) is UTF-8 so Chinese text stays readable; a real console
    # keeps its own encoding, and nothing ever crashes on a character the code page cannot show.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                if stream.isatty():
                    stream.reconfigure(errors="backslashreplace")
                else:
                    stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    from rhizome.cli import app

    app(prog_name="rhz")

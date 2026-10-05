# SPDX-License-Identifier: Apache-2.0
"""Log files: one per role (serve, mcp, cli), so the app and Claude's MCP server never fight over
one file (Windows cannot rename a file another process has open, which broke rotation and lost
records). Level from RHIZOME_LOG_LEVEL (default INFO)."""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler

from .config import Settings

NOISY = ("httpx", "httpcore", "mcp", "watchdog", "uvicorn.access", "multipart", "alembic.runtime.migration")


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotation that survives a file held open elsewhere (antivirus, an editor, a second process):
    on failure keep writing to the current file and try again later instead of losing records."""

    retry_after = 300.0

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self._next_try = 0.0

    def shouldRollover(self, record) -> int:  # noqa: N802 - logging API
        if time.monotonic() < self._next_try:
            return 0
        return super().shouldRollover(record)

    def doRollover(self) -> None:  # noqa: N802 - logging API
        try:
            super().doRollover()
        except OSError:
            self._next_try = time.monotonic() + self.retry_after
            if self.stream is None or self.stream.closed:
                self.stream = self._open()


def log_file(settings: Settings, role: str) -> str:
    return str(settings.logs_dir / f"rhizome-{role}.log")


def setup_logging(settings: Settings, verbose: bool = False, role: str = "cli") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_rhizome", False) for h in root.handlers):
        return
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = SafeRotatingFileHandler(log_file(settings, role), maxBytes=2_000_000, backupCount=5, encoding="utf-8",
                                 delay=True)
    fh.setFormatter(logging.Formatter(f"%(asctime)s %(levelname)s [{role} %(process)d] %(name)s: %(message)s"))
    fh._rhizome = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    level = os.environ.get("RHIZOME_LOG_LEVEL", "DEBUG" if verbose else "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

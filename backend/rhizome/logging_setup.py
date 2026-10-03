# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings


def setup_logging(settings: Settings, verbose: bool = False) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_rhizome", False) for h in root.handlers):
        return
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(settings.logs_dir / "rhizome.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh._rhizome = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

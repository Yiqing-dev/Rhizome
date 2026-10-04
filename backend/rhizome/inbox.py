# SPDX-License-Identifier: Apache-2.0
"""Inbox watcher: RXF files saved into the inbox are ingested automatically
(successes -> done/, failures -> error/ with a report to paste back into the chat)."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import get_settings

log = logging.getLogger(__name__)
# RXF is recognised by content, not by name: chat clients save exports as `rxf_version 1.yaml`,
# `export.txt`, `paper` (no extension) ... These suffixes are always treated as RXF, so a broken one
# still gets an error report; any other file is taken when it looks like RXF.
SUFFIXES = (".yaml", ".yml", ".rxf")
# companions and files still being written (browser downloads) are never ingested themselves
SKIP_SUFFIXES = (".pdf", ".part", ".partial", ".crdownload", ".download", ".tmp", ".swp")
_RXF_KEY = re.compile(r"^\s*(?:```[\w-]*\s*$\s*)?rxf_version\s*:", re.M)


def looks_like_rxf(p: Path, head: int = 65536) -> bool:
    try:
        with p.open("rb") as f:
            text = f.read(head).decode("utf-8-sig", errors="replace")
    except OSError:
        return False
    return bool(_RXF_KEY.search(text))


def is_candidate(p: Path) -> bool:
    """Cheap name checks only (the content check needs a fully written file)."""
    name = p.name.lower()
    return not (name.startswith((".", "~$")) or name.endswith(".error.txt") or p.suffix.lower() in SKIP_SUFFIXES)


def _stable(p: Path, wait: float = 0.5, tries: int = 20) -> bool:
    last = -1
    for _ in range(tries):
        if not p.exists():
            return False
        size = p.stat().st_size
        if size == last and size > 0:
            return True
        last = size
        time.sleep(wait)
    return p.exists()


def process(path: Path, on_result: Callable | None = None):
    """One file: ingest in its own transaction, then move it. Never raises (see ingest_file)."""
    from .pipeline.ingest import ingest_file

    if not is_candidate(path) or not _stable(path):
        return None
    if path.suffix.lower() not in SUFFIXES and not looks_like_rxf(path):
        log.info("inbox: %s is not an RXF export, left in place", path.name)
        return None
    res = ingest_file(path)
    log.info("inbox: %s -> %s", path.name, "ok" if res.ok else "error")
    if on_result:
        try:
            on_result(path, res)
        except Exception:
            log.exception("inbox callback failed")
    return res


def scan(on_result: Callable | None = None) -> int:
    inbox = get_settings().inbox
    n = 0
    for p in sorted(inbox.iterdir()) if inbox.exists() else []:
        if p.is_file() and is_candidate(p):
            try:
                if process(p, on_result) is not None:
                    n += 1
            except Exception:  # a single bad file must not stop the scan or kill the watcher thread
                log.exception("inbox: %s could not be processed", p)
    return n


class _Handler(FileSystemEventHandler):
    def __init__(self, on_result: Callable | None):
        self.on_result = on_result
        self._lock = threading.Lock()

    def _handle(self, path: str) -> None:
        p = Path(path)
        if p.parent.resolve() != get_settings().inbox.resolve():
            return
        with self._lock:
            try:
                process(p, self.on_result)
            except Exception:
                log.exception("inbox processing failed for %s", p)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(str(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(str(event.dest_path))


def watch(on_result: Callable | None = None, stop: threading.Event | None = None) -> None:
    inbox = get_settings().inbox
    inbox.mkdir(parents=True, exist_ok=True)
    scan(on_result)
    obs = Observer()
    obs.schedule(_Handler(on_result), str(inbox), recursive=False)
    obs.start()
    try:
        while not (stop and stop.is_set()):
            time.sleep(0.5)
    finally:
        obs.stop()
        obs.join()

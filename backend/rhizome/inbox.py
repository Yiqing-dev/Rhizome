# SPDX-License-Identifier: Apache-2.0
"""Inbox watcher: RXF files saved into the inbox are ingested automatically
(successes -> done/, failures -> error/ with a report to paste back into the chat)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import get_settings
from .db.session import session_scope

log = logging.getLogger(__name__)
SUFFIXES = (".yaml", ".yml", ".rxf")


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
    from .pipeline.ingest import ingest_path

    if path.suffix.lower() not in SUFFIXES or not _stable(path):
        return None
    with session_scope() as s:
        res = ingest_path(s, path)
    log.info("inbox: %s -> %s", path.name, "ok" if res.ok else "error")
    if on_result:
        on_result(path, res)
    return res


def scan(on_result: Callable | None = None) -> int:
    inbox = get_settings().inbox
    n = 0
    for p in sorted(inbox.iterdir()) if inbox.exists() else []:
        if p.is_file() and p.suffix.lower() in SUFFIXES:
            process(p, on_result)
            n += 1
    return n


class _Handler(FileSystemEventHandler):
    def __init__(self, on_result: Callable | None):
        self.on_result = on_result
        self._lock = threading.Lock()

    def _handle(self, path: str) -> None:
        p = Path(path)
        if p.parent != get_settings().inbox:
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

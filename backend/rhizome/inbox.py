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
RESCAN_SECONDS = 60
_RXF_KEY = re.compile(r"^[ \t]*rxf_version[ \t]*:", re.M)  # anywhere: prose or a fence may come first


def looks_like_rxf(p: Path, head: int = 65536) -> bool:
    try:
        with p.open("rb") as f:
            data = f.read(head)
    except OSError:
        return False
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 exports (Windows Notepad "Unicode")
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8-sig", errors="replace")
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


# One lock for event handling and scans (a scan and an event for the same file must not race),
# and the watcher's state for the home page.
_LOCK = threading.Lock()
STATUS: dict = {"watching": False, "last_scan": None, "pending": [], "ignored": []}


def status() -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in STATUS.items()}


def scan(on_result: Callable | None = None) -> int:
    """Process every candidate in the inbox, then record what is still waiting (left in place:
    library busy, model missing) and what was not recognised as RXF."""
    inbox = get_settings().inbox
    n = 0
    with _LOCK:
        for p in sorted(inbox.iterdir()) if inbox.exists() else []:
            if p.is_file() and is_candidate(p):
                try:
                    if process(p, on_result) is not None:
                        n += 1
                except Exception:  # a single bad file must not stop the scan or kill the watcher thread
                    log.exception("inbox: %s could not be processed", p)
        pending, ignored = [], []
        for p in sorted(inbox.iterdir()) if inbox.exists() else []:
            if p.is_file() and is_candidate(p):
                (pending if p.suffix.lower() in SUFFIXES or looks_like_rxf(p) else ignored).append(p.name)
        from datetime import datetime, timezone

        STATUS.update(last_scan=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      pending=pending, ignored=ignored)
    return n


class _Handler(FileSystemEventHandler):
    def __init__(self, on_result: Callable | None):
        self.on_result = on_result

    def _handle(self, path: str) -> None:
        p = Path(path)
        if p.parent.resolve() != get_settings().inbox.resolve():
            return
        with _LOCK:
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


def _start_observer(inbox: Path, handler: _Handler):
    obs = Observer()
    obs.schedule(handler, str(inbox), recursive=False)
    obs.start()
    return obs


def watch(on_result: Callable | None = None, stop: threading.Event | None = None) -> None:
    """Watch first, then scan what is already there (files arriving during a long backlog are not
    missed); rescan every RESCAN_SECONDS (files left in place, events lost while the laptop slept
    or on a synced / network folder); restart the observer if it died."""
    inbox = get_settings().inbox
    inbox.mkdir(parents=True, exist_ok=True)
    handler = _Handler(on_result)
    obs = _start_observer(inbox, handler)
    STATUS["watching"] = True
    scan(on_result)
    last_scan = time.monotonic()
    backoff = 1.0
    try:
        while not (stop and stop.is_set()):
            time.sleep(0.5)
            if not obs.is_alive():
                log.warning("inbox watcher stopped; restarting in %.0f s", backoff)
                STATUS["watching"] = False
                time.sleep(backoff)
                try:
                    obs = _start_observer(inbox, handler)
                    STATUS["watching"], backoff = True, 1.0
                except Exception:  # noqa: BLE001 - e.g. the folder is on a disconnected drive
                    log.exception("could not restart the inbox watcher")
                    backoff = min(backoff * 2, 300.0)
                    continue
            if time.monotonic() - last_scan > RESCAN_SECONDS:
                last_scan = time.monotonic()
                try:
                    scan(on_result)
                except Exception:  # noqa: BLE001
                    log.exception("inbox rescan failed")
    finally:
        STATUS["watching"] = False
        obs.stop()
        obs.join()

# SPDX-License-Identifier: Apache-2.0
"""HTTP client for the external lookups (OpenAlex, NCBI, BioStudies, Zenodo, forges).

TLS uses the operating system's certificate store when `truststore` is available, so a corporate
or campus proxy that re-signs HTTPS with its own root (installed in Windows) works; the proxy
itself comes from HTTPS_PROXY / HTTP_PROXY. Failures are counted per host and shown in Settings
and `rhz doctor`, because a lookup that fails only says 'unverified' at ingest time."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import __version__
from ..config import get_settings

_ssl_context: Any = None


def _verify() -> Any:
    global _ssl_context
    if _ssl_context is None:
        try:
            import ssl

            import truststore

            _ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:  # noqa: BLE001 - fall back to certifi
            _ssl_context = True
    return _ssl_context


def client(timeout: float = 15.0) -> httpx.Client:
    s = get_settings()
    ua = f"Rhizome/{__version__}" + (f" (mailto:{s.contact_email})" if s.contact_email else "")
    return httpx.Client(timeout=timeout, headers={"User-Agent": ua}, follow_redirects=True, verify=_verify())


_status: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _host(url: str) -> str:
    return urlparse(url).hostname or url


def record_failure(url: str, error: str) -> None:
    with _lock:
        h = _status.setdefault(_host(url), {"ok": 0, "failed": 0})
        h["failed"] += 1
        h["last_error"], h["last_error_at"] = error[:300], time.time()


def record_success(url: str) -> None:
    with _lock:
        h = _status.setdefault(_host(url), {"ok": 0, "failed": 0})
        h["ok"] += 1
        h["last_ok_at"] = time.time()


def network_status() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _status.items()}

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import httpx

from .. import __version__
from ..config import get_settings


def client() -> httpx.Client:
    s = get_settings()
    ua = f"Rhizome/{__version__}" + (f" (mailto:{s.contact_email})" if s.contact_email else "")
    return httpx.Client(timeout=15.0, headers={"User-Agent": ua}, follow_redirects=True)

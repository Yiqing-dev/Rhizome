# SPDX-License-Identifier: Apache-2.0
"""Backend i18n (gettext catalogs via Babel).

Message ids are stable keys (``rxf.report_title``); every key must exist in every locale
(checked by ``scripts/check_i18n.py`` in CI). Text is chosen by the *interface* language;
paper content is never translated.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from babel.messages.pofile import read_po

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
SUPPORTED = ("en", "zh_CN")


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = LOCALES_DIR / lang / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as f:
        cat = read_po(f, locale=lang)
    return {m.id: m.string for m in cat if m.id and m.string}


def _(key: str, lang: str | None = None, **kwargs: object) -> str:
    if lang is None:
        from ..config import ui_language

        lang = ui_language()
    if lang not in SUPPORTED:
        lang = "en"
    text = catalog(lang).get(key) or catalog("en").get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def format_date(dt, lang: str | None = None) -> str:
    from babel.dates import format_date as _fd

    from ..config import ui_language

    return _fd(dt, format="medium", locale=lang or ui_language())

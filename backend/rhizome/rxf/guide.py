# SPDX-License-Identifier: Apache-2.0
"""The export instructions the user puts into the Claude / ChatGPT Project (served by the CLI, the
API, the UI and the MCP server, so the installed app needs nothing on PATH)."""

from __future__ import annotations

from importlib import resources


def instructions(lang: str | None = None) -> str:
    if lang is None:
        from ..config import ui_language

        lang = ui_language()
    name = "export-instructions.zh.md" if lang.lower().startswith("zh") else "export-instructions.en.md"
    return resources.files("rhizome").joinpath("data", name).read_text("utf-8")

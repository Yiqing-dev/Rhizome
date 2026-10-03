#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI gate: zh-CN and en resources have identical keys (backend gettext + frontend JSON), and the
frontend has no hard-coded user-visible strings in JSX."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def po_keys(path: Path) -> dict[str, str]:
    keys, cur = {}, None
    for line in path.read_text("utf-8").splitlines():
        if line.startswith("msgid "):
            cur = json.loads(line[6:])
        elif line.startswith("msgstr ") and cur:
            keys[cur] = json.loads(line[7:])
            cur = None
    return keys


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def compare(name: str, a: dict, b: dict) -> None:
    for k in sorted(set(a) ^ set(b)):
        errors.append(f"{name}: key '{k}' missing in {'zh' if k in a else 'en'}")
    for k in sorted(set(a) & set(b)):
        if not a[k] or not b[k]:
            errors.append(f"{name}: key '{k}' has an empty translation")
        pa, pb = set(re.findall(r"\{(\w+)\}", a[k])), set(re.findall(r"\{(\w+)\}", b[k]))
        pa |= set(re.findall(r"\{\{(\w+)\}\}", a[k]))
        pb |= set(re.findall(r"\{\{(\w+)\}\}", b[k]))
        if pa != pb:
            errors.append(f"{name}: key '{k}' placeholders differ: {sorted(pa)} vs {sorted(pb)}")


loc = ROOT / "backend" / "rhizome" / "i18n" / "locales"
compare("backend", po_keys(loc / "en" / "LC_MESSAGES" / "messages.po"),
        po_keys(loc / "zh_CN" / "LC_MESSAGES" / "messages.po"))

fe = ROOT / "frontend" / "src" / "locales"
if fe.exists():
    en = flatten(json.loads((fe / "en.json").read_text("utf-8")))
    zh = flatten(json.loads((fe / "zh-CN.json").read_text("utf-8")))
    compare("frontend", en, zh)
    used = set()
    jsx_text = re.compile(r"(?<![=\-\s])>\s*([A-Za-z\u4e00-\u9fff][^<>{}\n]*?)\s*<")
    allow = {"bge-m3", "bge-reranker-v2-m3", "mDeBERTa-v3-base-xnli", "PDF"}  # proper names, never translated
    for f in (ROOT / "frontend" / "src").rglob("*.tsx"):
        src = f.read_text("utf-8")
        used |= set(re.findall(r"""\bt\(\s*['"]([\w.-]+)['"]""", src))
        for m in jsx_text.finditer(src):
            if m.group(1).strip() not in allow and not m.group(1).strip().startswith("//"):
                errors.append(f"{f.relative_to(ROOT)}: hard-coded UI text '{m.group(1).strip()[:40]}'")
    for k in sorted(used - set(en)):
        errors.append(f"frontend: t('{k}') used but not defined")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("i18n OK")

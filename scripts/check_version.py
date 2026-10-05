#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI gate: the version is written in several files; they must all agree.
Usage: python scripts/check_version.py [--expect X.Y.Z]"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
places = {
    "backend/pyproject.toml": re.search(r'^version = "([^"]+)"', (ROOT / "backend/pyproject.toml").read_text("utf-8"), re.M),
    "backend/rhizome/__init__.py": re.search(r'__version__ = "([^"]+)"', (ROOT / "backend/rhizome/__init__.py").read_text("utf-8")),
    "frontend/package.json": json.loads((ROOT / "frontend/package.json").read_text("utf-8")).get("version"),
    "desktop/package.json": json.loads((ROOT / "desktop/package.json").read_text("utf-8")).get("version"),
    "desktop/src-tauri/tauri.conf.json": json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text("utf-8")).get("version"),
    "desktop/src-tauri/Cargo.toml": re.search(r'^version = "([^"]+)"', (ROOT / "desktop/src-tauri/Cargo.toml").read_text("utf-8"), re.M),
}
lock = re.search(r'name = "rhizome-desktop"\nversion = "([^"]+)"', (ROOT / "desktop/src-tauri/Cargo.lock").read_text("utf-8"))
places["desktop/src-tauri/Cargo.lock"] = lock
found = {k: (v.group(1) if hasattr(v, "group") else v) for k, v in places.items()}
versions = set(found.values())
expect = sys.argv[sys.argv.index("--expect") + 1] if "--expect" in sys.argv else None
if expect:
    versions.add(expect)
if len(versions) != 1 or None in versions:
    for k, v in found.items():
        print(f"  {k}: {v}")
    sys.exit(f"version mismatch{' (expected ' + expect + ')' if expect else ''}")
print(f"version {versions.pop()} consistent across {len(found)} files")

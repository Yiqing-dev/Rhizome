#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write THIRD-PARTY-NOTICES.txt for the installer: every Python package frozen into the backend
(pip-licenses JSON on stdin or --pip FILE), the npm packages bundled into the UI (--npm FILE from
license-checker --json), the Rust crates linked into the shell (--cargo FILE from
`cargo metadata --format-version 1`), and the bundled fonts.

Usage: python scripts/gen_third_party_notices.py --pip pip.json --npm npm.json --cargo cargo.json -o OUT"""

import argparse
import json
from pathlib import Path

FONTS = [("Inter", "SIL Open Font License 1.1", "https://github.com/rsms/inter"),
         ("Source Serif 4", "SIL Open Font License 1.1", "https://github.com/adobe-fonts/source-serif")]

ap = argparse.ArgumentParser()
ap.add_argument("--pip")
ap.add_argument("--npm")
ap.add_argument("--cargo")
ap.add_argument("-o", "--out", required=True)
a = ap.parse_args()

lines = ["Rhizome ships the following third-party software. Each component keeps its own licence;",
         "the texts are available from the projects linked below and in the Python / npm / crates packages.", ""]


def section(title, rows):
    lines.append(f"== {title} ({len(rows)}) ==")
    for name, ver, lic, url in sorted(rows, key=lambda r: r[0].lower()):
        lines.append(f"{name} {ver}  {lic}" + (f"  {url}" if url else ""))
    lines.append("")


if a.pip:
    data = json.loads(Path(a.pip).read_text("utf-8"))
    section("Python packages (backend)", [(d["Name"], d["Version"], d["License"], d.get("URL") or "") for d in data
                                          if d["Name"].lower() not in ("rhizome", "pyinstaller", "pyinstaller-hooks-contrib")])
if a.npm:
    data = json.loads(Path(a.npm).read_text("utf-8"))
    rows = []
    for k, v in data.items():
        name, _, ver = k.rpartition("@")
        if name in ("rhizome-frontend", ""):
            continue
        lic = v.get("licenses", "")
        rows.append((name, ver, lic if isinstance(lic, str) else " OR ".join(lic), v.get("repository") or ""))
    section("npm packages (user interface)", rows)
if a.cargo:
    meta = json.loads(Path(a.cargo).read_text("utf-8"))
    rows = [(p["name"], p["version"], p.get("license") or "see repository", p.get("repository") or "")
            for p in meta.get("packages", []) if p["name"] != "rhizome-desktop"]
    section("Rust crates (desktop shell)", rows)
section("Fonts", [(n, "", lic, url) for n, lic, url in FONTS])
Path(a.out).write_text("\n".join(lines) + "\n", "utf-8")
print(f"wrote {a.out} ({len(lines)} lines)")

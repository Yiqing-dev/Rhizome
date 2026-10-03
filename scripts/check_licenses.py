#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI gate: no GPL / AGPL dependencies (LGPL is allowed). Reads `pip-licenses --format=json` or
`license-checker --json` output from stdin: python scripts/check_licenses.py pip|npm < report.json"""

import json
import re
import sys

STRONG_COPYLEFT = re.compile(r"(?<![L])(A?GPL|GNU General Public|GNU Affero)", re.I)
LESSER = re.compile(r"LGPL|Lesser General Public|Library General Public", re.I)
IGNORE = {"rhizome", "rhizome-frontend"}

kind = sys.argv[1]
data = json.load(sys.stdin)
items = ([(d["Name"], d["License"]) for d in data] if kind == "pip"
         else [(k.rsplit("@", 1)[0], v.get("licenses", "")) for k, v in data.items()])
bad = []
for name, lic in items:
    lic = lic if isinstance(lic, str) else " ".join(lic)
    if name in IGNORE:
        continue
    # dual-licensed packages that offer a permissive option are fine ("MIT OR GPL-3.0")
    options = re.split(r"\s+OR\s+|;\s*", lic.strip("()"))
    if all(STRONG_COPYLEFT.search(o) and not LESSER.search(o) for o in options if o):
        bad.append(f"{name}: {lic}")
if bad:
    print("Copyleft licenses not allowed:\n  " + "\n  ".join(bad))
    sys.exit(1)
print(f"licenses OK ({len(items)} packages)")

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI gate: no GPL / AGPL dependencies (LGPL is allowed). Reads `pip-licenses --format=json` or
`license-checker --json` output from stdin: python scripts/check_licenses.py pip|npm < report.json

A package that offers several licences (a list, "MIT OR GPL-3.0", "(MIT AND Apache-2.0)") is fine
when any option is not strong copyleft. Empty input is an error: a broken pipe must not pass."""

import json
import re
import sys

STRONG_COPYLEFT = re.compile(r"(?<![L])(A?GPL|GNU General Public|GNU Affero)", re.I)
LESSER = re.compile(r"LGPL|Lesser General Public|Library General Public", re.I)
IGNORE = {"rhizome", "rhizome-frontend", "pyinstaller", "pyinstaller-hooks-contrib"}  # build tools, not shipped

if len(sys.argv) != 2 or sys.argv[1] not in ("pip", "npm"):
    sys.exit("usage: check_licenses.py pip|npm < report.json")
kind = sys.argv[1]
raw = sys.stdin.read().strip()
if not raw:
    sys.exit("check_licenses: empty input (the licence report did not run)")
data = json.loads(raw)
items = ([(d["Name"], d["License"]) for d in data] if kind == "pip"
         else [(k.rsplit("@", 1)[0], v.get("licenses", "")) for k, v in data.items()])
if not items:
    sys.exit("check_licenses: the report lists no packages")


def options(lic) -> list[str]:
    parts = lic if isinstance(lic, list) else [lic]
    out: list[str] = []
    for part in parts:
        out += [o.strip() for o in re.split(r"\s+OR\s+|;\s*|\s+or\s+", str(part).strip("()")) if o.strip()]
    return out


bad = []
for name, lic in items:
    if name in IGNORE:
        continue
    opts = options(lic)
    if opts and all(STRONG_COPYLEFT.search(o) and not LESSER.search(o) for o in opts):
        bad.append(f"{name}: {lic}")
if bad:
    print("Copyleft licenses not allowed:\n  " + "\n  ".join(bad))
    sys.exit(1)
print(f"licenses OK ({len(items)} packages)")

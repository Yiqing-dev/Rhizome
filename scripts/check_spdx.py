#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI gate: every source file carries an SPDX-License-Identifier: Apache-2.0 header."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = {".py", ".ts", ".tsx", ".rs", ".css", ".mako"}
SKIP_PARTS = {"node_modules", "dist", "target", "build", "__pycache__"}
files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                       capture_output=True, text=True, check=True).stdout.split()
missing = []
for rel in files:
    p = ROOT / rel
    if p.suffix not in EXT or SKIP_PARTS & set(p.parts) or not p.exists():
        continue
    head = p.read_text("utf-8", errors="replace")[:400]
    if "SPDX-License-Identifier: Apache-2.0" not in head:
        missing.append(rel)
if missing:
    print("Missing SPDX header:\n  " + "\n  ".join(missing))
    sys.exit(1)
print("SPDX OK")

# SPDX-License-Identifier: Apache-2.0
# PyInstaller spec for the backend bundled into the desktop app (one-folder build).
#   pip install ./backend pyinstaller
#   cd desktop && pyinstaller rhz.spec --noconfirm --distpath build --workpath build/pyi
# Output: build/server/rhz(.exe) + build/server/_internal/  -> installed as <app>\server\
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821  (SPECPATH is provided by PyInstaller)

datas = []
datas += collect_data_files("rhizome", includes=["web/**", "i18n/**/*.po", "data/*"])
# Alembic loads env.py and version scripts from disk, so they ship as files, not bytecode
datas += collect_data_files("rhizome", include_py_files=True, includes=["migrations/**"])
# CLDR locale data: only what the two UI languages resolve to (full set is ~30 MB)
datas += collect_data_files("babel", includes=["global.dat", "locale-data/root.dat", "locale-data/en.dat",
                                               "locale-data/en_*.dat", "locale-data/zh.dat", "locale-data/zh_*.dat",
                                               "py.typed"])
datas += collect_data_files("jsonschema_specifications")   # JSON Schema meta-schemas
datas += collect_data_files("fsrs")

hidden = []
for pkg in ("rhizome", "uvicorn", "mcp", "alembic", "watchdog", "sqlalchemy.dialects.sqlite", "pydantic_settings"):
    hidden += collect_submodules(pkg)

# heavy optional extras are never bundled (models download at runtime only when installed separately)
excludes = ["torch", "transformers", "sentence_transformers", "onnxruntime", "llama_cpp", "graspologic",
            "tkinter", "matplotlib", "IPython", "pytest", "playwright"]

icon = str(ROOT / "desktop" / "src-tauri" / "icons" / "icon.ico")

a = Analysis(
    [str(ROOT / "desktop" / "rhz_entry.py")],
    pathex=[str(ROOT / "backend")],
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
# PyInstaller's own babel hook collects every CLDR locale; keep root/en/zh only (~30 MB less)
import re as _re

_KEEP_LOCALE = _re.compile(r"babel[\\/]locale-data[\\/](root|en|en_[A-Za-z_]+|zh|zh_[A-Za-z_]+)\.dat$")
a.datas = [d for d in a.datas
           if not _re.search(r"babel[\\/]locale-data[\\/]", d[0]) or _KEEP_LOCALE.search(d[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("X utf8_mode=1", None, "OPTION")],
    exclude_binaries=True,
    name="rhz",
    console=True,           # also the CLI / MCP server; the desktop shell starts it without a window
    icon=icon if sys.platform == "win32" else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="server", upx=False)

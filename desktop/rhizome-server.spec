# SPDX-License-Identifier: Apache-2.0
# PyInstaller spec: one-file backend used as the Tauri sidecar.
# Build:  cd desktop && pyinstaller rhizome-server.spec
# Then copy dist/rhizome-server(.exe) to src-tauri/binaries/rhizome-server-<target-triple>(.exe)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("rhizome", includes=["web/**", "i18n/**/*.po", "migrations/**", "data/*", "rxf/schemas/*"])
hidden = collect_submodules("rhizome") + collect_submodules("uvicorn") + ["babel.numbers"]

a = Analysis(["sidecar.py"], pathex=[], datas=datas, hiddenimports=hidden,
             excludes=["torch", "transformers", "sentence_transformers", "onnxruntime", "llama_cpp", "graspologic"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="rhizome-server", console=False, upx=False)

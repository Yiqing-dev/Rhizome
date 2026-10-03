# Rhizome desktop shell

Tauri v2 window around the Python backend (bundled with PyInstaller as the `rhizome-server` sidecar).
Models are not bundled; they download on first use. Output: a Windows MSI.

```powershell
# 1. UI into the Python package
cd frontend; npm ci; npm run build; cd ..
# 2. backend sidecar
pip install ./backend pyinstaller
cd desktop; pyinstaller rhizome-server.spec
copy dist\rhizome-server.exe src-tauri\binaries\rhizome-server-x86_64-pc-windows-msvc.exe
# 3. icons (once): npx @tauri-apps/cli icon path\to\logo.png
# 4. MSI
npx @tauri-apps/cli build
```

The release workflow (`.github/workflows/release.yml`) runs the same steps on a clean Windows runner.
Updates use the Tauri updater; configure `plugins.updater.endpoints` / `pubkey` before the first public
release. The backend backs up the database before every schema migration.

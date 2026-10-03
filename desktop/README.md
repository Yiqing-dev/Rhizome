# Rhizome for Windows

A Tauri v2 window around the Python backend. The backend is frozen with PyInstaller (one-folder) and
installed next to the app, so nothing else needs to be installed: no Python, no Node, no Docker.

## What gets installed where

| Location | Contents |
| --- | --- |
| Install folder (chosen in the installer; default `%LOCALAPPDATA%\Programs\Rhizome` per user, or `Program Files` for all users) | `Rhizome.exe` (window), `server\rhz.exe` + `server\_internal\` (backend, CLI, MCP server), `rhz.cmd` (CLI shim) |
| Data folder (default `%APPDATA%\Rhizome`; movable in Settings) | `rhizome.db`, `raw\` (your RXF exports and PDFs), `inbox\`, `backups\`, `logs\`, `models\`, `settings.json` |
| `%APPDATA%\Rhizome\location.json` | pointer written when you move the library elsewhere |
| WebView2 | installed by the installer if missing: `*-setup.exe` downloads it, `*-offline-setup.exe` carries it |

Uninstalling or upgrading never touches the data folder. **Portable mode:** put an empty file named
`portable` next to `Rhizome.exe` and the library lives in `data\` inside the install folder.

## How it runs

`Rhizome.exe` picks a free localhost port, generates an access token, starts `server\rhz.exe serve`
without a console window, waits until it accepts connections and opens the UI. Closing the window
stops the backend. If the window process dies, the backend notices (`RHIZOME_PARENT_PID`) and exits.
A second launch focuses the existing window. Backend output: `%APPDATA%\dev.rhizome.desktop\logs\`.

The same `rhz.exe` is the command-line tool (`rhz.cmd search …`; add the install folder to PATH to
use `rhz` anywhere) and the MCP server for Claude Desktop (Settings → *Connect to Claude Desktop*
writes `claude_desktop_config.json`, or use `"command": "<install>\\server\\rhz.exe", "args": ["mcp"]`).
When the app is running, the CLI and MCP server talk to it; otherwise they open the library directly.

Not bundled: the optional model packages (bge-m3, reranker, NLI, local LLM) — the built-in lightweight
models are used. The installer is not code-signed yet, so SmartScreen will warn on first run.

## Build

CI does this on a clean Windows runner (`.github/workflows/release.yml`, run manually or on a `v*` tag):

```powershell
cd frontend; npm ci; npm run build; cd ..                     # UI into backend/rhizome/web
pip install ./backend pyinstaller
cd desktop
pyinstaller rhz.spec --noconfirm --distpath build --workpath build/pyi   # -> build\server\
npm ci
npx tauri build --bundles nsis                               # -> src-tauri\target\release\bundle\nsis
npx tauri build --bundles nsis --config src-tauri/offline.conf.json   # offline variant
```

Icons are generated from `docs/logo.png` with `npm run icons`.

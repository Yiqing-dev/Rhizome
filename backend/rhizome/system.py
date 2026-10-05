# SPDX-License-Identifier: Apache-2.0
"""Desktop integration: where things live, opening folders, Claude Desktop MCP setup,
moving the data directory, and the running-server marker other processes use to find the app."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import (
    app_dir,
    get_settings,
    platform_data_dir,
    portable_dir,
    set_data_dir_pointer,
)

SERVER_FILE = "server.json"


# ---- running-server marker -------------------------------------------------------------

def write_server_marker(url: str) -> None:
    st = get_settings()
    st.data_dir.mkdir(parents=True, exist_ok=True)
    (st.data_dir / SERVER_FILE).write_text(json.dumps({"url": url, "pid": os.getpid()}), "utf-8")


def clear_server_marker() -> None:
    p = get_settings().data_dir / SERVER_FILE
    try:
        if p.exists() and json.loads(p.read_text("utf-8-sig")).get("pid") == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass


def running_server_url() -> str | None:
    p = get_settings().data_dir / SERVER_FILE
    try:
        return json.loads(p.read_text("utf-8-sig")).get("url") if p.exists() else None
    except (OSError, ValueError):
        return None


# ---- info / folders -------------------------------------------------------------------------

def models_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def local_llm_available() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except ImportError:
        return False


def cli_command() -> list[str]:
    """How another program (Claude Desktop) should start this installation's CLI."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = shutil.which("rhz")
    return [exe] if exe else [sys.executable, "-m", "rhizome.cli"]


def info() -> dict[str, Any]:
    st = get_settings()
    return {
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": sys.platform,
        "app_dir": str(app_dir()) if app_dir() else None,
        "portable": portable_dir() is not None,
        "data_dir": str(st.data_dir),
        "default_data_dir": str(platform_data_dir()),
        "inbox": str(st.inbox),
        "logs": str(st.logs_dir),
        "models_dir": str(st.models_dir),
        "models_available": models_available(),
        "local_llm_available": local_llm_available(),
        "cli": cli_command(),
        "claude_desktop": claude_desktop_snippet(),
        "claude_config_paths": [str(p) for p in claude_config_paths()],
        "backups": _backups(st),
        "model_problems": _model_problems(st),
    }


def _model_problems(st) -> list[dict[str, str]]:
    from .ml import model_problems

    return model_problems(st)


def _backups(st) -> dict[str, Any]:
    from .db.session import backup_status

    try:
        return backup_status(st)
    except OSError as e:  # e.g. a backup folder on a disconnected drive
        return {"dir": str(st.backups_dir), "count": 0, "bytes": 0, "last": None, "last_daily": None,
                "error": str(e)}


def _folder(target: str) -> Path:
    st = get_settings()
    paths = {"data": st.data_dir, "inbox": st.inbox, "logs": st.logs_dir, "models": st.models_dir,
             "backups": st.backups_dir}
    if target not in paths:
        raise ValueError(f"unknown folder {target}")
    paths[target].mkdir(parents=True, exist_ok=True)
    return paths[target]


def open_folder(target: str) -> Path:
    path = _folder(target)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return path


# ---- Claude Desktop -------------------------------------------------------------------------

def claude_config_paths() -> list[Path]:
    """Candidate claude_desktop_config.json locations (classic installer and MSIX/Store install)."""
    out: list[Path] = []
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        out.append(appdata / "Claude" / "claude_desktop_config.json")
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        packages = local / "Packages"
        if packages.exists():
            for pkg in packages.glob("Claude_*"):
                out.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:
        out.append(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Claude"
                   / "claude_desktop_config.json")
    return out


def claude_desktop_snippet() -> dict[str, Any]:
    """The `rhizome` MCP entry. The data directory is pinned only when this process was given one
    explicitly (RHIZOME_DATA_DIR): a library found through the pointer file or portable mode is
    found the same way by `rhz mcp`, and pinning it would go stale after moving the library."""
    cmd = cli_command()
    entry: dict[str, Any] = {"command": cmd[0], "args": [*cmd[1:], "mcp"]}
    explicit = os.environ.get("RHIZOME_DATA_DIR")
    if explicit and portable_dir() is None:
        entry["env"] = {"RHIZOME_DATA_DIR": str(Path(explicit).resolve())}
    return {"mcpServers": {"rhizome": entry}}


def unpin_claude_configs(old: Path) -> list[str]:
    """Drop a RHIZOME_DATA_DIR that pins Claude Desktop's `rhizome` server to ``old`` (written by an
    earlier version or by hand), so it follows the library after a move. Keeps a .bak."""
    changed = []
    for p in claude_config_paths():
        try:
            data = json.loads(p.read_text("utf-8-sig").strip() or "{}") if p.exists() else None
        except (OSError, ValueError):
            continue
        entry = (data or {}).get("mcpServers", {}).get("rhizome")
        env = entry.get("env") if isinstance(entry, dict) else None
        pinned = env.get("RHIZOME_DATA_DIR") if isinstance(env, dict) else None
        if not pinned or Path(pinned).resolve() != Path(old).resolve():
            continue
        shutil.copy2(p, p.with_suffix(".json.bak"))
        env.pop("RHIZOME_DATA_DIR")
        if not env:
            entry.pop("env")
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        changed.append(str(p))
    return changed


def install_claude_desktop() -> dict[str, Any]:
    """Merge the `rhizome` MCP server into every existing Claude Desktop config (or create the
    classic one). Other servers and settings are preserved; a .bak copy is kept."""
    snippet = claude_desktop_snippet()["mcpServers"]["rhizome"]
    paths = claude_config_paths()
    targets = [p for p in paths if p.parent.exists()] or paths[:1]
    written = []
    for p in targets:
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8-sig").strip() or "{}")
            except ValueError:
                raise ValueError(f"{p} is not valid JSON; fix or remove it first") from None
            shutil.copy2(p, p.with_suffix(".json.bak"))
        data.setdefault("mcpServers", {})["rhizome"] = snippet
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        written.append(str(p))
    return {"written": written, "entry": snippet, "restart_claude": True}


# ---- data directory -------------------------------------------------------------------------------

_SKIP = {SERVER_FILE, "token"}


def move_data_dir(path: str | None, copy: bool = True) -> dict[str, Any]:
    """Point the app at another data directory (None = default). Existing data is copied when the
    target is empty; nothing is deleted. Takes effect after restarting the app."""
    if portable_dir() is not None:
        raise ValueError("portable installation: data always lives next to the app")
    st = get_settings()
    target = Path(path).expanduser() if path else platform_data_dir()
    if not target.is_absolute():
        raise ValueError("an absolute path is required")
    target = target.resolve()
    if target == st.data_dir.resolve():
        return {"data_dir": str(target), "copied": False, "restart_required": False}
    if target.exists() and any(target.iterdir()):
        if not (target / "rhizome.db").exists():
            raise ValueError("target folder is not empty and does not contain a Rhizome library")
        copied = False  # switch to an existing library
    else:
        target.mkdir(parents=True, exist_ok=True)
        copied = False
        if copy and st.data_dir.exists():
            from .db.session import copy_sqlite

            for item in st.data_dir.iterdir():
                if item.name in _SKIP or item.name.startswith("rhizome.db"):
                    continue
                if item.is_dir():
                    shutil.copytree(item, target / item.name)
                else:
                    shutil.copy2(item, target / item.name)
            db = st.data_dir / "rhizome.db"
            if db.exists():
                copy_sqlite(db, target / "rhizome.db")  # consistent copy while the app is running
            copied = True
    set_data_dir_pointer(None if target == platform_data_dir().resolve() else target)
    claude = unpin_claude_configs(st.data_dir)
    return {"data_dir": str(target), "copied": copied, "restart_required": True,
            "claude_config_updated": claude}


# ---- parent watchdog ---------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(pid: int, interval: float = 2.0) -> None:
    """Exit when the desktop shell that started us is gone (crash, kill), so no orphan backend
    keeps the database open. Normal shutdown is handled by the shell itself."""
    import threading
    import time

    def loop() -> None:
        while pid_alive(pid):
            time.sleep(interval)
        clear_server_marker()
        os._exit(0)

    threading.Thread(target=loop, daemon=True, name="rhizome-parent-watch").start()

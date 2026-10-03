# SPDX-License-Identifier: Apache-2.0
"""Settings.

Everything that depends on the user's environment (paths, servers, model choices, language)
lives here and is persisted in ``<data_dir>/settings.json``. Nothing environment-specific is
hard-coded elsewhere. Precedence: environment variables (``RHIZOME_*``) > settings.json > defaults.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def platform_data_dir() -> Path:
    """The OS-conventional location (also where the data-directory pointer file lives)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Rhizome"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Rhizome"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "rhizome"


POINTER_FILE = "location.json"


def app_dir() -> Path | None:
    """Directory of the installed desktop app (frozen build), else None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def portable_dir() -> Path | None:
    """Portable mode: a file named `portable` next to the app (or one level up, beside Rhizome.exe)
    keeps all data in a `data` folder inside the installation directory."""
    base = app_dir()
    if base is None:
        return None
    for d in (base, base.parent, base.parent.parent):
        if (d / "portable").exists():
            return d / "data"
    return None


def default_data_dir() -> Path:
    """Resolution order: RHIZOME_DATA_DIR > portable install > pointer file > OS default."""
    env = os.environ.get("RHIZOME_DATA_DIR")
    if env:
        return Path(env)
    port = portable_dir()
    if port is not None:
        return port
    pointer = platform_data_dir() / POINTER_FILE
    if pointer.exists():
        try:
            target = json.loads(pointer.read_text("utf-8-sig")).get("data_dir")
            if target:
                return Path(target)
        except (OSError, ValueError):
            pass
    return platform_data_dir()


def set_data_dir_pointer(target: Path | None) -> Path:
    """Remember a custom data directory (None = back to the default). Takes effect on next start."""
    base = platform_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    pointer = base / POINTER_FILE
    if target is None or Path(target).resolve() == base.resolve():
        if pointer.exists():
            pointer.unlink()
    else:
        pointer.write_text(json.dumps({"data_dir": str(Path(target).resolve())}, ensure_ascii=False), "utf-8")
    return pointer


class RemoteTarget(BaseModel):
    """A remote machine (e.g. an HPC login node) that receives read-only snapshots."""

    name: str
    host: str  # user@host or an ssh config alias
    path: str = "~/.rhizome/rhizome.db"
    # Reuse an already-authenticated SSH connection (ControlMaster) for 2FA servers.
    control_path: str | None = None
    ssh_options: list[str] = Field(default_factory=list)


class Thresholds(BaseModel):
    merge_auto: float = 0.9  # >= : merge automatically
    merge_review: float = 0.6  # [review, auto) : review queue; below: new entity
    retro_k: int = 500
    retro_rerank_min: float = 0.3
    retro_auto_conf: float = 0.8
    topic_promote_works: int = 3
    recall_min: float = 0.25
    recall_limit: int = 5
    synthesis_sim: float = 0.55


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RHIZOME_", env_nested_delimiter="__", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    # None -> sqlite:///<data_dir>/rhizome.db ; "postgresql+psycopg://..." for the optional backend
    database_url: str | None = None
    language: Literal["auto", "en", "zh_CN"] = "auto"
    inbox_dir: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    api_url: str | None = None  # used by MCP/CLI clients; default http://host:port

    offline: bool = False  # no OpenAlex / NCBI / GitHub calls
    contact_email: str | None = None  # OpenAlex polite pool

    embedder: Literal["hashing", "bge-m3"] = "hashing"
    reranker: Literal["lexical", "bge-reranker-v2-m3"] = "lexical"
    nli: Literal["none", "mdeberta"] = "none"
    local_llm_path: Path | None = None  # GGUF file for Qwen3.5-2B; None disables
    # queue: defer to review queue / Claude via MCP. local: local small LLM. Plugins add more.
    inference_backend: str = "queue"

    thresholds: Thresholds = Field(default_factory=Thresholds)
    review_daily_new: int = 20
    review_daily_max: int = 100
    remotes: list[RemoteTarget] = Field(default_factory=list)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (env_settings, init_settings)

    # ---- derived paths -------------------------------------------------
    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'rhizome.db').as_posix()}"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def inbox(self) -> Path:
        return self.inbox_dir or (self.data_dir / "inbox")

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def base_url(self) -> str:
        return self.api_url or f"http://{self.host}:{self.port}"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.inbox, self.inbox / "done", self.inbox / "error",
                  self.logs_dir, self.backups_dir, self.models_dir):
            p.mkdir(parents=True, exist_ok=True)


SETTINGS_FILE = "settings.json"


def _settings_path(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def load_settings(data_dir: Path | None = None) -> Settings:
    data_dir = data_dir or default_data_dir()
    file_values: dict[str, Any] = {}
    p = _settings_path(data_dir)
    if p.exists():
        file_values = json.loads(p.read_text(encoding="utf-8-sig"))
    file_values["data_dir"] = data_dir
    return Settings(**file_values)


def save_settings(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude={"data_dir"})
    _settings_path(settings.data_dir).write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


_current: Settings | None = None


def get_settings() -> Settings:
    global _current
    if _current is None:
        _current = load_settings()
    return _current


def set_settings(settings: Settings) -> None:
    """Replace the process-wide settings (tests, CLI --data-dir, settings UI)."""
    global _current
    _current = settings
    _language_cache.cache_clear()


@lru_cache(maxsize=1)
def _language_cache(lang: str) -> str:
    if lang != "auto":
        return lang
    import locale

    try:
        loc = locale.getlocale()[0] or os.environ.get("LANG", "")
    except ValueError:
        loc = os.environ.get("LANG", "")
    return "zh_CN" if loc and loc.lower().startswith(("zh", "chinese")) else "en"


def ui_language(settings: Settings | None = None) -> str:
    return _language_cache((settings or get_settings()).language)

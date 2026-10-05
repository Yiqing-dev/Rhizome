# SPDX-License-Identifier: Apache-2.0
"""Settings.

Everything that depends on the user's environment (paths, servers, model choices, language)
lives here and is persisted in ``<data_dir>/settings.json``. Nothing environment-specific is
hard-coded elsewhere.

Precedence: explicit arguments (``Settings(...)``, ``--data-dir``, ``--snapshot``, test fixtures)
> environment variables (``RHIZOME_*``) > settings.json > defaults.

settings.json holds only what the user changed (:func:`update_settings`), so improved defaults in a
later version reach existing installs, and unknown keys from a newer version are kept.
"""

from __future__ import annotations

import json
import re
import logging
import os
import sys
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

log = logging.getLogger(__name__)


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


class LibraryNotFound(RuntimeError):
    """The library the pointer file names is not there (unplugged or re-lettered drive, renamed
    folder, %APPDATA% copied to a new PC). Never silently start a new empty library instead."""

    def __init__(self, path: Path):
        self.path = path
        from .i18n import _

        # settings cannot be loaded (that is the problem): language from the environment / locale
        lang = _language_cache(os.environ.get("RHIZOME_LANGUAGE") or "auto")
        super().__init__(_("config.library_missing", lang=lang, path=str(path),
                           pointer=str(platform_data_dir() / POINTER_FILE)))


def data_dir_source() -> tuple[str, Path]:
    """(source, path) with source env | portable | pointer | default.
    Resolution order: RHIZOME_DATA_DIR > portable install > pointer file > OS default."""
    env = os.environ.get("RHIZOME_DATA_DIR")
    if env:
        return "env", Path(env)
    port = portable_dir()
    if port is not None:
        return "portable", port
    pointer = platform_data_dir() / POINTER_FILE
    if pointer.exists():
        try:
            target = json.loads(pointer.read_text("utf-8-sig")).get("data_dir")
            if target:
                return "pointer", Path(target)
        except (OSError, ValueError):
            pass
    return "default", platform_data_dir()


def default_data_dir() -> Path:
    return data_dir_source()[1]


def check_data_dir() -> Path:
    """The library location, refusing a pointer target that does not exist."""
    source, path = data_dir_source()
    if source == "pointer" and not path.is_dir():
        raise LibraryNotFound(path)
    return path


def set_data_dir_pointer(target: Path | None) -> Path:
    """Remember a custom data directory (None = back to the default). Takes effect on next start."""
    base = platform_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    pointer = base / POINTER_FILE
    if target is None or Path(target).resolve() == base.resolve():
        if pointer.exists():
            pointer.unlink()
    else:
        _atomic_write(pointer, json.dumps({"data_dir": str(Path(target).resolve())}, ensure_ascii=False))
    return pointer


def _atomic_write(path: Path, text: str, backup: bool = False) -> None:
    """tmp + fsync + replace: a crash or full disk never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if backup and path.exists():
        bak = path.with_name(path.name + ".bak")
        try:
            os.replace(path, bak)
        except OSError:
            pass
    os.replace(tmp, path)


_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f]")


class RemoteTarget(BaseModel):
    """A remote machine (e.g. an HPC login node) that receives read-only snapshots. The fields end
    up on an ssh command line: no leading '-' (an option in disguise) and no whitespace or control
    characters anywhere."""

    name: str
    host: str  # user@host or an ssh config alias
    path: str = "~/.rhizome/rhizome.db"
    # Reuse an already-authenticated SSH connection (ControlMaster) for 2FA servers.
    control_path: str | None = None
    ssh_options: list[str] = Field(default_factory=list)
    timeout: int = Field(1800, ge=10, le=86400)  # seconds for the whole upload (one ssh session)

    @field_validator("name", "host", "path", "control_path")
    @classmethod
    def _plain(cls, v: str | None, info) -> str | None:
        if v is None:
            return v
        if not v or v.startswith("-") or _BAD_CHARS.search(v):
            raise ValueError(f"{info.field_name}: must not be empty, start with '-' or contain whitespace")
        return v

    @field_validator("ssh_options")
    @classmethod
    def _options(cls, v: list[str]) -> list[str]:
        for o in v:
            if not o or _BAD_CHARS.search(o) or o in ("-o", "ProxyCommand") or "ProxyCommand" in o or "LocalCommand" in o:
                raise ValueError("ssh_options: one token per entry, no whitespace, no ProxyCommand / LocalCommand")
        return v


class Thresholds(BaseModel):
    """Score bars. The defaults are for the built-in lexical models; THRESHOLD_PROFILES holds the
    starting points for the other embedders (their score scales differ), and a value you set
    yourself keeps winning across a model switch."""

    merge_auto: float = 0.9  # >= : merge automatically
    merge_review: float = 0.6  # [review, auto) : review queue; below: new entity
    retro_k: int = 500
    retro_rerank_min: float = 0.3
    retro_auto_conf: float = 0.8
    retro_queue_max: int = 30  # review items per retro-tagging run without a local judge
    topic_promote_works: int = 3
    recall_min: float = 0.25
    recall_limit: int = 5
    synthesis_sim: float = 0.55
    topic_relation_min: float = 0.35  # reranker score from which a new topic's neighbour is worth a breadth question
    code_sim_min: float = 0.2  # recall from code: vector similarity below which a snippet is ignored
    code_rel_min: float = 0.3  # ... and reranker relevance below which an asset is not shown


# Starting points per embedder (cosine of bge-m3 is denser near the top than the hashing model's).
THRESHOLD_PROFILES: dict[str, dict[str, float]] = {
    "hashing": {},
    "bge-m3": {"merge_auto": 0.92, "merge_review": 0.72, "synthesis_sim": 0.62, "recall_min": 0.35,
               "retro_rerank_min": 0.35, "topic_relation_min": 0.4, "code_sim_min": 0.3, "code_rel_min": 0.35},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RHIZOME_", env_nested_delimiter="__", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    # None -> sqlite:///<data_dir>/rhizome.db ; "postgresql+psycopg://..." for the optional backend
    database_url: str | None = None
    language: Literal["auto", "en", "zh_CN"] = "auto"
    inbox_dir: Path | None = None
    # None -> <data_dir>/backups. Point it at another disk or a synced folder: backups are closed,
    # consistent copies, unlike the live database (never sync the data dir itself while it runs).
    backup_dir: Path | None = None
    backup_keep_daily: int = Field(14, ge=1)
    host: str = "127.0.0.1"
    port: int = 8765
    api_url: str | None = None  # used by MCP/CLI clients; default http://host:port

    offline: bool = False  # no OpenAlex / NCBI / GitHub calls
    check_updates: bool = True  # once a day, ask GitHub whether a newer release exists (off when offline)
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
    _thresholds_set: set[str] = PrivateAttr(default_factory=set)  # the ones the user set explicitly

    @model_validator(mode="after")
    def _profile_thresholds(self) -> "Settings":
        self._thresholds_set = set(self.thresholds.model_fields_set)
        self.thresholds = self.thresholds_for(self.embedder)
        return self

    def thresholds_for(self, embedder: str) -> Thresholds:
        """The embedder's profile, overlaid with the values set explicitly (settings.json, env)."""
        base = dict(THRESHOLD_PROFILES.get(embedder) or {})
        base.update({k: getattr(self.thresholds, k) for k in self._thresholds_set})
        return Thresholds(**base)

    def with_embedder(self, embedder: str) -> "Settings":
        """A copy for another embedder: its thresholds follow (unless set explicitly)."""
        return self.model_copy(update={"embedder": embedder, "thresholds": self.thresholds_for(embedder)})

    @field_validator("backup_dir", "inbox_dir", "local_llm_path", mode="before")
    @classmethod
    def _absolute(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        if not Path(str(v)).expanduser().is_absolute():
            raise ValueError("must be an absolute path")
        return Path(str(v)).expanduser()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, _FileSource(settings_cls))

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
        return self.backup_dir or (self.data_dir / "backups")

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def base_url(self) -> str:
        return self.api_url or f"http://{self.host}:{self.port}"

    def ensure_dirs(self) -> None:
        if not self.data_dir.exists():
            from .system import secure_dir  # a new library folder is readable by this user only (Windows)

            secure_dir(self.data_dir)
        for p in (self.data_dir, self.raw_dir, self.inbox, self.inbox / "done", self.inbox / "error",
                  self.logs_dir, self.backups_dir, self.models_dir):
            p.mkdir(parents=True, exist_ok=True)


SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 1

# values read from settings.json for the Settings() being built (lowest-priority source)
_file_values: ContextVar[dict[str, Any]] = ContextVar("rhizome_settings_file", default={})
# problems found while loading settings.json, per data dir (shown in /system and the CLI)
_problems: dict[str, list[str]] = {}


class _FileSource(PydanticBaseSettingsSource):
    def get_field_value(self, field, field_name):  # pragma: no cover - __call__ is used instead
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(_file_values.get())


def _settings_path(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def _read_raw(data_dir: Path) -> tuple[dict[str, Any], list[str]]:
    """settings.json as written (falls back to settings.json.bak when it is unreadable)."""
    problems: list[str] = []
    p = _settings_path(data_dir)
    for cand in (p, p.with_name(p.name + ".bak")):
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            if cand != p:
                problems.append(f"{p.name} unreadable; using {cand.name}")
            return data, problems
        except (OSError, ValueError) as e:
            problems.append(f"{cand.name}: {e}")
    return {}, problems


def _build(data_dir: Path, file_values: dict[str, Any], problems: list[str]) -> Settings:
    """Settings from env + file; a field that does not validate is dropped (and reported) instead of
    making the app, the CLI and the MCP server fail to start."""
    values = {k: v for k, v in file_values.items() if k != "data_dir"}
    for _ in range(len(values) + 1):
        token = _file_values.set(values)
        try:
            return Settings(data_dir=data_dir)
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err.get("loc")} & set(values)
            if not bad:
                raise
            for k in sorted(bad):
                problems.append(f"{SETTINGS_FILE}: ignored invalid value for {k}")
                values.pop(k)
        finally:
            _file_values.reset(token)
    return Settings(data_dir=data_dir)  # pragma: no cover


def load_settings(data_dir: Path | None = None) -> Settings:
    data_dir = data_dir or default_data_dir()
    file_values, problems = _read_raw(data_dir)
    st = _build(data_dir, file_values, problems)
    _problems[str(data_dir)] = problems
    for msg in problems:
        log.warning(msg)
    return st


def settings_problems(settings: Settings | None = None) -> list[str]:
    return list(_problems.get(str((settings or get_settings()).data_dir), []))


def _defaults() -> dict[str, Any]:
    return Settings.model_construct().model_dump(mode="json", exclude={"data_dir"}) | {
        "thresholds": Thresholds().model_dump(mode="json")}


def update_settings(patch: dict[str, Any], data_dir: Path | None = None) -> Settings:
    """Persist only what changed: merge ``patch`` into the raw settings.json (``None`` resets a key,
    nested dicts merge), drop values equal to the defaults, keep unknown keys, validate, write
    atomically with a .bak, and make the result current."""
    data_dir = data_dir or get_settings().data_dir
    raw, _ = _read_raw(data_dir)
    for k, v in patch.items():
        if k == "data_dir":
            continue
        if v is None:
            raw.pop(k, None)
        elif isinstance(v, dict) and isinstance(raw.get(k), dict):
            merged = {**raw[k], **v}
            raw[k] = {kk: vv for kk, vv in merged.items() if vv is not None}
        else:
            raw[k] = v
    defaults = _defaults()
    for k in list(raw):
        if k == "thresholds" and isinstance(raw[k], dict):
            raw[k] = {kk: vv for kk, vv in raw[k].items() if defaults["thresholds"].get(kk) != vv}
            if not raw[k]:
                raw.pop(k)
        elif k in defaults and raw[k] == defaults[k]:
            raw.pop(k)
    token = _file_values.set({k: v for k, v in raw.items() if k != "settings_version"})
    try:
        Settings(data_dir=data_dir)  # validate before writing; raises on a bad value
    finally:
        _file_values.reset(token)
    raw["settings_version"] = SETTINGS_VERSION
    _atomic_write(_settings_path(data_dir), json.dumps(raw, indent=2, ensure_ascii=False), backup=True)
    st = load_settings(data_dir).model_copy(update=_overrides)
    if _current is None or _current.data_dir == data_dir:
        set_settings(st)
    return st


def save_settings(settings: Settings) -> None:
    """Persist the fields of ``settings`` that differ from the defaults (prefer update_settings)."""
    data = settings.model_dump(mode="json", exclude={"data_dir"})
    update_settings(data, settings.data_dir)


_current: Settings | None = None
# per-process overrides (`--lang`, `serve --port`): applied in memory, never written to settings.json
_overrides: dict[str, Any] = {}


def redact_url(url: str | None) -> str | None:
    """A database URL without its password (for /settings, rhz diag and logs)."""
    if not url:
        return url
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001 - an unparsable URL is not worth a crash here
        return "<redacted>"


def token_path(settings: Settings) -> Path:
    """Where the running app leaves its API token for the CLI and the MCP server."""
    return settings.data_dir / "token"


def snapshot_settings(path: Path) -> Settings:
    """The current settings (language, thresholds, embedder, models dir ...) pointed at a read-only
    snapshot file instead of the library's own database."""
    return get_settings().model_copy(update={"database_url": f"sqlite:///{Path(path).resolve().as_posix()}"})


def adopt_file_embedder() -> Settings:
    """Take the embedder from settings.json (another process may have switched it) without
    touching anything else this process holds in memory."""
    cur = get_settings()
    fresh = load_settings(cur.data_dir)
    if fresh.embedder != cur.embedder:
        cur = cur.with_embedder(fresh.embedder)
        set_settings(cur)
    return cur


def set_overrides(**values: Any) -> Settings:
    _overrides.update({k: v for k, v in values.items() if v is not None})
    st = get_settings().model_copy(update=_overrides)
    set_settings(st)
    return st


def get_settings() -> Settings:
    global _current
    if _current is None:
        _current = load_settings(check_data_dir())
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

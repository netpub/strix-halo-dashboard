from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Backend:
    name: str
    url: str
    model: str | None = None


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8787
    litellm_database_url: str | None = None
    litellm_url: str | None = None
    direct_history_path: Path = Path("data/direct.sqlite")
    direct_history_limit: int = 200
    fixtures: bool = False
    language: str = "en"
    poll_interval_s: float = 1.0
    spend_poll_interval_s: float = 2.0
    backends: list[Backend] = field(default_factory=list)
    static_dir: Path | None = None
    sys_root: Path = Path("/sys")
    proc_root: Path = Path("/proc")


def load_config(path: Path | None = None) -> Config:
    raw: dict = {}
    config_path = path or _config_path()
    if config_path is not None and config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise SystemExit(f"Configuration is not a mapping: {config_path}")
        raw = loaded

    listen = os.environ.get("HALO_LISTEN") or str(raw.get("listen") or "0.0.0.0:8787")
    host, port = _split_listen(listen)
    database = os.environ.get("HALO_LITELLM_DATABASE_URL")
    if database is None:
        database = raw.get("litellm_database_url") or None
    if isinstance(database, str) and not database.strip():
        database = None
    litellm_url = os.environ.get("HALO_LITELLM_URL")
    if litellm_url is None:
        litellm_url = str(raw.get("litellm_url") or "http://127.0.0.1:4000")
    litellm_url = litellm_url.strip() or None
    language = (os.environ.get("HALO_LANGUAGE") or str(raw.get("language") or "en")).strip().lower() or "en"

    fixtures_env = os.environ.get("HALO_FIXTURES")
    if fixtures_env is None:
        fixtures = bool(raw.get("fixtures", False))
    else:
        fixtures = fixtures_env.strip().lower() in {"1", "true", "yes", "on"}

    backends = []
    for item in raw.get("backends") or []:
        if not isinstance(item, dict) or not item.get("name") or not item.get("url"):
            raise SystemExit("Each backend entry needs a name and a url")
        backends.append(
            Backend(
                name=str(item["name"]),
                url=str(item["url"]).rstrip("/"),
                model=(str(item["model"]) if item.get("model") else None),
            )
        )

    static = os.environ.get("HALO_STATIC") or raw.get("static_dir")
    static_dir = Path(static) if static else _default_static_dir()
    if static_dir is not None and not static_dir.is_dir():
        static_dir = None

    history = Path(
        os.environ.get("HALO_DIRECT_HISTORY")
        or raw.get("direct_history_path")
        or "data/direct.sqlite"
    )
    return Config(
        host=host,
        port=port,
        litellm_database_url=database,
        litellm_url=litellm_url,
        direct_history_path=history,
        direct_history_limit=int(raw.get("direct_history_limit") or 200),
        fixtures=fixtures,
        language=language,
        poll_interval_s=float(raw.get("poll_interval_s") or 1),
        spend_poll_interval_s=float(raw.get("spend_poll_interval_s") or 2),
        backends=backends,
        static_dir=static_dir,
        sys_root=Path(os.environ.get("HALO_SYS_ROOT") or "/sys"),
        proc_root=Path(os.environ.get("HALO_PROC_ROOT") or "/proc"),
    )


def _config_path() -> Path | None:
    env = os.environ.get("HALO_CONFIG")
    if env:
        return Path(env)
    for candidate in (Path("config.yaml"), Path("/config/config.yaml")):
        if candidate.is_file():
            return candidate
    return None


def _split_listen(listen: str) -> tuple[str, int]:
    if ":" not in listen:
        raise SystemExit(f"listen must be host:port, not {listen!r}")
    host, _, port = listen.rpartition(":")
    return host or "0.0.0.0", int(port)


def _default_static_dir() -> Path | None:
    candidates = [
        Path.cwd() / "web" / "dist",
        Path(__file__).resolve().parents[2] / "web" / "dist",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None

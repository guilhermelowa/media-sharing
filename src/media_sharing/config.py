"""Configuration loading: YAML files + environment variable resolution.

Credentials are never stored in YAML. Instead ``accounts.yaml`` references
environment variables (e.g. ``token: env:BLUESKY_APP_PASSWORD``) and this module
expands them at load time.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Account

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_MEDIA_DIR = REPO_ROOT / "media"
DEFAULT_STATE_DIR = REPO_ROOT / "state"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def load_dotenv(path: Path = DEFAULT_ENV_FILE) -> None:
    """Populate ``os.environ`` from a .env file (existing vars win).

    Minimal built-in parser so we don't add a python-dotenv dependency.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Paths(BaseModel):
    inbox: Path = DEFAULT_MEDIA_DIR / "inbox"
    processed: Path = DEFAULT_MEDIA_DIR / "processed"
    failed: Path = DEFAULT_MEDIA_DIR / "failed"
    state_db: Path = DEFAULT_STATE_DIR / "state.db"
    sessions: Path = REPO_ROOT / "src" / "media_sharing" / "sessions"
    debug: Path = REPO_ROOT / "debug"
    mobile_flows: Path = REPO_ROOT / "config" / "mobile"
    artifacts: Path = REPO_ROOT / "debug" / "mobile"


class PlatformPolicy(BaseModel):
    """Per-platform fallback chain and limit metadata."""

    backends: list[str] = Field(default_factory=list)
    daily_cap: int | None = None
    min_interval_seconds: int = 0


class Settings(BaseModel):
    paths: Paths = Field(default_factory=Paths)
    accounts: list[Account] = Field(default_factory=list)
    platforms: dict[str, PlatformPolicy] = Field(default_factory=dict)
    media_public_base_url: str | None = None

    def accounts_for(self, platform: str | None = None, name: str | None = None) -> list[Account]:
        result = [a for a in self.accounts if a.enabled]
        if platform:
            result = [a for a in result if a.platform.value == platform]
        if name:
            result = [a for a in result if a.name == name]
        return result


def _expand_env(value):
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_dir: Path | None = None) -> Settings:
    load_dotenv()
    config_dir = config_dir or DEFAULT_CONFIG_DIR
    accounts_raw = _expand_env(_load_yaml(config_dir / "accounts.yaml"))
    platforms_raw = _load_yaml(config_dir / "platforms.yaml")

    accounts = [Account(**a) for a in accounts_raw.get("accounts", [])]
    policies = {
        name: PlatformPolicy(**policy)
        for name, policy in (platforms_raw.get("platforms", {}) or {}).items()
    }
    return Settings(
        accounts=accounts,
        platforms=policies,
        media_public_base_url=os.environ.get("MEDIA_PUBLIC_BASE_URL") or None,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()

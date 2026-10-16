"""Backend registry: maps names used in config to Publisher classes."""

from __future__ import annotations

from ..models import Account

# Import publisher modules.
from . import (
    aggregator,
    bluesky_api,
    instagram_mobile,
    kwai_mobile,
    kwai_playwright,
    tiktok_api,
    tiktok_mobile,
    tiktok_playwright,
    x_api,
    x_playwright,
)
from . import browser as _browser
from . import mobile as _mobile
from .base import Publisher

REGISTRY: dict[str, type[Publisher]] = {
    "bluesky_api": bluesky_api.BlueskyAPIPublisher,
    "x_api": x_api.XAPIPublisher,
    "x_playwright": x_playwright.XPlaywrightPublisher,
    "tiktok_api": tiktok_api.TikTokAPIPublisher,
    "tiktok_playwright": tiktok_playwright.TikTokPlaywrightPublisher,
    "kwai_playwright": kwai_playwright.KwaiPlaywrightPublisher,
    "aggregator": aggregator.AggregatorPublisher,
    "kwai_mobile": kwai_mobile.KwaiMobilePublisher,
    "tiktok_mobile": tiktok_mobile.TikTokMobilePublisher,
    "instagram_mobile": instagram_mobile.InstagramMobilePublisher,
    # Deferred:
    # "instagram_api": instagram_api.InstagramAPIPublisher,
    # "instagram_playwright": instagram_playwright.InstagramPlaywrightPublisher,
}

# Backends that offer an interactive browser login (name -> module).
LOGIN_BACKENDS = {
    "x_playwright": x_playwright,
    "tiktok_playwright": tiktok_playwright,
    "kwai_playwright": kwai_playwright,
}

# Backends that drive the official Android app via ADB + Maestro (name -> module).
MOBILE_BACKENDS = {
    "kwai_mobile": kwai_mobile,
    "tiktok_mobile": tiktok_mobile,
    "instagram_mobile": instagram_mobile,
}


def available() -> list[str]:
    return sorted(REGISTRY)


def build(name: str, account: Account, settings) -> Publisher:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown backend '{name}'. Available: {available()}") from None
    return cls(account, settings)


def interactive_login(backend_name: str, account: Account, settings) -> None:
    module = LOGIN_BACKENDS.get(backend_name)
    if module is None:
        raise KeyError(
            f"Backend '{backend_name}' has no interactive login. "
            f"Login-capable backends: {sorted(LOGIN_BACKENDS)}"
        )
    module.interactive_login(account, settings)


def browser_config(backend_name: str, account: Account):
    """Return the BrowserConfig used by a session-based backend."""
    module = LOGIN_BACKENDS.get(backend_name)
    if module is None:
        raise KeyError(f"Backend '{backend_name}' is not session-based.")
    return _browser.cfg(account, module.DEFAULTS)


def mobile_config(backend_name: str, account: Account):
    """Return the MaestroConfig used by a mobile backend."""
    module = MOBILE_BACKENDS.get(backend_name)
    if module is None:
        raise KeyError(f"Backend '{backend_name}' is not a mobile backend.")
    return _mobile.cfg(account, module.DEFAULTS)


def login_cookie_metadata(backend_name: str) -> tuple[list[str], set[str]]:
    """Cookie domains + required login cookie names for a session backend."""
    module = LOGIN_BACKENDS.get(backend_name)
    if module is None:
        raise KeyError(f"Backend '{backend_name}' has no session import metadata.")
    return (list(getattr(module, "COOKIE_DOMAINS", ())),
            set(getattr(module, "LOGIN_COOKIE_NAMES", ())))


def discover(backend_name: str, account: Account, settings, *, start_url=None,
             upload_path=None) -> dict:
    """Open a headed browser to help find selectors for an account."""
    return _browser.discover(settings, account, browser_config(backend_name, account),
                             start_url=start_url, upload_path=upload_path)

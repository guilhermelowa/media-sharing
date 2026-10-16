"""Shared Playwright helpers for session-based publishers (X, Kwai, ...).

Both backends follow the same flow — capture a login session once, then reuse it
to drive the site's web uploader. Selectors/URLs are supplied per platform via a
``BrowserConfig`` built from ``account.options`` (with per-platform defaults), so
calibration needs no code changes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Account, MediaItem
from .base import PublishError

log = logging.getLogger("media_sharing.browser")


@dataclass
class BrowserConfig:
    login_url: str
    upload_url: str
    file_input: str = "input[type=file]"
    caption_input: str | None = None
    publish_button: str = "button[type=submit]"
    success_url_glob: str = "**/*"
    headless: bool = True
    wait_after_click_ms: int = 3000
    editor_timeout_ms: int = 120_000
    # Optional: capture the created post id from a network response.
    response_url_regex: str | None = None
    id_json_key: str = "rest_id"
    post_url_template: str | None = None
    # Button labels (by accessible name) to click away obstructing modals/tips
    # before interacting, e.g. TikTok's "Got it" tutorial popups.
    dismiss_buttons: list[str] = field(default_factory=list)
    # Optional location picker (searchable Select). All three must be set.
    location_button: str | None = None
    location_input: str | None = None
    location_option: str = "[role=option]"
    extra: dict = field(default_factory=dict)


def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise PublishError(
            "playwright not installed; run: pip install 'media-sharing[playwright]' "
            "&& playwright install chromium",
            retryable=False, code="missing_dependency",
        ) from exc
    return sync_playwright


def session_path(settings, account: Account) -> Path:
    return Path(settings.paths.sessions) / f"{account.name}.json"


def cfg(account: Account, defaults: dict, *, overrides: dict | None = None) -> BrowserConfig:
    values = {**defaults, **(overrides or {})}
    known = {f for f in BrowserConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in values.items() if k in known}
    extra = {k: v for k, v in values.items() if k not in known}
    config = BrowserConfig(**kwargs)
    config.extra = extra
    return config


def _find_key(obj, key: str):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


# URL globs that match everything carry no signal about whether the user has
# finished signing in — treating them as a success condition would save the
# session before login completes.
_CATCH_ALL_GLOBS = {"", "*", "**", "**/*", "**/**", "**/**/*"}


def _has_url_signal(glob: str | None) -> bool:
    return bool(glob) and glob not in _CATCH_ALL_GLOBS


def _settle(page, timeout_ms: int = 10_000) -> None:
    """Give the page a moment to persist auth cookies/localStorage."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001 - best effort; page may keep polling
        log.debug("networkidle wait skipped: %s", exc)


def capture_session(settings, account: Account, config: BrowserConfig,
                    *, timeout_ms: int = 300_000) -> Path:
    """Open a headed browser for manual sign-in and persist the session."""
    sync_playwright = require_playwright()
    dest = session_path(settings, account)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(config.login_url)
        log.info("Sign in to %s in the opened browser window...", account.platform.value)
        prompt = f"Press Enter once you are logged in to {account.platform.value}... "
        if _has_url_signal(config.success_url_glob):
            try:
                page.wait_for_url(config.success_url_glob, timeout=timeout_ms)
            except Exception:  # noqa: BLE001 - user may navigate manually
                input(prompt)
        else:
            input(prompt)
        _settle(page)
        context.storage_state(path=str(dest))
        browser.close()
    log.info("Saved %s session to %s", account.platform.value, dest)
    return dest


def _dismiss_overlays(page, labels) -> None:
    """Click away tip/tutorial modals that intercept pointer events."""
    for label in labels or ():
        try:
            button = page.get_by_role("button", name=label, exact=True)
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3_000)
                log.info("dismissed modal via %r", label)
                page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001 - best effort
            log.debug("dismiss %r skipped: %s", label, exc)


def _set_location(page, config: BrowserConfig, location: str) -> None:
    page.click(config.location_button, timeout=15_000)
    page.fill(config.location_input, location)
    page.wait_for_timeout(2_000)
    option = page.locator(config.location_option).first
    option.wait_for(state="visible", timeout=15_000)
    option.click()
    log.info("set location to %r", location)
    page.wait_for_timeout(1_000)


def publish_via_browser(settings, account: Account, config: BrowserConfig,
                        media: MediaItem, text: str, *, options: dict | None = None) -> str:
    """Upload one file through the site's web UI; return the resulting post URL."""
    options = options or {}
    sync_playwright = require_playwright()
    source = session_path(settings, account)
    if not source.exists():
        raise PublishError(
            f"{account.platform.value}: no session for '{account.name}'. Run: "
            f"media-sharing login {account.platform.value} {account.name}",
            retryable=False, code="session_missing",
        )

    captured: dict = {}

    def _on_response(response):
        if not config.response_url_regex:
            return
        if not re.search(config.response_url_regex, response.url):
            return
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - non-JSON / body unavailable
            return
        value = _find_key(payload, config.id_json_key)
        if value is not None:
            captured["id"] = value

    headless = bool(config.headless)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(storage_state=str(source))
            page = context.new_page()
            if config.response_url_regex:
                page.on("response", _on_response)
            page.goto(config.upload_url, wait_until="domcontentloaded")

            page.wait_for_selector(config.file_input, state="attached", timeout=30_000)
            page.set_input_files(config.file_input, str(media.path))

            if text and config.caption_input:
                try:
                    page.wait_for_selector(config.caption_input,
                                           timeout=config.editor_timeout_ms)
                    _dismiss_overlays(page, config.dismiss_buttons)
                    page.fill(config.caption_input, text)
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: could not fill caption (%s)", account.platform.value, exc)

            location = options.get("location")
            if location and config.location_button and config.location_input:
                try:
                    _set_location(page, config, str(location))
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s: could not set location (%s)", account.platform.value, exc)

            _dismiss_overlays(page, config.dismiss_buttons)
            page.wait_for_selector(config.publish_button, timeout=config.editor_timeout_ms)
            page.click(config.publish_button, timeout=config.editor_timeout_ms)
            page.wait_for_timeout(config.wait_after_click_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - best effort, page may keep polling
                log.debug("%s: networkidle wait skipped (%s)", account.platform.value, exc)

            post_url = None
            if captured.get("id") and config.post_url_template:
                post_url = config.post_url_template.format(id=captured["id"])
            post_url = post_url or page.url
            browser.close()
    except PublishError:
        raise
    except Exception as exc:
        raise PublishError(f"{account.platform.value}: {exc}", retryable=True) from exc

    return post_url


_SCAN_JS = """
() => {
  const pick = (el, attrs) => {
    const out = {};
    for (const a of attrs) { const v = el.getAttribute(a); if (v) out[a] = v; }
    return out;
  };
  const desc = (el) => ({
    tag: el.tagName.toLowerCase(),
    ...pick(el, ['type','name','id','data-e2e','data-testid','data-test',
                 'aria-label','placeholder','accept','contenteditable','role']),
    text: (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim().slice(0, 60),
  });
  return {
    url: location.href,
    title: document.title,
    file_inputs: [...document.querySelectorAll('input[type=file]')].map(desc),
    buttons: [...document.querySelectorAll('button, [role=button], a[role=button]')]
               .map(desc).slice(0, 100),
    editables: [...document.querySelectorAll('[contenteditable=true], textarea')]
               .map(desc).slice(0, 40),
  };
}
"""


def discover(settings, account: Account, config: BrowserConfig,
             *, start_url: str | None = None, wait_forever: bool = True,
             upload_path: str | None = None) -> dict:
    """Open a headed browser to help find selectors for an account.

    Navigate (and reveal the upload dialog), then press Enter. Candidate file
    inputs, buttons and caption editors are printed and saved to debug/.

    Pass ``upload_path`` to select a local file automatically and advance to the
    editor stage (needed to see the caption box and Post button).
    """
    sync_playwright = require_playwright()
    start = start_url or config.upload_url or config.login_url
    session = session_path(settings, account)
    settings.paths.debug.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            storage_state=str(session) if session.exists() else None)
        page = context.new_page()
        page.goto(start, wait_until="domcontentloaded")
        print(f"\nOpened {start}")
        if session.exists():
            print(f"Loaded saved session: {session}")
        else:
            print(f"No saved session at {session} — "
                  f"run: media-sharing login {account.platform.value} {account.name}")

        if upload_path:
            print(f"Uploading {upload_path} so the editor (caption/Post) renders...")
            page.wait_for_selector(config.file_input, state="attached", timeout=30_000)
            page.set_input_files(config.file_input, upload_path)
            try:
                page.wait_for_load_state("networkidle", timeout=60_000)
            except Exception as exc:  # noqa: BLE001 - best effort
                log.debug("networkidle wait skipped: %s", exc)
            page.wait_for_timeout(20_000)
        else:
            print("Log in if needed, navigate to the upload screen, and reveal the "
                  "file picker / caption box.\nThen return here and press Enter.")
        if wait_forever:
            input("Press Enter to scan the page... ")

        info = page.evaluate(_SCAN_JS)
        html = page.content()
        shot = settings.paths.debug / f"{account.name}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("screenshot failed: %s", exc)
        html_path = settings.paths.debug / f"{account.name}.html"
        html_path.write_text(html, encoding="utf-8")
        browser.close()

    _print_scan(account, info, settings.paths.debug)
    return info


def _print_scan(account: Account, info: dict, debug_dir: Path) -> None:
    print(f"\n=== {account.platform.value} / {account.name} ===")
    print(f"url:   {info.get('url')}")
    print(f"title: {info.get('title')}")
    for key, label in (("file_inputs", "File inputs"),
                       ("editables", "Caption editors"),
                       ("buttons", "Buttons")):
        rows = info.get(key) or []
        print(f"\n{label} ({len(rows)}):")
        for row in rows:
            attrs = {k: v for k, v in row.items() if k not in ("tag", "text")}
            print(f"  <{row['tag']}> {attrs}  text={row.get('text')!r}")
    print(f"\nSaved debug/{account.name}.html and debug/{account.name}.png")
    print("Copy the best matches into account.options as file_input / "
          "caption_input / publish_button.")

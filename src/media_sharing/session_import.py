"""Import a real-browser login session into a Playwright ``storage_state`` file.

Automating a platform's login form is the single most bot-monitored flow, so
instead of driving it we copy the cookies you already have after logging in
manually. The resulting file has the same shape ``publish_via_browser`` already
consumes (``browser.py``), so the posting path is untouched.

Firefox is the first-class source: its ``cookies.sqlite`` is unencrypted, so no
system keyring access is needed. Chrome-family browsers work too but may require
keyring access to decrypt cookies; ``from_export_file`` accepts a Cookie-Editor
JSON export as an escape hatch.
"""

from __future__ import annotations

import http.cookiejar
import json
import logging
from pathlib import Path

from .backends.base import PublishError
from .backends.browser import session_path

log = logging.getLogger("media_sharing.session_import")

# Firefox stores sameSite as an integer in cookies.sqlite.
_FIREFOX_SAMESITE = {0: "None", 1: "Lax", 2: "Strict"}

# Cookie-Editor / similar exports use lowercase or long-form names.
_SAMESITE_STRINGS = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": None,
    "": None,
}


def normalize_samesite(value) -> str | None:
    """Coerce any sameSite encoding to Playwright's ``Strict|Lax|None``."""
    if value is None:
        return None
    if isinstance(value, int):
        return _FIREFOX_SAMESITE.get(value)
    return _SAMESITE_STRINGS.get(str(value).strip().lower(), "Lax")


def _extract_samesite(cookie: http.cookiejar.Cookie) -> str | None:
    for attr in ("SameSite", "samesite", "sameSite"):
        try:
            if cookie.has_nonstandard_attr(attr):
                return normalize_samesite(cookie.get_nonstandard_attr(attr))
        except AttributeError:  # pragma: no cover - defensive
            break
    value = getattr(cookie, "sameSite", None)
    if value is None:
        value = getattr(cookie, "samesite", None)
    return normalize_samesite(value)


# Firefox stores cookie expiry in milliseconds; Playwright wants seconds.
_FIREFOX_EXPIRES_THRESHOLD = 10_000_000_000  # any ms timestamp is > this


def _normalize_expires(value) -> int:
    """Return a Playwright-accepted expires (``-1`` or a positive second epoch)."""
    if value is None:
        return -1
    try:
        expires = int(value)
    except (TypeError, ValueError):
        return -1
    if expires > _FIREFOX_EXPIRES_THRESHOLD:
        expires //= 1000  # milliseconds -> seconds
    return expires if expires > 0 else -1


def cookie_to_storage(cookie: http.cookiejar.Cookie) -> dict:
    """Convert an ``http.cookiejar.Cookie`` to a Playwright storage_state entry."""
    try:
        http_only = bool(cookie.get_nonstandard_attr("HttpOnly", False))
    except AttributeError:  # pragma: no cover - defensive
        http_only = False
    entry = {
        "name": cookie.name,
        "value": cookie.value or "",
        "domain": cookie.domain or "",
        "path": cookie.path or "/",
        "expires": _normalize_expires(cookie.expires),
        "httpOnly": http_only,
        "secure": bool(cookie.secure),
    }
    same_site = _extract_samesite(cookie)
    if same_site is not None:  # Playwright rejects null; omit when unspecified.
        entry["sameSite"] = same_site
    return entry


def _domain_in(domain: str, suffixes) -> bool:
    normalized = (domain or "").lstrip(".").lower()
    for suffix in suffixes:
        suffix = suffix.lstrip(".").lower()
        if normalized == suffix or normalized.endswith("." + suffix):
            return True
    return False


def filter_cookies(cookies: list[dict], domains) -> list[dict]:
    if not domains:
        return list(cookies)
    return [c for c in cookies if _domain_in(c.get("domain", ""), domains)]


def from_export_file(path) -> list[dict]:
    """Load cookies from a Cookie-Editor JSON export (or an existing state file)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cookies" in raw:
        return list(raw["cookies"])
    result = []
    for c in raw:
        entry = {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": _normalize_expires(c.get("expirationDate", c.get("expires"))),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
        }
        same_site = normalize_samesite(c.get("sameSite"))
        if same_site is not None:
            entry["sameSite"] = same_site
        result.append(entry)
    return result


# browser_cookie3 only looks in ~/.mozilla/firefox; distro builds (and the
# Debian/Ubuntu package here) store profiles under ~/.config/mozilla/firefox,
# Snap and Flatpak use their own roots.
_FIREFOX_PROFILE_DIRS = (
    "~/.mozilla/firefox",
    "~/.config/mozilla/firefox",
    "~/snap/firefox/common/.mozilla/firefox",
    "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
)


def _profile_from_ini(base: Path) -> Path | None:
    """Resolve the default profile directory from a profiles.ini, if present."""
    ini = base / "profiles.ini"
    if not ini.exists():
        return None
    import configparser

    parser = configparser.ConfigParser()
    try:
        parser.read(ini)
    except configparser.Error:  # pragma: no cover - malformed ini
        return None
    path = None
    for section in parser.sections():
        if parser.has_option(section, "Default") and section.startswith("Install"):
            path = parser.get(section, "Default")
            break
    if path is None:
        for section in parser.sections():
            if section.startswith("Profile") and parser.getboolean(section, "Default", fallback=False):
                path = parser.get(section, "Path", fallback=None)
                break
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate


def _find_firefox_cookie_file() -> str | None:
    for raw in _FIREFOX_PROFILE_DIRS:
        base = Path(raw).expanduser()
        if not base.is_dir():
            continue
        profile = _profile_from_ini(base)
        if profile and (profile / "cookies.sqlite").exists():
            return str(profile / "cookies.sqlite")
        for candidate in sorted(base.glob("*.default*")):
            if (candidate / "cookies.sqlite").exists():
                return str(candidate / "cookies.sqlite")
    return None


def _firefox_cookie_file(profile: str | None) -> str | None:
    if not profile:
        return _find_firefox_cookie_file()
    p = Path(profile).expanduser()
    return str(p / "cookies.sqlite" if p.is_dir() else p)


def _load_browser_cookies(browser: str, profile: str | None):
    try:
        import browser_cookie3
    except ImportError as exc:
        raise PublishError(
            "browser_cookie3 not installed; run: uv sync --extra session_import "
            "(or pass --from-file with a Cookie-Editor export)",
            retryable=False, code="missing_dependency",
        ) from exc

    if browser == "firefox":
        try:
            return browser_cookie3.firefox(cookie_file=_firefox_cookie_file(profile))
        except Exception as exc:
            raise PublishError(
                f"could not read Firefox cookies ({exc}); make sure Firefox is "
                "installed and logged in, or pass --profile/--from-file",
                retryable=False, code="cookie_read_failed",
            ) from exc
    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        raise PublishError(
            f"unsupported browser '{browser}'; use firefox or a Cookie-Editor "
            "export via --from-file",
            retryable=False, code="bad_browser",
        )
    try:
        return loader(cookie_file=profile) if profile else loader()
    except TypeError:  # loader without a cookie_file kwarg
        return loader()
    except Exception as exc:
        raise PublishError(
            f"could not read {browser} cookies ({exc}); try --from-file with a "
            "Cookie-Editor export",
            retryable=False, code="cookie_read_failed",
        ) from exc


def load_cookies(browser: str = "firefox", profile: str | None = None,
                 export_file=None) -> list[dict]:
    if export_file:
        return from_export_file(export_file)
    jar = _load_browser_cookies(browser, profile)
    return [cookie_to_storage(cookie) for cookie in jar]


def write_session(settings, account, cookies: list[dict]) -> Path:
    dest = session_path(settings, account)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": cookies, "origins": []}
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def import_session(settings, account, *, browser: str = "firefox",
                   profile: str | None = None, export_file=None,
                   domains=(), required_names=()):
    """Import cookies, write ``sessions/<account>.json`` and report key cookies.

    Returns ``(dest, found_names, missing_names)``.
    """
    cookies = load_cookies(browser=browser, profile=profile, export_file=export_file)
    cookies = filter_cookies(cookies, domains)
    if not cookies:
        raise PublishError(
            f"no cookies found for {account.platform.value} in {browser}; make sure "
            "you are logged in there (or use --from-file with a Cookie-Editor export)",
            retryable=False, code="no_cookies",
        )
    dest = write_session(settings, account, cookies)
    found = sorted({c["name"] for c in cookies} & set(required_names))
    missing = sorted(set(required_names) - {c["name"] for c in cookies})
    return dest, found, missing


def verify_session(settings, account, config) -> tuple[bool, str]:
    """Open the uploader with the saved session and report if it's logged in."""
    from .backends.browser import require_playwright

    sync_playwright = require_playwright()
    source = session_path(settings, account)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(source))
        page = context.new_page()
        page.goto(config.upload_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(config.file_input, state="attached", timeout=20_000)
            logged_in = True
        except Exception:  # noqa: BLE001 - selector absent means likely logged out
            logged_in = False
        url = page.url
        browser.close()
    return logged_in, url

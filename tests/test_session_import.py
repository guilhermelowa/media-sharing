"""Session import: cookie -> Playwright storage_state mapping (no browser needed)."""

from __future__ import annotations

import http.cookiejar
import json

from media_sharing import session_import as si


def _cookie(name="sessionid", value="abc", domain=".tiktok.com", path="/",
            secure=True, expires=1234567890, rest=None):
    c = http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith("."),
        path=path, path_specified=True, secure=secure, expires=expires,
        discard=False, comment=None, comment_url=None, rest=rest or {}, rfc2109=False,
    )
    return c


def test_normalize_samesite():
    assert si.normalize_samesite(0) == "None"
    assert si.normalize_samesite(1) == "Lax"
    assert si.normalize_samesite(2) == "Strict"
    assert si.normalize_samesite("no_restriction") == "None"
    assert si.normalize_samesite("Strict") == "Strict"
    assert si.normalize_samesite(None) is None


def test_normalize_expires_handles_ms_and_nonpositive():
    assert si._normalize_expires(1807641899628) == 1807641899
    assert si._normalize_expires(1234567890) == 1234567890
    assert si._normalize_expires(0) == -1
    assert si._normalize_expires(-5) == -1
    assert si._normalize_expires(None) == -1


def test_cookie_to_storage_shape():
    entry = si.cookie_to_storage(_cookie(rest={"HttpOnly": True, "SameSite": "Lax"}))
    assert entry["name"] == "sessionid"
    assert entry["domain"] == ".tiktok.com"
    assert entry["expires"] == 1234567890
    assert entry["httpOnly"] is True
    assert entry["secure"] is True
    assert entry["sameSite"] == "Lax"


def test_filter_cookies_matches_subdomains_only():
    cookies = [
        {"name": "a", "domain": ".tiktok.com"},
        {"name": "b", "domain": "www.tiktok.com"},
        {"name": "c", "domain": "evil-tiktok.com"},
        {"name": "d", "domain": "example.com"},
    ]
    kept = {c["name"] for c in si.filter_cookies(cookies, ("tiktok.com",))}
    assert kept == {"a", "b"}


def test_from_export_file_converts_cookie_editor(tmp_path):
    export = [
        {"name": "sessionid", "value": "xyz", "domain": ".tiktok.com", "path": "/",
         "expirationDate": 111, "httpOnly": True, "secure": True, "sameSite": "lax"},
    ]
    f = tmp_path / "cookies.json"
    f.write_text(json.dumps(export), encoding="utf-8")
    cookies = si.from_export_file(f)
    assert cookies[0]["name"] == "sessionid"
    assert cookies[0]["expires"] == 111
    assert cookies[0]["sameSite"] == "Lax"


def test_from_export_file_passes_through_storage_state(tmp_path):
    state = {"cookies": [{"name": "sessionid", "value": "1", "domain": ".tiktok.com"}],
             "origins": []}
    f = tmp_path / "state.json"
    f.write_text(json.dumps(state), encoding="utf-8")
    assert si.from_export_file(f) == state["cookies"]

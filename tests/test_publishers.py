"""Publisher backend tests. External HTTP/SDK calls are mocked."""

from __future__ import annotations

import types

import pytest

from media_sharing.backends.aggregator import AggregatorPublisher
from media_sharing.backends.base import PublishError
from media_sharing.backends.bluesky_api import BlueskyAPIPublisher
from media_sharing.backends.kwai_playwright import KwaiPlaywrightPublisher
from media_sharing.backends.tiktok_api import TikTokAPIPublisher
from media_sharing.backends.x_api import XAPIPublisher
from media_sharing.config import Settings
from media_sharing.models import (
    Account,
    MediaItem,
    MediaType,
    Platform,
    PostMetadata,
    PostStatus,
)


def make_media(tmp_path, name="clip.mp4", media_type=MediaType.VIDEO, mime="video/mp4"):
    p = tmp_path / name
    p.write_bytes(b"x" * 1024)
    return MediaItem(path=p, sha256="deadbeef", media_type=media_type,
                     size_bytes=1024, mime_type=mime)


def account(platform, **creds):
    return Account(name="acct", platform=platform, credentials=creds)


# --- Bluesky ---------------------------------------------------------------

def test_bluesky_image(monkeypatch, tmp_path):
    captured = {}

    class FakeClient:
        def login(self, handle, password):
            captured["login"] = (handle, password)

        def send_image(self, text, image, image_alt, **kw):
            captured["text"] = text
            captured["alt"] = image_alt
            return types.SimpleNamespace(uri="at://did:plc:1/app.bsky.feed.post/rkey1",
                                         cid="cid1")

    monkeypatch.setattr("atproto.Client", FakeClient)
    pub = BlueskyAPIPublisher(
        account(Platform.BLUESKY, handle="me.bsky.social", app_password="pw"), Settings())
    media = make_media(tmp_path, "pic.jpg", MediaType.IMAGE, "image/jpeg")
    meta = PostMetadata(caption="hello", hashtags=["ai"])

    result = pub.publish(media, meta)

    assert result.status is PostStatus.PUBLISHED
    assert result.post_url == "https://bsky.app/profile/me.bsky.social/post/rkey1"
    assert "hello" in captured["text"] and "#ai" in captured["text"]


def test_bluesky_missing_credentials(tmp_path):
    pub = BlueskyAPIPublisher(account(Platform.BLUESKY), Settings())
    with pytest.raises(PublishError) as exc:
        pub.publish(make_media(tmp_path), PostMetadata(caption="x"))
    assert exc.value.retryable is False
    assert exc.value.code == "auth"


# --- X ---------------------------------------------------------------------

def test_x_small_image(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status
            self.text = ""

        def json(self):
            return self._payload

    class FakeSession:
        def post(self, url, **kw):
            calls.append(("post", url))
            if "upload" in url:
                return FakeResponse({"media_id_string": "123"})
            return FakeResponse({"data": {"id": "999"}})

        def get(self, url, **kw):
            return FakeResponse({})

    import requests
    monkeypatch.setattr(requests, "Session", FakeSession)

    pub = XAPIPublisher(account(Platform.X, api_key="k", api_secret="s",
                                access_token="t", access_secret="ts"), Settings())
    media = make_media(tmp_path, "pic.jpg", MediaType.IMAGE, "image/jpeg")
    result = pub.publish(media, PostMetadata(caption="hi"))

    assert result.status is PostStatus.PUBLISHED
    assert result.post_id == "999"
    assert result.post_url == "https://x.com/i/web/status/999"
    assert any("upload" in u for _, u in calls)


# --- TikTok ----------------------------------------------------------------

def test_tiktok_draft_mode(monkeypatch, tmp_path):
    put_calls = []
    post_calls = []

    class FakeResponse:
        def __init__(self, payload, status=200):
            self._payload = payload
            self.status_code = status
            self.text = ""

        def json(self):
            return self._payload

    import requests

    def fake_post(url, **kw):
        post_calls.append(url)
        return FakeResponse({"data": {"publish_id": "p1", "upload_url": "https://upload/x"}})

    def fake_put(url, **kw):
        put_calls.append((url, kw["headers"]["Content-Range"]))
        return FakeResponse({}, 201)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "put", fake_put)

    acct = Account(name="tiktok1", platform=Platform.TIKTOK, options={"mode": "draft"},
                   credentials={"access_token": "tok", "open_id": "o"})
    pub = TikTokAPIPublisher(acct, Settings())
    result = pub.publish(make_media(tmp_path), PostMetadata(caption="hey"))

    assert result.status is PostStatus.MANUAL_REQUIRED
    assert result.post_id == "p1"
    assert any("inbox/video/init" in u for u in post_calls)
    assert put_calls and put_calls[0][1] == "bytes 0-1023/1024"


def test_tiktok_rejects_image(tmp_path):
    pub = TikTokAPIPublisher(account(Platform.TIKTOK, access_token="t"), Settings())
    media = make_media(tmp_path, "pic.jpg", MediaType.IMAGE, "image/jpeg")
    with pytest.raises(PublishError) as exc:
        pub.publish(media, PostMetadata(caption="x"))
    assert exc.value.code == "media"


# --- Aggregator ------------------------------------------------------------

def test_aggregator_upload_post(monkeypatch, tmp_path):
    seen = {}

    class FakeResponse:
        status_code = 200
        content = b'{"request_id":"abc"}'

        def json(self):
            return {"request_id": "abc"}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: seen.update(kw) or FakeResponse())

    acct = Account(name="tiny", platform=Platform.TIKTOK,
                   options={"vendor": "upload-post"},
                   credentials={"api_key": "key", "profile": "prof"})
    pub = AggregatorPublisher(acct, Settings())
    result = pub.publish(make_media(tmp_path), PostMetadata(caption="hi"))

    assert result.status is PostStatus.PUBLISHED
    assert result.post_id == "abc"
    assert seen["headers"]["Authorization"] == "Apikey key"
    assert seen["data"]["platform[]"] == "tiktok"


# --- Kwai ------------------------------------------------------------------

def test_kwai_requires_session(tmp_path):
    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    pub = KwaiPlaywrightPublisher(account(Platform.KWAI), settings)
    assert pub.healthcheck() is False
    with pytest.raises(PublishError) as exc:
        pub.prepare()
    assert exc.value.code == "session_missing"


# --- X (Playwright) --------------------------------------------------------

def test_x_playwright_requires_session(tmp_path):
    from media_sharing.backends.x_playwright import XPlaywrightPublisher

    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    pub = XPlaywrightPublisher(account(Platform.X), settings)
    assert pub.healthcheck() is False
    with pytest.raises(PublishError) as exc:
        pub.prepare()
    assert exc.value.code == "session_missing"


def test_x_playwright_publishes_via_browser(monkeypatch, tmp_path):
    from media_sharing.backends import x_playwright as mod

    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    settings.paths.sessions.mkdir(parents=True)
    (settings.paths.sessions / "acct.json").write_text("{}")

    seen = {}

    def fake_publish(settings_, account_, config, media, text):
        seen["text"] = text
        seen["template"] = config.post_url_template
        return "https://x.com/i/web/status/777"

    monkeypatch.setattr(mod, "publish_via_browser", fake_publish)
    pub = mod.XPlaywrightPublisher(account(Platform.X), settings)
    result = pub.publish(make_media(tmp_path), PostMetadata(caption="hello", hashtags=["ai"]))

    assert result.status is PostStatus.PUBLISHED
    assert result.post_url == "https://x.com/i/web/status/777"
    assert "hello" in seen["text"] and "#ai" in seen["text"]
    assert seen["template"] == "https://x.com/i/web/status/{id}"


# --- Registry login routing ------------------------------------------------

def test_registry_login_dispatch():
    from media_sharing import backends

    assert "x_playwright" in backends.LOGIN_BACKENDS
    assert "tiktok_playwright" in backends.LOGIN_BACKENDS
    assert "bluesky_api" not in backends.LOGIN_BACKENDS
    with pytest.raises(KeyError):
        backends.interactive_login("bluesky_api", account(Platform.BLUESKY), Settings())


# --- TikTok (Playwright) ---------------------------------------------------

def test_tiktok_playwright_requires_session(tmp_path):
    from media_sharing.backends.tiktok_playwright import TikTokPlaywrightPublisher

    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    pub = TikTokPlaywrightPublisher(account(Platform.TIKTOK), settings)
    assert pub.healthcheck() is False
    with pytest.raises(PublishError) as exc:
        pub.prepare()
    assert exc.value.code == "session_missing"


# --- capture_session waits for sign-in -------------------------------------

def _fake_playwright(calls):
    class FakePage:
        def goto(self, url, **kwargs):
            calls.append(("goto", url))

        def wait_for_url(self, glob, timeout=None):
            calls.append(("wait_for_url", glob))

        def wait_for_load_state(self, state, timeout=None):
            calls.append(("load_state", state))

    class FakeContext:
        def new_page(self):
            return FakePage()

        def storage_state(self, path):
            calls.append(("storage_state", path))

    class FakeBrowser:
        def new_context(self):
            return FakeContext()

        def close(self):
            calls.append(("close", None))

    class FakeChromium:
        def launch(self, headless=True):
            calls.append(("launch", headless))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def sync_playwright():
        return FakePlaywright()

    return lambda: sync_playwright


@pytest.mark.parametrize("glob", ["", "**/*", "*", "**"])
def test_capture_session_prompts_when_glob_has_no_signal(monkeypatch, tmp_path, glob):
    from media_sharing.backends import browser as mod

    calls = []
    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    monkeypatch.setattr(mod, "require_playwright", _fake_playwright(calls))
    monkeypatch.setattr("builtins.input", lambda *a: calls.append(("input", a)))

    config = mod.BrowserConfig(login_url="https://example.test/", upload_url="x",
                               success_url_glob=glob)
    mod.capture_session(settings, account(Platform.KWAI), config)

    assert "input" in [c[0] for c in calls]
    assert "wait_for_url" not in [c[0] for c in calls]
    assert "storage_state" in [c[0] for c in calls]


def test_capture_session_waits_for_real_glob(monkeypatch, tmp_path):
    from media_sharing.backends import browser as mod

    calls = []
    settings = Settings()
    settings.paths.sessions = tmp_path / "sessions"
    monkeypatch.setattr(mod, "require_playwright", _fake_playwright(calls))
    monkeypatch.setattr("builtins.input",
                        lambda *a: pytest.fail("should not prompt"))

    config = mod.BrowserConfig(login_url="https://example.test/", upload_url="x",
                               success_url_glob="**/home**")
    mod.capture_session(settings, account(Platform.X), config)

    kinds = [c[0] for c in calls]
    assert "wait_for_url" in kinds
    assert "input" not in kinds


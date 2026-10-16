"""TikTok publisher via Playwright — for posting to your own feed.

Avoids the TikTok developer app entirely (no ToS/Privacy URL form, no app audit,
no OAuth). Authentication is a persisted session captured once by
``media-sharing login tiktok <account>``.

Selectors/URLs are configurable via ``account.options`` (see ``DEFAULTS``); these
defaults target the TikTok Studio web uploader and may need calibrating if the UI
changes. Keep volume low — automation carries ToS/ban risk.

Account options:
    headless            default True
    upload_url          override the uploader URL
    caption_input       CSS selector for the caption editor
    publish_button      CSS selector for the Post button
"""

from __future__ import annotations

from ..models import Account, MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption
from .base import Capabilities, Publisher, PublishError
from .browser import capture_session, cfg, publish_via_browser, session_path

DEFAULTS = {
    "login_url": "https://www.tiktok.com/login",
    "upload_url": "https://www.tiktok.com/tiktokstudio/upload",
    "file_input": "input[type=file]",
    "caption_input": "div[contenteditable='true']",
    "publish_button": "button[data-e2e='post_video_button']",
    "success_url_glob": "**/@*/video/**",
    "headless": True,
    "editor_timeout_ms": 180_000,
    "wait_after_click_ms": 8000,
    # Dismiss tip/tutorial modals (e.g. "New editing features added") that would
    # otherwise intercept the Post click.
    "dismiss_buttons": ["Got it"],
    # Location is a searchable Select; set via the sidecar option ``location``.
    "location_button": ".poi-search [role=combobox]",
    "location_input": "input[placeholder='Search locations']",
    "location_option": "[role=option]",
}

# Used by `media-sharing import-session` to pull a session from a real browser.
COOKIE_DOMAINS = ("tiktok.com",)
LOGIN_COOKIE_NAMES = frozenset({"sessionid", "sessionid_ss", "sid_guard", "sid_tt"})


class TikTokPlaywrightPublisher(Publisher):
    name = "tiktok_playwright"
    capabilities = Capabilities(
        platforms=(Platform.TIKTOK,),
        media_types=("video", "image"),
        needs_public_url=False,
        stores_session=True,
    )

    def _config(self):
        return cfg(self.account, DEFAULTS)

    def healthcheck(self) -> bool:
        return session_path(self.settings, self.account).exists()

    def prepare(self) -> None:
        if not self.healthcheck():
            raise PublishError(
                f"tiktok: no session for '{self.account.name}'. Run: "
                f"media-sharing login tiktok {self.account.name}",
                retryable=False, code="session_missing",
            )

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        text = compose_caption(meta, max_len=2200)
        config = self._config()
        if meta.options.get("headless") is not None:
            config.headless = bool(meta.options["headless"])
        post_url = publish_via_browser(self.settings, self.account, config, media,
                                       text, options=meta.options)
        return PublishResult(platform=Platform.TIKTOK, backend=self.name,
                             status=PostStatus.PUBLISHED, post_url=post_url)


def interactive_login(account: Account, settings) -> None:
    capture_session(settings, account, cfg(account, DEFAULTS))

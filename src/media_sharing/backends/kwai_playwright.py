"""Kwai publisher via Playwright.

Kwai has **no official posting API**, so this backend drives the web uploader in
a real browser. Authentication is a persisted session captured once by
``media-sharing login kwai <account>``.

Selectors/URLs are configurable via ``account.options`` (see ``DEFAULTS``); the
defaults are starting points and WILL need adjusting for your Kwai locale/UI.
Keep volume low — automation carries ToS/ban risk.
"""

from __future__ import annotations

from ..models import Account, MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption
from .base import Capabilities, Publisher, PublishError
from .browser import capture_session, cfg, publish_via_browser, session_path

DEFAULTS = {
    # Kwai posting is app-centric; the web surface is Kwai Studio. These are
    # placeholders — run `media-sharing discover kwai <account>` to find the
    # real URLs/selectors, then override them in the account's options.
    "login_url": "https://studio.kwai.com/",
    "upload_url": "https://studio.kwai.com/",
    "file_input": "input[type=file]",
    "caption_input": "div[contenteditable='true']",
    "publish_button": "button[type=submit]",
    # Leave empty: we don't know Studio's post-login URL, so login waits for you
    # to press Enter. Set a real post-login URL glob here once you find it.
    "success_url_glob": "",
    "headless": True,
    "editor_timeout_ms": 180_000,
    "wait_after_click_ms": 8000,
}


class KwaiPlaywrightPublisher(Publisher):
    name = "kwai_playwright"
    capabilities = Capabilities(
        platforms=(Platform.KWAI,),
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
                f"kwai: no session for '{self.account.name}'. Run: "
                f"media-sharing login kwai {self.account.name}",
                retryable=False, code="session_missing",
            )

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        text = compose_caption(meta, max_len=2000)
        config = self._config()
        if meta.options.get("headless") is not None:
            config.headless = bool(meta.options["headless"])
        post_url = publish_via_browser(self.settings, self.account, config, media, text)
        return PublishResult(platform=Platform.KWAI, backend=self.name,
                             status=PostStatus.PUBLISHED, post_url=post_url)


def interactive_login(account: Account, settings) -> None:
    capture_session(settings, account, cfg(account, DEFAULTS))

"""X (Twitter) publisher via Playwright — no API, no per-post cost.

X's API is pay-per-use (a plain post ~$0.015; a post with a URL ~$0.20), so this
backend drives the web composer in a real browser instead. Authentication is a
persisted session captured once by ``media-sharing login x <account>``.

The created post id is captured from the CreateTweet network response
(``rest_id``) to build a stable permalink. Selectors/URLs are configurable via
``account.options`` (see ``DEFAULTS``); calibrate if X changes its UI.

Account options:
    headless            default True
    upload_url          override the composer URL
    caption_input       CSS selector for the text editor
    publish_button      CSS selector for the Post button
"""

from __future__ import annotations

from ..models import Account, MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption
from .base import Capabilities, Publisher, PublishError
from .browser import capture_session, cfg, publish_via_browser, session_path

DEFAULTS = {
    "login_url": "https://x.com/i/flow/login",
    "upload_url": "https://x.com/compose/post",
    "file_input": "input[type=file]",
    "caption_input": "div[data-testid='tweetTextarea_0']",
    "publish_button": "div[data-testid='tweetButton']",
    "success_url_glob": "**/home**",
    "headless": True,
    "wait_after_click_ms": 4000,
    "response_url_regex": r"/CreateTweet",
    "id_json_key": "rest_id",
    "post_url_template": "https://x.com/i/web/status/{id}",
}


class XPlaywrightPublisher(Publisher):
    name = "x_playwright"
    capabilities = Capabilities(
        platforms=(Platform.X,),
        media_types=("image", "video"),
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
                f"x: no session for '{self.account.name}'. Run: "
                f"media-sharing login x {self.account.name}",
                retryable=False, code="session_missing",
            )

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        text = compose_caption(meta, max_len=280)
        config = self._config()
        if meta.options.get("headless") is not None:
            config.headless = bool(meta.options["headless"])
        post_url = publish_via_browser(self.settings, self.account, config, media, text)
        return PublishResult(platform=Platform.X, backend=self.name,
                             status=PostStatus.PUBLISHED, post_url=post_url)


def interactive_login(account: Account, settings) -> None:
    capture_session(settings, account, cfg(account, DEFAULTS))

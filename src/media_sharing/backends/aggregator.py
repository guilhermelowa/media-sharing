"""Aggregator backend — one HTTP call publishes to many platforms because the
vendor holds audited developer apps (notably for public TikTok).

Credentials (``account.credentials``): {api_key, profile}
Vendor selected per account via ``account.options["vendor"]``.

Supported vendors:
    upload-post  Direct multipart file upload; no public URL needed. (default)
    ayrshare     Requires media URLs (https URLs); set MEDIA_PUBLIC_BASE_URL or
                 pass ``options["media_url"]``. Also needs BYO X credentials since
                 2026-03-31.

None of the major vendors support Kwai, so this backend must never be used as a
Kwai fallback.
"""

from __future__ import annotations

from ..models import MediaItem, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption, is_video
from .base import Capabilities, Publisher, PublishError

UPLOAD_POST_URL = "https://api.upload-post.com/api/upload"
AYRSHARE_URL = "https://api.ayrshare.com/api/post"

# vendor-specific platform identifiers
AYRSHARE_PLATFORM = {
    "x": "twitter",
    "bluesky": "bluesky",
    "tiktok": "tiktok",
    "instagram": "instagram",
}


class AggregatorPublisher(Publisher):
    name = "aggregator"
    capabilities = Capabilities(
        platforms=(),
        media_types=("image", "video"),
        needs_public_url=False,
    )

    def _vendor(self) -> str:
        return str(self.account.options.get("vendor", "upload-post")).lower()

    def _api_key(self) -> str:
        key = self.account.credentials.get("api_key")
        if not key:
            raise PublishError("aggregator: missing api_key", retryable=False, code="auth")
        return key

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        vendor = self._vendor()
        if vendor == "upload-post":
            return self._publish_upload_post(media, meta)
        if vendor == "ayrshare":
            return self._publish_ayrshare(media, meta)
        raise PublishError(f"aggregator: unknown vendor '{vendor}'",
                           retryable=False, code="config")

    def _publish_upload_post(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        import requests

        platform = self.account.platform.value
        text = compose_caption(meta, max_len=2200)
        profile = self.account.credentials.get("profile", "")
        if not profile:
            raise PublishError("aggregator/upload-post: missing profile",
                               retryable=False, code="auth")

        data = {"user": profile, "platform[]": platform, "title": text}
        mime = media.mime_type or "application/octet-stream"
        field = "video" if is_video(media) else "photos[]"
        with media.path.open("rb") as fh:
            files = {field: (media.filename, fh, mime)}
            try:
                resp = requests.post(
                    UPLOAD_POST_URL,
                    headers={"Authorization": f"Apikey {self._api_key()}"},
                    data=data, files=files, timeout=180,
                )
            except Exception as exc:
                raise PublishError(f"aggregator/upload-post: {exc}", retryable=True) from exc

        if resp.status_code >= 400:
            retryable = resp.status_code >= 500 or resp.status_code == 429
            raise PublishError(
                f"aggregator/upload-post HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable, code=str(resp.status_code),
            )
        body = resp.json() if resp.content else {}
        request_id = str(body.get("request_id") or body.get("id") or "")
        return PublishResult(
            platform=self.account.platform, backend=self.name,
            status=PostStatus.PUBLISHED, post_id=request_id or None,
            detail="submitted via upload-post",
        )

    def _publish_ayrshare(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        import requests

        media_url = meta.options.get("media_url")
        if not media_url:
            base = getattr(self.settings, "media_public_base_url", None)
            if not base:
                raise PublishError(
                    "aggregator/ayrshare needs a public media URL: set "
                    "options['media_url'] or MEDIA_PUBLIC_BASE_URL",
                    retryable=False, code="needs_public_url",
                )
            media_url = f"{base.rstrip('/')}/{media.filename}"

        payload = {
            "post": compose_caption(meta, max_len=2200),
            "platforms": [AYRSHARE_PLATFORM.get(self.account.platform.value,
                                                self.account.platform.value)],
            "mediaUrls": [media_url],
        }
        try:
            resp = requests.post(
                AYRSHARE_URL,
                headers={"Authorization": f"Bearer {self._api_key()}",
                         "Content-Type": "application/json"},
                json=payload, timeout=120,
            )
        except Exception as exc:
            raise PublishError(f"aggregator/ayrshare: {exc}", retryable=True) from exc

        if resp.status_code >= 400:
            retryable = resp.status_code >= 500 or resp.status_code == 429
            raise PublishError(
                f"aggregator/ayrshare HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable, code=str(resp.status_code),
            )
        body = resp.json()
        post_url = None
        for entry in body.get("postIds", []) or []:
            if entry.get("status") == "success":
                post_url = entry.get("postUrl")
                break
        return PublishResult(
            platform=self.account.platform, backend=self.name,
            status=PostStatus.PUBLISHED, post_url=post_url,
            detail="submitted via ayrshare",
        )

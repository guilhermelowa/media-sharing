"""Bluesky publisher (AT Protocol).

Credentials (``account.credentials``):
    handle        e.g. "my.bsky.social"
    app_password  an app password (https://bsky.app/settings/app-passwords)

Images and videos are uploaded directly as blobs — no public URL required.
Dep: ``atproto`` (pip extra: media-sharing[bluesky]).
"""

from __future__ import annotations

from ..models import MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption, is_image, is_video
from .base import Capabilities, Publisher, PublishError

TEXT_LIMIT = 300


class BlueskyAPIPublisher(Publisher):
    name = "bluesky_api"
    capabilities = Capabilities(
        platforms=(Platform.BLUESKY,),
        media_types=("image", "video"),
        needs_public_url=False,
    )

    def _client(self):
        try:
            from atproto import Client
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise PublishError(
                "atproto not installed; run: pip install 'media-sharing[bluesky]'",
                retryable=False, code="missing_dependency",
            ) from exc

        creds = self.account.credentials
        handle = creds.get("handle")
        password = creds.get("app_password")
        if not handle or not password:
            raise PublishError("bluesky: missing handle/app_password",
                               retryable=False, code="auth")
        client = Client()
        client.login(handle, password)
        return client, handle

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        text = compose_caption(meta, max_len=TEXT_LIMIT)
        alt = str(meta.options.get("alt", ""))
        data = media.path.read_bytes()
        client, handle = self._client()

        try:
            if is_video(media):
                resp = client.send_video(text=text, video=data, video_alt=alt or None)
            elif is_image(media):
                resp = client.send_image(text=text, image=data, image_alt=alt)
            else:
                raise PublishError(f"unsupported media type: {media.media_type}",
                                   retryable=False, code="media")
        except PublishError:
            raise
        except Exception as exc:
            raise PublishError(f"bluesky: {exc}", retryable=True) from exc

        rkey = resp.uri.rsplit("/", 1)[-1]
        return PublishResult(
            platform=Platform.BLUESKY,
            backend=self.name,
            status=PostStatus.PUBLISHED,
            post_id=resp.uri,
            post_url=f"https://bsky.app/profile/{handle}/post/{rkey}",
        )

"""X (Twitter) publisher.

Credentials (``account.credentials``) — OAuth 1.0a user context:
    api_key, api_secret, access_token, access_secret

Media is uploaded directly (v1.1 chunked media upload), then a Post is created
via the v2 API. No public URL required.

Cost note (2026, pay-per-use): a plain post is ~$0.015; a post whose text
contains a URL is ~$0.20. Keep links out of ``caption`` if you want the cheap
tier. Dep: ``requests-oauthlib`` (pip extra: media-sharing[x]).
"""

from __future__ import annotations

import time

from ..models import MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption, is_video
from .base import Capabilities, Publisher, PublishError

TEXT_LIMIT = 280
UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEETS_URL = "https://api.x.com/2/tweets"
CHUNK_SIZE = 1 << 20  # 1 MiB


def _media_category(media: MediaItem) -> str:
    if is_video(media):
        return "tweet_video"
    if media.mime_type == "image/gif":
        return "tweet_gif"
    return "tweet_image"


class XAPIPublisher(Publisher):
    name = "x_api"
    capabilities = Capabilities(
        platforms=(Platform.X,),
        media_types=("image", "video"),
        needs_public_url=False,
    )

    def _auth(self):
        try:
            from requests_oauthlib import OAuth1
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise PublishError(
                "requests-oauthlib not installed; run: pip install 'media-sharing[x]'",
                retryable=False, code="missing_dependency",
            ) from exc

        c = self.account.credentials
        required = ("api_key", "api_secret", "access_token", "access_secret")
        missing = [k for k in required if not c.get(k)]
        if missing:
            raise PublishError(f"x: missing credentials {missing}",
                               retryable=False, code="auth")
        return OAuth1(c["api_key"], c["api_secret"], c["access_token"], c["access_secret"])

    def _upload_media(self, session, auth, media: MediaItem) -> str:
        data = media.path.read_bytes()
        category = _media_category(media)

        # Small images can be uploaded in a single request.
        if not is_video(media) and len(data) <= 5 * 1024 * 1024:
            resp = session.post(
                UPLOAD_URL, auth=auth, files={"media": (media.filename, data)},
                data={"media_category": category}, timeout=60,
            )
            _raise_for_status(resp)
            return _media_id(resp.json())

        # Chunked upload (required for video).
        resp = session.post(UPLOAD_URL, auth=auth, data={
            "command": "INIT",
            "total_bytes": len(data),
            "media_type": media.mime_type or "application/octet-stream",
            "media_category": category,
        }, timeout=60)
        _raise_for_status(resp)
        media_id = _media_id(resp.json())

        for index, start in enumerate(range(0, len(data), CHUNK_SIZE)):
            chunk = data[start:start + CHUNK_SIZE]
            resp = session.post(UPLOAD_URL, auth=auth, data={
                "command": "APPEND", "media_id": media_id, "segment_index": index,
            }, files={"media": chunk}, timeout=120)
            _raise_for_status(resp)

        resp = session.post(UPLOAD_URL, auth=auth,
                            data={"command": "FINALIZE", "media_id": media_id}, timeout=60)
        _raise_for_status(resp)
        _await_processing(session, auth, media_id, resp.json())
        return media_id

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        import requests

        auth = self._auth()
        text = compose_caption(meta, max_len=TEXT_LIMIT)
        session = requests.Session()

        try:
            media_id = self._upload_media(session, auth, media)
            resp = session.post(TWEETS_URL, auth=auth, timeout=60,
                                json={"text": text, "media": {"media_ids": [media_id]}})
            _raise_for_status(resp)
            post_id = resp.json()["data"]["id"]
        except PublishError:
            raise
        except Exception as exc:
            raise PublishError(f"x: {exc}", retryable=True) from exc

        return PublishResult(
            platform=Platform.X,
            backend=self.name,
            status=PostStatus.PUBLISHED,
            post_id=post_id,
            post_url=f"https://x.com/i/web/status/{post_id}",
        )


def _media_id(payload: dict) -> str:
    return str(payload.get("media_id_string") or payload.get("media_id"))


def _raise_for_status(resp) -> None:
    if resp.status_code >= 400:
        retryable = resp.status_code in (429, 500, 502, 503, 504)
        raise PublishError(f"x HTTP {resp.status_code}: {resp.text[:300]}",
                           retryable=retryable, code=str(resp.status_code))
    # v1.1 media endpoints can return 200 with an error body.
    try:
        body = resp.json()
    except ValueError:
        return
    if isinstance(body, dict) and "error" in body:
        raise PublishError(f"x API error: {body['error']}", retryable=False, code="api")


def _await_processing(session, auth, media_id: str, payload: dict,
                      *, max_wait: int = 300) -> None:
    info = (payload or {}).get("processing_info")
    waited = 0
    while info and info.get("state") in ("pending", "in_progress"):
        delay = int(info.get("check_after_secs", 5))
        time.sleep(delay)
        waited += delay
        if waited > max_wait:
            raise PublishError("x: media processing timed out", retryable=True,
                               code="processing_timeout")
        resp = session.get(UPLOAD_URL, auth=auth,
                           params={"command": "STATUS", "media_id": media_id}, timeout=60)
        _raise_for_status(resp)
        info = resp.json().get("processing_info")
    if info and info.get("state") == "failed":
        raise PublishError(f"x: media processing failed: {info.get('error')}",
                           retryable=False, code="processing_failed")

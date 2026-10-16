"""TikTok publisher (Content Posting API).

Credentials (``account.credentials``):
    access_token  user access token with scope video.publish or video.upload
    open_id       the authorized user's open_id

Account options (``account.options``):
    mode            "draft" (default, posts to the inbox for in-app publish) or
                    "direct" (goes live; requires an audited app)
    privacy_level   default "PUBLIC_TO_EVERYONE" (direct mode only)
    disable_duet / disable_comment / disable_stitch / is_aigc  (optional bools)

GATE: unaudited apps can only post SELF_ONLY to private accounts. Draft mode
always works and returns ``PostStatus.MANUAL_REQUIRED`` (the operator taps
publish in-app). For public direct posts without an audit, route this platform
through the ``aggregator`` backend instead.
"""

from __future__ import annotations

import math
import time

from ..models import MediaItem, Platform, PostMetadata, PostStatus, PublishResult
from ..utils import compose_caption, is_video
from .base import Capabilities, Publisher, PublishError

API = "https://open.tiktokapis.com/v2"
TEXT_LIMIT = 2200
MIN_CHUNK = 5 << 20
MAX_CHUNK = 64 << 20


class TikTokAPIPublisher(Publisher):
    name = "tiktok_api"
    capabilities = Capabilities(
        platforms=(Platform.TIKTOK,),
        media_types=("video",),
        needs_public_url=False,
    )

    def _token(self) -> str:
        token = self.account.credentials.get("access_token")
        if not token:
            raise PublishError("tiktok: missing access_token", retryable=False, code="auth")
        return token

    def _post(self, path: str, body: dict) -> dict:
        import requests

        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        resp = requests.post(f"{API}{path}", headers=headers, json=body, timeout=60)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise PublishError(f"tiktok HTTP {resp.status_code}: {resp.text[:300]}",
                               retryable=True, code=str(resp.status_code))
        if resp.status_code >= 400:
            raise PublishError(f"tiktok HTTP {resp.status_code}: {resp.text[:300]}",
                               retryable=False, code=str(resp.status_code))
        payload = resp.json()
        error = payload.get("error", {})
        if error.get("code") not in (None, "ok", ""):
            raise PublishError(f"tiktok API error: {error}", retryable=False,
                               code=error.get("code"))
        return payload.get("data", {})

    def _chunk_plan(self, size: int) -> tuple[int, int]:
        if size <= MAX_CHUNK:
            return size, 1
        chunks = math.ceil(size / MAX_CHUNK)
        return MAX_CHUNK, chunks

    def _upload(self, upload_url: str, media: MediaItem, chunk_size: int,
                chunk_count: int) -> None:
        import requests

        data = media.path.read_bytes()
        for index in range(chunk_count):
            start = index * chunk_size
            chunk = data[start:start + chunk_size]
            end = start + len(chunk) - 1
            resp = requests.put(
                upload_url, data=chunk, timeout=180,
                headers={
                    "Content-Type": media.mime_type or "video/mp4",
                    "Content-Range": f"bytes {start}-{end}/{len(data)}",
                },
            )
            if resp.status_code >= 400:
                retryable = resp.status_code >= 500 or resp.status_code == 429
                raise PublishError(f"tiktok upload HTTP {resp.status_code}: {resp.text[:200]}",
                                   retryable=retryable, code=str(resp.status_code))

    def _await_status(self, publish_id: str, *, max_wait: int = 300) -> str:
        waited = 0
        while waited <= max_wait:
            data = self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status")
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return status
            if status == "FAILED":
                raise PublishError(f"tiktok publish failed: {data.get('fail_reason')}",
                                   retryable=False, code="publish_failed")
            time.sleep(5)
            waited += 5
        raise PublishError("tiktok: publish status timed out", retryable=True,
                           code="status_timeout")

    def publish(self, media: MediaItem, meta: PostMetadata) -> PublishResult:
        if not is_video(media):
            raise PublishError("tiktok_api supports video only; use the aggregator backend "
                               "for photos", retryable=False, code="media")

        mode = str(self.account.options.get("mode", "draft")).lower()
        size = media.size_bytes or media.path.stat().st_size
        chunk_size, chunk_count = self._chunk_plan(size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": chunk_count,
        }

        if mode == "direct":
            post_info = {
                "title": compose_caption(meta, max_len=TEXT_LIMIT),
                "privacy_level": meta.options.get("privacy_level", "PUBLIC_TO_EVERYONE"),
                "disable_duet": bool(meta.options.get("disable_duet", False)),
                "disable_comment": bool(meta.options.get("disable_comment", False)),
                "disable_stitch": bool(meta.options.get("disable_stitch", False)),
            }
            data = self._post("/post/publish/video/init/",
                              {"post_info": post_info, "source_info": source_info})
        else:
            data = self._post("/post/publish/inbox/video/init/", {"source_info": source_info})

        upload_url = data.get("upload_url")
        publish_id = data.get("publish_id")
        if not upload_url or not publish_id:
            raise PublishError(f"tiktok: bad init response {data}", retryable=False,
                               code="bad_response")

        self._upload(upload_url, media, chunk_size, chunk_count)

        if mode != "direct":
            return PublishResult(
                platform=Platform.TIKTOK, backend=self.name,
                status=PostStatus.MANUAL_REQUIRED, post_id=publish_id,
                detail="uploaded to TikTok inbox; publish in-app",
            )

        self._await_status(publish_id)
        return PublishResult(
            platform=Platform.TIKTOK, backend=self.name,
            status=PostStatus.PUBLISHED, post_id=publish_id,
            post_url=f"https://www.tiktok.com/@{self.account.credentials.get('username', '')}",
        )
